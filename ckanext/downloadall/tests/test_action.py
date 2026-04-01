"""Tests for plugin.py."""
from ckan.tests import factories
from ckan.tests import helpers
from ckan import plugins as p


class TestDatastoreCreate(object):
    @classmethod
    def setup_class(cls):
        p.load('downloadall')
        p.load('datastore')
        helpers.reset_db()

    def setup_method(self):
        helpers.call_action('job_clear')

    @classmethod
    def teardown_class(cls):
        p.unload('downloadall')
        p.unload('datastore')

    def test_datastore_create(self):
        dataset = factories.Dataset(resources=[
            {'url': 'http://some.image.png', 'format': 'png'}])
        helpers.call_action('job_clear')

        helpers.call_action('datastore_create',
                            resource_id=dataset['resources'][0]['id'],
                            force=True)

        # Check the chained action caused the zip to be queued for update
        assert [job['title'] for job in helpers.call_action('job_list')] == \
            ['DownloadAll datastore_create "{}" {}'
             .format(dataset['name'], dataset['id'])]
