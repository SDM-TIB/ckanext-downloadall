import copy
import hashlib
import math
import os
import tempfile
import zipfile

import ckanapi
import ckanapi.datapackage
import requests
import six
from ckan import model
from ckan.lib import uploader
from ckan.plugins.toolkit import get_action, config
from ckan.logic import NotFound
import time
from datetime import datetime


log = __import__('logging').getLogger(__name__)


def parse_metadata_modified_to_date_time(metadata_modified):
    '''
    Convert a metadata_modified timestamp string to a tuple suitable for
    zipfile.ZipInfo.date_time.

    :param metadata_modified: ISO format timestamp string (e.g., '2024-03-25T10:30:00.123456')
    :return: Tuple of (year, month, day, hour, minute, second) or None if parsing fails
    '''
    if not metadata_modified:
        log.debug('metadata_modified is empty or None')
        return None

    log.debug('Attempting to parse metadata_modified: "{}"'.format(metadata_modified))

    try:
        # Parse ISO format timestamp (handles both with and without microseconds)
        if 'T' in metadata_modified:
            # ISO format with T separator
            dt_str = metadata_modified.split('.')[0]  # Remove microseconds if present
            log.debug('After removing microseconds: "{}"'.format(dt_str))
            dt = datetime.strptime(dt_str, '%Y-%m-%dT%H:%M:%S')
        else:
            # Try parsing without time component
            dt = datetime.strptime(metadata_modified.split()[0], '%Y-%m-%d')

        date_tuple = (dt.year, dt.month, dt.day, dt.hour, dt.minute, dt.second)
        log.info('Successfully parsed metadata_modified "{}" to date_time: {}'.format(
            metadata_modified, date_tuple))
        return date_tuple
    except (ValueError, AttributeError) as e:
        log.error('Could not parse metadata_modified "{}": {}'.format(
            metadata_modified, str(e)))
        return None


def update_zip(package_id, skip_if_no_changes=True):
    '''
    Create/update a dataset's zip resource, containing the other resources
    and some metadata.

    :param skip_if_no_changes: If true, and there is an existing zip for this
        dataset, it will compare a freshly generated package.json against what
        is in the existing zip, and if there are no changes (ignoring the
        Download All Zip) then it will skip downloading the resources and
        updating the zip.
    '''
    # TODO deal with private datasets - 'ignore_auth': True
    site_user = get_action('get_site_user')({'ignore_auth': True}, {})
    context = {
        'model': model,
        'session': model.Session,
        'user': site_user['name'],
        'ignore_auth': False,
    }

    try:
        dataset = get_action('package_show')(context, {'id': package_id})
    except NotFound:
        log.warning(
            'Package %s not found - it may have been deleted or the job '
            'was enqueued before the dataset was committed to the database. '
            'Skipping zip update.', package_id
        )
        return

    log.debug('Updating zip: {}'.format(dataset['name']))

    datapackage, ckan_and_datapackage_resources, existing_zip_resource = \
        generate_datapackage_json(package_id)

    if skip_if_no_changes and existing_zip_resource and \
            not has_datapackage_changed_significantly(
                datapackage, ckan_and_datapackage_resources,
                existing_zip_resource):
        log.info('Skipping updating the zip - the datapackage.json is not '
                 'changed sufficiently: {}'.format(dataset['name']))
        return

    prefix = "{}-".format(dataset['name'])
    with tempfile.NamedTemporaryFile(prefix=prefix, suffix='.zip') as fp:
        write_zip(fp, datapackage, ckan_and_datapackage_resources,
                  dataset_metadata_modified=dataset.get('metadata_modified'))

        # Upload resource to CKAN as a new/updated resource
        local_ckan = ckanapi.LocalCKAN()
        fp.seek(0)
        resource = dict(
            package_id=dataset['id'],
            url='dummy-value',
            upload=fp,
            name='All resource data',
            format='ZIP',
            downloadall_metadata_modified=dataset['metadata_modified'],
            downloadall_datapackage_hash=hash_datapackage(datapackage)
        )

        if not existing_zip_resource:
            log.debug('Writing new zip resource - {}'.format(dataset['name']))
            local_ckan.action.resource_create(**resource)
        else:
            # TODO update the existing zip resource (using patch?)
            log.debug('Updating zip resource - {}'.format(dataset['name']))
            local_ckan.action.resource_patch(
                id=existing_zip_resource['id'],
                **resource)


class DownloadError(Exception):
    pass


def has_datapackage_changed_significantly(
        datapackage, ckan_and_datapackage_resources, existing_zip_resource):
    '''Compare the freshly generated datapackage with the existing one and work
    out if it is changed enough to warrant regenerating the zip.

    :returns bool: True if the data package has really changed and needs
        regenerating
    '''
    assert existing_zip_resource
    new_hash = hash_datapackage(datapackage)
    old_hash = existing_zip_resource.get('downloadall_datapackage_hash')
    return new_hash != old_hash


def hash_datapackage(datapackage):
    '''Returns a hash of the canonized version of the given datapackage
    (metadata).
    '''
    canonized = canonized_datapackage(datapackage)
    m = hashlib.md5(six.text_type(make_hashable(canonized)).encode('utf8'))
    return m.hexdigest()


def make_hashable(obj):
    if isinstance(obj, (tuple, list)):
        return tuple((make_hashable(e) for e in obj))
    if isinstance(obj, dict):
        return tuple(sorted((k, make_hashable(v)) for k, v in list(obj.items())))
    return obj


def canonized_datapackage(datapackage):
    '''
    The given datapackage is 'canonized', so that an exsting one can be
    compared with a freshly generated one, to see if the zip needs
    regenerating.

    Datapackages resources have either:
    * local paths (downloaded into the package) OR
    * OR remote paths (URLs)
    To allow datapackages to be compared, the canonization converts local
    resources to remote ones.
    '''
    datapackage_ = copy.deepcopy(datapackage)
    # convert resources to remote paths
    # i.e.
    #
    #   "path": "annual-.csv", "sources": [
    #     {
    #       "path": "https://example.com/file.csv",
    #       "title": "annual.csv"
    #     }
    #   ],
    #
    # ->
    #
    #   "path": "https://example.com/file.csv",
    for res in datapackage_.get('resources', []):
        try:
            remote_path = res['sources'][0]['path']
        except KeyError:
            continue
        res['path'] = remote_path
        del res['sources']
    return datapackage_


def generate_datapackage_json(package_id):
    '''Generates the datapackage - metadata that would be saved as
    datapackage.json.
    '''
    site_user = get_action('get_site_user')({'ignore_auth': True}, {})
    context = {
        'model': model,
        'session': model.Session,
        'user': site_user['name'],
        'ignore_auth': False,
    }
    dataset = get_action('package_show')(
        context, {'id': package_id})

    # filter out resources that are not suitable for inclusion in the data
    # package
    local_ckan = ckanapi.LocalCKAN()
    dataset, resources_to_include, existing_zip_resource = \
        remove_resources_that_should_not_be_included_in_the_datapackage(
            dataset)

    # get the datapackage (metadata)
    datapackage = ckanapi.datapackage.dataset_to_datapackage(dataset)

    # populate datapackage with the schema from the Datastore data
    # dictionary
    ckan_and_datapackage_resources = list(zip(
        resources_to_include,
        datapackage.get('resources', [])))
    for res, datapackage_res in ckan_and_datapackage_resources:
        ckanapi.datapackage.populate_datastore_res_fields(
            ckan=local_ckan, res=res)
        ckanapi.datapackage.populate_schema_from_datastore(
            cres=res, dres=datapackage_res)

    # add in any other dataset fields, if configured
    fields_to_include = config.get(
        'ckanext.downloadall.dataset_fields_to_add_to_datapackage',
        '').split()
    for key in fields_to_include:
        datapackage[key] = dataset.get(key)

    return (datapackage, ckan_and_datapackage_resources,
            existing_zip_resource)


def write_zip(fp, datapackage, ckan_and_datapackage_resources, dataset_metadata_modified=None):
    '''
    Downloads resources and writes the zip file.

    :param fp: Open file that the zip can be written to
    :param dataset_metadata_modified: Dataset's metadata_modified timestamp for datapackage.json
    '''
    with zipfile.ZipFile(fp, 'w', zipfile.ZIP_DEFLATED, allowZip64=True) \
            as zipf:
        i = 0
        for res, dres in ckan_and_datapackage_resources:
            i += 1

            log.debug('Downloading resource {}/{}: {}'
                      .format(i, len(ckan_and_datapackage_resources),
                              res['url']))
            # ckanapi.datapackage.resource_filename() requires 'format' to be
            # present; default to empty string when the resource has none.
            dres.setdefault('format', '')
            filename = ckanapi.datapackage.resource_filename(dres)
            try:
                download_resource_into_zip(
                    res['url'], filename, zipf,
                    resource_id=res.get('id'),
                    package_id=res.get('package_id'),
                    metadata_modified=res.get('metadata_modified'))
            except DownloadError:
                # The dres['path'] is left as the url - i.e. an 'external
                # resource' of the data package.
                continue

            save_local_path_in_datapackage_resource(dres, res, filename)

            # TODO optimize using the file_hash

        # Add the datapackage.json
        write_datapackage_json(datapackage, zipf, dataset_metadata_modified)

    statinfo = os.stat(fp.name)
    filesize = statinfo.st_size

    log.info('Zip created: {} {} bytes'.format(fp.name, filesize))

    return filesize


def save_local_path_in_datapackage_resource(datapackage_resource, res,
                                            filename):
    # save path in datapackage.json - i.e. now pointing at the file
    # bundled in the data package zip
    title = datapackage_resource.get('title') or res.get('title') \
        or res.get('name', '')
    datapackage_resource['sources'] = [
        {'title': title, 'path': datapackage_resource['path']}]
    datapackage_resource['path'] = filename


def get_resource_size(url, filepath=None):
    """
    Get the size of a resource in bytes.

    :param url: URL of the resource
    :param filepath: Local file path (if resource is uploaded locally)
    :return: Size in bytes, or None if size cannot be determined
    """
    # Try local file first if filepath provided
    if filepath and os.path.exists(filepath):
        try:
            return os.path.getsize(filepath)
        except OSError as e:
            log.warning('Could not get size of local file {}: {}'.format(
                filepath, str(e)))
            return None

    # Try HEAD request for remote resource
    try:
        response = requests.head(url, allow_redirects=True, timeout=10)
        response.raise_for_status()
        content_length = response.headers.get('Content-Length')
        if content_length:
            return int(content_length)
    except (requests.RequestException, ValueError) as e:
        log.debug('Could not get size via HEAD request for {}: {}'.format(
            url, str(e)))

    return None


def check_resource_size_limit(size, url):
    """
    Check if a resource size exceeds the configured maximum.

    :param size: Size in bytes (or None if unknown)
    :param url: URL of the resource (for logging)
    :return: True if resource should be included, False if it exceeds limit
    """
    max_size_str = config.get('ckanext.downloadall.max_resource_size')

    if not max_size_str:
        # No limit configured
        return True

    if size is None:
        # Cannot determine size, allow download by default
        log.debug('Resource size unknown for {}, allowing download'.format(
            url))
        return True

    try:
        max_size = int(max_size_str)
    except ValueError:
        log.error('Invalid value for ckanext.downloadall.max_resource_size: {}'
                  .format(max_size_str))
        return True

    if size > max_size:
        log.warning(
            'Resource {} size {} exceeds maximum size {}. '
            'Resource will be skipped.'.format(
                url, format_bytes(size), format_bytes(max_size)))
        return False

    return True


def download_resource_into_zip(url, filename, zipf, resource_id=None, package_id=None, metadata_modified=None):
    # Try to get the resource from local storage first
    if resource_id and package_id:
        try:
            context = {
                'model': model,
                'session': model.Session,
                'ignore_auth': True,
                'user': get_action('get_site_user')(
                    {'ignore_auth': True})['name'],
            }
            resource_dict = get_action('resource_show')(
                context, {'id': resource_id})

            # Get metadata_modified from resource_show
            resource_metadata_modified = resource_dict.get('metadata_modified')
            log.debug('Resource {} metadata_modified: {}'.format(
                resource_id, resource_metadata_modified))

            # Check if this is an uploaded resource (not a link)
            if resource_dict.get('url_type') == 'upload':
                upload = uploader.get_resource_uploader(resource_dict)
                filepath = upload.get_path(resource_id)

                if filepath and os.path.exists(filepath):
                    # Check file size before processing
                    file_size = get_resource_size(url, filepath)
                    if not check_resource_size_limit(file_size, url):
                        raise DownloadError(
                            'Resource exceeds maximum size limit')

                    log.debug('Using local file: {}'.format(filepath))

                    # Read file content
                    with open(filepath, 'rb') as local_file:
                        file_content = local_file.read()

                    # Calculate hash
                    hash_object = hashlib.md5()
                    hash_object.update(file_content)
                    file_hash = hash_object.hexdigest()
                    size = len(file_content)

                    # Create ZipInfo with proper timestamp from resource_show
                    zinfo = zipfile.ZipInfo(filename=filename)
                    date_time = parse_metadata_modified_to_date_time(resource_metadata_modified)
                    if date_time:
                        zinfo.date_time = date_time
                        log.info('Successfully set ZipInfo.date_time for {} to {} (from metadata_modified: {})'.format(
                            filename, zinfo.date_time, resource_metadata_modified))
                    else:
                        # Fallback to current time if parsing fails
                        zinfo.date_time = time.localtime()[:6]
                        log.warning('Using current time for {} - failed to parse metadata_modified'.format(filename))
                    zinfo.compress_type = zipfile.ZIP_DEFLATED

                    # Use writestr to properly preserve timestamp
                    zipf.writestr(zinfo, file_content)
                    log.info('Wrote {} bytes to ZIP with filename "{}"'.format(len(file_content), filename))

                    log.debug(
                        'Added from local storage: {}, hash: {}'
                        .format(format_bytes(size), file_hash))
                    return
        except Exception as e:
            log.warning(
                'Could not access local file for resource {}: {}. '
                'Falling back to HTTP download.'
                .format(resource_id, str(e)))

    # Fall back to HTTP download for remote resources or if local access fails
    # Check resource size before downloading
    resource_size = get_resource_size(url)
    if not check_resource_size_limit(resource_size, url):
        log.error('Resource {} exceeds maximum size limit and will not '
                  'be downloaded'.format(url))
        raise DownloadError('Resource exceeds maximum size limit')

    try:
        r = requests.get(url, stream=True, timeout=60)
        r.raise_for_status()
    except requests.ConnectionError:
        log.error('URL {url} refused connection. The resource will not'
                  ' be downloaded'.format(url=url))
        raise DownloadError()
    except requests.exceptions.HTTPError as e:
        log.error('URL {url} status error: {status}. The resource will'
                  ' not be downloaded'
                  .format(url=url, status=e.response.status_code))
        raise DownloadError()
    except requests.exceptions.RequestException as e:
        log.error('URL {url} download request exception: {error}'
                  .format(url=url, error=str(e)))
        raise DownloadError()
    except Exception as e:
        log.error('URL {url} download exception: {error}'
                  .format(url=url, error=str(e)))
        raise DownloadError()

    # Download content to memory
    file_content = b''
    hash_object = hashlib.md5()

    for chunk in r.iter_content(chunk_size=8192):
        file_content += chunk
        hash_object.update(chunk)

    size = len(file_content)
    file_hash = hash_object.hexdigest()

    # Create ZipInfo with proper timestamp
    # For remote resources, try to get metadata from resource_show if available
    resource_metadata_modified = None
    if resource_id:
        try:
            context = {
                'model': model,
                'session': model.Session,
                'ignore_auth': True,
                'user': get_action('get_site_user')(
                    {'ignore_auth': True})['name'],
            }
            resource_dict = get_action('resource_show')(
                context, {'id': resource_id})
            resource_metadata_modified = resource_dict.get('metadata_modified')
        except Exception:
            # If we can't get resource_show, fall back to parameter
            resource_metadata_modified = metadata_modified
    else:
        resource_metadata_modified = metadata_modified

    zinfo = zipfile.ZipInfo(filename=filename)
    date_time = parse_metadata_modified_to_date_time(resource_metadata_modified)
    if date_time:
        zinfo.date_time = date_time
        log.debug('Set timestamp for {} to {}'.format(filename, date_time))
    else:
        # Fallback to current time if parsing fails
        zinfo.date_time = time.localtime()[:6]
        log.warning('Using current time for {} - failed to parse metadata_modified'.format(filename))
    zinfo.compress_type = zipfile.ZIP_DEFLATED

    # Use writestr to properly preserve timestamp
    zipf.writestr(zinfo, file_content)

    log.debug('Downloaded {}, hash: {}'
              .format(format_bytes(size), file_hash))


def write_datapackage_json(datapackage, zipf, metadata_modified=None):
    # Create ZipInfo with proper timestamp for datapackage.json
    zinfo = zipfile.ZipInfo(filename='datapackage.json')
    date_time = parse_metadata_modified_to_date_time(metadata_modified)
    if date_time:
        zinfo.date_time = date_time
    zinfo.compress_type = zipfile.ZIP_DEFLATED

    # Write the json content
    json_content = ckanapi.cli.utils.pretty_json(datapackage)
    zipf.writestr(zinfo, json_content)
    log.debug('Added datapackage.json with timestamp from {}'.format(metadata_modified))


def format_bytes(size_bytes):
    if size_bytes == 0:
        return "0 bytes"
    size_name = ("bytes", "KB", "MB", "GB", "TB", "PB", "EB", "ZB", "YB")
    i = int(math.floor(math.log(size_bytes, 1024)))
    p = math.pow(1024, i)
    s = round(size_bytes / p, 1)
    return '{} {}'.format(s, size_name[i])


def remove_resources_that_should_not_be_included_in_the_datapackage(dataset):
    resource_formats_to_ignore = ['API', 'api']  # TODO make it configurable

    # Check if external resources should be included
    include_external = config.get(
        'ckanext.downloadall.include_external_resources', 'true').lower()
    include_external_resources = include_external in ['true', '1', 'yes']

    existing_zip_resource = None
    resources_to_include = []
    for i, res in enumerate(dataset.get('resources', [])):
        if res.get('downloadall_metadata_modified'):
            # this is an existing zip of all the other resources
            log.debug('Resource resource {}/{} skipped - is the zip itself'
                      .format(i + 1, len(dataset.get('resources', []))))
            existing_zip_resource = res
            continue

        if res.get('format', '') in resource_formats_to_ignore:
            log.debug('Resource resource {}/{} skipped - because it is '
                      'format {}'.format(i + 1, len(dataset.get('resources', [])),
                                         res.get('format', '')))
            continue

        # Skip external resources (links) if configured to do so
        if not include_external_resources:
            url_type = res.get('url_type', '')
            if url_type != 'upload':
                log.debug('Resource {}/{} skipped - external resource (link) '
                          'excluded from zip. URL: {}'
                          .format(i + 1, len(dataset.get('resources', [])),
                                  res.get('url', 'unknown')))
                continue

        resources_to_include.append(res)
    dataset = dict(dataset, resources=resources_to_include)
    return dataset, resources_to_include, existing_zip_resource
