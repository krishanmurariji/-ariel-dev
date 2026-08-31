"""
0002 moved GoogleDriveBackup from a service-account file to OAuth
access/refresh tokens, but scheduler.py's google_drive_backup() and
gdrive.py's authenticate()/upload_file() were never updated to match - they
still read the now-nonexistent service_account_file field and only know how
to authenticate with a service-account key, not OAuth tokens, and there was
never an authorization flow built to obtain those tokens in the first place.
Net effect: the scheduled backup job crashed with AttributeError on every
run. Reverts to the service-account design, which scheduler.py/gdrive.py
already implement correctly and which needs no token refresh handling for
an unattended job.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('horilla_backup', '0002_remove_googledrivebackup_service_account_file_and_more'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='googledrivebackup',
            name='access_token',
        ),
        migrations.RemoveField(
            model_name='googledrivebackup',
            name='oauth_credentials_file',
        ),
        migrations.RemoveField(
            model_name='googledrivebackup',
            name='refresh_token',
        ),
        migrations.RemoveField(
            model_name='googledrivebackup',
            name='token_expiry',
        ),
        migrations.AddField(
            model_name='googledrivebackup',
            name='service_account_file',
            field=models.FileField(blank=True, help_text='Make sure your file is in JSON format and contains your Google Service Account credentials', null=True, upload_to='gdrive_service_account_file', verbose_name='Service Account File'),
        ),
        migrations.AlterField(
            model_name='googledrivebackup',
            name='gdrive_folder_id',
            field=models.CharField(help_text="Google Drive folder ID where backups will be stored. The service account's email (from the JSON file) must have write access to this folder - share the folder with it like any other collaborator.", max_length=255, verbose_name='Gdrive Folder ID'),
        ),
    ]
