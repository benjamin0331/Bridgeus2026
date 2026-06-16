"""Unit tests for M3 infer_reasoning_mode (pure function, no DB).

The function itself lives in ai_agent.py (Benjamin's M3 implementation); these
tests lock in its branch behavior, including the Q10 opponent-view path.
"""

from apps.matching.services.ai_agent import infer_reasoning_mode


def test_extreme_stance_with_certainty_marker_is_polarized():
    # is_extreme (>=6) AND a polarized marker present → polarized.
    mode = infer_reasoning_mode(
        user_stance_score=7.0,
        user_initial_argument="我完全支持核電，這點毫無疑問",
    )
    assert mode == "polarized"


def test_moderate_stance_with_two_hedges_is_collaborative():
    # not extreme AND hedge_count >= 2 → collaborative.
    mode = infer_reasoning_mode(
        user_stance_score=4.5,
        user_initial_argument="雖然有風險，但我覺得可能利大於弊",
    )
    assert mode == "collaborative"


def test_long_opponent_view_without_certainty_is_collaborative():
    # Third branch: articulating the opposing view at length (>=50 chars), no
    # certainty markers, not extreme → collaborative (perspective-taking signal).
    opponent_view = (
        "反對方擔心核廢料處理與地震風險，認為再生能源更值得投資，"
        "也質疑核電廠除役成本被低估，這些顧慮我認為都站得住腳"
    )
    mode = infer_reasoning_mode(
        user_stance_score=4.0,
        user_initial_argument="核電有其價值",
        opponent_view_text=opponent_view,
    )
    assert mode == "collaborative"


def test_extreme_stance_without_certainty_marker_is_unknown():
    # is_extreme but no polarized marker → first branch misses; short text → unknown.
    mode = infer_reasoning_mode(
        user_stance_score=7.0,
        user_initial_argument="核電很好",
    )
    assert mode == "unknown"


def test_extreme_stance_blocks_hedge_collaborative_path():
    # Hedges present but stance is extreme → second/third branches require
    # not-extreme, so it falls through to unknown rather than collaborative.
    mode = infer_reasoning_mode(
        user_stance_score=1.0,
        user_initial_argument="雖然另一方面也許很複雜",
    )
    assert mode == "unknown"


def test_moderate_low_signal_is_unknown():
    mode = infer_reasoning_mode(
        user_stance_score=4.0,
        user_initial_argument="還在考慮",
    )
    assert mode == "unknown"


def test_q10_signal_combines_with_initial_argument_for_certainty():
    # Polarized marker can come from the Q10 text; combined with extreme stance → polarized.
    mode = infer_reasoning_mode(
        user_stance_score=2.0,
        user_initial_argument="核電不安全",
        opponent_view_text="支持方絕對是被利益綁架",
    )
    assert mode == "polarized"
