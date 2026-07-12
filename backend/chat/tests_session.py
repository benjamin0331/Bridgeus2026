"""
Tests for chat.services.session (end_session / generate_summary)
and the consumer's session-end paths (explicit message, broadcast).
"""

import asyncio
from datetime import timedelta
from unittest.mock import MagicMock, patch

import pytest
from asgiref.sync import sync_to_async
from channels.testing import WebsocketCommunicator
from django.contrib.auth import get_user_model
from django.utils import timezone

from chat.models import Conversation, Message, StanceDrift
from chat.services.session import aend_session, agenerate_summary, end_session, generate_summary

User = get_user_model()

TEST_CHANNEL_LAYERS = {
    "default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}
}

create_user = sync_to_async(User.objects.create_user, thread_sensitive=True)


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _user(name):
    return User.objects.create_user(username=name, password="x")


def _conv(user_a, user_b, *, started_at=None):
    return Conversation.objects.create(
        topic_id=1,
        user_a=user_a,
        user_b=user_b,
        session_number=1,
        status=Conversation.Status.ACTIVE,
        started_at=started_at or timezone.now() - timedelta(minutes=10),
    )


def _msg(conv, sender, content="hello", emotion_score=None):
    return Message.objects.create(
        conversation=conv,
        sender=sender,
        content=content,
        emotion_score=emotion_score,
    )


# ------------------------------------------------------------------
# end_session — stats correctness
# ------------------------------------------------------------------

@pytest.mark.django_db
def test_end_session_marks_completed():
    alice = _user("es_comp_a")
    bob = _user("es_comp_b")
    conv = _conv(alice, bob)

    end_session(conv.id)

    conv.refresh_from_db()
    assert conv.status == Conversation.Status.COMPLETED


@pytest.mark.django_db
def test_end_session_sets_ended_at():
    alice = _user("es_ended_a")
    bob = _user("es_ended_b")
    conv = _conv(alice, bob)

    end_session(conv.id)

    conv.refresh_from_db()
    assert conv.ended_at is not None


@pytest.mark.django_db
def test_end_session_duration_minutes():
    alice = _user("es_dur_a")
    bob = _user("es_dur_b")
    started = timezone.now() - timedelta(minutes=30)
    conv = _conv(alice, bob, started_at=started)

    stats = end_session(conv.id)

    # 30 min ± 1 min tolerance
    assert 29 <= stats["duration_minutes"] <= 31


@pytest.mark.django_db
def test_end_session_duration_zero_when_no_started_at():
    alice = _user("es_nodur_a")
    bob = _user("es_nodur_b")
    conv = Conversation.objects.create(
        topic_id=1, user_a=alice, user_b=bob,
        session_number=1, status=Conversation.Status.ACTIVE,
    )

    stats = end_session(conv.id)

    assert stats["duration_minutes"] == 0.0


@pytest.mark.django_db
def test_end_session_total_messages():
    alice = _user("es_total_a")
    bob = _user("es_total_b")
    conv = _conv(alice, bob)
    _msg(conv, alice)
    _msg(conv, alice)
    _msg(conv, bob)

    stats = end_session(conv.id)

    assert stats["total_messages"] == 3


@pytest.mark.django_db
def test_end_session_messages_per_user():
    alice = _user("es_mpu_a")
    bob = _user("es_mpu_b")
    conv = _conv(alice, bob)
    _msg(conv, alice)
    _msg(conv, alice)
    _msg(conv, bob)

    stats = end_session(conv.id)

    assert stats["messages_per_user"][alice.id] == 2
    assert stats["messages_per_user"][bob.id] == 1


@pytest.mark.django_db
def test_end_session_avg_emotion_score():
    alice = _user("es_emo_a")
    bob = _user("es_emo_b")
    conv = _conv(alice, bob)
    _msg(conv, alice, emotion_score=0.4)
    _msg(conv, alice, emotion_score=0.6)
    _msg(conv, bob, emotion_score=0.9)

    stats = end_session(conv.id)

    assert stats["avg_emotion_score_per_user"][alice.id] == pytest.approx(0.5, abs=0.001)
    assert stats["avg_emotion_score_per_user"][bob.id] == pytest.approx(0.9, abs=0.001)


@pytest.mark.django_db
def test_end_session_avg_emotion_none_when_no_scores():
    alice = _user("es_emonone_a")
    bob = _user("es_emonone_b")
    conv = _conv(alice, bob)
    _msg(conv, alice)  # no emotion_score

    stats = end_session(conv.id)

    assert stats["avg_emotion_score_per_user"][alice.id] is None


@pytest.mark.django_db
def test_end_session_blocked_and_prompts_passthrough():
    alice = _user("es_blk_a")
    bob = _user("es_blk_b")
    conv = _conv(alice, bob)

    stats = end_session(conv.id, blocked_count=3, system_prompts_triggered=5)

    assert stats["blocked_count"] == 3
    assert stats["system_prompts_triggered"] == 5


@pytest.mark.django_db
def test_end_session_stance_drift_final():
    alice = _user("es_drift_a")
    bob = _user("es_drift_b")
    conv = _conv(alice, bob)
    StanceDrift.objects.create(conversation=conv, user=alice, drift_value=0.25)
    StanceDrift.objects.create(conversation=conv, user=alice, drift_value=0.40)
    StanceDrift.objects.create(conversation=conv, user=bob, drift_value=0.10)

    stats = end_session(conv.id)

    # Latest drift value per user
    assert stats["stance_drift_final"][alice.id] == pytest.approx(0.4, abs=0.0001)
    assert stats["stance_drift_final"][bob.id] == pytest.approx(0.1, abs=0.0001)


@pytest.mark.django_db
def test_end_session_stance_drift_none_when_no_records():
    alice = _user("es_driftnone_a")
    bob = _user("es_driftnone_b")
    conv = _conv(alice, bob)

    stats = end_session(conv.id)

    assert stats["stance_drift_final"][alice.id] is None
    assert stats["stance_drift_final"][bob.id] is None


@pytest.mark.django_db
def test_end_session_saves_stats_to_db():
    alice = _user("es_save_a")
    bob = _user("es_save_b")
    conv = _conv(alice, bob)
    _msg(conv, alice)

    end_session(conv.id)

    conv.refresh_from_db()
    assert conv.stats is not None
    assert conv.stats["total_messages"] == 1


@pytest.mark.django_db
def test_end_session_empty_conversation():
    alice = _user("es_empty_a")
    bob = _user("es_empty_b")
    conv = _conv(alice, bob)

    stats = end_session(conv.id)

    assert stats["total_messages"] == 0
    assert stats["messages_per_user"][alice.id] == 0
    assert stats["messages_per_user"][bob.id] == 0


# ------------------------------------------------------------------
# generate_summary
# ------------------------------------------------------------------

@pytest.mark.django_db
def test_generate_summary_no_api_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    alice = _user("gs_nokey_a")
    bob = _user("gs_nokey_b")
    conv = _conv(alice, bob)
    _msg(conv, alice, "I support nuclear power.")
    _msg(conv, bob, "I am against it.")

    result = generate_summary(conv.id)

    assert result == ""
    conv.refresh_from_db()
    assert conv.summary == ""


@pytest.mark.django_db
def test_generate_summary_no_messages(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    alice = _user("gs_nomsg_a")
    bob = _user("gs_nomsg_b")
    conv = _conv(alice, bob)

    result = generate_summary(conv.id)

    assert result == ""


@pytest.mark.django_db
def test_generate_summary_with_mock_llm(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    mock_content = MagicMock()
    mock_content.text = "雙方討論了核能議題，立場有所靠近。"

    mock_response = MagicMock()
    mock_response.content = [mock_content]

    mock_client = MagicMock()
    mock_client.messages.create.return_value = mock_response

    alice = _user("gs_mock_a")
    bob = _user("gs_mock_b")
    conv = _conv(alice, bob)
    _msg(conv, alice, "Support nuclear energy.")
    _msg(conv, bob, "I disagree.")

    with patch("anthropic.Anthropic", return_value=mock_client):
        result = generate_summary(conv.id)

    assert result == "雙方討論了核能議題，立場有所靠近。"
    conv.refresh_from_db()
    assert conv.summary == "雙方討論了核能議題，立場有所靠近。"


@pytest.mark.django_db
def test_generate_summary_api_error_returns_empty(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    mock_client = MagicMock()
    mock_client.messages.create.side_effect = RuntimeError("API down")

    alice = _user("gs_err_a")
    bob = _user("gs_err_b")
    conv = _conv(alice, bob)
    _msg(conv, alice, "some content")

    with patch("anthropic.Anthropic", return_value=mock_client):
        result = generate_summary(conv.id)

    assert result == ""


# ------------------------------------------------------------------
# Consumer: explicit end_session message
# ------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("dummy", [None])  # avoids name collision
async def test_explicit_end_session_message(dummy, settings):
    settings.CHANNEL_LAYERS = TEST_CHANNEL_LAYERS

    from take_a_bridge.asgi import application

    alice = await create_user(username="end_explicit_a", password="x")
    bob = await create_user(username="end_explicit_b", password="x")

    started = timezone.now() - timedelta(minutes=5)
    conv = await Conversation.objects.acreate(
        topic_id=1, user_a=alice, user_b=bob,
        session_number=1, status=Conversation.Status.ACTIVE,
        started_at=started,
    )

    comm = WebsocketCommunicator(application, f"/ws/hh/{conv.id}/?user_id={alice.id}")
    connected, _ = await comm.connect()
    assert connected

    await comm.send_json_to({"type": "end_session"})

    # Should receive a session_ended broadcast
    found = False
    for _ in range(20):
        try:
            msg = await comm.receive_json_from(timeout=1)
            if msg.get("type") == "session_ended":
                found = True
                assert msg["reason"] == "explicit"
                assert "stats" in msg
                break
        except Exception:
            break

    assert found, "session_ended message not received after explicit end_session"

    conv_db = await Conversation.objects.aget(id=conv.id)
    assert conv_db.status == Conversation.Status.COMPLETED

    await comm.disconnect()


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_session_ended_stats_contains_required_keys(settings):
    settings.CHANNEL_LAYERS = TEST_CHANNEL_LAYERS

    from take_a_bridge.asgi import application

    alice = await create_user(username="end_keys_a", password="x")
    bob = await create_user(username="end_keys_b", password="x")

    started = timezone.now() - timedelta(minutes=2)
    conv = await Conversation.objects.acreate(
        topic_id=1, user_a=alice, user_b=bob,
        session_number=1, status=Conversation.Status.ACTIVE,
        started_at=started,
    )
    await Message.objects.acreate(
        conversation=conv, sender=alice, content="Hello", emotion_score=0.3
    )

    comm = WebsocketCommunicator(application, f"/ws/hh/{conv.id}/?user_id={alice.id}")
    await comm.connect()
    await comm.send_json_to({"type": "end_session"})

    stats = None
    for _ in range(20):
        try:
            msg = await comm.receive_json_from(timeout=1)
            if msg.get("type") == "session_ended":
                stats = msg["stats"]
                break
        except Exception:
            break

    assert stats is not None
    required_keys = {
        "conversation_id", "session_number", "duration_minutes",
        "total_messages", "messages_per_user", "avg_emotion_score_per_user",
        "blocked_count", "system_prompts_triggered", "stance_drift_final",
    }
    assert required_keys.issubset(stats.keys())
    assert stats["total_messages"] == 1

    await comm.disconnect()
