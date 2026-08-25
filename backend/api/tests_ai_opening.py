"""
pytest tests for AI 開場（opening brief）— H-AI 與 H-H。

涵蓋：
  1. 錨點排序 fallback：依前測開放式作答挑出相關面向
  2. AI_OPENING_ENABLED=0 時兩邊都不生成
  3. H-AI：開場寫進 session history 第一則 agent 訊息、重複 POST 不重生
  4. H-H：一房一則、兩位參與者拿到同一則、房間 payload 帶回開場
  5. H-H 開場不得含任一方問卷原文（避免污染後測基線）

Run from backend/:
    pytest api/tests_ai_opening.py -v
"""
import json

import pytest
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.utils import timezone
from rest_framework.test import APIClient

from api.models import DialogueMatch, DialogueSessionRecord, UserStanceProfile
from apps.matching.services import opening as opening_service

User = get_user_model()

Q9_A = "我最擔心的是核廢料沒地方放，處置場一直找不到地方蓋。"
Q10_A = "支持的人會說核電是穩定的低碳基載電力。"
Q9_B = "我認為在再生能源補上來之前，核電是必要的橋接方案。"
Q10_B = "反對的人擔心地震帶上的事故風險。"


def _stub_llm(monkeypatch, payload: dict | None):
    """把 LLM 換成固定回應；payload=None 模擬呼叫失敗（走 fallback）。"""
    monkeypatch.setattr(
        opening_service,
        "_call_llm",
        lambda prompt: None if payload is None else json.dumps(payload),
    )


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear()
    yield
    cache.clear()


# ── 服務層 ─────────────────────────────────────────────────────────────


class TestOpeningService:
    def test_fallback_ranks_relevant_anchors_first(self):
        """作答提到核廢料處置，核廢處理應排在錨點原順序之前。"""
        ranked = opening_service._rank_anchors(102, [Q9_A])
        assert ranked[0]["id"] == "anchor_waste"

    def test_fallback_matches_near_synonyms(self):
        """「核廢料沒地方放」與錨點描述「核廢料處置」不同詞，仍要能對上。

        整詞比對在這裡會判定零命中，所以排序用的是字元 bigram 交集。
        """
        assert opening_service._bigrams("核廢料沒地方放") & opening_service._bigrams(
            "核廢料處置、最終儲存"
        )

    def test_disabled_returns_none(self, monkeypatch):
        monkeypatch.setenv("AI_OPENING_ENABLED", "0")
        assert opening_service.build_ai_opening(topic_id=102, q9=Q9_A, q10=Q10_A) is None
        assert (
            opening_service.build_match_opening(
                topic_id=102,
                participant_a={"q9": Q9_A, "q10": Q10_A},
                participant_b={"q9": Q9_B, "q10": Q10_B},
            )
            is None
        )

    def test_llm_failure_falls_back(self, monkeypatch):
        _stub_llm(monkeypatch, None)
        result = opening_service.build_ai_opening(topic_id=102, q9=Q9_A, q10=Q10_A)
        assert result["source"] == "fallback"
        assert len(result["directions"]) == opening_service.DIRECTION_COUNT
        assert result["text"]

    def test_llm_payload_is_used(self, monkeypatch):
        _stub_llm(
            monkeypatch,
            {
                "greeting": "你提到處置場的問題，我們就從那裡開始。",
                "directions": [{"title": "最終處置的可行性", "detail": "選址卡在哪裡？"}],
            },
        )
        result = opening_service.build_ai_opening(topic_id=102, q9=Q9_A, q10=Q10_A)
        assert result["source"] == "llm"
        assert "處置場" in result["text"]
        assert result["directions"][0]["title"] == "最終處置的可行性"

    def test_malformed_llm_output_falls_back(self, monkeypatch):
        monkeypatch.setattr(opening_service, "_call_llm", lambda prompt: "好的，這是開場：")
        result = opening_service.build_ai_opening(topic_id=102, q9=Q9_A, q10=Q10_A)
        assert result["source"] == "fallback"

    def test_match_fallback_does_not_quote_survey_text(self, monkeypatch):
        """H-H 開場不得出現任一方的問卷原文——那是後測要比對的基線。"""
        _stub_llm(monkeypatch, None)
        result = opening_service.build_match_opening(
            topic_id=102,
            participant_a={"q9": Q9_A, "q10": Q10_A},
            participant_b={"q9": Q9_B, "q10": Q10_B},
        )
        for sentence in (Q9_A, Q10_A, Q9_B, Q10_B):
            assert sentence not in result["text"]


# ── H-AI 端點 ──────────────────────────────────────────────────────────


@pytest.fixture
def ai_session(db):
    user = User.objects.create_user(username="opening_ai_user", password="pass1234!")
    DialogueSessionRecord.objects.create(
        user=user,
        session_id="openingsession1",
        topic_id=102,
        topic_title="台灣核能議題討論",
        collection_name="nuclear_energy_all",
        survey_context={"survey_open_answers": {"Q9": Q9_A, "Q10": Q10_A}},
        session_state={
            "topic": "台灣核能議題討論",
            "user_stance_label": "較反對核電",
            "user_stance_score": 2.5,
            "history": [],
        },
        last_activity_at=timezone.now(),
    )
    client = APIClient()
    client.force_authenticate(user=user)
    return client, user


@pytest.mark.django_db
class TestAiSessionOpening:
    def test_opening_becomes_first_agent_message(self, ai_session, monkeypatch):
        client, user = ai_session
        _stub_llm(monkeypatch, None)

        response = client.post("/api/dialogue/sessions/openingsession1/opening/")
        assert response.status_code == 200, response.data
        assert response.data["opening"]["source"] == "fallback"
        assert response.data["history"][0]["role"] == "agent"

        record = DialogueSessionRecord.objects.get(session_id="openingsession1")
        history = record.session_state["history"]
        assert len(history) == 1
        assert history[0]["role"] == "agent"
        assert history[0]["content"] == response.data["opening"]["content"]

    def test_second_post_does_not_regenerate(self, ai_session, monkeypatch):
        client, _ = ai_session
        _stub_llm(monkeypatch, None)

        first = client.post("/api/dialogue/sessions/openingsession1/opening/")
        second = client.post("/api/dialogue/sessions/openingsession1/opening/")

        assert second.status_code == 200
        assert second.data["opening"]["source"] == "existing"
        assert second.data["opening"]["content"] == first.data["opening"]["content"]
        record = DialogueSessionRecord.objects.get(session_id="openingsession1")
        assert len(record.session_state["history"]) == 1

    def test_disabled_returns_null_opening(self, ai_session, monkeypatch):
        client, _ = ai_session
        monkeypatch.setenv("AI_OPENING_ENABLED", "0")

        response = client.post("/api/dialogue/sessions/openingsession1/opening/")
        assert response.status_code == 200
        assert response.data["opening"] is None
        record = DialogueSessionRecord.objects.get(session_id="openingsession1")
        assert record.session_state["history"] == []

    def test_unknown_session_is_404(self, ai_session):
        client, _ = ai_session
        response = client.post("/api/dialogue/sessions/nosuchsession/opening/")
        assert response.status_code == 404

    def test_opening_lets_a_short_reply_through_the_input_gate(
        self, ai_session, monkeypatch
    ):
        """開場邀請對方挑一個方向，接著回「第二個」不該被當成低訊息量輸入。

        開場沒有對應的 AIConversation turn，所以 input gate 規則 5 用的
        `prev_ai_is_question` 必須另外看 history 第一則是不是 agent。
        """
        from api.views import _previous_ai_turn_is_question
        from apps.matching.services.input_gate import InputVerdict, classify

        client, user = ai_session
        _stub_llm(monkeypatch, None)

        assert not _previous_ai_turn_is_question(
            user_id=user.id, session_id="openingsession1"
        )
        assert classify("第二個") is InputVerdict.LOW_INFORMATION

        client.post("/api/dialogue/sessions/openingsession1/opening/")

        assert _previous_ai_turn_is_question(
            user_id=user.id, session_id="openingsession1"
        )
        assert (
            classify("第二個", prev_ai_is_question=True) is InputVerdict.VALID
        )


# ── H-H 端點 ───────────────────────────────────────────────────────────


@pytest.fixture
def match_room(db):
    user_a = User.objects.create_user(username="opening_hh_a", password="pass1234!")
    user_b = User.objects.create_user(username="opening_hh_b", password="pass1234!")
    for user, q9, q10, score, category in (
        (user_a, Q9_A, Q10_A, 2.5, "oppose"),
        (user_b, Q9_B, Q10_B, 6.0, "support"),
    ):
        UserStanceProfile.objects.create(
            user=user,
            topic_id=102,
            stance_score=score,
            stance_category=category,
            survey_open_answers={"Q9": q9, "Q10": q10},
        )
    match = DialogueMatch.objects.create(
        topic_id=102,
        user_a=user_a,
        user_b=user_b,
        user_a_score=2.5,
        user_b_score=6.0,
        room_id="openingroom1",
    )
    return match, user_a, user_b


def _client_for(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


@pytest.mark.django_db
class TestMatchRoomOpening:
    def test_creates_one_brief_shared_by_both(self, match_room, monkeypatch):
        match, user_a, user_b = match_room
        _stub_llm(monkeypatch, None)

        first = _client_for(user_a).post("/api/matching/rooms/openingroom1/opening/")
        assert first.status_code == 200, first.data
        assert first.data["opening"]["source"] == "fallback"

        second = _client_for(user_b).post("/api/matching/rooms/openingroom1/opening/")
        assert second.data["opening"]["content"] == first.data["opening"]["content"]
        assert match.__class__.objects.get(id=match.id).opening_brief

    def test_room_messages_payload_carries_opening(self, match_room, monkeypatch):
        match, user_a, user_b = match_room
        _stub_llm(monkeypatch, None)
        client_a = _client_for(user_a)

        before = client_a.get("/api/matching/rooms/openingroom1/messages/")
        assert before.data["opening"] is None

        client_a.post("/api/matching/rooms/openingroom1/opening/")

        after = _client_for(user_b).get("/api/matching/rooms/openingroom1/messages/")
        assert after.data["opening"]["content"]
        assert after.data["opening"]["source"] == "fallback"

    def test_concurrent_post_reports_pending_instead_of_second_generation(
        self, match_room, monkeypatch
    ):
        """鎖被別人拿走時回 pending=True，不重複呼叫 LLM。"""
        match, user_a, _ = match_room
        _stub_llm(monkeypatch, None)
        cache.add(f"match_opening_lock:{match.id}", "1", timeout=90)

        response = _client_for(user_a).post("/api/matching/rooms/openingroom1/opening/")
        assert response.status_code == 200
        assert response.data == {"opening": None, "pending": True}

    def test_disabled_leaves_no_brief(self, match_room, monkeypatch):
        _, user_a, _ = match_room
        monkeypatch.setenv("AI_OPENING_ENABLED", "0")

        response = _client_for(user_a).post("/api/matching/rooms/openingroom1/opening/")
        assert response.data["opening"] is None
        assert response.data["pending"] is False

    def test_outsider_gets_404(self, match_room):
        outsider = User.objects.create_user(username="opening_outsider")
        response = _client_for(outsider).post(
            "/api/matching/rooms/openingroom1/opening/"
        )
        assert response.status_code == 404
