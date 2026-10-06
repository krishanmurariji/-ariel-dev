"""
ASGI config for horilla project.

It exposes the ASGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/4.1/howto/deployment/asgi/
"""

import os

from horilla.scheduler_guard import install as run_scheduled_jobs_once
from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "horilla.settings")

# Several workers each boot Django; only one may run the scheduled jobs.
run_scheduled_jobs_once()

# get_asgi_application() must run before importing anything that touches
# Django models/apps (channels routing imports recruitment.consumers,
# which imports models) - AppRegistryNotReady otherwise.
django_asgi_app = get_asgi_application()

from channels.auth import AuthMiddlewareStack
from channels.routing import ProtocolTypeRouter, URLRouter
from channels.security.websocket import AllowedHostsOriginValidator

import recruitment.routing

application = ProtocolTypeRouter(
    {
        "http": django_asgi_app,
        # AuthMiddlewareStack parses the session cookie for every websocket
        # connection, giving the consumer both scope["user"] (Django-auth
        # employees) and scope["session"] (candidates, who never log in via
        # Django auth and are instead tracked by a "candidate_id" session
        # key - see candidate_login_required, recruitment/decorators.py).
        #
        # AllowedHostsOriginValidator rejects a websocket opened from a page on
        # any other site. Without it a malicious page could connect using the
        # visitor's session cookie (cross-site WebSocket hijacking), because
        # browsers do not apply CORS to websockets.
        "websocket": AllowedHostsOriginValidator(
            AuthMiddlewareStack(URLRouter(recruitment.routing.websocket_urlpatterns))
        ),
    }
)
