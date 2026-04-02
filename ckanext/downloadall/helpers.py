import ckan.plugins.toolkit as toolkit

DEFAULT_JOB_TIMEOUT = 1800


def get_job_timeout():
    return toolkit.config.get(
        'ckanext.downloadall.job_timeout', DEFAULT_JOB_TIMEOUT)


def pop_zip_resource(pkg):
    '''Finds the zip resource in a package's resources, removes it from the
    package and returns it. NB the package doesn't have the zip resource in it
    any more.
    '''
    zip_res = None
    non_zip_resources = []
    for res in pkg.get('resources', []):
        if res.get('downloadall_metadata_modified'):
            zip_res = res
        else:
            non_zip_resources.append(res)
    pkg['resources'] = non_zip_resources
    return zip_res


def is_streaming(pkg):
    '''Jinja2 helper: returns True when a dataset is large enough that its
    "Download all" ZIP will be streamed on demand rather than served from a
    pre-generated file stored in the filestore.

    Usage in templates:
        {% if h.downloadall_is_streaming(pkg) %} … {% endif %}
    '''
    from ckanext.downloadall.streaming import should_stream
    return should_stream(pkg)
