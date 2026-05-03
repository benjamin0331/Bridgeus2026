import os

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'BridgeUs_Django.settings')

from django.core.asgi import get_asgi_application
django_asgi_app = get_asgi_application()

from channels.routing import ProtocolTypeRouter, URLRouter
from api.routing import websocket_urlpatterns as ai_ws_patterns
from chat.routing import websocket_urlpatterns as hh_ws_patterns

application = ProtocolTypeRouter({
    "http": django_asgi_app,
    "websocket": URLRouter(ai_ws_patterns + hh_ws_patterns),
})
