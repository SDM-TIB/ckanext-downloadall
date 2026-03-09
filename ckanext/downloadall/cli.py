# encoding: utf-8

import click

try:
    # CKAN 2.9+
    from ckan.cli import (
        click_config_option, load_config
    )
except ImportError:
    # CKAN 2.7, 2.8
    from ckan.lib.cli import click_config_option
    from ckan.lib.cli import _get_config as load_config

from ckan.config.middleware import make_app
import ckan.plugins.toolkit as toolkit
from ckan import model
from ckan.lib.jobs import DEFAULT_QUEUE_NAME

from . import tasks


class MockTranslator(object):
    def gettext(self, value):
        return value

    def ugettext(self, value):
        return value

    def ungettext(self, singular, plural, n):
        if n > 1:
            return plural
        return singular


class CkanCommand(object):

    def __init__(self, conf=None):
        self.config = load_config(conf)

        # package_update needs a translator defined i.e. _()
        from paste.registry import Registry
        import pylons
        registry = Registry()
        registry.prepare()
        registry.register(pylons.translator, MockTranslator())

        self.app = make_app(self.config.global_conf, **self.config.local_conf)


@click.group()
@click.help_option('-h', '--help')
@click_config_option
@click.pass_context
def cli(ctx, config, *args, **kwargs):
    ctx.obj = CkanCommand(config)


@cli.command('update-zip', short_help='Update zip file for a dataset')
@click.argument('dataset_ref')
@click.option('--synchronous', '-s',
              help='Do it in the same process (not the worker)',
              is_flag=True)
def update_zip(dataset_ref, synchronous):
    ''' update-zip <package-name>

    Generates zip file for a dataset, downloading its resources.'''
    if synchronous:
        tasks.update_zip(dataset_ref)
    else:
        toolkit.enqueue_job(
            tasks.update_zip, [dataset_ref],
            title='DownloadAll {operation} "{name}" {id}'.format(
                operation='cli-requested', name=dataset_ref,
                id=dataset_ref),
            queue=DEFAULT_QUEUE_NAME)
    click.secho('update-zip: SUCCESS', fg='green', bold=True)


@cli.command('update-all-zips',
             short_help='Update zip files for all datasets')
@click.option('--synchronous', '-s',
              help='Do it in the same process (not the worker)',
              is_flag=True)
def update_all_zips(synchronous):
    ''' update-all-zips <package-name>

    Generates zip file for all datasets. It is done synchronously.'''
    context = {'model': model, 'session': model.Session}
    datasets = toolkit.get_action('package_list')(context, {})
    for i, dataset_name in enumerate(datasets):
        if synchronous:
            print(('Processing dataset {}/{}'.format(i + 1, len(datasets))))
            tasks.update_zip(dataset_name)
        else:
            print(('Queuing dataset {}/{}'.format(i + 1, len(datasets))))
            toolkit.enqueue_job(
                tasks.update_zip, [dataset_name],
                title='DownloadAll {operation} "{name}" {id}'.format(
                    operation='cli-requested', name=dataset_name,
                    id=dataset_name),
                queue=DEFAULT_QUEUE_NAME)

    click.secho('update-all-zips: SUCCESS', fg='green', bold=True)
