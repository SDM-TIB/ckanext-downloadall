def get_zip_resource(resources):
    for res in resources:
        if res['name'] == u'All resource data':
            return res
