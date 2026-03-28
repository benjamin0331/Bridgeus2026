"""
ASGI config for BridgeUs — Django Channels entry point.
"""
import os
from django.core.asgi import get_asgi_application
from channels.routing import ProtocolTypeRouter, URLRouter
from channels.auth import AuthMiddlewareStack

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.dev')

django_asgi_app = get_asgi_application()

from apps.dialogue.routing import websocket_urlpatterns as dialogue_ws
from apps.nlp_analysis.routing import websocket_urlpatterns as nlp_ws

application = ProtocolTypeRouter({
    'http': django_asgi_app,
    'websocket': AuthMiddlewareStack(
        URLRouter(
            dialogue_ws + nlp_ws
        )
    ),
})
