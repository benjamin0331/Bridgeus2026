"""Unit tests for the pure-rule input gate.

No DB, no cache, no network — if any of these tests start needing a fixture,
the gate has stopped being a pure rule module and the token-cost guarantee
(microsecond-level, runnable on the WebSocket sync path) is gone.
"""

import pytest

from apps.matching.services.input_gate import (
    COOLDOWN_SECONDS,
    FALLBACK_LOW_INFORMATION,
    FALLBACK_NON_LINGUISTIC,
    FALLBACK_PROFANITY_ONLY,
    InputVerdict,
    ai_turn_is_question,
    classify,
    fallback_message,
    is_meaningless,
    is_short_response,
    is_substantive_message,
    rate_limit_notice,
    throttle_tier,
)


# ── 攔截 ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "text",
    ["54", "6456", "45656", "!!!!", "aaaaaaa", "123123123", "。。。"],
)
def test_blocks_meaningless_input(text):
    assert is_meaningless(text) is True


@pytest.mark.parametrize(
    "text",
    ["54", "6456", "45656", "!!!!", "123123123", "。。。"],
)
def test_pure_symbol_or_digit_is_non_linguistic(text):
    """規則 2 — 不含 CJK 也不含拉丁字母。"""
    assert classify(text) is InputVerdict.NON_LINGUISTIC


def test_keyboard_mash_is_non_linguistic():
    """規則 3 — 有拉丁字母但字元重複度過低。"""
    assert classify("aaaaaaa") is InputVerdict.NON_LINGUISTIC
    assert classify("asdasdasdasd") is InputVerdict.NON_LINGUISTIC


def test_low_semantic_char_ratio_is_non_linguistic():
    """規則 4 — 語意字元佔比低於 0.4。"""
    assert classify("a123456789") is InputVerdict.NON_LINGUISTIC


def test_blocked_even_when_prev_ai_asked_a_question():
    """非語言性輸入不因為 AI 剛提問就變成合法回答。"""
    assert is_meaningless("6456", prev_ai_is_question=True) is True


# ── 放行 ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text", ["好", "不同意", "嗯", "OK", "ok", "好的。"])
def test_short_valid_passes_when_ai_just_asked(text):
    assert is_meaningless(text, prev_ai_is_question=True) is False


def test_substantive_statement_passes_without_question_context():
    assert is_meaningless("我覺得核廢料處理是關鍵") is False
    assert classify("我覺得核廢料處理是關鍵") is InputVerdict.VALID


def test_statement_with_numbers_passes():
    """規則 4 的 0.4 門檻必須讓數據型發言通過。"""
    assert is_meaningless("2025 年核電佔比約 6.3%，這個數字才是重點") is False


# ── 邊界：規則 5 是本次設計的核心 ────────────────────────────────────────

def test_short_valid_blocked_without_question_context():
    """同一則「好」：AI 剛提問 → 合法輪次；無提問脈絡 → 低訊息量。

    長度本身不是獨立判準，一定要搭配 prev_ai_is_question 才成立。
    """
    assert classify("好", prev_ai_is_question=False) is InputVerdict.LOW_INFORMATION
    assert classify("好", prev_ai_is_question=True) is InputVerdict.VALID


def test_short_non_whitelisted_text_is_low_information():
    assert classify("核能", prev_ai_is_question=False) is InputVerdict.LOW_INFORMATION


def test_whitelist_hit_is_never_non_linguistic():
    """規則 1 豁免規則 2/3/4，攔截時只會是 LOW_INFORMATION。"""
    assert classify("不", prev_ai_is_question=False) is InputVerdict.LOW_INFORMATION


def test_empty_input_is_blocked():
    assert is_meaningless("") is True
    assert is_meaningless("   ") is True


# ── 規則 0：單字粗口 ────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "text",
    [
        "幹",
        "幹幹",
        "幹幹幹",
        "幹幹幹幹幹幹幹幹",
        "幹!!!",
        "幹！！！",
        "幹。",
        "幹 幹 幹",
        "操",
        "靠",
        "屌",
    ],
)
def test_standalone_profanity_is_blocked(text):
    assert classify(text) is InputVerdict.PROFANITY_ONLY


@pytest.mark.parametrize(
    "text",
    ["幹", "幹幹幹", "幹！！！", "幹 幹 幹", "操", "靠"],
)
def test_standalone_profanity_is_blocked_even_after_an_ai_question(text):
    """「好」在 AI 提問後是合法輪次；「幹」不是。規則 0 不看脈絡。"""
    assert classify(text, prev_ai_is_question=True) is InputVerdict.PROFANITY_ONLY
    assert is_meaningless(text, prev_ai_is_question=True) is True


@pytest.mark.parametrize(
    "text",
    [
        "幹嘛這樣講",
        "他是行政院的幹部之一",
        "這棵樹的樹幹很粗",
        "核電廠的操作程序很嚴格",
        "這個方案不夠可靠",
        "電網的主幹線路老舊",
    ],
)
def test_profanity_characters_in_ordinary_words_are_not_blocked(text):
    """「幹」不能進 BLACKLIST 的原因：子字串比對會誤殺這些句子。"""
    assert classify(text) is InputVerdict.VALID


def test_profanity_with_real_content_is_not_rule_zero():
    """帶情緒的論述交給黑名單與情緒偵測，不歸規則 0 管。"""
    assert classify("幹，核電根本就是騙局") is InputVerdict.VALID


def test_repeated_profanity_escalates_like_any_other_block():
    """「幹×n」逐則累加，第 6 則進冷卻——這就是 n 次也被擋下的機制。"""
    verdicts = [classify("幹" * n, prev_ai_is_question=True) for n in range(1, 7)]
    assert all(verdict is InputVerdict.PROFANITY_ONLY for verdict in verdicts)
    assert throttle_tier(6) == "cooldown"


# ── Fallback 語氣 ───────────────────────────────────────────────────────

def test_fallback_pool_matches_verdict():
    assert fallback_message(InputVerdict.NON_LINGUISTIC, 1) in FALLBACK_NON_LINGUISTIC
    assert fallback_message(InputVerdict.LOW_INFORMATION, 1) in FALLBACK_LOW_INFORMATION
    assert fallback_message(InputVerdict.PROFANITY_ONLY, 1) in FALLBACK_PROFANITY_ONLY


def test_profanity_fallback_is_not_the_low_information_one():
    """對著打「幹」的人回「可以再多說一點嗎」是答非所問。"""
    assert (
        fallback_message(InputVerdict.PROFANITY_ONLY, 1)
        not in FALLBACK_LOW_INFORMATION + FALLBACK_NON_LINGUISTIC
    )


def test_consecutive_blocks_rotate_the_fallback():
    first = fallback_message(InputVerdict.NON_LINGUISTIC, 1)
    second = fallback_message(InputVerdict.NON_LINGUISTIC, 2)
    assert first != second


def test_no_fallback_blames_the_participant():
    """指責性語氣會污染 Part E 體驗評估與 Part C-2 對話品質感知。"""
    banned = ("無意義", "沒有辦法承接", "一直是", "請勿", "不當", "違反")
    pools = (
        FALLBACK_NON_LINGUISTIC + FALLBACK_LOW_INFORMATION + FALLBACK_PROFANITY_ONLY
    )
    for message in pools:
        assert not any(phrase in message for phrase in banned), message


# ── 遞進節流 ────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "count,expected",
    [
        (1, "bubble"),
        (2, "bubble"),
        (3, "notice"),
        (5, "notice"),
        (6, "cooldown"),
        (12, "cooldown"),
    ],
)
def test_throttle_tier(count, expected):
    assert throttle_tier(count) == expected


def test_cooldown_is_sixty_seconds():
    assert COOLDOWN_SECONDS == 60


def test_rate_limit_notice_distinguishes_reasons():
    assert rate_limit_notice("too_fast") != rate_limit_notice("too_many")


# ── 共用的實質發言判斷（§7 NLP 管線）────────────────────────────────────

@pytest.mark.parametrize("text", ["好", "嗯", "OK", "不同意", "核能", "54"])
def test_short_or_blocked_text_is_not_substantive(text):
    assert is_substantive_message(text) is False


def test_real_argument_is_substantive():
    assert is_substantive_message("核廢料最終處置場址三十年沒有選出來") is True


def test_is_short_response_covers_whitelist_and_length():
    assert is_short_response("好的") is True
    assert is_short_response("嗯") is True
    assert is_short_response("我反對重啟") is False


# ── ai_turn_is_question：策略層優先，句尾問號為 fallback ────────────────

def test_perspective_flip_type_is_always_a_question():
    """C 型 = 視角翻轉型，結尾必為視角翻轉提問。"""
    assert ai_turn_is_question("結尾沒有問號。", "開新方向|C|拋出視角翻轉") is True


def test_deepening_type_is_never_a_question():
    """E 型 = 承接深化型，prompt 明寫「全程不拋問題」。"""
    assert ai_turn_is_question("你怎麼看？", "收斂|E|使用者釘住成本") is False


def test_falls_back_to_trailing_question_mark_for_other_types():
    assert ai_turn_is_question("那你會怎麼決定？", "收斂|A|直接論述") is True
    assert ai_turn_is_question("這是我的判斷。", "收斂|A|直接論述") is False


def test_falls_back_when_judgment_is_missing_or_malformed():
    assert ai_turn_is_question("你認為呢？", "") is True
    assert ai_turn_is_question("你認為呢？", "attempt 1 judgment 亂碼") is True
    assert ai_turn_is_question("我的看法是這樣。", "") is False


def test_half_width_question_mark_counts():
    assert ai_turn_is_question("So what would you do?", "") is True
