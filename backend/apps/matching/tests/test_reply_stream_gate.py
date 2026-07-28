"""Unit tests for ReplyStreamGate — the streaming output-contract gate.

The gate is the only thing standing between the model's <judgment> block and
the participant's screen. The frontend renders every chunk the moment it
arrives, so a leak cannot be repaired after the fact: these tests pin the
behaviour at chunk boundaries, which is where a naive implementation breaks.
"""

import pytest

from apps.matching.services.ai_agent import ReplyStreamGate

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
