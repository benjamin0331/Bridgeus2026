"""
Tests for chat.services.filter (Stage 1 blacklist).

Stage 2 classifier is TODO; tests only cover Stage 1 behaviour.
"""

import pytest

from chat.services._blacklist import BLACKLIST
from chat.services.filter import acheck_content, check_content, check_content_sync


# ---------- return shape ----------

def test_return_keys_and_types_clean():
    r = check_content("核能政策需要更多理性討論")
    assert set(r.keys()) == {"is_blocked", "reason", "matched_word"}
    assert r["is_blocked"] is False
    assert r["reason"] is None
    assert r["matched_word"] is None


def test_return_keys_and_types_blocked():
    r = check_content("你是白痴")
    assert set(r.keys()) == {"is_blocked", "reason", "matched_word"}
    assert r["is_blocked"] is True
    assert r["reason"] == "blacklist"
    assert isinstance(r["matched_word"], str)


# ---------- blacklist matching ----------

def test_clear_personal_insult_blocked():
    r = check_content("你是白痴")
    assert r["is_blocked"] is True
    assert r["matched_word"] == "白痴"


def test_blacklisted_word_at_start_of_sentence():
    r = check_content("智障才會說出這種話")
    assert r["is_blocked"] is True
    assert r["matched_word"] == "智障"


def test_blacklisted_word_in_middle_of_sentence():
    # Key requirement: detect even when word is embedded, not standalone
    r = check_content("你這個白痴根本不可理喻")
    assert r["is_blocked"] is True
    assert r["matched_word"] == "白痴"


def test_blacklisted_word_at_end_of_sentence():
    r = check_content("他真的是個廢物")
    assert r["is_blocked"] is True
    assert r["matched_word"] == "廢物"


def test_threat_word_blocked():
    r = check_content("你給我去死")
    assert r["is_blocked"] is True


def test_threat_go_away_blocked():
    r = check_content("快滾開！")
    assert r["is_blocked"] is True


def test_insult_surrounded_by_punctuation():
    r = check_content("，你這個人渣，根本不值得討論。")
    assert r["is_blocked"] is True
    assert r["matched_word"] == "人渣"


def test_multiple_blacklisted_words_returns_first_found():
    # "智障" and "廢物" both present; should block with one of them
    r = check_content("你這個智障廢物")
    assert r["is_blocked"] is True
    assert r["matched_word"] in BLACKLIST


# ---------- not blocked — strong but acceptable dialogue ----------

def test_strong_opposition_not_blocked():
    r = check_content("我強烈反對核能發電，這是完全不可接受的政策方向")
    assert r["is_blocked"] is False


def test_firm_disagreement_not_blocked():
    r = check_content("你的論點根本站不住腳，我完全不同意這個說法")
    assert r["is_blocked"] is False


def test_negative_policy_opinion_not_blocked():
    r = check_content("這個能源政策是錯誤的，會帶來長遠危害")
    assert r["is_blocked"] is False


def test_frustrated_but_clean_not_blocked():
    r = check_content("我實在不明白為什麼還有人支持這種立場！")
    assert r["is_blocked"] is False


def test_empty_string_not_blocked():
    r = check_content("")
    assert r["is_blocked"] is False


def test_whitespace_only_not_blocked():
    r = check_content("   ")
    assert r["is_blocked"] is False


# ---------- check_content_sync ----------

def test_sync_blocks_same_words_as_full():
    for text in ["你是白痴", "去你的", "你這個混蛋", "我反對這個政策"]:
        assert check_content_sync(text)["is_blocked"] == check_content(text)["is_blocked"]


def test_sync_returns_correct_matched_word():
    r = check_content_sync("你這個狗東西")
    assert r["is_blocked"] is True
    assert r["matched_word"] == "狗東西"


def test_sync_clean_returns_nulls():
    r = check_content_sync("核能議題需要更多公開討論")
    assert r == {"is_blocked": False, "reason": None, "matched_word": None}


# ---------- async wrapper ----------

@pytest.mark.asyncio
async def test_async_blocked_matches_sync():
    text = "你這個人渣到底在說什麼"
    sync_r = check_content(text)
    async_r = await acheck_content(text)
    assert sync_r == async_r


@pytest.mark.asyncio
async def test_async_clean_matches_sync():
    text = "我認為雙方都有值得傾聽的觀點"
    sync_r = check_content(text)
    async_r = await acheck_content(text)
    assert sync_r == async_r
