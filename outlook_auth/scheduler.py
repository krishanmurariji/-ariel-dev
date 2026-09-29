"""
outlook_auth/scheduler.py

"""

import logging
import sys

from apscheduler.schedulers.background import BackgroundScheduler

from horilla.background_utils import with_db_cleanup

logger = logging.getLogger(__name__)


@with_db_cleanup
def refresh_outlook_auth_token():
    """
    scheduler method to refresh token
    """
    from outlook_auth.models import AzureApi
    from outlook_auth.views import (
        OutlookReauthRequired,
        refresh_is_due,
        refresh_outlook_token,
    )

    for api in AzureApi.objects.all():
        if not api.token:
            continue
        # An account Microsoft told us needs a person to sign in again is only
        # retried now and then - hammering a request that cannot succeed just
        # fills the log (and counts as failed sign-ins on the Microsoft side).
        if not refresh_is_due(api):
            continue
        try:
            refresh_outlook_token(api)
            logger.info("Updated token for %s outlook auth", api)
        except OutlookReauthRequired as error:
            logger.warning("Outlook for %s needs to be connected again: %s", api, error)
        except Exception:  # noqa: BLE001
            logger.exception("Could not refresh the Outlook token for %s", api)


if not any(
    cmd in sys.argv
    for cmd in ["makemigrations", "migrate", "compilemessages", "flush", "shell", "test"]
):
    scheduler = BackgroundScheduler()
    scheduler.add_job(
        refresh_outlook_auth_token,
        "interval",
        minutes=50,
        id="refresh_outlook_auth_token",
    )
    scheduler.start()
