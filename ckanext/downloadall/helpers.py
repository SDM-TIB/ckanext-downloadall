def separate_off_zip_resource(resources):
    zip_res = None
    non_zip_resources = []
    for res in resources:
        if res['name'] == u'All resource data':
            zip_res = res
        else:
            non_zip_resources.append(res)
    return zip_res, non_zip_resources
