"""
Tests for chat.services.ai_assist and the consumer's emotion-overflow flow.

Coverage:
  - rephrase_message: mock LLM, no API key fallback, API error fallback,
    rephrased output is calmer (emotion score drops)
  - suggest_direction / redirect_to_topic: mock LLM, fallback
  - Consumer: emotion overflow → ai_suggestion sent, not relayed
  - Consumer: accept / modify / ignore → correct relay + DB record
  - Consumer: normal message unaffected
"""

from unittest.mock import MagicMock, patch

import pytest
from asgiref.sync import sync_to_async
from channels.testing import WebsocketCommunicator
from django.contrib.auth import get_user_model
from django.utils import timezone

from chat.models import AISuggestion, Conversation, Message
from chat.services.ai_assist import (
    redirect_to_topic,
    rephrase_message,
    suggest_direction,
)

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


def _conv(user_a, user_b):
    return Conversation.objects.create(
        topic_id=1, user_a=user_a, user_b=user_b,
        session_number=1, status=Conversation.Status.ACTIVE,
        started_at=timezone.now(),
    )


def _mock_claude(text: str):
    mock_content = MagicMock()
    mock_content.text = text
    mock_response = MagicMock()
    mock_response.content = [mock_content]
    mock_client = MagicMock()
    mock_client.messages.create.return_value = mock_response
    return mock_client


# ------------------------------------------------------------------
# rephrase_message
# ------------------------------------------------------------------

@pytest.mark.django_db
def test_rephrase_with_mock_llm(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    calm = "我認為您的論點有些不足，讓我們理性地討論一下。"
    with patch("anthropic.Anthropic", return_value=_mock_claude(calm)):
        result = rephrase_message("你根本是個蠢貨！", "核能議題")
    assert result == calm


@pytest.mark.django_db
def test_rephrase_no_api_key_raises_runtime_error(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(RuntimeError):
        rephrase_message("你根本是個廢物！", "核能議題")


@pytest.mark.django_db
def test_rephrase_api_error_raises_runtime_error(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    mock_client = MagicMock()
    mock_client.messages.create.side_effect = RuntimeError("API down")
    with patch("anthropic.Anthropic", return_value=mock_client):
        with pytest.raises(RuntimeError):
            rephrase_message("你這個人完全沒道理！", "核能議題")


@pytest.mark.django_db
def test_rephrase_output_is_calmer(monkeypatch):
    """
    Mock LLM returns a known calm sentence.
    Verify the calm version scores lower on the emotion model than the angry original.
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    angry = "你根本是個蠢貨，說的話完全沒有道理，讓人火大！"
    calm = "我認為您的觀點有些不夠充分，讓我提出幾個反駁的理由。"

    with patch("anthropic.Anthropic", return_value=_mock_claude(calm)):
        result = rephrase_message(angry, "核能議題")

    assert result == calm

    from chat.services.emotion import analyze_emotion
    angry_score = analyze_emotion(angry)["score"]
    calm_score = analyze_emotion(result)["score"]
    print(f"\n  angry_score={angry_score:.4f}, calm_score={calm_score:.4f}")
    assert calm_score < angry_score, (
        f"Rephrased text should be calmer: angry={angry_score:.4f}, calm={calm_score:.4f}"
    )


# ------------------------------------------------------------------
# suggest_direction
# ------------------------------------------------------------------

@pytest.mark.django_db
def test_suggest_direction_with_mock_llm(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    alice = _user("ai_dir_a")
    bob = _user("ai_dir_b")
    conv = _conv(alice, bob)
    Message.objects.create(conversation=conv, sender=alice, content="核能議題很重要")

    suggested = "換個角度：核能對台灣能源安全的長期影響是什麼？"
    with patch("anthropic.Anthropic", return_value=_mock_claude(suggested)):
        result = suggest_direction(conv.id, "核能議題")
    assert result == suggested


@pytest.mark.django_db
def test_suggest_direction_no_api_key_fallback(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    alice = _user("ai_dirfb_a")
    bob = _user("ai_dirfb_b")
    conv = _conv(alice, bob)

    result = suggest_direction(conv.id, "核能議題")
    assert isinstance(result, str) and len(result) > 0


# ------------------------------------------------------------------
# redirect_to_topic
# ------------------------------------------------------------------

@pytest.mark.django_db
def test_redirect_to_topic_with_mock_llm(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    alice = _user("ai_redir_a")
    bob = _user("ai_redir_b")
    conv = _conv(alice, bob)

    hint = "我們來把焦點拉回核能安全的討論吧。"
    with patch("anthropic.Anthropic", return_value=_mock_claude(hint)):
        result = redirect_to_topic(conv.id, "核能議題")
    assert result == hint


@pytest.mark.django_db
def test_redirect_to_topic_no_api_key_fallback(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    alice = _user("ai_redirfb_a")
    bob = _user("ai_redirfb_b")
    conv = _conv(alice, bob)

    result = redirect_to_topic(conv.id, "核能議題")
    assert isinstance(result, str) and len(result) > 0


# ------------------------------------------------------------------
# Consumer: emotion overflow flow
# ------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_emotion_overflow_not_relayed_to_bob(settings, monkeypatch):
    """High-emotion message should NOT be relayed; Bob receives nothing."""
    settings.CHANNEL_LAYERS = TEST_CHANNEL_LAYERS
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    from take_a_bridge.asgi import application

    alice = await create_user(username="emo_relay_a", password="x")
    bob = await create_user(username="emo_relay_b", password="x")
    conv = await Conversation.objects.acreate(
        topic_id=1, user_a=alice, user_b=bob,
        session_number=1, status=Conversation.Status.ACTIVE,
        started_at=timezone.now(),
    )

    comm_a = WebsocketCommunicator(application, f"/ws/hh/{conv.id}/?user_id={alice.id}")
    comm_b = WebsocketCommunicator(application, f"/ws/hh/{conv.id}/?user_id={bob.id}")
    await comm_a.connect()
    await comm_b.connect()

    # Patch emotion to always return over-threshold
    high_emotion = {"score": 0.95, "label": "negative", "is_over_threshold": True}
    with patch("chat.consumers.aget_analyze_emotion", return_value=high_emotion):
        await comm_a.send_json_to({"content": "你這個人真的很討厭！"})
        alice_msg = await comm_a.receive_json_from(timeout=5)

    # Alice gets ai_suggestion; no API key → LLM unavailable → only modify/ignore
    assert alice_msg["type"] == "ai_suggestion"
    assert alice_msg["category"] == "rephrase"
    assert "suggested_content" in alice_msg
    assert "modify" in alice_msg["actions"]
    assert "ignore" in alice_msg["actions"]

    # Bob gets nothing (no relay)
    assert await comm_b.receive_nothing(timeout=1)

    await comm_a.disconnect()
    await comm_b.disconnect()


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_emotion_overflow_creates_aisuggestion_record(settings, monkeypatch):
    settings.CHANNEL_LAYERS = TEST_CHANNEL_LAYERS
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    from take_a_bridge.asgi import application

    alice = await create_user(username="emo_db_a", password="x")
    bob = await create_user(username="emo_db_b", password="x")
    conv = await Conversation.objects.acreate(
        topic_id=1, user_a=alice, user_b=bob,
        session_number=1, status=Conversation.Status.ACTIVE,
        started_at=timezone.now(),
    )

    comm_a = WebsocketCommunicator(application, f"/ws/hh/{conv.id}/?user_id={alice.id}")
    await comm_a.connect()

    high_emotion = {"score": 0.95, "label": "negative", "is_over_threshold": True}
    with patch("chat.consumers.aget_analyze_emotion", return_value=high_emotion):
        await comm_a.send_json_to({"content": "你這個人真的很讓人火大！"})
        await comm_a.receive_json_from(timeout=5)

    count = await AISuggestion.objects.filter(conversation=conv, user=alice).acount()
    assert count == 1

    record = await AISuggestion.objects.filter(conversation=conv, user=alice).afirst()
    assert record.category == AISuggestion.Category.REPHRASE
    assert record.user_action is None  # not yet responded

    await comm_a.disconnect()


# ------------------------------------------------------------------
# Consumer: suggestion response — accept / modify / ignore
# ------------------------------------------------------------------

async def _setup_pending_suggestion(application, conv, alice, bob, monkeypatch):
    """Helper: connect, send high-emotion message, receive ai_suggestion."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    comm_a = WebsocketCommunicator(application, f"/ws/hh/{conv.id}/?user_id={alice.id}")
    comm_b = WebsocketCommunicator(application, f"/ws/hh/{conv.id}/?user_id={bob.id}")
    await comm_a.connect()
    await comm_b.connect()

    high_emotion = {"score": 0.95, "label": "negative", "is_over_threshold": True}
    with patch("chat.consumers.aget_analyze_emotion", return_value=high_emotion):
        await comm_a.send_json_to({"content": "你這個人真的很讓人火大！"})
        suggestion_msg = await comm_a.receive_json_from(timeout=5)

    return comm_a, comm_b, suggestion_msg


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_accept_suggestion_relays_ai_content(settings, monkeypatch):
    settings.CHANNEL_LAYERS = TEST_CHANNEL_LAYERS

    from take_a_bridge.asgi import application

    alice = await create_user(username="acc_a", password="x")
    bob = await create_user(username="acc_b", password="x")
    conv = await Conversation.objects.acreate(
        topic_id=1, user_a=alice, user_b=bob,
        session_number=1, status=Conversation.Status.ACTIVE,
        started_at=timezone.now(),
    )

    comm_a, comm_b, suggestion_msg = await _setup_pending_suggestion(
        application, conv, alice, bob, monkeypatch
    )
    ai_content = suggestion_msg["suggested_content"]

    await comm_a.send_json_to({"type": "accept_suggestion", "content": ai_content})

    # Bob receives relay with AI content
    bob_msg = await comm_b.receive_json_from(timeout=5)
    assert bob_msg["content"] == ai_content
    assert bob_msg["sender_id"] == alice.id

    # DB record updated
    record = await AISuggestion.objects.filter(conversation=conv, user=alice).afirst()
    assert record.user_action == "accept"

    await comm_a.disconnect()
    await comm_b.disconnect()


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_modify_suggestion_relays_modified_content(settings, monkeypatch):
    settings.CHANNEL_LAYERS = TEST_CHANNEL_LAYERS

    from take_a_bridge.asgi import application

    alice = await create_user(username="mod_a", password="x")
    bob = await create_user(username="mod_b", password="x")
    conv = await Conversation.objects.acreate(
        topic_id=1, user_a=alice, user_b=bob,
        session_number=1, status=Conversation.Status.ACTIVE,
        started_at=timezone.now(),
    )

    comm_a, comm_b, _ = await _setup_pending_suggestion(
        application, conv, alice, bob, monkeypatch
    )
    user_edited = "我覺得你說的有些地方值得商榷，讓我解釋一下。"

    await comm_a.send_json_to({"type": "modify_suggestion", "content": user_edited})

    bob_msg = await comm_b.receive_json_from(timeout=5)
    assert bob_msg["content"] == user_edited

    record = await AISuggestion.objects.filter(conversation=conv, user=alice).afirst()
    assert record.user_action == "modify"
    assert record.modified_content == user_edited

    await comm_a.disconnect()
    await comm_b.disconnect()


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_ignore_suggestion_relays_original_content(settings, monkeypatch):
    settings.CHANNEL_LAYERS = TEST_CHANNEL_LAYERS

    from take_a_bridge.asgi import application

    alice = await create_user(username="ign_a", password="x")
    bob = await create_user(username="ign_b", password="x")
    conv = await Conversation.objects.acreate(
        topic_id=1, user_a=alice, user_b=bob,
        session_number=1, status=Conversation.Status.ACTIVE,
        started_at=timezone.now(),
    )

    comm_a, comm_b, suggestion_msg = await _setup_pending_suggestion(
        application, conv, alice, bob, monkeypatch
    )
    original = suggestion_msg["original_content"]

    await comm_a.send_json_to({"type": "ignore_suggestion"})

    bob_msg = await comm_b.receive_json_from(timeout=5)
    assert bob_msg["content"] == original

    record = await AISuggestion.objects.filter(conversation=conv, user=alice).afirst()
    assert record.user_action == "ignore"

    await comm_a.disconnect()
    await comm_b.disconnect()


# ------------------------------------------------------------------
# Consumer: normal message unaffected
# ------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_normal_message_relayed_without_suggestion(settings):
    """Low-emotion message should relay directly without ai_suggestion."""
    settings.CHANNEL_LAYERS = TEST_CHANNEL_LAYERS

    from take_a_bridge.asgi import application

    alice = await create_user(username="norm_a", password="x")
    bob = await create_user(username="norm_b", password="x")
    conv = await Conversation.objects.acreate(
        topic_id=1, user_a=alice, user_b=bob,
        session_number=1, status=Conversation.Status.ACTIVE,
        started_at=timezone.now(),
    )

    comm_a = WebsocketCommunicator(application, f"/ws/hh/{conv.id}/?user_id={alice.id}")
    comm_b = WebsocketCommunicator(application, f"/ws/hh/{conv.id}/?user_id={bob.id}")
    await comm_a.connect()
    await comm_b.connect()

    low_emotion = {"score": 0.05, "label": "positive", "is_over_threshold": False}
    with patch("chat.consumers.aget_analyze_emotion", return_value=low_emotion):
        await comm_a.send_json_to({"content": "我認為核能是穩定的能源選項。"})
        bob_msg = await comm_b.receive_json_from(timeout=5)

    # Bob gets relay directly (no ai_suggestion on Alice's side)
    assert bob_msg["content"] == "我認為核能是穩定的能源選項。"
    assert bob_msg["sender_id"] == alice.id

    # Alice gets the relay echo too, but NOT an ai_suggestion
    alice_msg = await comm_a.receive_json_from(timeout=2)
    assert alice_msg.get("type") != "ai_suggestion"

    # No AISuggestion record created
    count = await AISuggestion.objects.filter(conversation=conv).acount()
    assert count == 0

    await comm_a.disconnect()
    await comm_b.disconnect()
