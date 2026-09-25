import logging
import os

from apscheduler.schedulers.background import BackgroundScheduler
from django.core.management import call_command

from horilla import settings
from horilla.background_utils import with_db_cleanup

from .gdrive import *

# from horilla.settings import DBBACKUP_STORAGE_OPTIONS
from .models import *
from .pgdump import *
from .zip import *

logger = logging.getLogger(__name__)

scheduler = BackgroundScheduler()

# def backup_database():
#     folder_path = DBBACKUP_STORAGE_OPTIONS['location']
#     local_backup = LocalBackup.objects.first()
#     if folder_path and local_backup:
#         DBBACKUP_STORAGE_OPTIONS['location'] = local_backup.backup_path
#         folder_path = DBBACKUP_STORAGE_OPTIONS['location']
#         if local_backup.backup_db:
#             call_command('dbbackup')
#         if local_backup.backup_media:
#             call_command("mediabackup")
#         files = sorted(os.listdir(folder_path), key=lambda x: os.path.getctime(os.path.join(folder_path, x)))

#         # Remove all files except the last two
#         if len(files) > 2:
#             for file_name in files[:-2]:
#                 file_path = os.path.join(folder_path, file_name)
#                 if os.path.isfile(file_path):
#                     try:
#                         os.remove(file_path)
#                     except:
#                         pass


# def start_backup_job():
#     """
#     Start the backup job based on the LocalBackup configuration.
#     """
#     # Check if any LocalBackup object exists
#     if LocalBackup.objects.exists():
#         local_backup = LocalBackup.objects.first()

#             # Remove existing job if it exists
#         try:
#             scheduler.remove_job('backup_job')
#         except:
#             pass

#         # Add new job based on LocalBackup configuration
#         if local_backup.interval:
#             scheduler.add_job(backup_database, 'interval', seconds=local_backup.seconds, id='backup_job')
#         else:
#             scheduler.add_job(backup_database, trigger='cron', hour=local_backup.hour, minute=local_backup.minute, id='backup_job')
#         # Start the scheduler if it's not already running
#         if not scheduler.running:
#             scheduler.start()
#     else:
#         stop_backup_job()


# def stop_backup_job():
#     """
#     Stop the backup job if it exists.
#     """
#     try:
#         scheduler.remove_job('backup_job')
#     except:
#         pass


# def restart_backup_job():
#     """
#     Restart the backup job by stopping it and starting it again.
#     """
#     stop_backup_job()
#     start_backup_job()


@with_db_cleanup
def google_drive_backup():
    # Runs unattended on a background thread (APScheduler), so this is the
    # only place any failure here ever gets recorded - previously an
    # exception from any step below (a bad pg_dump, an upload rejected for
    # lacking Drive folder permission, ...) just vanished, leaving the
    # Settings page's "active" flag looking healthy indefinitely while
    # nothing actually landed in Drive.
    if not GoogleDriveBackup.objects.exists():
        return
    google_drive = GoogleDriveBackup.objects.first()
    service_account_file = google_drive.service_account_file.path
    gdrive_folder_id = google_drive.gdrive_folder_id

    if google_drive.backup_db:
        try:
            db = settings.DATABASES["default"]
            dump_postgres_db(
                db_name=db["NAME"],
                username=db["USER"],
                output_file="backupdb.dump",
                password=db["PASSWORD"],
            )
            upload_file("backupdb.dump", service_account_file, gdrive_folder_id)
        except Exception:
            logger.exception("Google Drive database backup failed")
        finally:
            if os.path.exists("backupdb.dump"):
                os.remove("backupdb.dump")

    if google_drive.backup_media:
        try:
            folder_to_zip = settings.MEDIA_ROOT
            output_zip_file = "media.zip"
            zip_folder(folder_to_zip, output_zip_file)
            upload_file("media.zip", service_account_file, gdrive_folder_id)
        except Exception:
            logger.exception("Google Drive media backup failed")
        finally:
            if os.path.exists("media.zip"):
                os.remove("media.zip")


def start_gdrive_backup_job():
    """
    Start the backup job based on the LocalBackup configuration.
    """
    # Check if any Gdrive Backup object exists
    if GoogleDriveBackup.objects.exists():
        gdrive_backup = GoogleDriveBackup.objects.first()

        # Remove existing job if it exists. This used to look for
        # "backup_job" - a leftover id from the old local-backup code below
        # - which never matched the real "gdrive_backup_job" id the jobs
        # below actually register, so it always silently failed (caught by
        # the bare except) and never removed anything. Combined with no
        # replace_existing=True on add_job, saving the backup settings more
        # than once would have raised a ConflictingIdError from APScheduler
        # refusing to register a second job under the same id - or, if that
        # was somehow swallowed upstream, left duplicate jobs (and their
        # duplicate leaked DB connections, see google_drive_backup) running
        # side by side. Fixed to match the real id, with replace_existing
        # as a second line of defense either way.
        try:
            scheduler.remove_job("gdrive_backup_job")
        except Exception:
            pass
        # Add new job based on Gdrive Backup configuration
        if gdrive_backup.interval:
            scheduler.add_job(
                google_drive_backup,
                "interval",
                seconds=gdrive_backup.seconds,
                id="gdrive_backup_job",
                replace_existing=True,
            )
        else:
            scheduler.add_job(
                google_drive_backup,
                trigger="cron",
                hour=gdrive_backup.hour,
                minute=gdrive_backup.minute,
                id="gdrive_backup_job",
                replace_existing=True,
            )

        # Start the scheduler if it's not already running
        if not scheduler.running:
            scheduler.start()

    else:
        stop_gdrive_backup_job()


def stop_gdrive_backup_job():
    """
    Stop the backup job if it exists.
    """
    try:
        scheduler.remove_job("gdrive_backup_job")
    except:
        pass


# def restart_gdrive_backup_job():
#     """
#     Restart the backup job by stopping it and starting it again.
#     """
#     stop_gdrive_backup_job()
#     start_gdrive_backup_job()
