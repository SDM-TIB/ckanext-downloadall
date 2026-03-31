import re

import ckan.plugins as plugins
import ckan.plugins.toolkit as toolkit
from ckan.lib.jobs import DEFAULT_QUEUE_NAME
from ckan.lib.plugins import DefaultTranslation

from ckan import model

from .tasks import update_zip
from . import helpers
from . import action
from . import cli
from . import streaming


log = __import__('logging').getLogger(__name__)


class DownloadallPlugin(plugins.SingletonPlugin, DefaultTranslation):
    plugins.implements(plugins.ITranslation)
    plugins.implements(plugins.IConfigurer)
    plugins.implements(plugins.IDomainObjectModification)
    plugins.implements(plugins.ITemplateHelpers)
    plugins.implements(plugins.IPackageController, inherit=True)
    plugins.implements(plugins.IActions)
    plugins.implements(plugins.IClick)
    plugins.implements(plugins.IBlueprint)

    # IClick

    def get_commands(self):
        return cli.get_commands()

    # IBlueprint

    def get_blueprint(self):
        return [streaming.downloadall_blueprint]

    # IConfigurer

    def update_config(self, config_):
        toolkit.add_template_directory(config_, 'templates')
        toolkit.add_public_directory(config_, 'public')
        toolkit.add_resource('fanstatic', 'downloadall')

    # IDomainObjectModification

    def notify(self, entity, operation):
        '''
        Send a notification on entity modification.

        :param entity: instance of module.Package.
        :param operation: 'new', 'changed' or 'deleted'.
        '''
        if operation == 'deleted':
            return

        log.debug('{} {} \'{}\''
                  .format(operation, type(entity).__name__, entity.name))
        # We should regenerate zip if these happen:
        # 1 change of title, description etc (goes into package.json)
        # 2 add/change/delete resource metadata
        # 3 change resource data by upload (results in URL change)
        # BUT not:
        # 4 if this was just an update of the Download All zip itself
        #   (or you get an infinite loop)
        #
        # 1&2&3 - will change package.json and notify(res) and possibly
        #         notify(package) too
        # 4 - will cause this notifies but package.json only in limited places
        #
        # SO if package.json (not including Package Zip bits) remains the same
        # then we do not need to regenerate zip.
        if isinstance(entity, model.Package):
            enqueue_update_zip(entity.name, entity.id, operation)
        elif isinstance(entity, model.Resource):
            if entity.extras.get('downloadall_metadata_modified'):
                # this is the zip of all the resources - no need to react to
                # it being changed
                log.debug('Ignoring change to zip resource')
                return
            dataset = entity.related_packages()[0]
            enqueue_update_zip(dataset.name, dataset.id, operation)
        else:
            return

    # ITemplateHelpers

    def get_helpers(self):
        return {
            'downloadall__pop_zip_resource': helpers.pop_zip_resource,
            'downloadall_is_streaming': helpers.is_streaming,
        }

    # IPackageController

    def before_index(self, pkg_dict):
        try:
            if 'All resource data' in pkg_dict['res_name']:
                # we've got a 'Download all zip', so remove it's ZIP from the
                # SOLR facet of resource formats, as it's not really a data
                # resource
                pkg_dict['res_format'].remove('ZIP')
        except KeyError:
            # this happens when you save a new package without a resource yet
            pass
        return pkg_dict

    # IActions

    def get_actions(self):
        actions = {}
        if plugins.get_plugin('datastore'):
            # datastore is enabled, so we need to chain the datastore_create
            # action, to update the zip when it is called
            actions['datastore_create'] = action.datastore_create
        return actions


def enqueue_update_zip(dataset_name, dataset_id, operation):
    # For datasets above the streaming threshold, the ZIP is generated on
    # demand – there is nothing to pre-generate, so skip the job entirely.
    try:
        pkg_dict = toolkit.get_action('package_show')(
            {'ignore_auth': True}, {'id': dataset_id})
        if streaming.should_stream(pkg_dict):
            log.info(
                'Dataset %s exceeds stream threshold – skipping '
                'pre-generation, will be streamed on demand.',
                dataset_name)
            return
    except toolkit.ObjectNotFound:
        log.warning('enqueue_update_zip: dataset not found: %s', dataset_id)
        return

    # skip task if the dataset is already queued
    queue = DEFAULT_QUEUE_NAME
    jobs = toolkit.get_action('job_list')(
        {'ignore_auth': True}, {'queues': [queue]})
    if jobs:
        for job in jobs:
            if not job['title']:
                continue
            match = re.match(
                r'DownloadAll \w+ "[^"]*" ([\w-]+)', job['title'])
            if match:
                queued_dataset_id = match.groups()[0]
                if dataset_id == queued_dataset_id:
                    log.info('Already queued dataset: {} {}'
                             .format(dataset_name, dataset_id))
                    return

    # add this dataset to the queue
    log.debug('Queuing job update_zip: {} {}'
              .format(operation, dataset_name))

    toolkit.enqueue_job(
        update_zip, [dataset_id],
        title='DownloadAll {} "{}" {}'.format(operation, dataset_name,
                                               dataset_id),
        queue=queue)
