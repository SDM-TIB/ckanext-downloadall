import tempfile
import zipfile
import os
import urlparse
import hashlib
import io

import requests
from ckanapi import LocalCKAN
import ckanapi.datapackage

from ckan import model
from ckan.plugins.toolkit import get_action


log = __import__('logging').getLogger(__name__)


def update_zip(package_id):
    '''
    Create/update the a dataset's zip resource, containing the other resources
    and some metadata.
    '''
    # TODO deal with private datasets - 'ignore_auth': True
    context = {'model': model, 'session': model.Session}
    dataset = get_action('package_show')(context, {'id': package_id})
    log.debug('Updating zip {}'.format(dataset['name']))

    # 'filename' = "{0}.zip".format(dataset['name'])
    with tempfile.NamedTemporaryFile() as fp:
        existing_zip_resource, filesize = write_zip(fp, package_id)

        # Upload resource to CKAN as a new/updated resource
        registry = LocalCKAN()
        fp.seek(0)
        resource = dict(
            package_id=dataset['id'],
            url='dummy-value',
            upload=fp,
            name=u'All resource data',
            downloadall_metadata_modified=dataset['metadata_modified']
        )

        if not existing_zip_resource:
            log.debug('Writing new zip resource - {}'.format(dataset['name']))
            registry.action.resource_create(**resource)
        else:
            # TODO update the existing zip resource (using patch?)
            log.debug('Updating zip resource - {}'.format(dataset['name']))
            registry.action.resource_patch(
                id=existing_zip_resource['id'],
                **resource)


def write_zip(fp, package_id):
    '''
    Downloads resources and writes the zip file.

    :param fp: Open file that the zip can be written to
    '''
    with zipfile.ZipFile(fp, 'w', zipfile.ZIP_DEFLATED, allowZip64=True) \
            as zipf:
        context = {'model': model, 'session': model.Session}
        dataset = get_action('package_show')(
            context, {'id': package_id})

        # Ignore a pre-existing zip resource
        existing_zip_resource = None
        for res in dataset['resources'][:]:
            if res.get('downloadall_metadata_modified'):
                if existing_zip_resource:
                    log.error(
                        'Multiple "Download all zip" resources in dataset!')
                existing_zip_resource = res
                dataset['resources'].remove(res)

        # download all the data and write it to the zip
        # (requires https://github.com/ckan/ckanapi/commit/7ed29c0 )
        ckanapi.datapackage.populate_datastore_fields(
            ckan=LocalCKAN(), dataset=dataset)
        with io.StringIO() as error_file:
            datapackage_dir, datapackage, json_path = \
                ckanapi.datapackage.create_datapackage(
                    record=dataset, base_path='/tmp', stderr=error_file)
            errors = error_file.getvalue()
        if errors:
            log.error('Error in create_datapackage(): {}'.format(errors))
            raise Exception('Error in create_datapackage(): {}'.format(errors))

        # copy the files into the zip
        for i, res in enumerate(datapackage['resources']):
            log.debug('Copying into zip resource {}/{}: {}'.format(
                i + 1, len(datapackage['resources']), res['path']))
            zipf.write(os.path.join(datapackage_dir, res['path']))

    statinfo = os.stat(fp.name)
    filesize = statinfo.st_size

    log.info('Zip created: {} {} bytes'.format(fp.name, filesize))

    return existing_zip_resource, filesize
