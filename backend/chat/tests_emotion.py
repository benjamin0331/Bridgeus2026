"""
Tests for chat.services.emotion.

Known model characteristic: lxyuan is topic-sensitive — sentences that discuss
negative topics (accidents, risks) score higher even with calm tone. Tests
deliberately use tone-discriminating sentences (personal attacks vs neutral
dialogue) rather than topic-discriminating ones to avoid this confound.

Actual scores observed during development:
  "我認為這個政策有改善空間"    → 0.2293  (positive dominant)
  "這個議題需要更多理性討論"    → 0.2659  (positive dominant)
  "你這種想法根本就是白痴"      → 0.6653  (negative dominant)
  "你根本不懂！別再廢話了！"    → 0.8739  (negative dominant, over threshold)
"""

import pytest

from chat.services.emotion import EMOTION_THRESHOLD, aget_analyze_emotion, analyze_emotion


# ---------- return shape ----------

def test_return_keys_and_types():
    r = analyze_emotion("核能政策需要更多討論")
    assert set(r.keys()) == {"score", "label", "is_over_threshold"}
    assert isinstance(r["score"], float)
    assert 0.0 <= r["score"] <= 1.0
    assert r["label"] in {"positive", "neutral", "negative"}
    assert isinstance(r["is_over_threshold"], bool)


def test_empty_string_returns_zero():
    r = analyze_emotion("")
    assert r == {"score": 0.0, "label": "neutral", "is_over_threshold": False}


def test_whitespace_only_returns_zero():
    r = analyze_emotion("   ")
    assert r == {"score": 0.0, "label": "neutral", "is_over_threshold": False}


# ---------- intensity discrimination ----------

def test_calm_constructive_statement_low():
    # neutral / constructive — should not trigger any intervention
    r = analyze_emotion("我認為這個政策有改善空間")
    assert r["score"] < 0.5, f"Calm statement scored too high: {r['score']:.4f}"


def test_calm_dialogue_statement_low():
    r = analyze_emotion("這個議題需要更多理性討論")
    assert r["score"] < 0.5, f"Calm statement scored too high: {r['score']:.4f}"


def test_personal_insult_high_score():
    # direct personal insult — "Your kind of thinking is simply idiotic"
    r = analyze_emotion("你這種想法根本就是白痴")
    assert r["score"] > 0.6, f"Personal insult scored too low: {r['score']:.4f}"


def test_angry_exclamation_high_score():
    # hostile exclamation — "You don't understand at all! Stop talking nonsense!"
    r = analyze_emotion("你根本不懂！別再廢話了！")
    assert r["score"] > 0.6, f"Angry exclamation scored too low: {r['score']:.4f}"


def test_hostile_yelling_triggers_threshold():
    # Score 0.87 in practice — should fire the calming intervention
    r = analyze_emotion("你根本不懂！別再廢話了！")
    assert r["is_over_threshold"] is True, (
        f"Expected is_over_threshold=True, got score={r['score']:.4f}"
    )


# ---------- threshold consistency ----------

def test_is_over_threshold_always_consistent_with_score():
    """is_over_threshold must equal (score >= EMOTION_THRESHOLD) for any input."""
    sentences = [
        "我支持核能發電的立場",
        "你這種想法根本就是白痴",
        "你根本不懂！別再廢話了！",
        "這個數據需要進一步驗證",
        "我認為雙方都有值得思考的觀點",
        "",
    ]
    for text in sentences:
        r = analyze_emotion(text)
        expected_flag = r["score"] >= EMOTION_THRESHOLD
        assert r["is_over_threshold"] == expected_flag, (
            f"Flag inconsistent for {text!r}: "
            f"score={r['score']:.4f}, threshold={EMOTION_THRESHOLD}"
        )


# ---------- async wrapper ----------

@pytest.mark.asyncio
async def test_async_wrapper_matches_sync():
    text = "核能的安全性是值得討論的議題"
    sync_r = analyze_emotion(text)
    async_r = await aget_analyze_emotion(text)
    assert sync_r == async_r
