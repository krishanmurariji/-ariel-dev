"""
vertex/scheduler.py

Background jobs of Ariel Vertex, on the APScheduler pattern the other Ariel HR modules use
(see payroll_pro/scheduler.py). One job runs every 15 minutes and sends the daily expense summary
after 18:00 and the weekly one on Fridays after 18:00; vertex.expenses makes sure each goes out once.
"""

import logging
import sys

from apscheduler.schedulers.background import BackgroundScheduler

from horilla.background_utils import with_db_cleanup

logger = logging.getLogger(__name__)


@with_db_cleanup
def expense_summaries_tick():
    from vertex import expenses

    try:
        sent = expenses.run_due_summaries()
        if sent:
            logger.info("Vertex expense summaries sent: %s", ", ".join(sent))
    except Exception:
        logger.exception("Vertex expense summary job failed")


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
        "vertex_demo_users",
        "vertex_expense_summary",
    ]
):
    scheduler = BackgroundScheduler()
    scheduler.add_job(expense_summaries_tick, "interval", minutes=15)
    scheduler.start()
