"""
ASGI config for pisa project.

It exposes the ASGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/5.1/howto/deployment/asgi/
"""

import os

from channels.auth import AuthMiddlewareStack
from channels.routing import ProtocolTypeRouter, URLRouter
from channels.security.websocket import AllowedHostsOriginValidator
from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "pisa.settings")

# Initialise Django (populate the app registry) before importing routing, which pulls in
# consumers that touch models.
django_asgi_app = get_asgi_application()

import homework.routing  # noqa: E402

application = ProtocolTypeRouter(
    {
        "http": django_asgi_app,
        # Session-cookie auth (scope["user"]) + same-origin check for the Lean LSP socket.
        "websocket": AllowedHostsOriginValidator(
            AuthMiddlewareStack(URLRouter(homework.routing.websocket_urlpatterns))
        ),
    }
)
