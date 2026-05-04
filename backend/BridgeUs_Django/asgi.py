import os

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'BridgeUs_Django.settings')

from django.core.asgi import get_asgi_application

django_asgi_app = get_asgi_application()

from channels.routing import ProtocolTypeRouter, URLRouter

from api.routing import websocket_urlpatterns as ai_websocket_urlpatterns
from chat.routing import websocket_urlpatterns as chat_websocket_urlpatterns

application = ProtocolTypeRouter(
    {
        'http': django_asgi_app,
        'websocket': URLRouter(
            ai_websocket_urlpatterns + chat_websocket_urlpatterns
        ),
    }
)
