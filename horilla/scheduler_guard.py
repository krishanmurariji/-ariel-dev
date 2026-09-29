"""
horilla/scheduler_guard.py

Make the scheduled jobs run once, not once per web worker.

Around a dozen modules each create their own APScheduler BackgroundScheduler
and start it while Django boots. The production server runs several worker
processes (gunicorn), and every worker boots Django, so every scheduled job
ran once per worker - e.g. automatic payslip generation every 3 hours, three
times over.

install() is called from the server entry points only (asgi.py / wsgi.py), so
management commands and `runserver` behave exactly as before. It makes a
scheduler that is started *while Django boots* first take an exclusive lock on
a small file; the worker that gets the lock runs the jobs, the others skip
starting theirs. The lock is released by the operating system when the owning
process exits for any reason (crash, kill, gunicorn's max_requests recycling),
so the next worker that boots takes over - jobs never stay off.

A scheduler started later, from inside a web request (e.g. switching on Drive
backup or saving a biometric device schedule), is not affected.
"""

import hashlib
import logging
import os
import tempfile

logger = logging.getLogger(__name__)

_installed = False
_lock_file = None  # kept referenced for the life of the process: closing it frees the lock
_owner = None


def _lock_path():
    from django.conf import settings

    tag = hashlib.sha1(str(settings.BASE_DIR).encode()).hexdigest()[:10]
    return os.path.join(tempfile.gettempdir(), f"ariel_hr_scheduler_{tag}.lock")


def _try_lock(handle):
    if os.name == "nt":
        import msvcrt

        try:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False
    import fcntl

    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def owns_scheduler_lock():
    """True in the one process that runs the scheduled jobs."""
    global _lock_file, _owner
    if _owner is not None:
        return _owner
    try:
        handle = open(_lock_path(), "a+")
        if _try_lock(handle):
            _lock_file = handle
            _owner = True
            logger.info("Scheduled jobs: this process (pid %s) runs them.", os.getpid())
        else:
            handle.close()
            _owner = False
            logger.info(
                "Scheduled jobs: another worker runs them; pid %s skips.", os.getpid()
            )
    except Exception:
        # If the lock cannot be used at all, run the jobs (the old behaviour)
        # rather than risk running none.
        logger.exception("Scheduled jobs: lock unavailable, running them here.")
        _owner = True
    return _owner


def install():
    global _installed
    if _installed:
        return
    _installed = True

    from apscheduler.schedulers.background import BackgroundScheduler
    from django.apps import apps

    original_start = BackgroundScheduler.start

    def start(self, *args, **kwargs):
        if not apps.ready and not owns_scheduler_lock():
            return None
        return original_start(self, *args, **kwargs)

    BackgroundScheduler.start = start
