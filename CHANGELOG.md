# Changelog
All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## Unreleased

### Added
- Configurable hybrid zip approach: small datasets (below `ckanext.downloadall.stream_threshold_bytes`) are pre-generated and stored in the filestore; large datasets are assembled and streamed on the fly to the browser without consuming extra disk space.
- New config option `ckanext.downloadall.max_resource_size`: maximum size in bytes for individual resources to be included in the zip; resources exceeding the limit are excluded and marked as external in `datapackage.json`.
- New config option `ckanext.downloadall.include_external_resources`: controls whether externally-linked resources (non-upload `url_type`) are included in the zip (default: `true`).
- New config option `ckanext.downloadall.job_timeout`: background job timeout in seconds (default: `1800`). Previously this was hardcoded.
- `--force` flag added to the CLI `update-zip` and `update-all-zips` commands to bypass the skip-if-no-changes check.
- `metadata_modified` timestamp is now preserved on the dataset after the zip resource is created or updated, avoiding spurious re-triggers of zip regeneration.
- German translation
- Each resource in `datapackage.json` now includes a `ckan_url_type` field (`"upload"` for files bundled in the ZIP, `"external"` for external links). This allows consumers of the datapackage to reliably distinguish uploaded files from linked resources without inspecting the `path` field. Closes [#27](https://github.com/SDM-TIB/ckanext-downloadall/issues/27).

### Changed
- Resources that are stored locally in the CKAN filestore are now read directly from disk instead of being re-downloaded over HTTP, significantly improving performance and reducing network overhead.
- `requests.get()` now uses an explicit 60-second timeout to prevent hung downloads.
- Dropped support for Python 3.7; minimum supported version is now Python 3.8.
- Dropped support for CKAN 2.8 and earlier; minimum supported version is now CKAN 2.9.

### Fixed
- Fixed `NotAuthorized` error occurring in background jobs when the site user context was not set up correctly.
- Fixed race condition when a zip update job is enqueued before the dataset is fully committed to the database on package create; the job now waits and retries gracefully.
- Fixed ZIP file not being written correctly in certain code paths.
- Fixed CLI command failures caused by incorrect argument handling.
- Fixed crash when a dataset has no resources.
- Fixed crash when a resource has no `format` field set.

## [0.1.0] - 2019-11-12

### Added
- Config option added: ckanext.downloadall.dataset_fields_to_add_to_datapackage for including custom fields from the dataset in the datapackage.json

### Fixed
- Fixed home page exception KeyError: 'resources'

## [0.0.2] - 2019-06-30
### Added
- Command-line interface.
- Schema added to the datapackage.json if a resource's Data Dictionary is completed.

### Changed
- Dependencies moved to setup.py's install_requires, for convenience during install.

### Fixed
- Fixed exception when non-download-all jobs are put on the CKAN background task queue.
- Fixed position of the "Download all" button to avoid overlapping bottom edge when no dataset.notes.
- Zip resource is not now shown in the sidebar resources on the resource preview page.
- Zip format is not now shown in the search facets for the Download All zip.
- Fix updating the zip when changes are made to the core dataset metadata (e.g. dataset title).

## [0.0.1] - 2019-05-27 - Initial release
### Added
- Generates a zip when a resource URL changes
- Zip contains resources and basic datapackage.json
- 'Download all' button placed on the dataset page
