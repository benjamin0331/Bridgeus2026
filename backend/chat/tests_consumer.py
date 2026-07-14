"""
Integration tests for HumanHumanConsumer — NLP pipeline integration.

These tests exercise the full flow: content filter intercept, relay, DB persist,
and background NLP analysis (embedding + emotion detection).

WARNING: test_nlp_analysis_saved_to_db and test_high_emotion_message_relayed_with_warning
load real ML models on first run (~30-60s). Subsequent runs within the same pytest session
are faster because singleton models stay loaded.
"""

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
from asgiref.sync import sync_to_async
from channels.testing import WebsocketCommunicator
from django.contrib.auth import get_user_model
from django.test import override_settings

from chat.models import AISuggestion, Conversation

User = get_user_model()

TEST_CHANNEL_LAYERS = {
    "default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}
}

create_user = sync_to_async(User.objects.create_user, thread_sensitive=True)


async def _make_conversation(user_a, user_b, topic_id=1):
    return await Conversation.objects.acreate(
        topic_id=topic_id,
        user_a=user_a,
        user_b=user_b,
        session_number=1,
        status=Conversation.Status.ACTIVE,
    )


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
@override_settings(CHANNEL_LAYERS=TEST_CHANNEL_LAYERS)
async def test_normal_message_relayed_to_peer():
    """Clean message passes filter, relays to both participants with correct schema."""
    from take_a_bridge.asgi import application

    alice = await create_user(username="alice_ci1", password="x")
    bob = await create_user(username="bob_ci1", password="x")
    conv = await _make_conversation(alice, bob)

    comm_a = WebsocketCommunicator(application, f"/ws/hh/{conv.id}/?user_id={alice.id}")
    comm_b = WebsocketCommunicator(application, f"/ws/hh/{conv.id}/?user_id={bob.id}")
    await comm_a.connect()
    await comm_b.connect()

    await comm_a.send_json_to({"content": "核能政策需要更多理性討論"})

    resp_b = await comm_b.receive_json_from(timeout=5)
    assert resp_b["sender_id"] == alice.id
    assert resp_b["content"] == "核能政策需要更多理性討論"
    assert resp_b["conversation_id"] == conv.id
    assert "timestamp" in resp_b

    await comm_a.disconnect()
    await comm_b.disconnect()


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
@override_settings(CHANNEL_LAYERS=TEST_CHANNEL_LAYERS)
async def test_nlp_analysis_saved_to_db():
    """Background NLP task writes embedding (384-dim) and emotion_score to DB after relay."""
    from take_a_bridge.asgi import application
    from chat.models import Message

    alice = await create_user(username="alice_ci2", password="x")
    bob = await create_user(username="bob_ci2", password="x")
    conv = await _make_conversation(alice, bob)

    comm_a = WebsocketCommunicator(application, f"/ws/hh/{conv.id}/?user_id={alice.id}")
    await comm_a.connect()

    await comm_a.send_json_to({"content": "這個議題需要更多討論空間"})
    await comm_a.receive_json_from(timeout=5)  # consume relay echo

    # Poll until background NLP task writes both fields (max 60s for cold model load)
    msg = None
    for _ in range(60):
        await asyncio.sleep(1)
        msg = await Message.objects.aget(conversation=conv, sender=alice)
        if msg.embedding is not None and msg.emotion_score is not None:
            break

    assert msg is not None, "Message not found in DB"
    assert msg.embedding is not None, "Embedding not written by NLP background task"
    assert len(msg.embedding) == 384, f"Expected 384-dim embedding, got {len(msg.embedding)}"
    assert msg.emotion_score is not None, "emotion_score not written by NLP background task"
    assert 0.0 <= msg.emotion_score <= 1.0

    await comm_a.disconnect()


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
@override_settings(CHANNEL_LAYERS=TEST_CHANNEL_LAYERS)
async def test_blocked_message_not_relayed_sender_gets_prompt():
    """Blacklisted content: Alice gets content_blocked system_prompt; Bob receives nothing."""
    from take_a_bridge.asgi import application

    alice = await create_user(username="alice_ci3", password="x")
    bob = await create_user(username="bob_ci3", password="x")
    conv = await _make_conversation(alice, bob)

    comm_a = WebsocketCommunicator(application, f"/ws/hh/{conv.id}/?user_id={alice.id}")
    comm_b = WebsocketCommunicator(application, f"/ws/hh/{conv.id}/?user_id={bob.id}")
    await comm_a.connect()
    await comm_b.connect()

    await comm_a.send_json_to({"content": "你這個白痴在說什麼"})

    resp = await comm_a.receive_json_from(timeout=5)
    assert resp["type"] == "system_prompt"
    assert resp["category"] == "content_blocked"
    assert isinstance(resp["message"], str) and len(resp["message"]) > 0

    # No relay should have occurred — Bob's queue must be empty
    assert await comm_b.receive_nothing(timeout=1), "Bob should not receive a blocked message"

    await comm_a.disconnect()
    await comm_b.disconnect()


# ------------------------------------------------------------------
# Off-topic redirect: LLM path
# ------------------------------------------------------------------

async def _make_conversation_with_anchor(user_a, user_b, topic_id=1):
    """Conversation with a 384-dim anchor so topic checks actually run."""
    return await Conversation.objects.acreate(
        topic_id=topic_id,
        user_a=user_a,
        user_b=user_b,
        session_number=1,
        status=Conversation.Status.ACTIVE,
        topic_anchor_embedding=[0.0] * 384,
    )


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
@override_settings(CHANNEL_LAYERS=TEST_CHANNEL_LAYERS)
async def test_off_topic_sends_ai_suggestion_redirect(monkeypatch):
    """Off-topic detection now sends ai_suggestion (not system_prompt) with LLM-generated text."""
    from take_a_bridge.asgi import application

    alice = await create_user(username="ot_a1", password="x")
    bob = await create_user(username="ot_b1", password="x")
    conv = await _make_conversation_with_anchor(alice, bob)

    comm_a = WebsocketCommunicator(application, f"/ws/hh/{conv.id}/?user_id={alice.id}")
    comm_b = WebsocketCommunicator(application, f"/ws/hh/{conv.id}/?user_id={bob.id}")
    await comm_a.connect()
    await comm_b.connect()

    low_emotion = {"score": 0.05, "label": "neutral", "is_over_threshold": False}
    off_topic_result = {"is_off_topic": True}
    llm_redirect = "我們把焦點拉回議題吧，對方想聽你對這個議題的看法。"

    with patch("chat.consumers.aget_analyze_emotion", return_value=low_emotion), \
         patch("chat.consumers.acheck_topic_relevance", new=AsyncMock(return_value=off_topic_result)), \
         patch("chat.consumers.aredirect_to_topic", new=AsyncMock(return_value=llm_redirect)):
        await comm_a.send_json_to({"content": "今天天氣真不錯"})
        # Bob receives relay
        bob_msg = await comm_b.receive_json_from(timeout=5)
        assert bob_msg["content"] == "今天天氣真不錯"
        # Alice also receives the relay echo (no type key), then the redirect suggestion
        relay_echo = await comm_a.receive_json_from(timeout=5)
        assert "type" not in relay_echo  # relay echoes have no type
        alice_msg = await comm_a.receive_json_from(timeout=5)

    assert alice_msg["type"] == "ai_suggestion"
    assert alice_msg["category"] == "redirect"
    assert alice_msg["suggested_content"] == llm_redirect
    assert alice_msg["actions"] == ["accept", "ignore"]
    assert "original_content" not in alice_msg  # redirect has no original

    await comm_a.disconnect()
    await comm_b.disconnect()


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
@override_settings(CHANNEL_LAYERS=TEST_CHANNEL_LAYERS)
async def test_off_topic_llm_failure_falls_back_to_template(monkeypatch):
    """When LLM fails, redirect falls back to OFF_TOPIC_MESSAGES template."""
    from take_a_bridge.asgi import application
    from chat.consumers import OFF_TOPIC_MESSAGES

    alice = await create_user(username="ot_fb_a", password="x")
    bob = await create_user(username="ot_fb_b", password="x")
    conv = await _make_conversation_with_anchor(alice, bob)

    comm_a = WebsocketCommunicator(application, f"/ws/hh/{conv.id}/?user_id={alice.id}")
    comm_b = WebsocketCommunicator(application, f"/ws/hh/{conv.id}/?user_id={bob.id}")
    await comm_a.connect()
    await comm_b.connect()

    low_emotion = {"score": 0.05, "label": "neutral", "is_over_threshold": False}
    off_topic_result = {"is_off_topic": True}
    failing_llm = AsyncMock(side_effect=RuntimeError("LLM down"))

    with patch("chat.consumers.aget_analyze_emotion", return_value=low_emotion), \
         patch("chat.consumers.acheck_topic_relevance", new=AsyncMock(return_value=off_topic_result)), \
         patch("chat.consumers.aredirect_to_topic", new=failing_llm):
        await comm_a.send_json_to({"content": "我最近在看一部電影"})
        await comm_b.receive_json_from(timeout=5)  # relay to bob
        await comm_a.receive_json_from(timeout=5)  # relay echo to alice
        alice_msg = await comm_a.receive_json_from(timeout=5)

    assert alice_msg["type"] == "ai_suggestion"
    assert alice_msg["category"] == "redirect"
    assert alice_msg["suggested_content"] in OFF_TOPIC_MESSAGES

    await comm_a.disconnect()
    await comm_b.disconnect()


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
@override_settings(CHANNEL_LAYERS=TEST_CHANNEL_LAYERS)
async def test_off_topic_saves_aisuggestion_record(monkeypatch):
    """Off-topic redirect creates an AISuggestion with original_content=None."""
    from take_a_bridge.asgi import application

    alice = await create_user(username="ot_db_a", password="x")
    bob = await create_user(username="ot_db_b", password="x")
    conv = await _make_conversation_with_anchor(alice, bob)

    comm_a = WebsocketCommunicator(application, f"/ws/hh/{conv.id}/?user_id={alice.id}")
    comm_b = WebsocketCommunicator(application, f"/ws/hh/{conv.id}/?user_id={bob.id}")
    await comm_a.connect()
    await comm_b.connect()

    low_emotion = {"score": 0.05, "label": "neutral", "is_over_threshold": False}
    off_topic_result = {"is_off_topic": True}
    llm_redirect = "把討論帶回主題吧！"

    with patch("chat.consumers.aget_analyze_emotion", return_value=low_emotion), \
         patch("chat.consumers.acheck_topic_relevance", new=AsyncMock(return_value=off_topic_result)), \
         patch("chat.consumers.aredirect_to_topic", new=AsyncMock(return_value=llm_redirect)):
        await comm_a.send_json_to({"content": "昨天去爬山了"})
        await comm_b.receive_json_from(timeout=5)   # relay to bob
        await comm_a.receive_json_from(timeout=5)   # relay echo to alice
        await comm_a.receive_json_from(timeout=5)   # redirect suggestion to alice

    record = await AISuggestion.objects.filter(conversation=conv, user=alice).afirst()
    assert record is not None
    assert record.category == AISuggestion.Category.REDIRECT
    assert record.original_content is None
    assert record.suggested_content == llm_redirect
    assert record.user_action is None  # not yet responded

    await comm_a.disconnect()
    await comm_b.disconnect()


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
@override_settings(CHANNEL_LAYERS=TEST_CHANNEL_LAYERS)
async def test_redirect_accept_records_action_no_relay(monkeypatch):
    """Accepting a redirect suggestion records action in DB but does NOT relay any content."""
    from take_a_bridge.asgi import application

    alice = await create_user(username="ot_acc_a", password="x")
    bob = await create_user(username="ot_acc_b", password="x")
    conv = await _make_conversation_with_anchor(alice, bob)

    comm_a = WebsocketCommunicator(application, f"/ws/hh/{conv.id}/?user_id={alice.id}")
    comm_b = WebsocketCommunicator(application, f"/ws/hh/{conv.id}/?user_id={bob.id}")
    await comm_a.connect()
    await comm_b.connect()

    low_emotion = {"score": 0.05, "label": "neutral", "is_over_threshold": False}
    off_topic_result = {"is_off_topic": True}
    llm_redirect = "回到主題聊聊"

    with patch("chat.consumers.aget_analyze_emotion", return_value=low_emotion), \
         patch("chat.consumers.acheck_topic_relevance", new=AsyncMock(return_value=off_topic_result)), \
         patch("chat.consumers.aredirect_to_topic", new=AsyncMock(return_value=llm_redirect)):
        await comm_a.send_json_to({"content": "最近手遊很好玩"})
        await comm_b.receive_json_from(timeout=5)  # relay to bob
        await comm_a.receive_json_from(timeout=5)  # relay echo to alice
        await comm_a.receive_json_from(timeout=5)  # redirect suggestion

    # Alice accepts the redirect
    await comm_a.send_json_to({"type": "accept_suggestion"})

    # Bob must NOT receive any relay for the redirect accept
    assert await comm_b.receive_nothing(timeout=1)

    record = await AISuggestion.objects.filter(conversation=conv, user=alice).afirst()
    assert record.user_action == "accept"

    await comm_a.disconnect()
    await comm_b.disconnect()


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
@override_settings(CHANNEL_LAYERS=TEST_CHANNEL_LAYERS)
async def test_redirect_ignore_records_action_no_relay(monkeypatch):
    """Ignoring a redirect suggestion records action in DB but does NOT relay any content."""
    from take_a_bridge.asgi import application

    alice = await create_user(username="ot_ign_a", password="x")
    bob = await create_user(username="ot_ign_b", password="x")
    conv = await _make_conversation_with_anchor(alice, bob)

    comm_a = WebsocketCommunicator(application, f"/ws/hh/{conv.id}/?user_id={alice.id}")
    comm_b = WebsocketCommunicator(application, f"/ws/hh/{conv.id}/?user_id={bob.id}")
    await comm_a.connect()
    await comm_b.connect()

    low_emotion = {"score": 0.05, "label": "neutral", "is_over_threshold": False}
    off_topic_result = {"is_off_topic": True}

    with patch("chat.consumers.aget_analyze_emotion", return_value=low_emotion), \
         patch("chat.consumers.acheck_topic_relevance", new=AsyncMock(return_value=off_topic_result)), \
         patch("chat.consumers.aredirect_to_topic", new=AsyncMock(return_value="回到主題")):
        await comm_a.send_json_to({"content": "今天吃了很好吃的拉麵"})
        await comm_b.receive_json_from(timeout=5)  # relay to bob
        await comm_a.receive_json_from(timeout=5)  # relay echo to alice
        await comm_a.receive_json_from(timeout=5)  # redirect suggestion

    await comm_a.send_json_to({"type": "ignore_suggestion"})

    assert await comm_b.receive_nothing(timeout=1)

    record = await AISuggestion.objects.filter(conversation=conv, user=alice).afirst()
    assert record.user_action == "ignore"

    await comm_a.disconnect()
    await comm_b.disconnect()


# ------------------------------------------------------------------
# Inactivity watcher: suggest_direction
# ------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
@override_settings(CHANNEL_LAYERS=TEST_CHANNEL_LAYERS)
async def test_inactivity_watcher_sends_direction_to_both():
    """After mutual silence, both users receive ai_suggestion with category=direction."""
    from take_a_bridge.asgi import application

    alice = await create_user(username="inact_a1", password="x")
    bob = await create_user(username="inact_b1", password="x")
    conv = await _make_conversation(alice, bob)

    direction_text = "換個角度：從對方最在意的面向切入"

    # Speed up the watcher so the test doesn't wait 2 minutes
    with patch("chat.consumers._INACTIVITY_SECONDS", 0.2), \
         patch("chat.consumers._INACTIVITY_CHECK_INTERVAL", 0.1), \
         patch("chat.consumers._INACTIVITY_GRACE_SECONDS", 0), \
         patch("chat.consumers.asuggest_direction", new=AsyncMock(return_value=direction_text)):
        comm_a = WebsocketCommunicator(application, f"/ws/hh/{conv.id}/?user_id={alice.id}")
        comm_b = WebsocketCommunicator(application, f"/ws/hh/{conv.id}/?user_id={bob.id}")
        await comm_a.connect()
        await comm_b.connect()

        # check_interval=0.1s, threshold=0.2s → watcher fires after ~0.3s from connect
        await asyncio.sleep(0.5)

        alice_msg = await comm_a.receive_json_from(timeout=3)
        bob_msg = await comm_b.receive_json_from(timeout=3)

    assert alice_msg["type"] == "ai_suggestion"
    assert alice_msg["category"] == "direction"
    assert alice_msg["suggested_content"] == direction_text
    assert alice_msg["actions"] == ["accept", "ignore"]

    assert bob_msg["type"] == "ai_suggestion"
    assert bob_msg["category"] == "direction"
    assert bob_msg["suggested_content"] == direction_text

    await comm_a.disconnect()
    await comm_b.disconnect()


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
@override_settings(CHANNEL_LAYERS=TEST_CHANNEL_LAYERS)
async def test_inactivity_watcher_fires_only_once_without_activity():
    """Watcher fires exactly once per inactivity window; continued silence does NOT re-trigger."""
    from take_a_bridge.asgi import application

    alice = await create_user(username="inact_once_a", password="x")
    bob = await create_user(username="inact_once_b", password="x")
    conv = await _make_conversation(alice, bob)

    call_count = 0

    async def counting_suggest(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        return "只應觸發一次"

    with patch("chat.consumers._INACTIVITY_SECONDS", 0.15), \
         patch("chat.consumers._INACTIVITY_CHECK_INTERVAL", 0.1), \
         patch("chat.consumers._INACTIVITY_GRACE_SECONDS", 0), \
         patch("chat.consumers.asuggest_direction", new=AsyncMock(side_effect=counting_suggest)):
        comm_a = WebsocketCommunicator(application, f"/ws/hh/{conv.id}/?user_id={alice.id}")
        comm_b = WebsocketCommunicator(application, f"/ws/hh/{conv.id}/?user_id={bob.id}")
        await comm_a.connect()
        await comm_b.connect()

        # Wait long enough for multiple watcher cycles without any activity
        await asyncio.sleep(0.6)

        # Drain messages so disconnect is clean
        while not await comm_a.receive_nothing(timeout=0.05):
            await comm_a.receive_json_from(timeout=0.1)
        while not await comm_b.receive_nothing(timeout=0.05):
            await comm_b.receive_json_from(timeout=0.1)

    assert call_count == 1, f"Expected exactly 1 trigger, got {call_count}"

    await comm_a.disconnect()
    await comm_b.disconnect()


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
@override_settings(CHANNEL_LAYERS=TEST_CHANNEL_LAYERS)
async def test_inactivity_watcher_resets_on_activity():
    """A message from either user resets the inactivity timer."""
    from take_a_bridge.asgi import application

    alice = await create_user(username="inact_rst_a", password="x")
    bob = await create_user(username="inact_rst_b", password="x")
    conv = await _make_conversation(alice, bob)

    call_count = 0

    async def counting_suggest(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        return "討論方向建議"

    low_emotion = {"score": 0.05, "label": "neutral", "is_over_threshold": False}

    with patch("chat.consumers._INACTIVITY_SECONDS", 0.3), \
         patch("chat.consumers._INACTIVITY_CHECK_INTERVAL", 0.1), \
         patch("chat.consumers._INACTIVITY_GRACE_SECONDS", 0), \
         patch("chat.consumers.asuggest_direction", new=AsyncMock(side_effect=counting_suggest)), \
         patch("chat.consumers.aget_analyze_emotion", return_value=low_emotion), \
         patch("chat.consumers.acheck_topic_relevance", new=AsyncMock(return_value={"is_off_topic": False})):
        comm_a = WebsocketCommunicator(application, f"/ws/hh/{conv.id}/?user_id={alice.id}")
        comm_b = WebsocketCommunicator(application, f"/ws/hh/{conv.id}/?user_id={bob.id}")
        await comm_a.connect()
        await comm_b.connect()

        # Send a message at ~0.2s to reset the timer (before 0.3s threshold)
        await asyncio.sleep(0.2)
        await comm_a.send_json_to({"content": "核能政策的長期影響"})
        await comm_b.receive_json_from(timeout=3)  # relay to bob

        # Wait another 0.25s — NOT enough to re-trigger (0.25s < 0.3s from reset)
        await asyncio.sleep(0.25)

    assert call_count == 0, "Suggestion should not fire if activity reset the timer"

    await comm_a.disconnect()
    await comm_b.disconnect()


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
@override_settings(CHANNEL_LAYERS=TEST_CHANNEL_LAYERS)
async def test_inactivity_direction_llm_failure_uses_fallback():
    """When LLM fails, direction suggestion uses DIRECTION_FALLBACK_MESSAGES template."""
    from take_a_bridge.asgi import application
    from chat.consumers import DIRECTION_FALLBACK_MESSAGES

    alice = await create_user(username="inact_fb_a", password="x")
    bob = await create_user(username="inact_fb_b", password="x")
    conv = await _make_conversation(alice, bob)

    with patch("chat.consumers._INACTIVITY_SECONDS", 0.2), \
         patch("chat.consumers._INACTIVITY_CHECK_INTERVAL", 0.1), \
         patch("chat.consumers._INACTIVITY_GRACE_SECONDS", 0), \
         patch("chat.consumers.asuggest_direction", new=AsyncMock(side_effect=RuntimeError("LLM down"))):
        comm_a = WebsocketCommunicator(application, f"/ws/hh/{conv.id}/?user_id={alice.id}")
        comm_b = WebsocketCommunicator(application, f"/ws/hh/{conv.id}/?user_id={bob.id}")
        await comm_a.connect()
        await comm_b.connect()

        await asyncio.sleep(0.5)

        alice_msg = await comm_a.receive_json_from(timeout=3)

    assert alice_msg["type"] == "ai_suggestion"
    assert alice_msg["category"] == "direction"
    assert alice_msg["suggested_content"] in DIRECTION_FALLBACK_MESSAGES

    await comm_a.disconnect()
    await comm_b.disconnect()


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
@override_settings(CHANNEL_LAYERS=TEST_CHANNEL_LAYERS)
async def test_inactivity_direction_saves_aisuggestion_for_both_users():
    """Direction suggestion creates one AISuggestion record per user."""
    from take_a_bridge.asgi import application

    alice = await create_user(username="inact_db_a", password="x")
    bob = await create_user(username="inact_db_b", password="x")
    conv = await _make_conversation(alice, bob)

    direction_text = "可以從社會成本的角度討論"

    with patch("chat.consumers._INACTIVITY_SECONDS", 0.2), \
         patch("chat.consumers._INACTIVITY_CHECK_INTERVAL", 0.1), \
         patch("chat.consumers._INACTIVITY_GRACE_SECONDS", 0), \
         patch("chat.consumers.asuggest_direction", new=AsyncMock(return_value=direction_text)):
        comm_a = WebsocketCommunicator(application, f"/ws/hh/{conv.id}/?user_id={alice.id}")
        comm_b = WebsocketCommunicator(application, f"/ws/hh/{conv.id}/?user_id={bob.id}")
        await comm_a.connect()
        await comm_b.connect()

        await asyncio.sleep(0.5)

        await comm_a.receive_json_from(timeout=3)  # consume direction msg
        await comm_b.receive_json_from(timeout=3)

    records = await AISuggestion.objects.filter(conversation=conv).acount()
    assert records == 2, f"Expected 2 direction records (one per user), got {records}"

    alice_rec = await AISuggestion.objects.filter(conversation=conv, user=alice).afirst()
    bob_rec = await AISuggestion.objects.filter(conversation=conv, user=bob).afirst()
    assert alice_rec.category == AISuggestion.Category.DIRECTION
    assert alice_rec.original_content is None
    assert alice_rec.suggested_content == direction_text
    assert bob_rec.category == AISuggestion.Category.DIRECTION

    await comm_a.disconnect()
    await comm_b.disconnect()


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
@override_settings(CHANNEL_LAYERS=TEST_CHANNEL_LAYERS)
async def test_direction_accept_records_action_no_relay():
    """Accepting a direction suggestion records the action but does NOT relay content."""
    from take_a_bridge.asgi import application

    alice = await create_user(username="dir_acc_a", password="x")
    bob = await create_user(username="dir_acc_b", password="x")
    conv = await _make_conversation(alice, bob)

    with patch("chat.consumers._INACTIVITY_SECONDS", 0.2), \
         patch("chat.consumers._INACTIVITY_CHECK_INTERVAL", 0.1), \
         patch("chat.consumers._INACTIVITY_GRACE_SECONDS", 0), \
         patch("chat.consumers.asuggest_direction", new=AsyncMock(return_value="新討論角度")):
        comm_a = WebsocketCommunicator(application, f"/ws/hh/{conv.id}/?user_id={alice.id}")
        comm_b = WebsocketCommunicator(application, f"/ws/hh/{conv.id}/?user_id={bob.id}")
        await comm_a.connect()
        await comm_b.connect()

        await asyncio.sleep(0.5)

        await comm_a.receive_json_from(timeout=3)  # direction suggestion
        await comm_b.receive_json_from(timeout=3)

    # Alice responds with accept
    await comm_a.send_json_to({"type": "accept_suggestion"})

    # Bob must NOT receive relay (direction is acknowledgement only)
    assert await comm_b.receive_nothing(timeout=1)

    record = await AISuggestion.objects.filter(conversation=conv, user=alice).afirst()
    assert record.user_action == "accept"

    await comm_a.disconnect()
    await comm_b.disconnect()


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
@override_settings(CHANNEL_LAYERS=TEST_CHANNEL_LAYERS)
async def test_direction_ignore_records_action_no_relay():
    """Ignoring a direction suggestion records the action but does NOT relay content."""
    from take_a_bridge.asgi import application

    alice = await create_user(username="dir_ign_a", password="x")
    bob = await create_user(username="dir_ign_b", password="x")
    conv = await _make_conversation(alice, bob)

    with patch("chat.consumers._INACTIVITY_SECONDS", 0.2), \
         patch("chat.consumers._INACTIVITY_CHECK_INTERVAL", 0.1), \
         patch("chat.consumers._INACTIVITY_GRACE_SECONDS", 0), \
         patch("chat.consumers.asuggest_direction", new=AsyncMock(return_value="新方向")):
        comm_a = WebsocketCommunicator(application, f"/ws/hh/{conv.id}/?user_id={alice.id}")
        comm_b = WebsocketCommunicator(application, f"/ws/hh/{conv.id}/?user_id={bob.id}")
        await comm_a.connect()
        await comm_b.connect()

        await asyncio.sleep(0.5)

        await comm_a.receive_json_from(timeout=3)
        await comm_b.receive_json_from(timeout=3)

    await comm_a.send_json_to({"type": "ignore_suggestion"})

    assert await comm_b.receive_nothing(timeout=1)

    record = await AISuggestion.objects.filter(conversation=conv, user=alice).afirst()
    assert record.user_action == "ignore"

    await comm_a.disconnect()
    await comm_b.disconnect()


# ==================================================================
# Phase 10 — edge cases
# ==================================================================

# ------------------------------------------------------------------
# Inactivity watcher limits
# ------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
@override_settings(CHANNEL_LAYERS=TEST_CHANNEL_LAYERS)
async def test_inactivity_max_three_triggers():
    """Watcher fires at most _INACTIVITY_MAX_TRIGGERS times, then exits."""
    import chat.consumers as consumers_mod
    from take_a_bridge.asgi import application

    alice = await create_user(username="imax_a", password="x")
    bob = await create_user(username="imax_b", password="x")
    conv = await _make_conversation(alice, bob)

    call_count = 0

    async def counting_suggest(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        return "換個角度"

    with (
        patch("chat.consumers._INACTIVITY_SECONDS", 0.05),
        patch("chat.consumers._INACTIVITY_CHECK_INTERVAL", 0.03),
        patch("chat.consumers._INACTIVITY_GRACE_SECONDS", 0),
        patch("chat.consumers._INACTIVITY_MIN_INTERVAL_SECONDS", 0),
        patch("chat.consumers._INACTIVITY_MAX_TRIGGERS", 3),
        patch("chat.consumers.asuggest_direction", new=AsyncMock(side_effect=counting_suggest)),
    ):
        comm_a = WebsocketCommunicator(application, f"/ws/hh/{conv.id}/?user_id={alice.id}")
        comm_b = WebsocketCommunicator(application, f"/ws/hh/{conv.id}/?user_id={bob.id}")
        await comm_a.connect()
        await comm_b.connect()

        # First inactivity window → trigger 1
        await asyncio.sleep(0.12)
        # Simulate activity to reset the window
        consumers_mod._conversation_last_active[conv.id] = datetime.now(timezone.utc)
        await asyncio.sleep(0.12)
        # Second reset → trigger 2
        consumers_mod._conversation_last_active[conv.id] = datetime.now(timezone.utc)
        await asyncio.sleep(0.12)
        # Third reset → trigger 3; watcher then exits
        consumers_mod._conversation_last_active[conv.id] = datetime.now(timezone.utc)
        await asyncio.sleep(0.15)

    assert call_count == 3, f"Expected exactly 3 triggers (MAX=3), got {call_count}"

    while not await comm_a.receive_nothing(timeout=0.1):
        await comm_a.receive_json_from(timeout=0.2)
    while not await comm_b.receive_nothing(timeout=0.1):
        await comm_b.receive_json_from(timeout=0.2)

    await comm_a.disconnect()
    await comm_b.disconnect()


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
@override_settings(CHANNEL_LAYERS=TEST_CHANNEL_LAYERS)
async def test_inactivity_grace_period_suppresses_trigger():
    """No direction suggestion fires within the grace period."""
    from take_a_bridge.asgi import application

    alice = await create_user(username="igrace_a", password="x")
    bob = await create_user(username="igrace_b", password="x")
    conv = await _make_conversation(alice, bob)

    call_count = 0

    async def counting_suggest(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        return "提示"

    with (
        patch("chat.consumers._INACTIVITY_SECONDS", 0.05),
        patch("chat.consumers._INACTIVITY_CHECK_INTERVAL", 0.03),
        patch("chat.consumers._INACTIVITY_GRACE_SECONDS", 10000),  # effectively infinite
        patch("chat.consumers.asuggest_direction", new=AsyncMock(side_effect=counting_suggest)),
    ):
        comm_a = WebsocketCommunicator(application, f"/ws/hh/{conv.id}/?user_id={alice.id}")
        comm_b = WebsocketCommunicator(application, f"/ws/hh/{conv.id}/?user_id={bob.id}")
        await comm_a.connect()
        await comm_b.connect()

        await asyncio.sleep(0.3)

    assert call_count == 0, "No trigger should fire during grace period"

    await comm_a.disconnect()
    await comm_b.disconnect()


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
@override_settings(CHANNEL_LAYERS=TEST_CHANNEL_LAYERS)
async def test_inactivity_min_interval_prevents_rapid_refiring():
    """Second trigger is suppressed until _INACTIVITY_MIN_INTERVAL_SECONDS elapses."""
    import chat.consumers as consumers_mod
    from take_a_bridge.asgi import application

    alice = await create_user(username="iminint_a", password="x")
    bob = await create_user(username="iminint_b", password="x")
    conv = await _make_conversation(alice, bob)

    call_count = 0

    async def counting_suggest(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        return "換個角度"

    with (
        patch("chat.consumers._INACTIVITY_SECONDS", 0.05),
        patch("chat.consumers._INACTIVITY_CHECK_INTERVAL", 0.03),
        patch("chat.consumers._INACTIVITY_GRACE_SECONDS", 0),
        patch("chat.consumers._INACTIVITY_MIN_INTERVAL_SECONDS", 10),  # 10s min gap
        patch("chat.consumers.asuggest_direction", new=AsyncMock(side_effect=counting_suggest)),
    ):
        comm_a = WebsocketCommunicator(application, f"/ws/hh/{conv.id}/?user_id={alice.id}")
        comm_b = WebsocketCommunicator(application, f"/ws/hh/{conv.id}/?user_id={bob.id}")
        await comm_a.connect()
        await comm_b.connect()

        # Wait for trigger 1
        await asyncio.sleep(0.15)
        # Simulate activity to reset inactivity window
        consumers_mod._conversation_last_active[conv.id] = datetime.now(timezone.utc)
        # Wait — 2nd trigger blocked by min interval (10s >> 0.2s)
        await asyncio.sleep(0.2)

    assert call_count == 1, f"Min interval should block 2nd trigger, got {call_count}"

    while not await comm_a.receive_nothing(timeout=0.1):
        await comm_a.receive_json_from(timeout=0.2)
    while not await comm_b.receive_nothing(timeout=0.1):
        await comm_b.receive_json_from(timeout=0.2)

    await comm_a.disconnect()
    await comm_b.disconnect()


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
@override_settings(CHANNEL_LAYERS=TEST_CHANNEL_LAYERS)
async def test_inactivity_pauses_on_disconnect():
    """Watcher exits when any user disconnects; no further triggers fire."""
    import chat.consumers as consumers_mod
    from take_a_bridge.asgi import application

    alice = await create_user(username="idisc_a", password="x")
    bob = await create_user(username="idisc_b", password="x")
    conv = await _make_conversation(alice, bob)

    call_count = 0

    async def counting_suggest(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        return "提示"

    with (
        patch("chat.consumers._INACTIVITY_SECONDS", 0.05),
        patch("chat.consumers._INACTIVITY_CHECK_INTERVAL", 0.03),
        patch("chat.consumers._INACTIVITY_GRACE_SECONDS", 0),
        patch("chat.consumers._INACTIVITY_MIN_INTERVAL_SECONDS", 0),
        patch("chat.consumers.asuggest_direction", new=AsyncMock(side_effect=counting_suggest)),
    ):
        comm_a = WebsocketCommunicator(application, f"/ws/hh/{conv.id}/?user_id={alice.id}")
        comm_b = WebsocketCommunicator(application, f"/ws/hh/{conv.id}/?user_id={bob.id}")
        await comm_a.connect()
        await comm_b.connect()

        # Wait for trigger 1
        await asyncio.sleep(0.15)

        # Bob disconnects → adds conv_id to _conversation_disconnected
        await comm_b.disconnect()

        # Simulate activity to allow a potential 2nd trigger window
        consumers_mod._conversation_last_active[conv.id] = datetime.now(timezone.utc)

        # Wait — watcher should have exited; no 2nd trigger
        await asyncio.sleep(0.2)

    assert call_count == 1, f"Watcher exits on disconnect; expected 1 trigger, got {call_count}"

    while not await comm_a.receive_nothing(timeout=0.1):
        await comm_a.receive_json_from(timeout=0.2)

    await comm_a.disconnect()


# ------------------------------------------------------------------
# Rephrase LLM failure → modify/ignore only
# ------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
@override_settings(CHANNEL_LAYERS=TEST_CHANNEL_LAYERS)
async def test_rephrase_llm_failure_gives_modify_ignore_actions():
    """When LLM fails to rephrase, fallback template is used with only modify/ignore actions."""
    from take_a_bridge.asgi import application
    from chat.consumers import _REPHRASE_FALLBACK_TEMPLATE

    alice = await create_user(username="rpfb_a", password="x")
    bob = await create_user(username="rpfb_b", password="x")
    conv = await _make_conversation(alice, bob)

    comm_a = WebsocketCommunicator(application, f"/ws/hh/{conv.id}/?user_id={alice.id}")
    await comm_a.connect()

    high_emotion = {"score": 0.95, "label": "negative", "is_over_threshold": True}

    with patch("chat.consumers.check_content_sync", return_value={"is_blocked": False}), \
         patch("chat.consumers.aget_analyze_emotion", return_value=high_emotion), \
         patch("chat.consumers.arephrase_message", new=AsyncMock(side_effect=RuntimeError("LLM down"))):
        await comm_a.send_json_to({"content": "這讓我非常憤怒！"})
        alice_msg = await comm_a.receive_json_from(timeout=5)

    assert alice_msg["type"] == "ai_suggestion"
    assert alice_msg["category"] == "rephrase"
    assert alice_msg["suggested_content"] == _REPHRASE_FALLBACK_TEMPLATE
    assert alice_msg["actions"] == ["modify", "ignore"]

    await comm_a.disconnect()


# ------------------------------------------------------------------
# Modify second emotion check
# ------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
@override_settings(CHANNEL_LAYERS=TEST_CHANNEL_LAYERS)
async def test_modify_second_emotion_check_re_intercepts():
    """Modify response re-runs emotion check; still high emotion → second ai_suggestion."""
    from take_a_bridge.asgi import application

    alice = await create_user(username="mod2_a", password="x")
    bob = await create_user(username="mod2_b", password="x")
    conv = await _make_conversation(alice, bob)

    comm_a = WebsocketCommunicator(application, f"/ws/hh/{conv.id}/?user_id={alice.id}")
    comm_b = WebsocketCommunicator(application, f"/ws/hh/{conv.id}/?user_id={bob.id}")
    await comm_a.connect()
    await comm_b.connect()

    high_emotion = {"score": 0.95, "label": "negative", "is_over_threshold": True}
    calm = "請用更理性的方式表達。"

    # First intercept (retry_count → 1)
    with patch("chat.consumers.aget_analyze_emotion", return_value=high_emotion), \
         patch("chat.consumers.arephrase_message", new=AsyncMock(return_value=calm)):
        await comm_a.send_json_to({"content": "你真的很討厭！"})
        first_msg = await comm_a.receive_json_from(timeout=5)

    assert first_msg["type"] == "ai_suggestion"

    # Alice edits — still high emotion → second intercept (retry_count → 2)
    with patch("chat.consumers.aget_analyze_emotion", return_value=high_emotion), \
         patch("chat.consumers.arephrase_message", new=AsyncMock(return_value=calm)):
        await comm_a.send_json_to({"type": "modify_suggestion", "content": "我真的很不滿！"})
        second_msg = await comm_a.receive_json_from(timeout=5)

    assert second_msg["type"] == "ai_suggestion"
    assert second_msg["category"] == "rephrase"
    assert await comm_b.receive_nothing(timeout=1)

    await comm_a.disconnect()
    await comm_b.disconnect()


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
@override_settings(CHANNEL_LAYERS=TEST_CHANNEL_LAYERS)
async def test_modify_force_relay_at_max_intercepts():
    """After _REPHRASE_MAX_INTERCEPTS, modify is force-relayed to Bob regardless of emotion."""
    from take_a_bridge.asgi import application

    alice = await create_user(username="force_a", password="x")
    bob = await create_user(username="force_b", password="x")
    conv = await _make_conversation(alice, bob)

    comm_a = WebsocketCommunicator(application, f"/ws/hh/{conv.id}/?user_id={alice.id}")
    comm_b = WebsocketCommunicator(application, f"/ws/hh/{conv.id}/?user_id={bob.id}")
    await comm_a.connect()
    await comm_b.connect()

    high_emotion = {"score": 0.95, "label": "negative", "is_over_threshold": True}
    calm = "請用更理性的方式表達。"

    # Patch MAX to 1: first intercept exhausts the quota
    with patch("chat.consumers._REPHRASE_MAX_INTERCEPTS", 1):
        with patch("chat.consumers.aget_analyze_emotion", return_value=high_emotion), \
             patch("chat.consumers.arephrase_message", new=AsyncMock(return_value=calm)):
            await comm_a.send_json_to({"content": "你真的很讓人火大！"})
            msg1 = await comm_a.receive_json_from(timeout=5)
        assert msg1["type"] == "ai_suggestion"

        # Modify: retry_count=1 >= MAX=1 → force relay
        final = "我對你的說法有些意見。"
        with patch("chat.consumers.aget_analyze_emotion", return_value=high_emotion):
            await comm_a.send_json_to({"type": "modify_suggestion", "content": final})
            bob_msg = await comm_b.receive_json_from(timeout=5)

    assert bob_msg["content"] == final
    assert bob_msg["sender_id"] == alice.id

    await comm_a.disconnect()
    await comm_b.disconnect()


# ------------------------------------------------------------------
# AISuggestion research fields
# ------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
@override_settings(CHANNEL_LAYERS=TEST_CHANNEL_LAYERS)
async def test_aisuggestion_research_fields_written():
    """AISuggestion has trigger_score, response_time_ms, final_content, context_message_ids."""
    import pytest as _pytest
    from take_a_bridge.asgi import application

    alice = await create_user(username="fields_a", password="x")
    bob = await create_user(username="fields_b", password="x")
    conv = await _make_conversation(alice, bob)

    comm_a = WebsocketCommunicator(application, f"/ws/hh/{conv.id}/?user_id={alice.id}")
    await comm_a.connect()

    high_emotion = {"score": 0.87, "label": "negative", "is_over_threshold": True}
    calm = "我覺得我們可以更理性地討論。"

    with patch("chat.consumers.aget_analyze_emotion", return_value=high_emotion), \
         patch("chat.consumers.arephrase_message", new=AsyncMock(return_value=calm)):
        await comm_a.send_json_to({"content": "你說的話真的太沒道理了！"})
        await comm_a.receive_json_from(timeout=5)  # ai_suggestion

    # Alice ignores → final_content = original_content
    await comm_a.send_json_to({"type": "ignore_suggestion"})
    await asyncio.sleep(0.3)

    record = await AISuggestion.objects.filter(conversation=conv, user=alice).afirst()
    assert record is not None
    assert record.trigger_score == _pytest.approx(0.87, abs=0.01)
    assert record.response_time_ms is not None
    assert record.response_time_ms >= 0
    assert record.final_content == "你說的話真的太沒道理了！"
    assert isinstance(record.context_message_ids, list)

    await comm_a.disconnect()
