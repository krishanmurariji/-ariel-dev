"""
offboarding/scheduler.py

Daily housekeeping for exits: anyone whose last working day has passed moves
from "Exits in Progress" to "Exited", and exit tasks whose trigger date has
come are handed to their owners.
"""

import logging
import sys

from apscheduler.schedulers.background import BackgroundScheduler

from horilla.background_utils import with_db_cleanup

logger = logging.getLogger(__name__)


@with_db_cleanup
def move_due_exits():
    from offboarding.exit_service import process_due_exits
    from offboarding.exit_tasks import trigger_due

    moved = process_due_exits()
    if moved:
        logger.info("Moved %s employee(s) to Exited", moved)
    fired = trigger_due()
    if fired:
        logger.info("Handed %s exit task(s) to their owners", fired)
    from offboarding.exit_survey import send_due

    surveys = send_due()
    if surveys:
        logger.info("Sent %s exit survey(s)", surveys)


if not any(
    cmd in sys.argv
    for cmd in [
        "makemigrations",
        "migrate",
        "compilemessages",
        "flush",
        "shell",
        "collectstatic",
        "test",
    ]
):
    scheduler = BackgroundScheduler()
    scheduler.add_job(move_due_exits, "interval", hours=3, id="offboarding_move_due_exits")
    scheduler.start()
