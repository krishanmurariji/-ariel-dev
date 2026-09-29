"""
WSGI config for horilla project.

It exposes the WSGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/4.1/howto/deployment/wsgi/
"""

import os

from horilla.scheduler_guard import install as run_scheduled_jobs_once
from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "horilla.settings")

# Several workers each boot Django; only one may run the scheduled jobs.
run_scheduled_jobs_once()

application = get_wsgi_application()
