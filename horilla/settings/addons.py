"""
horilla/settings/addons.py

Optional cloud storage for uploaded files (STORAGES["default"]). With none
of the environment variables below set, uploads stay on local disk exactly
as configured in base.py - nothing here has any effect at all in that case,
which is every deployment of this app today.

This used to set DEFAULT_FILE_STORAGE, the setting name Django 5.1 removed
in favour of STORAGES - Django 5.2 (pinned in requirements.txt) silently
ignores that legacy name, so this file's S3 config, and the GCS config
documented in .env.dist, have never actually taken effect: production has
always been writing uploads to local disk regardless of what AWS_*/GS_*
variables were set. See horilla/media_storage_s3.py and
horilla/media_storage_gcs.py for the classes STORAGES now actually points
at.

IMPORTANT - before turning either of these on for real: several places in
this codebase still read `settings.MEDIA_ROOT` directly with `os.path`
calls, assuming uploads are real files on local disk (the Google Drive
backup zips MEDIA_ROOT, the payroll/PDF export code resolves media paths
from disk, a few employee/recruitment file-download views do the same).
Switching STORAGES["default"] to a bucket does not update those - they need
their own fix first, or things like the Drive backup will silently start
backing up an empty folder. Ask before flipping this on in production.
"""

from .local_settings import *  # noqa: F401,F403 - INSTALLED_APPS/STORAGES/env come from here

if env("AWS_ACCESS_KEY_ID", default=None):
    AWS_ACCESS_KEY_ID = env("AWS_ACCESS_KEY_ID")
    AWS_SECRET_ACCESS_KEY = env("AWS_SECRET_ACCESS_KEY")
    AWS_STORAGE_BUCKET_NAME = env("AWS_STORAGE_BUCKET_NAME")
    # No default region: an unset AWS_S3_REGION_NAME lets boto3 fall back to
    # its own resolution (AWS_DEFAULT_REGION / profile config), which is
    # correct for real AWS S3 and Backblaze B2. Only Cloudflare R2 wants the
    # literal string "auto" - set AWS_S3_REGION_NAME=auto for that provider.
    AWS_S3_REGION_NAME = env("AWS_S3_REGION_NAME", default=None)
    AWS_S3_ADDRESSING_STYLE = env("AWS_S3_ADDRESSING_STYLE", default="virtual")
    # Cloudflare R2, Backblaze B2 and other S3-compatible stores need a
    # custom endpoint - leave unset for real AWS S3.
    AWS_S3_ENDPOINT_URL = env("AWS_S3_ENDPOINT_URL", default=None)
    # Private bucket, short-lived signed URLs by default - see
    # media_storage_s3.PrivateS3MediaStorage's docstring.
    AWS_DEFAULT_ACL = env("AWS_DEFAULT_ACL", default="private")
    AWS_QUERYSTRING_AUTH = env.bool("AWS_QUERYSTRING_AUTH", default=True)
    AWS_QUERYSTRING_EXPIRE = env.int("AWS_QUERYSTRING_EXPIRE", default=300)
    AWS_S3_FILE_OVERWRITE = False

    if "storages" not in INSTALLED_APPS:
        INSTALLED_APPS.append("storages")

    STORAGES["default"] = {"BACKEND": "horilla.media_storage_s3.PrivateS3MediaStorage"}

elif env("GS_BUCKET_NAME", default=None):
    GS_BUCKET_NAME = env("GS_BUCKET_NAME")
    GS_PROJECT_ID = env("GS_PROJECT_ID", default=None)
    # Left unset (bucket's own default) rather than "private": a bucket with
    # uniform bucket-level access on - Google's current recommendation, and
    # the only option for newly created buckets - rejects an explicit
    # per-object ACL outright.
    GS_DEFAULT_ACL = env("GS_DEFAULT_ACL", default=None)
    GS_QUERYSTRING_AUTH = env.bool("GS_QUERYSTRING_AUTH", default=True)
    GS_FILE_OVERWRITE = False
    # google-cloud-storage reads GOOGLE_APPLICATION_CREDENTIALS (a path to
    # the service-account JSON) itself - nothing else to configure here.

    if "storages" not in INSTALLED_APPS:
        INSTALLED_APPS.append("storages")

    STORAGES["default"] = {"BACKEND": "horilla.media_storage_gcs.PrivateGCSMediaStorage"}

# MEDIA_URL/MEDIA_ROOT are a local-filesystem concept - S3Boto3Storage and
# GoogleCloudStorage build their own URLs from the bucket, so neither needs
# (or should have) these overridden. The previous version of this file did
# override them, unconditionally requiring MEDIA_URL/MEDIA_ROOT/NAMESPACE to
# be set with no default - env('MEDIA_URL') on a fresh AWS_ACCESS_KEY_ID
# setup with nothing else configured raised ImproperlyConfigured before the
# app could even start.
