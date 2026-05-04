from django.urls import re_path

from . import consumers

websocket_urlpatterns = [
    re_path(
        r"ws/dialogue/(?P<session_id>[0-9a-f]+)/$",
        consumers.DialogueStreamConsumer.as_asgi(),
    ),
    re_path(
        r"ws/matching/rooms/(?P<room_id>[0-9a-f]+)/$",
        consumers.MatchRoomConsumer.as_asgi(),
    ),
]
