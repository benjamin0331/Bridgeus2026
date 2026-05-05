from uuid import uuid4
from unittest.mock import patch

import pytest
from asgiref.sync import sync_to_async
from channels.testing import WebsocketCommunicator
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import override_settings
from rest_framework_simplejwt.tokens import AccessToken

from api.models import AIConversation, DialogueMatch, MatchMessage

User = get_user_model()

TEST_CHANNEL_LAYERS = {
    "default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}
}

create_user = sync_to_async(User.objects.create_user, thread_sensitive=True)


class FakeStreamingDialogueAgent:
    async def astream_respond(self, session):
        yield f"AI reply to: {session.history[-1].content}"


async def _access_token_for(user):
    return await sync_to_async(lambda: str(AccessToken.for_user(user)))()


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
@override_settings(CHANNEL_LAYERS=TEST_CHANNEL_LAYERS)
async def test_dialogue_websocket_persists_completed_turn():
    from BridgeUs_Django.asgi import application
    from apps.matching.services.ai_agent import DialogueSession

    user = await create_user(username="ai_ws_user", password="secret123")
    session_id = uuid4().hex
    session = DialogueSession(
        topic="台灣核能議題討論",
        topic_description="討論台灣是否應使用核能。",
        agent_stance="較反對核電",
        agent_stance_summary="以反方角度提出核安與核廢料疑慮。",
        user_stance_label="較支持核電",
        user_stance_score=6.5,
    )
    await sync_to_async(cache.set)(
        f"dialogue_session:{session_id}",
        {
            "user_id": user.id,
            "session_id": session_id,
            "topic_id": 102,
            "topic_title": "台灣核能議題討論",
            "collection_name": "nuclear_energy_all",
            "session": session.to_dict(),
        },
    )

    token = await _access_token_for(user)
    communicator = WebsocketCommunicator(
        application,
        f"/ws/dialogue/{session_id}/?token={token}",
    )

    with patch("api.views.get_dialogue_agent", return_value=FakeStreamingDialogueAgent()):
        connected, _ = await communicator.connect()
        assert connected

        await communicator.send_json_to(
            {"type": "user_message", "content": "核能真的比較穩定嗎？"}
        )
        stream_message = await communicator.receive_json_from(timeout=3)
        end_message = await communicator.receive_json_from(timeout=3)

    assert stream_message == {
        "type": "agent_stream",
        "content": "AI reply to: 核能真的比較穩定嗎？",
    }
    assert end_message == {"type": "agent_stream_end"}

    saved_turn = await AIConversation.objects.aget(session_id=session_id)
    assert saved_turn.user_id == user.id
    assert saved_turn.topic_id == 102
    assert saved_turn.user_prompt == "核能真的比較穩定嗎？"
    assert saved_turn.ai_response == "AI reply to: 核能真的比較穩定嗎？"

    await communicator.disconnect()


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
@override_settings(CHANNEL_LAYERS=TEST_CHANNEL_LAYERS)
async def test_match_room_websocket_persists_and_broadcasts_message():
    from BridgeUs_Django.asgi import application

    alice = await create_user(username="match_ws_alice", password="secret123")
    bob = await create_user(username="match_ws_bob", password="secret123")
    match = await DialogueMatch.objects.acreate(
        topic_id=102,
        user_a=alice,
        user_b=bob,
        user_a_score=6.5,
        user_b_score=2.0,
        room_id=uuid4().hex,
        status=DialogueMatch.Status.ACTIVE,
    )

    alice_token = await _access_token_for(alice)
    bob_token = await _access_token_for(bob)
    comm_a = WebsocketCommunicator(
        application,
        f"/ws/matching/rooms/{match.room_id}/?token={alice_token}",
    )
    comm_b = WebsocketCommunicator(
        application,
        f"/ws/matching/rooms/{match.room_id}/?token={bob_token}",
    )

    connected_a, _ = await comm_a.connect()
    connected_b, _ = await comm_b.connect()
    assert connected_a
    assert connected_b

    await comm_a.send_json_to({"type": "match_message", "content": "我想先談核安。"})
    response_a = await comm_a.receive_json_from(timeout=3)
    response_b = await comm_b.receive_json_from(timeout=3)

    assert response_a["type"] == "match_message"
    assert response_a == response_b
    payload = response_a["message"]
    assert payload["room_id"] == match.room_id
    assert payload["sender_id"] == alice.id
    assert payload["sender_name"] == "匿名使用者"
    assert payload["content"] == "我想先談核安。"

    saved_message = await MatchMessage.objects.aget(id=payload["id"])
    assert saved_message.match_id == match.id
    assert saved_message.sender_id == alice.id
    assert saved_message.content == "我想先談核安。"

    await comm_a.disconnect()
    await comm_b.disconnect()
