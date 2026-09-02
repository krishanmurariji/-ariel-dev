"""
ASGI config for horilla project.

It exposes the ASGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/4.1/howto/deployment/asgi/
"""

import os

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "horilla.settings")

# get_asgi_application() must run before importing anything that touches
# Django models/apps (channels routing imports recruitment.consumers,
# which imports models) - AppRegistryNotReady otherwise.
django_asgi_app = get_asgi_application()

from channels.auth import AuthMiddlewareStack
from channels.routing import ProtocolTypeRouter, URLRouter

import recruitment.routing

application = ProtocolTypeRouter(
    {
        "http": django_asgi_app,
        # AuthMiddlewareStack parses the session cookie for every websocket
        # connection, giving the consumer both scope["user"] (Django-auth
        # employees) and scope["session"] (candidates, who never log in via
        # Django auth and are instead tracked by a "candidate_id" session
        # key - see candidate_login_required, recruitment/decorators.py).
        "websocket": AuthMiddlewareStack(
            URLRouter(recruitment.routing.websocket_urlpatterns)
        ),
    }
)
