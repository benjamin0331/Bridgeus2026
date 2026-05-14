"""
Integration tests for HumanHumanConsumer — NLP pipeline integration.

These tests exercise the full flow: content filter intercept, relay, DB persist,
and background NLP analysis (embedding + emotion detection).

WARNING: test_nlp_analysis_saved_to_db and test_high_emotion_message_relayed_with_warning
load real ML models on first run (~30-60s). Subsequent runs within the same pytest session
are faster because singleton models stay loaded.
"""

import asyncio

import pytest
from asgiref.sync import sync_to_async
from channels.testing import WebsocketCommunicator
from django.contrib.auth import get_user_model
from django.test import override_settings

from chat.models import Conversation

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
    from BridgeUs_Django.asgi import application

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
    from BridgeUs_Django.asgi import application
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
    from BridgeUs_Django.asgi import application

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


