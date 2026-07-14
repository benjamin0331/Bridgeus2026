import os

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'take_a_bridge.settings')

from django.core.asgi import get_asgi_application

django_asgi_app = get_asgi_application()

from channels.routing import ProtocolTypeRouter, URLRouter

from api.routing import websocket_urlpatterns as ai_websocket_urlpatterns

application = ProtocolTypeRouter(
    {
        'http': django_asgi_app,
        'websocket': URLRouter(ai_websocket_urlpatterns),
    }
)
