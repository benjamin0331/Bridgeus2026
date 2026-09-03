"""Unit tests for ReplyStreamGate — the streaming output-contract gate.

The gate is the only thing standing between the model's <judgment> block and
the participant's screen. The frontend renders every chunk the moment it
arrives, so a leak cannot be repaired after the fact: these tests pin the
behaviour at chunk boundaries, which is where a naive implementation breaks.
"""

import pytest

from apps.matching.services.ai_agent import (
    _JUDGMENT_LEAK_PATTERNS,
    ReplyStreamGate,
    detect_judgment_leak,
    salvage_reply,
)

_JUDGMENT_BODY = (
    "使用者在收斂到成本這個子問題。判定為「收斂」,"
    "強制使用承接深化型(E)。"
)
EXPECTED_REPLY = "核電的優勢是低碳穩定,不是便宜。"

# Current production shape: the model rejects assistant prefill, so the stream
# carries the full <judgment>…</judgment> tag pair.
PAYLOAD_WITH_OPEN_TAG = (
    f"<judgment>{_JUDGMENT_BODY}</judgment><reply>{EXPECTED_REPLY}</reply>"
)
# Shape if prefill ever becomes available: the stream starts inside the block.
PAYLOAD_WITHOUT_OPEN_TAG = (
    f"{_JUDGMENT_BODY}</judgment><reply>{EXPECTED_REPLY}</reply>"
)
# The reason the gate is two-stage: 第十節 of the system prompt contains the
# literal "<reply>" string, so the model can mimic it inside its judgment. A
# gate that opens on the first "<reply>" would start streaming from here on.
MIMICRY_SENTENCE = "本輪 <reply> 只寫 3 句,結尾不拋問題。"
PAYLOAD_WITH_MIMICRY = (
    f"<judgment>{_JUDGMENT_BODY}{MIMICRY_SENTENCE}</judgment>"
    f"<reply>{EXPECTED_REPLY}</reply>"
)
PAYLOADS = [
    PAYLOAD_WITH_OPEN_TAG,
    PAYLOAD_WITHOUT_OPEN_TAG,
    PAYLOAD_WITH_MIMICRY,
]

FORBIDDEN = [
    "判定為",
    "承接深化型",
    "<judgment>",
    "</judgment>",
    "<reply>",
    "</reply>",
]

CHUNK_SIZES = [1, 2, 3, 5, 7, 13, 1000]

shapes_and_sizes = pytest.mark.parametrize(
    ("payload", "chunk_size"),
    [(payload, size) for payload in PAYLOADS for size in CHUNK_SIZES],
)


def _drain(payload: str, chunk_size: int) -> tuple[ReplyStreamGate, str]:
    """Feed `payload` through a gate in fixed-size chunks; return (gate, emitted)."""
    gate = ReplyStreamGate()
    emitted = ""
    for i in range(0, len(payload), chunk_size):
        emitted += gate.feed(payload[i:i + chunk_size])
    tail, ok = gate.finish()
    emitted += tail
    assert ok is True
    return gate, emitted


@shapes_and_sizes
def test_gate_emits_only_reply_body(payload, chunk_size):
    _, emitted = _drain(payload, chunk_size)
    assert emitted == EXPECTED_REPLY


@shapes_and_sizes
def test_gate_emission_contains_no_judgment_language(payload, chunk_size):
    _, emitted = _drain(payload, chunk_size)
    for token in FORBIDDEN:
        assert token not in emitted


@shapes_and_sizes
def test_gate_reply_property_matches_emission(payload, chunk_size):
    gate, emitted = _drain(payload, chunk_size)
    assert gate.reply == emitted == EXPECTED_REPLY


@shapes_and_sizes
def test_gate_captures_judgment_separately(payload, chunk_size):
    gate, _ = _drain(payload, chunk_size)
    assert gate.judgment
    assert "判定為" in gate.judgment
    assert "<judgment>" not in gate.judgment
    assert "</judgment>" not in gate.judgment


@shapes_and_sizes
def test_gate_never_emits_partial_closing_tag(payload, chunk_size):
    """A gate that forgets to hold back the tail leaks `</rep` before closing.

    The reply body is plain Chinese prose, so any '<' or '/' in an emission can
    only have come from a tag fragment.
    """
    gate = ReplyStreamGate()
    for i in range(0, len(payload), chunk_size):
        visible = gate.feed(payload[i:i + chunk_size])
        assert "<" not in visible
        assert "/" not in visible
    tail, ok = gate.finish()
    assert ok is True
    assert "<" not in tail


@pytest.mark.parametrize("chunk_size", CHUNK_SIZES)
def test_mimicked_reply_tag_inside_judgment_does_not_open_the_gate(chunk_size):
    """The model writing "<reply>" inside its judgment must not open the gate.

    Single-stage gating (find the first "<reply>") leaks every judgment word
    after the mimicked tag; two-stage gating waits for "</judgment>" first.
    """
    gate, emitted = _drain(PAYLOAD_WITH_MIMICRY, chunk_size)

    assert emitted == EXPECTED_REPLY
    assert gate.reply == EXPECTED_REPLY

    # Nothing from the mimicry sentence may appear in the visible stream.
    assert MIMICRY_SENTENCE not in emitted
    for fragment in ["本輪", "只寫 3 句", "結尾不拋問題", "判定為", "承接深化型"]:
        assert fragment not in emitted

    # It stays where it belongs: the research-only judgment column.
    assert "本輪" in gate.judgment
    assert "判定為" in gate.judgment


def test_judgment_never_closed_fails_closed_even_with_reply_tag():
    """A stream that mimics <reply> but never closes the judgment block must
    still fail closed — the gate must not fall back to single-stage behaviour."""
    gate = ReplyStreamGate()
    emitted = ""
    for char in "<judgment>判定為「收斂」。本輪 <reply> 只寫 3 句,不拋問題。":
        emitted += gate.feed(char)
    tail, ok = gate.finish()
    assert ok is False
    assert emitted == ""
    assert tail == ""
    assert gate.reply == ""
    # Diagnostics still capture the whole thing for the ERROR log.
    assert "本輪" in gate.buffered_preview


def test_gate_fails_closed_when_reply_never_opens():
    gate = ReplyStreamGate()
    emitted = ""
    for char in "<judgment>判定為「收斂」,強制使用承接深化型(E)。我覺得核電還行。":
        emitted += gate.feed(char)
    tail, ok = gate.finish()
    assert ok is False
    assert emitted == ""
    assert tail == ""
    assert gate.reply == ""


def test_gate_fails_closed_on_empty_stream():
    gate = ReplyStreamGate()
    tail, ok = gate.finish()
    assert ok is False
    assert tail == ""


def test_gate_suppresses_trailing_content_after_close():
    payload = (
        "<judgment>判定完成。</judgment><reply>正文。</reply>"
        "補充:本輪使用結構 E,因為使用者在收斂。"
    )
    gate = ReplyStreamGate()
    emitted = ""
    for char in payload:
        emitted += gate.feed(char)
    tail, ok = gate.finish()
    emitted += tail
    assert ok is True
    assert emitted == "正文。"
    assert "結構 E" not in emitted
    assert gate.reply == "正文。"


def test_gate_handles_unclosed_reply_as_contract_met():
    """Truncated output (max_tokens hit) still counts as opened: the participant
    already saw the partial reply, so suppressing it retroactively is pointless.
    What matters is that the judgment block never escaped."""
    gate = ReplyStreamGate()
    emitted = ""
    for char in "<judgment>判定。</judgment><reply>核電的優勢是低碳":
        emitted += gate.feed(char)
    tail, ok = gate.finish()
    emitted += tail
    assert ok is True
    assert emitted == "核電的優勢是低碳"
    assert "判定" not in emitted


def test_gate_ignores_content_fed_after_close():
    gate = ReplyStreamGate()
    gate.feed("<judgment>j</judgment><reply>正文。</reply>")
    assert gate.feed("洩漏的尾巴") == ""
    assert gate.reply == "正文。"


def test_gate_holds_back_short_buffer_until_more_arrives():
    """With fewer than len('</reply>')-1 chars buffered, nothing is emitted yet."""
    gate = ReplyStreamGate()
    gate.feed("<judgment>j</judgment><reply>")
    assert gate.feed("核電") == ""      # 2 chars < 7-char hold-back
    tail, ok = gate.finish()
    assert ok is True
    assert tail == "核電"


# ═══════════════════════════════════════════════════════════
# In-reply judgment language (second leak shape)
#
# The tag boundary holds, but the model writes judgment vocabulary *inside*
# <reply>. Tag parsing cannot see this; only the keyword check can.
# ═══════════════════════════════════════════════════════════

# The reported case, verbatim from the participant's screen.
REPORTED_LEAK_REPLY = (
    "內部判斷：使用者這一輪是在開啟新方向——提供了一段具體的政策現況背景，"
    "等待我回應。 這個現況其實說明了一件事：台灣的核電政策一直在「政治可行性」"
    "和「現實需求」之間拉扯，但核二、核三被評估為「具再運轉可行性」，並不等於"
    "「安全無虞可以直接重啟」——這只是技術評估的第一步，後面還有自主安全檢查、"
    "再運轉計畫審查，以及最根本的問題：核廢料的最終處置場址至今仍沒有著落。 "
    "政策轉彎本身不能替核廢料問題解套。"
)
REPORTED_LEAK_SALVAGED_PREFIX = "這個現況其實說明了一件事"


def _wrap(reply: str) -> str:
    return f"<judgment>收斂|E|測試</judgment><reply>{reply}</reply>"


def test_reported_in_reply_leak_is_rejected():
    gate = ReplyStreamGate()
    gate.feed(_wrap(REPORTED_LEAK_REPLY))
    tail, ok = gate.finish()

    assert ok is False
    assert tail == ""                       # nothing emitted even on the tail
    assert gate.leak_pattern == "內部判斷"    # first pattern in the text
    assert gate.reply == REPORTED_LEAK_REPLY  # kept intact for salvage


def test_type_name_in_reply_is_rejected():
    gate = ReplyStreamGate()
    gate.feed(_wrap("這一輪我採用承接深化型,直接給你我的判斷:核廢料處置確實無解。"))
    _, ok = gate.finish()
    assert ok is False
    assert gate.leak_pattern == "強制使用" or gate.leak_pattern == "承接深化型"


@pytest.mark.parametrize("pattern", _JUDGMENT_LEAK_PATTERNS)
def test_every_leak_pattern_is_detected(pattern):
    """Each entry in the list must actually be caught end-to-end."""
    gate = ReplyStreamGate()
    gate.feed(_wrap(f"核電的優勢是低碳穩定。{pattern}。這是正文的其他部分。"))
    tail, ok = gate.finish()

    assert ok is False
    assert tail == ""
    assert gate.leak_pattern is not None
    assert gate.leak_pattern in pattern or pattern in gate.leak_pattern


@pytest.mark.parametrize("pattern", _JUDGMENT_LEAK_PATTERNS)
def test_detect_judgment_leak_returns_the_hit(pattern):
    assert detect_judgment_leak(f"前綴{pattern}後綴") is not None


def test_clean_reply_passes():
    gate = ReplyStreamGate()
    gate.feed(_wrap(EXPECTED_REPLY))
    tail, ok = gate.finish()
    assert ok is True
    assert gate.leak_pattern is None
    assert tail == ""       # already emitted during feed
    assert gate.reply == EXPECTED_REPLY


# ── 5b: false-positive regression corpus ────────────────────────────────────

def _load_live_reply_samples():
    import json
    from pathlib import Path

    path = Path(__file__).resolve().parent / "fixtures" / "live_reply_samples.json"
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)["samples"]


# Captured before the live corpus existed. Kept in code because the JSON file is
# regenerated on every live run — these three must survive that.
SEED_REPLY_SAMPLES = [
    {
        "source": "api_aiconversation id=1 (2026-06-24)",
        "reply": (
            "好，這場對話就到這裡。\n\n"
            "你對核電的疑慮是真實的，核廢料處理和安全風險確實是支持核電的一方"
            "必須正面面對的問題，不是能輕易帶過的。我的立場是核電在台灣當前的"
            "能源轉型中仍有其位置，但這不代表反對核電的考量沒有道理。\n\n"
            "如果之後有機會繼續討論，歡迎再來。"
        ),
    },
    {
        "source": "prefill probe 2026-08-01",
        "reply": (
            "核電的成本問題很複雜：建造成本高且工期長，但運營後的邊際發電成本"
            "相對低廉，這兩個數字常常被分開引用來支持不同結論。"
        ),
    },
    {
        "source": "salvageable body of the reported in-reply leak",
        "reply": (
            "這個現況其實說明了一件事：台灣的核電政策一直在「政治可行性」和"
            "「現實需求」之間拉扯，但核二、核三被評估為「具再運轉可行性」，"
            "並不等於「安全無虞可以直接重啟」——這只是技術評估的第一步，"
            "後面還有自主安全檢查、再運轉計畫審查，以及最根本的問題："
            "核廢料的最終處置場址至今仍沒有著落。 政策轉彎本身不能替核廢料"
            "問題解套。"
        ),
    },
]

LIVE_REPLY_SAMPLES = SEED_REPLY_SAMPLES + _load_live_reply_samples()


def test_live_reply_corpus_is_not_empty():
    """Guards against the corpus silently emptying and the check below passing
    for the wrong reason."""
    assert len(LIVE_REPLY_SAMPLES) >= 25


@pytest.mark.parametrize(
    "sample",
    LIVE_REPLY_SAMPLES,
    ids=[s["source"] for s in LIVE_REPLY_SAMPLES],
)
def test_no_false_positive_on_real_replies(sample):
    """Real replies participants legitimately saw must never be blocked.

    A false positive is worse than the bug: it turns a good turn into an error
    message. If this fails, report the pattern and the sentence — do not widen
    the sentence or narrow the pattern list without deciding that deliberately.
    """
    hit = detect_judgment_leak(sample["reply"])
    assert hit is None, (
        f"false positive: pattern {hit!r} matched a legitimate reply from "
        f"{sample['source']!r}\n---\n{sample['reply']}"
    )


def test_no_false_positive_on_ordinary_nuclear_debate_prose():
    """Hand-written sentences that sit close to the pattern vocabulary without
    being judgment language."""
    benign = [
        "核電的成本要看你把除役和最終處置算不算進去。",
        "你判斷的依據是什麼？我想知道你用哪些數字。",
        "這個決定本輪立法院會期應該不會處理完。",
        "使用者付費的原則在電價上其實一直沒有落實。",
        "德國的案例說明了一件事：廢核之後的替代方案必須先到位。",
        "我承認我的立場有弱點，核廢料的最終處置確實還沒有解答。",
        "我們可以換個方向討論，談談電網韌性。",
        "深化能源轉型的討論比爭論單一技術更有意義。",
    ]
    for sentence in benign:
        assert detect_judgment_leak(sentence) is None, sentence


# ── 5c: salvage ─────────────────────────────────────────────────────────────

def test_salvage_recovers_body_after_judgment_sentence():
    salvaged = salvage_reply(REPORTED_LEAK_REPLY)

    assert salvaged is not None
    assert salvaged.startswith(REPORTED_LEAK_SALVAGED_PREFIX)
    assert detect_judgment_leak(salvaged) is None
    assert len(salvaged) >= 40
    assert "內部判斷" not in salvaged
    assert "使用者這一輪" not in salvaged
    assert "開啟新方向" not in salvaged


def test_salvage_returns_none_when_everything_is_judgment():
    text = (
        "內部判斷：使用者這一輪是在開啟新方向。"
        "本輪強制使用承接深化型(E)。判定為「收斂」。"
    )
    assert salvage_reply(text) is None


def test_salvage_returns_none_when_remainder_too_short():
    assert salvage_reply("內部判斷：這是判定。太短了。") is None


def test_salvage_returns_none_without_sentence_boundary():
    assert salvage_reply("內部判斷：使用者這一輪是在開啟新方向而且沒有句號") is None


def test_salvage_leaves_clean_text_recoverable():
    """A clean reply is never fed to salvage in production, but if it were, the
    function must not corrupt it into something misleading."""
    salvaged = salvage_reply(
        "核電的優勢是低碳穩定，不是便宜。"
        "真正的爭點在於核廢料的最終處置場址至今仍沒有著落，"
        "而這件事不會因為機組本身通過安全檢查就自動獲得解決。"
    )
    assert salvaged is not None
    assert salvaged.startswith("真正的爭點")
    assert detect_judgment_leak(salvaged) is None


# ── 開頭空白 ──────────────────────────────────────────────────────────
#
# prompt 第十節示範的格式把回應寫在 <reply> 的下一行,模型照做,所以開閘後
# 第一個字元通常是 "\n"。前端逐 chunk 原樣渲染,那個換行就成了畫面上的空行。


def _drain_chunks(chunks: list[str]) -> str:
    gate = ReplyStreamGate()
    out = "".join(gate.feed(chunk) for chunk in chunks)
    tail, ok = gate.finish()
    assert ok
    return out + tail


def test_newline_after_reply_tag_is_not_pushed_to_the_frontend():
    assert _drain_chunks([
        f"<judgment>{_JUDGMENT_BODY}</judgment>\n<reply>\n核電的爭點在核廢處置。",
        "</reply>",
    ]) == "核電的爭點在核廢處置。"


def test_leading_whitespace_is_stripped_across_a_chunk_boundary():
    """The tag and the whitespace after it can arrive in separate chunks."""
    assert _drain_chunks([
        f"<judgment>{_JUDGMENT_BODY}</judgment>",
        "<reply>",
        "\n",
        "  核電的爭點在核廢處置。",
        "</reply>",
    ]) == "核電的爭點在核廢處置。"


def test_blank_lines_inside_the_reply_body_are_preserved():
    """Only the opening whitespace is noise; paragraph breaks are intent."""
    assert _drain_chunks([
        f"<judgment>{_JUDGMENT_BODY}</judgment><reply>\n第一段。\n\n第二段。</reply>",
    ]) == "第一段。\n\n第二段。"


def test_leading_whitespace_is_stripped_when_the_body_only_arrives_at_finish():
    """A reply short enough to stay inside the </reply> lookahead buffer never
    passes through feed()'s emit path — finish() has to strip it too."""
    assert _drain_chunks([f"<judgment>{_JUDGMENT_BODY}</judgment><reply>\n短"]) == "短"
