# encoding: utf-8

import click
import ckan.plugins.toolkit as toolkit
from ckan import model
from ckan.lib.jobs import DEFAULT_QUEUE_NAME

from . import tasks
from . import streaming


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

    Generates zip file for a dataset, downloading its resources.
    Datasets above the stream threshold are skipped (they are streamed on
    demand instead).
    """
    context = {'model': model, 'session': model.Session, 'ignore_auth': True}
    try:
        pkg_dict = toolkit.get_action('package_show')(
            context, {'id': dataset_ref})
    except toolkit.ObjectNotFound:
        raise click.ClickException(
            'Dataset not found: {}'.format(dataset_ref))

    if streaming.should_stream(pkg_dict):
        click.secho(
            'Skipped (above stream threshold – will be streamed on demand): '
            '{}'.format(dataset_ref),
            fg='yellow')
        return

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
    """ update-all-zips

    Generates zip file for all datasets that are below the stream threshold.
    Datasets at or above the threshold are skipped – their ZIPs are generated
    on demand at download time.
    """
    context = {'model': model, 'session': model.Session, 'ignore_auth': True}
    datasets = toolkit.get_action('package_list')(context, {})

    skipped = 0
    processed = 0

    for i, dataset_name in enumerate(datasets):
        try:
            pkg_dict = toolkit.get_action('package_show')(
                context, {'id': dataset_name})
        except toolkit.ObjectNotFound:
            click.echo('Dataset not found, skipping: {}'.format(dataset_name))
            continue

        if streaming.should_stream(pkg_dict):
            click.echo(
                'Skipping {}/{} {} (above stream threshold)'.format(
                    i + 1, len(datasets), dataset_name))
            skipped += 1
            continue

        processed += 1
        if synchronous:
            click.echo(
                'Processing {}/{} {}'.format(i + 1, len(datasets), dataset_name))
            tasks.update_zip(dataset_name)
        else:
            click.echo(
                'Queuing {}/{} {}'.format(i + 1, len(datasets), dataset_name))
            toolkit.enqueue_job(
                tasks.update_zip, [dataset_name],
                title='DownloadAll {operation} "{name}" {id}'.format(
                    operation='cli-requested', name=dataset_name,
                    id=dataset_name),
                queue=DEFAULT_QUEUE_NAME)

    click.secho(
        'update-all-zips: SUCCESS  (processed: {}, skipped as streamable: {})'
        .format(processed, skipped),
        fg='green', bold=True)
