"""
horilla/media_storage_s3.py

STORAGES["default"] when AWS_ACCESS_KEY_ID is set (see horilla/settings/
addons.py) - uploaded files (payslips, employee documents, resumes, company
logos, ...) go to an S3 bucket, or anything S3-compatible (Backblaze B2,
Cloudflare R2, MinIO, ...) via AWS_S3_ENDPOINT_URL, instead of local disk.

Only ever referenced by *name* (a string in STORAGES["default"]["BACKEND"]),
never imported directly by any settings module - Django's storage machinery
imports it lazily, on the first actual file operation, and only when this
class is actually selected. That means boto3 (an optional dependency, only
needed for this path) is never imported, and can never break the app, for
the - currently universal - case where no bucket is configured at all.
"""

from storages.backends.s3boto3 import S3Boto3Storage


class PrivateS3MediaStorage(S3Boto3Storage):
    """
    Private by default: every file URL is a short-lived signed link
    (AWS_QUERYSTRING_AUTH / AWS_QUERYSTRING_EXPIRE, set in addons.py) rather
    than a permanently public one - the same "don't be H-1 as well" posture
    this project already wants for local uploads, applied to the bucket too.
    AWS_S3_FILE_OVERWRITE is off: this app's own upload_path() already gives
    every upload a random suffix, so a same-name collision would only ever
    happen from something going wrong upstream - overwriting the old file
    silently is the wrong failure mode for that.
    """
