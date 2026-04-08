"""
streaming.py – on-demand ZIP streaming for ckanext-downloadall.

Datasets whose total resource size is >= the configured threshold are streamed
on demand instead of being pre-generated and stored in the filestore.

Configuration (ckan.ini):
    # Bytes. Default = 314572800 (300 MB).
    ckanext.downloadall.stream_threshold_bytes = 314572800

Requires:
    pip install zipstream-ng
"""

import logging

import requests
import zipstream  # zipstream-ng
from flask import Blueprint, Response, stream_with_context

import ckan.plugins.toolkit as toolkit
import ckanapi
import ckanapi.datapackage
import ckanapi.cli.utils

from ckan import model

log = logging.getLogger(__name__)

downloadall_blueprint = Blueprint(u'downloadall_stream', __name__, )


def get_threshold():
    """Return the configured size threshold in bytes (int)."""
    return toolkit.asint(
        toolkit.config.get(
            u'ckanext.downloadall.stream_threshold_bytes',
            104_857_600,   # 100 MB
        )
    )


def dataset_total_size(pkg_dict):
    """
    Sum the ``size`` metadata field of all non-bundle resources.
    Resources without a size value contribute 0.
    """
    total = 0
    for res in pkg_dict.get(u'resources', []):
        if res.get(u'downloadall_metadata_modified'):
            # this is the bundle zip itself – skip it
            continue
        try:
            total += int(res.get(u'size') or 0)
        except (TypeError, ValueError):
            pass
    return total


def should_stream(pkg_dict):
    """
    Return True when this dataset's total resource size meets or exceeds the
    configured threshold and its ZIP should be streamed on demand.
    """
    return dataset_total_size(pkg_dict) >= get_threshold()


@downloadall_blueprint.route(u'/dataset/<dataset_id>/download_all')
def download_all(dataset_id):
    """
    Unified Download-All endpoint.

    Small datasets  → 302 redirect to the pre-generated bundle resource URL.
    Large datasets  → ZIP assembled and streamed on the fly, no disk storage.
    """
    context = {
        u'model': model,
        u'session': model.Session,
        u'user': toolkit.c.user,
    }
    try:
        pkg_dict = toolkit.get_action(u'package_show')(
            context, {u'id': dataset_id})
    except toolkit.ObjectNotFound:
        toolkit.abort(404, toolkit._(u'Dataset not found'))
    except toolkit.NotAuthorized:
        toolkit.abort(403, toolkit._(u'Not authorised to read this dataset'))

    if should_stream(pkg_dict):
        return _stream_zip_response(pkg_dict)

    # Small dataset – redirect to the pre-generated bundle resource.
    bundle_res = _find_bundle_resource(pkg_dict)
    if bundle_res and bundle_res.get(u'url'):
        return toolkit.redirect_to(bundle_res[u'url'])

    # Bundle not generated yet (worker hasn't run) – stream on the fly so the
    # user gets their data immediately rather than a 404.  The background job
    # will still complete and store the pre-generated zip for future requests.
    log.info(
        u'downloadall: pre-generated zip not found for %s, '
        u'falling back to on-demand streaming.',
        dataset_id,
    )
    return _stream_zip_response(pkg_dict)


def _find_bundle_resource(pkg_dict):
    """Return the pre-generated zip resource dict, or None."""
    for res in pkg_dict.get(u'resources', []):
        if res.get(u'downloadall_metadata_modified'):
            return res
    return None


def _iter_resource_chunks(url, chunk_size=1 << 16):
    """Generator: yield raw bytes from *url* in chunks."""
    try:
        r = requests.get(url, stream=True, timeout=60)
        r.raise_for_status()
        for chunk in r.iter_content(chunk_size=chunk_size):
            if chunk:
                yield chunk
    except Exception as exc:
        log.error(
            u'downloadall streaming: failed to fetch %s – %s', url, exc)
        # Yield nothing; the entry appears as an empty file inside the ZIP.


def _stream_zip_response(pkg_dict):
    """Build and return a Flask streaming Response for the dataset ZIP."""
    from ckanext.downloadall.tasks import generate_datapackage_json

    dataset_name = pkg_dict.get(u'name', u'dataset')

    try:
        datapackage, ckan_and_dp_resources, _ = \
            generate_datapackage_json(pkg_dict[u'id'])
    except Exception as exc:
        log.error(
            u'downloadall streaming: could not generate datapackage for '
            u'%s – %s', dataset_name, exc)
        toolkit.abort(500, toolkit._(u'Could not build ZIP manifest.'))

    def _generate():
        zs = zipstream.ZipStream(compress_type=zipstream.ZIP_STORED)

        for res, dres in ckan_and_dp_resources:
            url = res.get(u'url')
            if not url:
                continue
            # ckanapi.datapackage.resource_filename() requires 'format' to be
            # present; default to empty string when the resource has none.
            dres.setdefault(u'format', u'')
            filename = ckanapi.datapackage.resource_filename(dres)
            log.debug(
                u'downloadall stream: adding %s from %s', filename, url)
            zs.add(_iter_resource_chunks(url), arcname=filename)

        # Add the same datapackage.json manifest as the pre-generated ZIP.
        manifest_bytes = ckanapi.cli.utils.pretty_json(datapackage)
        zs.add([manifest_bytes], arcname=u'datapackage.json')

        yield from zs

    filename = u'{}.zip'.format(dataset_name)
    return Response(
        stream_with_context(_generate()),
        mimetype=u'application/zip',
        headers={
            u'Content-Disposition':
                u'attachment; filename="{}"'.format(filename),
            u'X-Content-Type-Options': u'nosniff',
        },
        direct_passthrough=True,
    )
