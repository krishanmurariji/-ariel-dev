"""
horilla/media_storage_gcs.py

STORAGES["default"] when GS_BUCKET_NAME is set and AWS_ACCESS_KEY_ID is not
(see horilla/settings/addons.py) - the Google Cloud Storage equivalent of
media_storage_s3.py. See that file's docstring for why this is only ever
referenced by name, never imported directly.
"""

from storages.backends.gcloud import GoogleCloudStorage


class PrivateGCSMediaStorage(GoogleCloudStorage):
    """Private by default - see media_storage_s3.PrivateS3MediaStorage."""
