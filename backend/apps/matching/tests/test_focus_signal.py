"""
pytest tests for detect_focus_signal() and DialogueSession focus signal integration.

These tests are pure unit tests — no Django DB, no LLM, no embedding model.
Run from backend/:
    pytest apps/matching/tests/test_focus_signal.py -v
"""

import pytest

from apps.matching.services.ai_agent import (
    DialogueSession,
    detect_focus_signal,
    infer_reasoning_mode,
)


# ─────────────────────────────────────────────────────────────────────────────
# detect_focus_signal — positive cases
# ─────────────────────────────────────────────────────────────────────────────

class TestDetectFocusSignalPositive:
    """每個 case 都應 return True（命中至少一條規則）。"""

    def test_rejection_of_hypothetical_decision_maker(self):
        # Rule 2: 拒絕假設 — 不是決策者 + 不做 + 假設
        assert detect_focus_signal("我不是決策者，不做此假設") is True

    def test_rejection_of_hypothetical_assumption(self):
        # Rule 2: 拒絕假設 — 我不是 + 假設
        assert detect_focus_signal("我不是在做假設，我說的是現實狀況") is True

    def test_explicit_anchor_with_followup(self):
        # Rule 3: 明確指認 — 我想表示
        assert detect_focus_signal("我想表示的是這個，那麼？") is True

    def test_explicit_anchor_just_is_this(self):
        # Rule 3: 明確指認 — 就是這個
        assert detect_focus_signal("對，就是這個，這才是核心問題") is True

    def test_premise_anchoring(self):
        # Caught via hardcoded "先說清楚" in _FOCUS_EXPLICIT (not a generalizable
        # scope-narrowing rule — see TODO Rule 4 in detect_focus_signal docstring).
        assert detect_focus_signal("先說清楚是前提，不然討論沒有意義") is True

    def test_confirmation_seeking_bu_shi_ma(self):
        # Rule 1: 確認尋求 — 不是嗎
        assert detect_focus_signal("對，台灣就是困在這裡不是嗎？") is True

    def test_confirmation_seeking_dui_ba(self):
        # Rule 1: 確認尋求 — 對吧
        assert detect_focus_signal("缺電問題根本是產業問題，對吧？") is True

    def test_confirmation_seeking_shi_ma(self):
        # Rule 1: 確認尋求 — 是嗎
        assert detect_focus_signal("你說的成本優勢其實也有前提，是嗎？") is True

    def test_explicit_my_point_is(self):
        # Rule 3: 明確指認 — 我的重點是
        assert detect_focus_signal("我的重點是核廢料才是真正的問題，其他都是次要的") is True

    def test_rejection_bu_zuo_jia_she(self):
        # Rule 2: 拒絕假設 — 不做 + 假設
        assert detect_focus_signal("我不做這個假設，請用實際情境來討論") is True


# ─────────────────────────────────────────────────────────────────────────────
# detect_focus_signal — negative cases
# ─────────────────────────────────────────────────────────────────────────────

class TestDetectFocusSignalNegative:
    """每個 case 都應 return False（一般論述或開啟新方向的提問）。"""

    def test_general_policy_statement(self):
        # 一般論述句，無確認/拒絕/指認
        assert detect_focus_signal("我認為台灣的能源政策需要從多個角度考量") is False

    def test_open_new_direction_question(self):
        # 開啟新方向的提問，不是聚焦信號
        assert detect_focus_signal("核能在經濟上有哪些具體優勢？") is False

    def test_topic_redirect_question(self):
        # 開啟新話題，不是確認
        assert detect_focus_signal("那德國廢核的經驗你怎麼看？") is False

    def test_factual_assertion(self):
        # 事實陳述，沒有確認尋求
        assert detect_focus_signal("台灣目前的備用容量率大約是 15%") is False

    def test_new_question_with_ni_juede(self):
        # 含「你覺得」但非「你覺得呢」（連續字串不match）
        assert detect_focus_signal("你覺得核廢料最終處置場要放哪裡比較好？") is False

    def test_genuine_exploration(self):
        # 邀請新探索，非確認舊論點
        assert detect_focus_signal("如果台灣的地震風險可以透過技術降低，情況會不會不同？") is False

    def test_pure_scope_narrowing_no_markers(self):
        # 收窄語義，但無任何 marker → 預期 false negative（Rule 4 TODO）
        # 這句話語義上是聚焦信號，但三條規則無法捕捉
        assert detect_focus_signal("我只是在說核廢料這一件事") is False


# ─────────────────────────────────────────────────────────────────────────────
# DialogueSession integration — focus_signal_count & effective_reasoning_mode
# ─────────────────────────────────────────────────────────────────────────────

class TestDialogueSessionFocusSignalIntegration:

    def test_initial_count_is_zero(self):
        session = DialogueSession()
        assert session.focus_signal_count == 0

    def test_effective_mode_unknown_below_threshold(self):
        session = DialogueSession(user_reasoning_mode="unknown", focus_signal_count=1)
        assert session.effective_reasoning_mode == "unknown"

    def test_effective_mode_upgrades_at_threshold(self):
        session = DialogueSession(user_reasoning_mode="unknown", focus_signal_count=2)
        assert session.effective_reasoning_mode == "collaborative"

    def test_effective_mode_upgrades_above_threshold(self):
        session = DialogueSession(user_reasoning_mode="unknown", focus_signal_count=5)
        assert session.effective_reasoning_mode == "collaborative"

    def test_polarized_mode_not_affected_by_count(self):
        # polarized 模式不受 focus_signal_count 影響
        session = DialogueSession(user_reasoning_mode="polarized", focus_signal_count=10)
        assert session.effective_reasoning_mode == "polarized"

    def test_collaborative_mode_not_affected_by_count(self):
        # 已是 collaborative，count 不改變它
        session = DialogueSession(user_reasoning_mode="collaborative", focus_signal_count=0)
        assert session.effective_reasoning_mode == "collaborative"

    def test_serialization_roundtrip_preserves_count(self):
        session = DialogueSession(user_reasoning_mode="unknown", focus_signal_count=2)
        restored = DialogueSession.from_dict(session.to_dict())
        assert restored.focus_signal_count == 2
        assert restored.user_reasoning_mode == "unknown"
        assert restored.effective_reasoning_mode == "collaborative"

    def test_lock_on_upgrade(self):
        # 升級那輪直接寫死 user_reasoning_mode，不再靠 property 即時推導
        # 模擬 consumers._stream_response 的鎖定邏輯
        session = DialogueSession(user_reasoning_mode="unknown", focus_signal_count=1)
        # 第 2 次 focus signal 到達，鎖定
        session.focus_signal_count += 1
        if session.focus_signal_count >= 2:
            session.user_reasoning_mode = "collaborative"
        assert session.user_reasoning_mode == "collaborative"
        # 序列化後 mode 是 collaborative，不再是 unknown
        restored = DialogueSession.from_dict(session.to_dict())
        assert restored.user_reasoning_mode == "collaborative"
        assert restored.effective_reasoning_mode == "collaborative"

    def test_serialization_missing_count_defaults_to_zero(self):
        # 舊版 session dict 沒有 focus_signal_count，應向後兼容
        old_dict = DialogueSession(user_reasoning_mode="unknown").to_dict()
        del old_dict["focus_signal_count"]
        restored = DialogueSession.from_dict(old_dict)
        assert restored.focus_signal_count == 0


# ─────────────────────────────────────────────────────────────────────────────
# infer_reasoning_mode — _POLARIZED_MARKERS regression tests
# ─────────────────────────────────────────────────────────────────────────────

class TestInferReasoningModeMarkers:
    """確認 "根本不" 移除後不誤判，且其他確定性 marker 仍正常觸發 polarized。"""

    def test_genben_bu_does_not_trigger_polarized(self):
        # "根本不" 是強調副詞，不應在 score=1.5 的強反核使用者造成 polarized 誤判
        # 典型句式：「核廢料根本不是問題」「政府根本不重視安全」
        result = infer_reasoning_mode(
            user_stance_score=1.5,
            user_initial_argument="核廢料根本不是問題，政府根本不重視安全。",
        )
        assert result != "polarized", (
            '"根本不" should not trigger polarized; got polarized which would lock '
            "the session and block focus-signal detection for the entire dialogue."
        )

    def test_jue_dui_still_triggers_polarized(self):
        # 移除 "根本不" 後，真正的確定性 marker（「絕對」）仍應觸發 polarized
        result = infer_reasoning_mode(
            user_stance_score=1.5,
            user_initial_argument="我絕對反對重啟核電，這是不可動搖的立場。",
        )
        assert result == "polarized", (
            '"絕對" is a genuine certainty marker and should still produce polarized.'
        )

    def test_bu_ke_neng_jie_shou_still_triggers_polarized(self):
        # 另一個確定性 marker「不可能接受」+ 極端分數 → 仍 polarized
        result = infer_reasoning_mode(
            user_stance_score=1.5,
            user_initial_argument="核電的風險我不可能接受，任何理由都不夠。",
        )
        assert result == "polarized"
