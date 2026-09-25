"""ASGI config for GreenITESO."""

import os

from django.core.asgi import get_asgi_application

# import notifications.routing

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "green_iteso.settings")
application = get_asgi_application()
# application = ProtocolTypeRouter({
#     "http": django_asgi_app,
#     "websocket": AuthMiddlewareStack(
#         URLRouter(
#             notifications.routing.websocket_urlpatterns
#         )
#     ),
# })
