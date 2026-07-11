import asyncio
from uuid import uuid4
from unittest.mock import AsyncMock, patch

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


def make_test_embedding(first_value):
    return [float(first_value), *([0.0] * 383)]


@pytest.fixture(autouse=True)
def disable_hh_ai_assist_env(monkeypatch):
    monkeypatch.setenv("H_H_AI_ASSIST_ENABLED", "false")


class FakeStreamingDialogueAgent:
    async def astream_respond(self, session):
        yield f"AI reply to: {session.history[-1].content}"


async def _access_token_for(user):
    return await sync_to_async(lambda: str(AccessToken.for_user(user)))()


async def _make_active_match(alice, bob):
    return await DialogueMatch.objects.acreate(
        topic_id=102,
        user_a=alice,
        user_b=bob,
        user_a_score=6.5,
        user_b_score=2.0,
        room_id=uuid4().hex,
        status=DialogueMatch.Status.ACTIVE,
    )


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
            "survey_context": {"q9_embedding": make_test_embedding(1)},
            "session": session.to_dict(),
        },
    )

    token = await _access_token_for(user)
    communicator = WebsocketCommunicator(
        application,
        f"/ws/dialogue/{session_id}/?token={token}",
    )

    with (
        patch("api.views.get_dialogue_agent", return_value=FakeStreamingDialogueAgent()),
        patch("api.consumers.aget_embedding", new=AsyncMock(return_value=make_test_embedding(-1))),
    ):
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
    assert end_message["type"] == "agent_stream_end"
    assert end_message["stance_drift"]["drift_value"] == 2.0
    assert end_message["stance_drift"]["measured_at"]

    saved_turn = await AIConversation.objects.aget(session_id=session_id)
    assert saved_turn.user_id == user.id
    assert saved_turn.topic_id == 102
    assert saved_turn.user_prompt == "核能真的比較穩定嗎？"
    assert saved_turn.ai_response == "AI reply to: 核能真的比較穩定嗎？"

    await communicator.disconnect()


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
@override_settings(CHANNEL_LAYERS=TEST_CHANNEL_LAYERS)
async def test_dialogue_websocket_ignores_non_object_payload_without_crashing():
    from BridgeUs_Django.asgi import application
    from apps.matching.services.ai_agent import DialogueSession

    user = await create_user(username="ai_ws_payload_user", password="secret123")
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
            "survey_context": {"q9_embedding": make_test_embedding(1)},
            "session": session.to_dict(),
        },
    )

    token = await _access_token_for(user)
    communicator = WebsocketCommunicator(
        application,
        f"/ws/dialogue/{session_id}/?token={token}",
    )

    with (
        patch("api.views.get_dialogue_agent", return_value=FakeStreamingDialogueAgent()),
        patch("api.consumers.aget_embedding", new=AsyncMock(return_value=make_test_embedding(-1))),
    ):
        assert (await communicator.connect())[0]
        await communicator.send_to(text_data="[1]")
        assert await communicator.receive_nothing(timeout=0.5)

        await communicator.send_json_to(
            {"type": "user_message", "content": "第二次送正常 payload"}
        )
        stream_message = await communicator.receive_json_from(timeout=3)
        end_message = await communicator.receive_json_from(timeout=3)

    assert stream_message["type"] == "agent_stream"
    assert end_message["type"] == "agent_stream_end"
    saved_turn = await AIConversation.objects.aget(session_id=session_id)
    assert saved_turn.user_prompt == "第二次送正常 payload"

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


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
@override_settings(CHANNEL_LAYERS=TEST_CHANNEL_LAYERS)
async def test_match_room_websocket_ignores_non_string_content_without_crashing():
    from BridgeUs_Django.asgi import application

    alice = await create_user(username="match_ws_bad_content_alice", password="secret123")
    bob = await create_user(username="match_ws_bad_content_bob", password="secret123")
    match = await _make_active_match(alice, bob)

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

    assert (await comm_a.connect())[0]
    assert (await comm_b.connect())[0]

    await comm_a.send_json_to({"type": "match_message", "content": ["bad"]})
    assert await comm_a.receive_nothing(timeout=0.5)
    assert await comm_b.receive_nothing(timeout=0.5)

    await comm_a.send_json_to({"type": "match_message", "content": "正常訊息"})
    response_a = await comm_a.receive_json_from(timeout=3)
    response_b = await comm_b.receive_json_from(timeout=3)

    assert response_a["type"] == "match_message"
    assert response_a == response_b
    assert response_a["message"]["content"] == "正常訊息"
    assert await MatchMessage.objects.filter(match=match).acount() == 1

    await comm_a.disconnect()
    await comm_b.disconnect()


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
@override_settings(CHANNEL_LAYERS=TEST_CHANNEL_LAYERS)
async def test_match_room_ai_assist_blocks_blacklisted_message():
    from BridgeUs_Django.asgi import application

    alice = await create_user(username="assist_block_alice", password="secret123")
    bob = await create_user(username="assist_block_bob", password="secret123")
    match = await _make_active_match(alice, bob)

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
    assert (await comm_a.connect())[0]
    assert (await comm_b.connect())[0]

    with patch("api.consumers.hh_ai_assist_enabled", return_value=True):
        await comm_a.send_json_to({"type": "match_message", "content": "你這個白痴"})
        prompt = await comm_a.receive_json_from(timeout=3)

    assert prompt["type"] == "match_system_prompt"
    assert prompt["category"] == "content_blocked"
    assert "message" in prompt
    assert await comm_b.receive_nothing(timeout=1)
    assert await MatchMessage.objects.filter(match=match).acount() == 0

    await comm_a.disconnect()
    await comm_b.disconnect()


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
@override_settings(CHANNEL_LAYERS=TEST_CHANNEL_LAYERS)
async def test_match_room_ai_assist_sends_rephrase_suggestion_without_relay():
    from BridgeUs_Django.asgi import application
    from api.models import MatchAISuggestion

    alice = await create_user(username="assist_rephrase_alice", password="secret123")
    bob = await create_user(username="assist_rephrase_bob", password="secret123")
    match = await _make_active_match(alice, bob)

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
    assert (await comm_a.connect())[0]
    assert (await comm_b.connect())[0]

    high_emotion = {"score": 0.91, "label": "negative", "is_over_threshold": True}
    with patch("api.consumers.hh_ai_assist_enabled", return_value=True), \
         patch("api.consumers.aget_analyze_emotion", new=AsyncMock(return_value=high_emotion)), \
         patch("api.consumers.arephrase_match_message", new=AsyncMock(return_value=("請用更平和的語氣表達。", True))):
        await comm_a.send_json_to({"type": "match_message", "content": "你完全不懂核電"})
        suggestion = await comm_a.receive_json_from(timeout=3)

    assert suggestion["type"] == "match_ai_suggestion"
    assert suggestion["category"] == "rephrase"
    assert suggestion["original_content"] == "你完全不懂核電"
    assert suggestion["suggested_content"] == "請用更平和的語氣表達。"
    assert suggestion["actions"] == ["accept", "modify", "ignore"]
    assert await comm_b.receive_nothing(timeout=1)
    assert await MatchMessage.objects.filter(match=match).acount() == 0
    saved = await MatchAISuggestion.objects.aget(id=suggestion["suggestion_id"])
    assert saved.trigger_score == 0.91
    assert saved.user_action is None

    await comm_a.disconnect()
    await comm_b.disconnect()


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
@override_settings(CHANNEL_LAYERS=TEST_CHANNEL_LAYERS)
async def test_match_room_high_emotion_without_second_person_is_not_intercepted():
    """High-arousal factual venting (no 你/您/妳) should relay, not trigger rephrase."""
    from BridgeUs_Django.asgi import application

    alice = await create_user(username="vent_alice", password="secret123")
    bob = await create_user(username="vent_bob", password="secret123")
    match = await _make_active_match(alice, bob)

    alice_token = await _access_token_for(alice)
    bob_token = await _access_token_for(bob)
    comm_a = WebsocketCommunicator(application, f"/ws/matching/rooms/{match.room_id}/?token={alice_token}")
    comm_b = WebsocketCommunicator(application, f"/ws/matching/rooms/{match.room_id}/?token={bob_token}")
    assert (await comm_a.connect())[0]
    assert (await comm_b.connect())[0]

    high_emotion = {"score": 0.95, "label": "negative", "is_over_threshold": True}
    with patch("api.consumers.hh_ai_assist_enabled", return_value=True), \
         patch("api.consumers.aget_analyze_emotion", new=AsyncMock(return_value=high_emotion)):
        await comm_a.send_json_to({"type": "match_message", "content": "核電根本就是一場騙局"})
        relayed = await comm_a.receive_json_from(timeout=3)

    # No rephrase suggestion — the message is broadcast as-is to both participants.
    assert relayed["type"] == "match_message"
    assert relayed["message"]["content"] == "核電根本就是一場騙局"
    assert (await comm_b.receive_json_from(timeout=3))["message"]["content"] == "核電根本就是一場騙局"
    assert await MatchMessage.objects.filter(match=match).acount() == 1

    await comm_a.disconnect()
    await comm_b.disconnect()


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
@override_settings(CHANNEL_LAYERS=TEST_CHANNEL_LAYERS)
async def test_match_room_ai_assist_accepts_suggestion_and_relays_suggested_content():
    from BridgeUs_Django.asgi import application
    from api.models import MatchAISuggestion

    alice = await create_user(username="assist_accept_alice", password="secret123")
    bob = await create_user(username="assist_accept_bob", password="secret123")
    match = await _make_active_match(alice, bob)

    alice_token = await _access_token_for(alice)
    bob_token = await _access_token_for(bob)
    comm_a = WebsocketCommunicator(application, f"/ws/matching/rooms/{match.room_id}/?token={alice_token}")
    comm_b = WebsocketCommunicator(application, f"/ws/matching/rooms/{match.room_id}/?token={bob_token}")
    assert (await comm_a.connect())[0]
    assert (await comm_b.connect())[0]

    high_emotion = {"score": 0.95, "label": "negative", "is_over_threshold": True}
    with patch("api.consumers.hh_ai_assist_enabled", return_value=True), \
         patch("api.consumers.aget_analyze_emotion", new=AsyncMock(return_value=high_emotion)), \
         patch("api.consumers.arephrase_match_message", new=AsyncMock(return_value=("我不同意你的核電看法，但想理解你的理由。", True))):
        await comm_a.send_json_to({"type": "match_message", "content": "你完全不懂"})
        suggestion = await comm_a.receive_json_from(timeout=3)
        await comm_a.send_json_to({
            "type": "accept_suggestion",
            "suggestion_id": suggestion["suggestion_id"],
        })
        response_a = await comm_a.receive_json_from(timeout=3)
        response_b = await comm_b.receive_json_from(timeout=3)

    assert response_a == response_b
    assert response_a["type"] == "match_message"
    assert response_a["message"]["content"] == "我不同意你的核電看法，但想理解你的理由。"
    saved = await MatchAISuggestion.objects.aget(id=suggestion["suggestion_id"])
    assert saved.user_action == MatchAISuggestion.Action.ACCEPT
    assert saved.final_content == "我不同意你的核電看法，但想理解你的理由。"

    await comm_a.disconnect()
    await comm_b.disconnect()


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
@override_settings(CHANNEL_LAYERS=TEST_CHANNEL_LAYERS)
async def test_match_room_ai_assist_modifies_suggestion_after_second_emotion_check():
    from BridgeUs_Django.asgi import application
    from api.models import MatchAISuggestion

    alice = await create_user(username="assist_modify_alice", password="secret123")
    bob = await create_user(username="assist_modify_bob", password="secret123")
    match = await _make_active_match(alice, bob)

    alice_token = await _access_token_for(alice)
    bob_token = await _access_token_for(bob)
    comm_a = WebsocketCommunicator(application, f"/ws/matching/rooms/{match.room_id}/?token={alice_token}")
    comm_b = WebsocketCommunicator(application, f"/ws/matching/rooms/{match.room_id}/?token={bob_token}")
    assert (await comm_a.connect())[0]
    assert (await comm_b.connect())[0]

    high_emotion = {"score": 0.95, "label": "negative", "is_over_threshold": True}
    low_emotion = {"score": 0.10, "label": "neutral", "is_over_threshold": False}
    with patch("api.consumers.hh_ai_assist_enabled", return_value=True), \
         patch("api.consumers.aget_analyze_emotion", new=AsyncMock(side_effect=[high_emotion, low_emotion])), \
         patch("api.consumers.arephrase_match_message", new=AsyncMock(return_value=("請改成比較平和的說法。", True))):
        await comm_a.send_json_to({"type": "match_message", "content": "你完全不懂"})
        suggestion = await comm_a.receive_json_from(timeout=3)
        await comm_a.send_json_to({
            "type": "modify_suggestion",
            "suggestion_id": suggestion["suggestion_id"],
            "content": "我不同意，但想聽聽你的理由。",
        })
        response_a = await comm_a.receive_json_from(timeout=3)
        response_b = await comm_b.receive_json_from(timeout=3)

    assert response_a == response_b
    assert response_a["message"]["content"] == "我不同意，但想聽聽你的理由。"
    saved = await MatchAISuggestion.objects.aget(id=suggestion["suggestion_id"])
    assert saved.user_action == MatchAISuggestion.Action.MODIFY
    assert saved.modified_content == "我不同意，但想聽聽你的理由。"
    assert saved.final_content == "我不同意，但想聽聽你的理由。"

    await comm_a.disconnect()
    await comm_b.disconnect()


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
@override_settings(CHANNEL_LAYERS=TEST_CHANNEL_LAYERS)
async def test_match_room_ai_assist_relays_when_emotion_analysis_times_out():
    from BridgeUs_Django.asgi import application

    alice = await create_user(username="assist_timeout_alice", password="secret123")
    bob = await create_user(username="assist_timeout_bob", password="secret123")
    match = await _make_active_match(alice, bob)

    alice_token = await _access_token_for(alice)
    bob_token = await _access_token_for(bob)
    comm_a = WebsocketCommunicator(application, f"/ws/matching/rooms/{match.room_id}/?token={alice_token}")
    comm_b = WebsocketCommunicator(application, f"/ws/matching/rooms/{match.room_id}/?token={bob_token}")
    assert (await comm_a.connect())[0]
    assert (await comm_b.connect())[0]

    async def slow_emotion(_content):
        await asyncio.sleep(1)
        return {"score": 0.99, "label": "negative", "is_over_threshold": True}

    with patch("api.consumers.hh_ai_assist_enabled", return_value=True), \
         patch("api.consumers.ai_assist_timeout_seconds", return_value=0.01), \
         patch("api.consumers.aget_analyze_emotion", new=AsyncMock(side_effect=slow_emotion)), \
         patch("api.consumers.aget_embedding", new=AsyncMock(return_value=[0.0] * 384)):
        await comm_a.send_json_to({"type": "match_message", "content": "我想先談核能穩定供電。"})
        response_a = await comm_a.receive_json_from(timeout=0.5)
        response_b = await comm_b.receive_json_from(timeout=0.5)

    assert response_a == response_b
    assert response_a["type"] == "match_message"
    assert response_a["message"]["content"] == "我想先談核能穩定供電。"
    saved_message = await MatchMessage.objects.aget(id=response_a["message"]["id"])
    assert saved_message.emotion_score is None

    await comm_a.disconnect()
    await comm_b.disconnect()

@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
@override_settings(CHANNEL_LAYERS=TEST_CHANNEL_LAYERS)
async def test_match_room_ai_assist_rephrase_fallback_disallows_accept():
    from BridgeUs_Django.asgi import application

    alice = await create_user(username="assist_fallback_alice", password="secret123")
    bob = await create_user(username="assist_fallback_bob", password="secret123")
    match = await _make_active_match(alice, bob)

    alice_token = await _access_token_for(alice)
    comm_a = WebsocketCommunicator(application, f"/ws/matching/rooms/{match.room_id}/?token={alice_token}")
    assert (await comm_a.connect())[0]

    high_emotion = {"score": 0.95, "label": "negative", "is_over_threshold": True}
    with patch("api.consumers.hh_ai_assist_enabled", return_value=True), \
         patch("api.consumers.aget_analyze_emotion", new=AsyncMock(return_value=high_emotion)), \
         patch("api.consumers.arephrase_match_message", new=AsyncMock(return_value=("你的發言可能帶有較強烈的情緒，建議修改後再發送。", False))):
        await comm_a.send_json_to({"type": "match_message", "content": "你完全不懂"})
        suggestion = await comm_a.receive_json_from(timeout=3)

    assert suggestion["type"] == "match_ai_suggestion"
    assert suggestion["actions"] == ["modify", "ignore"]

    await comm_a.disconnect()


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
@override_settings(CHANNEL_LAYERS=TEST_CHANNEL_LAYERS)
async def test_match_room_pushes_sender_drift_after_each_message():
    """Each user message recomputes and pushes that speaker's own drift (mirrors the
    H-AI per-turn cadence, no 200-char throttle) and only to the sender."""
    from BridgeUs_Django.asgi import application

    alice = await create_user(username="drift_alice", password="secret123")
    bob = await create_user(username="drift_bob", password="secret123")
    match = await _make_active_match(alice, bob)

    alice_token = await _access_token_for(alice)
    bob_token = await _access_token_for(bob)
    comm_a = WebsocketCommunicator(application, f"/ws/matching/rooms/{match.room_id}/?token={alice_token}")
    comm_b = WebsocketCommunicator(application, f"/ws/matching/rooms/{match.room_id}/?token={bob_token}")
    assert (await comm_a.connect())[0]
    assert (await comm_b.connect())[0]

    low_emotion = {"score": 0.1, "label": "neutral", "is_over_threshold": False}
    drift_mock = AsyncMock(return_value={"drift_value": 0.42, "direction": "approaching"})
    with patch("api.consumers.hh_ai_assist_enabled", return_value=True), \
         patch("api.consumers.aget_analyze_emotion", new=AsyncMock(return_value=low_emotion)), \
         patch("api.consumers.aget_embedding", new=AsyncMock(return_value=make_test_embedding(1))), \
         patch("api.consumers.aget_topic_anchor_embedding", new=AsyncMock(return_value=make_test_embedding(0.5))), \
         patch("api.consumers.acheck_match_topic_relevance", new=AsyncMock(return_value={"is_off_topic": False, "relevance_score": 0.9})), \
         patch("api.consumers.adetect_match_stalemate", new=AsyncMock(return_value={"is_stalemate": False})), \
         patch("api.consumers.acalculate_match_stance_drift", new=drift_mock):
        # A short (<200 char) message must still trigger a drift recompute.
        await comm_a.send_json_to({"type": "match_message", "content": "短短一句話。"})

        msg_a = await comm_a.receive_json_from(timeout=3)
        msg_b = await comm_b.receive_json_from(timeout=3)
        assert msg_a["type"] == "match_message"
        assert msg_b["type"] == "match_message"

        drift_a = await comm_a.receive_json_from(timeout=3)
        assert drift_a["type"] == "match_stance_drift"
        assert drift_a["stance_drift"]["drift_value"] == 0.42
        assert drift_a["stance_drift"]["measured_at"]

        # The other user is not sent a drift push for the sender's message.
        assert await comm_b.receive_nothing(timeout=1)

    # Drift was recomputed for the sender only.
    drift_mock.assert_awaited_once_with(match_id=match.id, user_id=alice.id)

    await comm_a.disconnect()
    await comm_b.disconnect()
