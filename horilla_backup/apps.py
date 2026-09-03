import logging
import os
import sys

from django.apps import AppConfig

logger = logging.getLogger(__name__)

# Google Drive backup's BackgroundScheduler (horilla_backup/scheduler.py)
# only ever gets armed by an admin visiting Settings and clicking Start -
# nothing re-registers the job on process startup, so a normal
# deploy/restart/crash silently kills it while GoogleDriveBackup.active
# (a stored DB flag, not a live check) keeps showing "running" in the UI
# forever after. Resuming it here means a restart just picks the schedule
# back up instead of requiring someone to notice backups stopped and
# manually re-click Start. Skipped for one-off management commands (would
# otherwise spin up a scheduler thread for `manage.py shell`/`test`/etc.)
# and, under `runserver`'s autoreloader, its outer watcher process (only
# the actual child serving requests has RUN_MAIN=true) - both to avoid
# starting duplicate/pointless scheduler threads.
_SKIP_COMMANDS = {
    "makemigrations",
    "migrate",
    "collectstatic",
    "shell",
    "shell_plus",
    "test",
    "createsuperuser",
    "dbshell",
    "check",
    "loaddata",
    "dumpdata",
    "showmigrations",
}


class BackupConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "horilla_backup"

    def ready(self):
        from django.urls import include, path

        from horilla.urls import urlpatterns

        urlpatterns.append(
            path("backup/", include("horilla_backup.urls")),
        )
        super().ready()
        self._resume_gdrive_backup_if_active()

    def _resume_gdrive_backup_if_active(self):
        command = sys.argv[1] if len(sys.argv) > 1 else None
        if command in _SKIP_COMMANDS:
            return
        if command == "runserver" and os.environ.get("RUN_MAIN") != "true":
            return

        try:
            from django.db import connection
            from django.db.utils import DatabaseError

            from .models import GoogleDriveBackup

            if connection.vendor != "postgresql":
                return
            if GoogleDriveBackup.objects.filter(active=True).exists():
                from .scheduler import start_gdrive_backup_job

                start_gdrive_backup_job()
                logger.info("Resumed Google Drive backup schedule on startup.")
        except DatabaseError:
            # Table doesn't exist yet - a fresh install still migrating,
            # nothing to resume.
            pass
