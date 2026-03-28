from django.urls import re_path

# TODO: M5 WebSocket consumers (CCND real-time push)
# from .consumers import CCNDConsumer

websocket_urlpatterns = [
    # re_path(r'ws/ccnd/(?P<session_id>\w+)/$', CCNDConsumer.as_asgi()),
]
