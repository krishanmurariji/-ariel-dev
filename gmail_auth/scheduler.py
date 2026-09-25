"""
gmail_auth/scheduler.py

"""

import logging
import sys

from apscheduler.schedulers.background import BackgroundScheduler

from horilla.background_utils import with_db_cleanup

logger = logging.getLogger(__name__)


@with_db_cleanup
def refresh_google_auth_token():
    """
    scheduler method to refresh token
    """
    from gmail_auth.models import GoogleApi
    from gmail_auth.views import refresh_google_token

    # token is a JSONField with default=dict, so it is never actually
    # NULL - filter out the "never connected yet" rows by excluding the
    # empty-dict default instead of an isnull lookup.
    apis = GoogleApi.objects.exclude(token={})
    for api in apis:
        try:
            refresh_google_token(api)
            logger.info(f"Updated token for {api} gmail auth")
        except Exception as e:
            logger.error(e)


if not any(
    cmd in sys.argv
    for cmd in ["makemigrations", "migrate", "compilemessages", "flush", "shell", "test"]
):
    scheduler = BackgroundScheduler()
    scheduler.add_job(
        refresh_google_auth_token,
        "interval",
        minutes=50,
        id="refresh_google_auth_token",
    )
    scheduler.start()
