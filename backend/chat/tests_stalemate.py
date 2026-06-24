"""
Tests for chat.services.stalemate.

Embedding construction strategy for deterministic stalemate tests:
  - "Stable round" : Alice = unit_vec(dim=0), Bob = unit_vec(dim=1)
    cosine_distance ≈ 1.0 for every pair → std_dev ≈ 0 → stalemate
  - "Shifting round": Alice = unit_vec(dim=0), Bob shifts from dim=0 (dist≈0)
    through dim=1 (dist≈1) to opposite-dim=0 (dist≈2)
    → std_dev >> 0.05 → no stalemate

No real model inference is needed for stalemate/distance tests — we inject
pre-computed embeddings directly into Message records.
Model inference IS used for extract_opponent_keywords (real KeyBERT + jieba).
"""

import numpy as np
import pytest
from asgiref.sync import sync_to_async
from django.contrib.auth import get_user_model

from chat.models import Conversation, Message
from chat.services.stalemate import (
    STALEMATE_THRESHOLD,
    adetect_stalemate,
    aextract_opponent_keywords,
    build_stalemate_prompt,
    detect_stalemate,
    extract_opponent_keywords,
)

User = get_user_model()


# ---------- helpers ----------

def _unit(dim: int) -> list[float]:
    v = np.zeros(384, dtype=np.float32)
    v[dim] = 1.0
    return v.tolist()


def _make_user(username: str):
    return User.objects.create_user(username=username, password="x")


def _make_conv(user_a, user_b):
    return Conversation.objects.create(
        topic_id=1, user_a=user_a, user_b=user_b,
        session_number=1, status=Conversation.Status.ACTIVE,
    )


def _make_msg(conv, sender, content=".", embedding=None):
    return Message.objects.create(
        conversation=conv, sender=sender, content=content, embedding=embedding,
    )


# ---------- detect_stalemate: return shape ----------

@pytest.mark.django_db
def test_detect_stalemate_return_shape():
    alice = _make_user("sm_shape_a")
    bob = _make_user("sm_shape_b")
    conv = _make_conv(alice, bob)
    rng = np.random.default_rng(0)
    for _ in range(5):
        _make_msg(conv, alice, embedding=rng.random(384).tolist())
        _make_msg(conv, bob, embedding=rng.random(384).tolist())

    r = detect_stalemate(conv.id, window_size=5)
    assert set(r.keys()) == {"is_stalemate", "distance_trend", "std_dev"}
    assert isinstance(r["is_stalemate"], bool)
    assert isinstance(r["distance_trend"], list)
    assert isinstance(r["std_dev"], float)


@pytest.mark.django_db
def test_distance_trend_length_matches_available_pairs():
    alice = _make_user("sm_trend_a")
    bob = _make_user("sm_trend_b")
    conv = _make_conv(alice, bob)
    rng = np.random.default_rng(1)
    for _ in range(5):
        _make_msg(conv, alice, embedding=rng.random(384).tolist())
        _make_msg(conv, bob, embedding=rng.random(384).tolist())

    r = detect_stalemate(conv.id, window_size=5)
    assert len(r["distance_trend"]) == 5


# ---------- detect_stalemate: insufficient data ----------

@pytest.mark.django_db
def test_single_pair_not_stalemate():
    alice = _make_user("sm_single_a")
    bob = _make_user("sm_single_b")
    conv = _make_conv(alice, bob)
    _make_msg(conv, alice, embedding=_unit(0))
    _make_msg(conv, bob, embedding=_unit(1))

    r = detect_stalemate(conv.id, window_size=5)
    assert r["is_stalemate"] is False
    assert r["distance_trend"] == []  # fewer than 2 pairs


@pytest.mark.django_db
def test_no_embeddings_returns_not_stalemate():
    alice = _make_user("sm_null_a")
    bob = _make_user("sm_null_b")
    conv = _make_conv(alice, bob)
    for _ in range(5):
        _make_msg(conv, alice, embedding=None)
        _make_msg(conv, bob, embedding=None)

    r = detect_stalemate(conv.id, window_size=5)
    assert r["is_stalemate"] is False
    assert r["distance_trend"] == []


@pytest.mark.django_db
def test_partial_embeddings_counted_correctly():
    """3 pairs have embeddings, 2 don't; window_size=5 → not enough → not stalemate."""
    alice = _make_user("sm_partial_a")
    bob = _make_user("sm_partial_b")
    conv = _make_conv(alice, bob)
    rng = np.random.default_rng(2)
    for _ in range(3):
        _make_msg(conv, alice, embedding=rng.random(384).tolist())
        _make_msg(conv, bob, embedding=rng.random(384).tolist())
    for _ in range(2):
        _make_msg(conv, alice, embedding=None)
        _make_msg(conv, bob, embedding=None)

    r = detect_stalemate(conv.id, window_size=5)
    assert r["is_stalemate"] is False
    assert len(r["distance_trend"]) == 3


# ---------- detect_stalemate: stalemate case ----------

@pytest.mark.django_db
def test_stalemate_detected_when_positions_stable():
    """
    Alice: unit_vec(dim=0), Bob: unit_vec(dim=1) — orthogonal, cosine_dist ≈ 1.0.
    Tiny noise (σ=0.002) keeps std_dev << STALEMATE_THRESHOLD → stalemate.
    """
    alice = _make_user("sm_stale_a")
    bob = _make_user("sm_stale_b")
    conv = _make_conv(alice, bob)

    a_base = np.array(_unit(0))
    b_base = np.array(_unit(1))
    rng = np.random.default_rng(42)
    for _ in range(5):
        a_emb = (a_base + rng.normal(0, 0.002, 384)).tolist()
        b_emb = (b_base + rng.normal(0, 0.002, 384)).tolist()
        _make_msg(conv, alice, "核能是安全的", a_emb)
        _make_msg(conv, bob, "核能是危險的", b_emb)

    r = detect_stalemate(conv.id, window_size=5)
    print(f"\n  [stalemate] std_dev={r['std_dev']:.4f}, distances={r['distance_trend']}")
    assert r["is_stalemate"] is True
    assert r["std_dev"] < STALEMATE_THRESHOLD


# ---------- detect_stalemate: no stalemate case ----------

@pytest.mark.django_db
def test_not_stalemate_when_distance_shifts():
    """
    Alice: fixed at unit_vec(dim=0).
    Bob: shifts from same direction as Alice (dist≈0) to opposite (dist≈2).
    distances ≈ [0, 0.5, 1.0, 1.5, 2.0] → std_dev ≈ 0.71 >> STALEMATE_THRESHOLD.
    """
    alice = _make_user("sm_shift_a")
    bob = _make_user("sm_shift_b")
    conv = _make_conv(alice, bob)

    a_base = np.array(_unit(0), dtype=np.float32)
    # Bob embeddings designed to give cosine_distances ≈ 0, 0.5, 1.0, 1.5, 2.0
    # cosine_similarity = 1 - distance, so: 1.0, 0.5, 0.0, -0.5, -1.0
    # cosine_sim = dot(a,b)/(|a||b|) = cos(θ); b = [cos(θ), sin(θ), 0, ...]
    import math
    target_distances = [0.0, 0.5, 1.0, 1.5, 2.0]
    for d in target_distances:
        cos_sim = 1.0 - d
        sin_val = math.sqrt(max(0.0, 1.0 - cos_sim ** 2))
        b = np.zeros(384, dtype=np.float32)
        b[0] = cos_sim
        b[1] = sin_val
        _make_msg(conv, alice, "alice view", a_base.tolist())
        _make_msg(conv, bob, "bob shifting", b.tolist())

    r = detect_stalemate(conv.id, window_size=5)
    print(f"\n  [shifting] std_dev={r['std_dev']:.4f}, distances={r['distance_trend']}")
    assert r["is_stalemate"] is False
    assert r["std_dev"] >= STALEMATE_THRESHOLD


# ---------- extract_opponent_keywords ----------

@pytest.mark.django_db
def test_extract_keywords_returns_list_of_strings():
    alice = _make_user("kw_shape_a")
    bob = _make_user("kw_shape_b")
    conv = _make_conv(alice, bob)

    for i in range(5):
        _make_msg(conv, bob, f"核能發電的安全性一直是爭議焦點，台灣的能源政策需要長遠規劃 {i}")
    _make_msg(conv, alice, "我認為再生能源是更好的選擇")

    kws = extract_opponent_keywords(conv.id, alice.id, n_messages=5, top_n=2)
    print(f"\n  keywords={kws}")
    assert isinstance(kws, list)
    assert len(kws) <= 2
    assert all(isinstance(k, str) and len(k) >= 2 for k in kws)


@pytest.mark.django_db
def test_extract_keywords_no_opponent_messages_returns_empty():
    alice = _make_user("kw_empty_a")
    bob = _make_user("kw_empty_b")
    conv = _make_conv(alice, bob)
    _make_msg(conv, alice, "我在說話")

    kws = extract_opponent_keywords(conv.id, alice.id, n_messages=5, top_n=2)
    assert kws == []


@pytest.mark.django_db
def test_extract_keywords_contains_chinese_characters():
    """Extracted keywords must contain Chinese characters (not garbage tokens)."""
    alice = _make_user("kw_zh_a")
    bob = _make_user("kw_zh_b")
    conv = _make_conv(alice, bob)

    for msg in [
        "核能發電的碳排放量遠低於燃煤電廠",
        "台灣的能源轉型需要考慮核能的基載供電能力",
        "第三代核能反應爐的安全設計大幅降低事故風險",
    ]:
        _make_msg(conv, bob, msg)
    _make_msg(conv, alice, "我反對核能發電")

    kws = extract_opponent_keywords(conv.id, alice.id, n_messages=5, top_n=2)
    print(f"\n  keywords={kws}")
    assert len(kws) >= 1
    assert any(any("一" <= c <= "鿿" for c in kw) for kw in kws)


@pytest.mark.django_db
def test_extract_keywords_top_n_respected():
    alice = _make_user("kw_topn_a")
    bob = _make_user("kw_topn_b")
    conv = _make_conv(alice, bob)
    for _ in range(5):
        _make_msg(conv, bob, "能源政策、核能安全、碳排放目標、再生能源補貼、電力穩定供應")
    _make_msg(conv, alice, "我支持再生能源")

    kws = extract_opponent_keywords(conv.id, alice.id, n_messages=5, top_n=3)
    assert len(ws := kws) <= 3


# ---------- build_stalemate_prompt ----------

def test_build_prompt_contains_keyword():
    p = build_stalemate_prompt(["能源轉型"])
    assert "能源轉型" in p


def test_build_prompt_empty_keywords_fallback():
    p = build_stalemate_prompt([])
    assert isinstance(p, str) and len(p) > 0
    assert "{keyword}" not in p


def test_build_prompt_randomness():
    prompts = {build_stalemate_prompt(["核能"]) for _ in range(60)}
    assert len(prompts) > 1, "Expected multiple different templates to appear"


# ---------- async wrappers ----------

@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_async_detect_stalemate_wrapper():
    create_user = sync_to_async(User.objects.create_user, thread_sensitive=True)
    alice = await create_user(username="sm_async_a", password="x")
    bob = await create_user(username="sm_async_b", password="x")
    conv = await Conversation.objects.acreate(
        topic_id=1, user_a=alice, user_b=bob,
        session_number=1, status=Conversation.Status.ACTIVE,
    )

    a_base = np.array(_unit(0))
    b_base = np.array(_unit(1))
    rng = np.random.default_rng(77)
    for _ in range(5):
        await Message.objects.acreate(
            conversation=conv, sender=alice, content="a",
            embedding=(a_base + rng.normal(0, 0.002, 384)).tolist(),
        )
        await Message.objects.acreate(
            conversation=conv, sender=bob, content="b",
            embedding=(b_base + rng.normal(0, 0.002, 384)).tolist(),
        )

    r = await adetect_stalemate(conv.id, window_size=5)
    assert r["is_stalemate"] is True
    assert len(r["distance_trend"]) == 5


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_async_extract_keywords_wrapper():
    create_user = sync_to_async(User.objects.create_user, thread_sensitive=True)
    alice = await create_user(username="kw_async_a", password="x")
    bob = await create_user(username="kw_async_b", password="x")
    conv = await Conversation.objects.acreate(
        topic_id=1, user_a=alice, user_b=bob,
        session_number=1, status=Conversation.Status.ACTIVE,
    )
    for msg in ["核能發電是台灣能源政策的重要議題", "安全性與碳排放都需要納入考量"]:
        await Message.objects.acreate(conversation=conv, sender=bob, content=msg)
    await Message.objects.acreate(conversation=conv, sender=alice, content="我反對")

    kws = await aextract_opponent_keywords(conv.id, alice.id, n_messages=5, top_n=2)
    assert isinstance(kws, list)
