"""
horilla/background_utils.py

Django only closes a database connection automatically as part of the
normal HTTP request/response cycle. Anything that opens one from outside
that cycle - a manually-started threading.Thread, or a job run by
APScheduler's BackgroundScheduler - never gets that connection closed by
Django on its own; it's only reclaimed whenever the garbage collector
eventually gets around to it, which is not guaranteed to happen promptly
(or, under load, before the database's connection limit is reached).

This is exactly what caused a production outage: every one of ~50
background jobs/threads across the app leaked one database connection
per run, and the most frequent of them fire every 60 seconds. Once every
available connection slot was taken, the whole app went down with
"remaining connection slots are reserved for roles with the SUPERUSER
attribute" on every single request, and stayed down until the process
was restarted (which is what finally freed the leaked connections).

Use @with_db_cleanup on any function that is the *target* of a
threading.Thread(...) or scheduler.add_job(...) call - i.e. code that
runs outside a normal Django view/request - so its connection is always
closed when it finishes, success or failure.

This unconditionally force-closes the connection (django.db.connections
.close_all()) rather than the age-gated close_old_connections() a normal
Django view relies on. That gating exists to let CONN_MAX_AGE (600s here)
amortize a connection across a *burst of web requests* - it deliberately
keeps a connection open if it's not old enough yet, on the assumption
another request will reuse it any second. A background job doesn't fit
that assumption: this app alone starts ~12 independent
BackgroundScheduler() instances (one per module) plus one more per
biometric device, each with its own thread pool, so relying on the
age-gate meant dozens of independent worker threads could each end up
holding their own connection open and idle for the full 10 minutes for a
job that only fires once every 60 seconds to once a day - not leaked
forever like before, but still a large, unnecessary idle baseline that
was itself observed pushing the server close to its connection limit
again shortly after a restart. Force-closing trades a small, infrequent
per-run reconnect cost for never holding a connection open when nothing
is using it.
"""

import functools
import logging

from django.db import connections

logger = logging.getLogger(__name__)


def with_db_cleanup(func):
    """
    Wraps a function meant to run in a background thread or scheduled job
    so its database connection is force-closed when the function returns,
    even if it raised. Does not suppress or alter the function's own
    return value or exceptions - it only guarantees the connection
    cleanup runs.
    """

    @functools.wraps(func)
    def _wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        finally:
            connections.close_all()

    return _wrapper
