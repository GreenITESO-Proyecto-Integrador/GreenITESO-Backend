"""ASGI config for GreenITESO: HTTP through Django, WebSockets through Channels."""

import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "green_iteso.settings")

# The ASGI app must be built before importing code that touches Django models.
# pylint: disable=wrong-import-position
from channels.routing import ProtocolTypeRouter, URLRouter  # noqa: E402
from channels.security.websocket import AllowedHostsOriginValidator  # noqa: E402
from django.core.asgi import get_asgi_application  # noqa: E402

django_asgi_app = get_asgi_application()

from green_iteso.notifications.routing import websocket_urlpatterns  # noqa: E402

application = ProtocolTypeRouter(
    {
        "http": django_asgi_app,
        "websocket": AllowedHostsOriginValidator(URLRouter(websocket_urlpatterns)),
    }
)
