from django.urls import re_path

from . import consumers

websocket_urlpatterns = [
    re_path(
        r"ws/hh/(?P<conversation_id>\d+)/$",
        consumers.HumanHumanConsumer.as_asgi(),
    ),
]
