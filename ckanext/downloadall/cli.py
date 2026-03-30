# encoding: utf-8

import click
import ckan.plugins.toolkit as toolkit
from ckan import model
from ckan.lib.jobs import DEFAULT_QUEUE_NAME

from . import tasks


def get_commands():
    return [cli]


@click.group('downloadall')
@click.help_option('-h', '--help')
def cli():
    pass


@cli.command('update-zip', short_help='Update zip file for a dataset')
@click.argument('dataset_ref')
@click.option('--synchronous', '-s',
              help='Do it in the same process (not the worker)',
              is_flag=True)
def update_zip(dataset_ref, synchronous):
    """ update-zip <package-name>

    Generates zip file for a dataset, downloading its resources."""
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
    """ update-all-zips <package-name>

    Generates zip file for all datasets. It is done synchronously."""
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
