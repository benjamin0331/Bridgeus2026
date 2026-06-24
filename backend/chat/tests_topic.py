"""
Tests for chat.services.topic (window-based topic-deviation detection).

API: check_topic_relevance(conversation_id, user_id, anchor_embedding, window=5)

Anchor topic: "台灣是否應該擴大發展核能發電"
"""

import pytest
from django.contrib.auth import get_user_model

from chat.models import Conversation, Message
from chat.services.embedding import get_embedding
from chat.services.topic import (
    TOPIC_RELEVANCE_THRESHOLD,
    acheck_topic_relevance,
    aget_topic_anchor_embedding,
    check_topic_relevance,
    get_topic_anchor_embedding,
)

User = get_user_model()
ANCHOR_DESCRIPTION = "台灣是否應該擴大發展核能發電"


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------

@pytest.fixture(scope="module")
def anchor_embedding():
    return get_topic_anchor_embedding(ANCHOR_DESCRIPTION)


def _user(name):
    return User.objects.create_user(username=name, password="x")


def _conv(user_a, user_b):
    return Conversation.objects.create(
        topic_id=1, user_a=user_a, user_b=user_b,
        session_number=1, status=Conversation.Status.ACTIVE,
    )


def _msg(conv, sender, content):
    emb = get_embedding(content)
    return Message.objects.create(
        conversation=conv, sender=sender, content=content, embedding=emb
    )


def _msg_no_emb(conv, sender, content="hello"):
    return Message.objects.create(
        conversation=conv, sender=sender, content=content
    )


# ------------------------------------------------------------------
# Anchor embedding shape (unchanged)
# ------------------------------------------------------------------

def test_anchor_embedding_shape():
    emb = get_topic_anchor_embedding(ANCHOR_DESCRIPTION)
    assert isinstance(emb, list)
    assert len(emb) == 384
    assert all(isinstance(v, float) for v in emb)


# ------------------------------------------------------------------
# No messages → fail-open (on-topic)
# ------------------------------------------------------------------

@pytest.mark.django_db
def test_no_messages_returns_on_topic(anchor_embedding):
    alice = _user("tp_nomsg_a")
    bob = _user("tp_nomsg_b")
    conv = _conv(alice, bob)

    r = check_topic_relevance(conv.id, alice.id, anchor_embedding)
    assert r["is_off_topic"] is False
    assert r["relevance_score"] == 1.0


@pytest.mark.django_db
def test_messages_without_embeddings_treated_as_no_messages(anchor_embedding):
    alice = _user("tp_noemb_a")
    bob = _user("tp_noemb_b")
    conv = _conv(alice, bob)
    _msg_no_emb(conv, alice)

    r = check_topic_relevance(conv.id, alice.id, anchor_embedding)
    assert r["is_off_topic"] is False


# ------------------------------------------------------------------
# Return shape
# ------------------------------------------------------------------

@pytest.mark.django_db
def test_return_keys_and_types(anchor_embedding):
    alice = _user("tp_shape_a")
    bob = _user("tp_shape_b")
    conv = _conv(alice, bob)
    _msg(conv, alice, "核能的安全性值得深入討論")

    r = check_topic_relevance(conv.id, alice.id, anchor_embedding)
    assert set(r.keys()) == {"relevance_score", "is_off_topic"}
    assert isinstance(r["relevance_score"], float)
    assert 0.0 <= r["relevance_score"] <= 1.0
    assert isinstance(r["is_off_topic"], bool)


# ------------------------------------------------------------------
# Threshold consistency
# ------------------------------------------------------------------

@pytest.mark.django_db
def test_threshold_consistency(anchor_embedding):
    alice = _user("tp_thresh_a")
    bob = _user("tp_thresh_b")
    conv = _conv(alice, bob)

    sentences = [
        "我認為核能的安全風險被高估了",
        "今天中午吃什麼好呢",
        "日本福島事件讓很多人改變想法",
        "政府應該優先考慮再生能源替代方案",
    ]
    for text in sentences:
        # Each test uses its own fresh message
        Message.objects.filter(conversation=conv, sender=alice).delete()
        _msg(conv, alice, text)
        r = check_topic_relevance(conv.id, alice.id, anchor_embedding)
        expected = r["relevance_score"] < TOPIC_RELEVANCE_THRESHOLD
        assert r["is_off_topic"] == expected, (
            f"Inconsistent for {text!r}: "
            f"score={r['relevance_score']:.4f}, threshold={TOPIC_RELEVANCE_THRESHOLD}"
        )


# ------------------------------------------------------------------
# On-topic sentences
# ------------------------------------------------------------------

@pytest.mark.django_db
def test_on_topic_direct_nuclear_statement(anchor_embedding):
    alice = _user("tp_on1_a")
    bob = _user("tp_on1_b")
    conv = _conv(alice, bob)
    _msg(conv, alice, "我認為核能的安全風險被高估了")

    r = check_topic_relevance(conv.id, alice.id, anchor_embedding)
    print(f"\n  [on-topic] relevance_score={r['relevance_score']:.4f}")
    assert r["is_off_topic"] is False


@pytest.mark.django_db
def test_on_topic_energy_policy(anchor_embedding):
    alice = _user("tp_on2_a")
    bob = _user("tp_on2_b")
    conv = _conv(alice, bob)
    _msg(conv, alice, "台灣的能源政策需要長遠規劃")

    r = check_topic_relevance(conv.id, alice.id, anchor_embedding)
    print(f"\n  [on-topic] relevance_score={r['relevance_score']:.4f}")
    assert r["is_off_topic"] is False


@pytest.mark.django_db
def test_on_topic_fukushima_indirect(anchor_embedding):
    alice = _user("tp_fuku_a")
    bob = _user("tp_fuku_b")
    conv = _conv(alice, bob)
    _msg(conv, alice, "日本福島事件讓很多人改變想法")

    r = check_topic_relevance(conv.id, alice.id, anchor_embedding)
    print(f"\n  [edge-case Fukushima] relevance_score={r['relevance_score']:.4f}")
    assert r["is_off_topic"] is False, (
        f"Fukushima (nuclear-related) wrongly flagged off-topic: score={r['relevance_score']:.4f}"
    )


# ------------------------------------------------------------------
# Off-topic sentences
# ------------------------------------------------------------------

@pytest.mark.django_db
def test_off_topic_lunch(anchor_embedding):
    alice = _user("tp_off1_a")
    bob = _user("tp_off1_b")
    conv = _conv(alice, bob)
    _msg(conv, alice, "今天中午吃什麼好呢")

    r = check_topic_relevance(conv.id, alice.id, anchor_embedding)
    print(f"\n  [off-topic] relevance_score={r['relevance_score']:.4f}")
    assert r["is_off_topic"] is True


@pytest.mark.django_db
def test_off_topic_baseball(anchor_embedding):
    alice = _user("tp_off2_a")
    bob = _user("tp_off2_b")
    conv = _conv(alice, bob)
    _msg(conv, alice, "你覺得今天棒球比賽結果怎麼樣")

    r = check_topic_relevance(conv.id, alice.id, anchor_embedding)
    print(f"\n  [off-topic] relevance_score={r['relevance_score']:.4f}")
    assert r["is_off_topic"] is True


# ------------------------------------------------------------------
# Window behaviour
# ------------------------------------------------------------------

@pytest.mark.django_db
def test_window_limits_messages_used(anchor_embedding):
    """Only the last `window` messages should be averaged."""
    alice = _user("tp_win_a")
    bob = _user("tp_win_b")
    conv = _conv(alice, bob)

    # 4 off-topic messages followed by 1 on-topic
    for _ in range(4):
        _msg(conv, alice, "今天棒球比賽很精彩")
    _msg(conv, alice, "核能是穩定的基載能源")

    # window=1 → only the last on-topic message → should be on-topic
    r = check_topic_relevance(conv.id, alice.id, anchor_embedding, window=1)
    print(f"\n  [window=1, last=on-topic] score={r['relevance_score']:.4f}")
    assert r["is_off_topic"] is False


@pytest.mark.django_db
def test_fewer_than_window_messages_ok(anchor_embedding):
    """Fewer messages than window should not error."""
    alice = _user("tp_few_a")
    bob = _user("tp_few_b")
    conv = _conv(alice, bob)
    _msg(conv, alice, "核能的廢料處理是重要議題")  # only 1 message, window=5

    r = check_topic_relevance(conv.id, alice.id, anchor_embedding, window=5)
    assert isinstance(r["relevance_score"], float)


@pytest.mark.django_db
def test_only_sender_messages_used(anchor_embedding):
    """Bob's messages must not affect Alice's topic check."""
    alice = _user("tp_sender_a")
    bob = _user("tp_sender_b")
    conv = _conv(alice, bob)

    # Bob sends off-topic messages
    for _ in range(5):
        _msg(conv, bob, "今天中午吃什麼")

    # Alice sends on-topic message
    _msg(conv, alice, "核能的碳排放比天然氣低很多")

    r = check_topic_relevance(conv.id, alice.id, anchor_embedding)
    assert r["is_off_topic"] is False


# ------------------------------------------------------------------
# Async wrappers
# ------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_async_check_matches_sync(anchor_embedding):
    from asgiref.sync import sync_to_async

    alice = await sync_to_async(User.objects.create_user)(username="tp_async_a", password="x")
    bob = await sync_to_async(User.objects.create_user)(username="tp_async_b", password="x")
    conv = await Conversation.objects.acreate(
        topic_id=1, user_a=alice, user_b=bob,
        session_number=1, status=Conversation.Status.ACTIVE,
    )
    emb = get_embedding("核能電廠的建設成本相當高昂")
    await Message.objects.acreate(
        conversation=conv, sender=alice,
        content="核能電廠的建設成本相當高昂", embedding=emb,
    )

    _async_sync_check = sync_to_async(check_topic_relevance, thread_sensitive=False)
    sync_r = await _async_sync_check(conv.id, alice.id, anchor_embedding)
    async_r = await acheck_topic_relevance(conv.id, alice.id, anchor_embedding)
    assert sync_r == async_r


@pytest.mark.asyncio
async def test_async_anchor_matches_sync():
    sync_emb = get_topic_anchor_embedding(ANCHOR_DESCRIPTION)
    async_emb = await aget_topic_anchor_embedding(ANCHOR_DESCRIPTION)
    assert len(sync_emb) == len(async_emb) == 384
    for a, b in zip(sync_emb, async_emb):
        assert abs(a - b) < 1e-6
