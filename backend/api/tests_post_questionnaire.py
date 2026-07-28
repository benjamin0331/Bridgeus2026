"""
pytest tests for POST /api/post-questionnaire/ and PATCH …/consent/

Scenarios:
  1. Normal H-AI submission — all fields valid, discomfort_flag=False
  2. H-H submission — opponent_judgment must be null, C4 not validated
  3. Reverse scoring — C1-2, C1-6, C1-7, C1-8 are 8-raw; s_post correct
  4. Withdrawal — consent_confirmed=False marks the record accordingly

Run from backend/:
    pytest api/tests_post_questionnaire.py -v
"""
import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APIClient

from api.models import DialogueMatch, DialogueSessionRecord

User = get_user_model()

BASE_C1 = {
    "post_likert_1": 6,
    "post_likert_2": 3,  # reverse → 8-3=5
    "post_likert_3": 4,
    "post_likert_4": 5,
    "post_likert_5": 6,
    "post_likert_6": 2,  # reverse → 8-2=6
    "post_likert_7": 3,  # reverse → 8-3=5
    "post_likert_8": 4,  # reverse → 8-4=4
}
BASE_C2 = {
    "exp_stance_change_1": 4,
    "exp_stance_change_2": 5,
    "exp_quality_1": 6,
    "exp_quality_2": 5,
    "exp_reflection_1": 3,
    "exp_reflection_2": 4,
}
BASE_C3 = {
    "ccnd_attention": 5,
    "ccnd_awareness": 4,
    "ccnd_influence": 3,
}
BASE_D = {
    "post_open_comprehension": "這是一段超過五十個字的測試文字，用來驗證 D1 的最低字數限制是否正確運作。" * 2,
    "post_open_feedback": "對話體驗良好。",
}
BASE_E = {
    "discomfort_flag": False,
}


def _make_ai_payload(**overrides):
    payload = {
        "topic_id": 102,
        "session_id": "testsessionid123",
        "experiment_condition": "ai",
        "opponent_judgment": 2,
        **BASE_C1,
        **BASE_C2,
        **BASE_C3,
        **BASE_D,
        **BASE_E,
    }
    payload.update(overrides)
    return payload


def _make_hh_payload(**overrides):
    payload = {
        "topic_id": 102,
        "room_id": "testroomid456",
        "experiment_condition": "hh",
        "opponent_judgment": None,
        **BASE_C1,
        **BASE_C2,
        **BASE_C3,
        **BASE_D,
        **BASE_E,
    }
    payload.update(overrides)
    return payload


@pytest.fixture
def auth_client(db):
    user = User.objects.create_user(username="testuser_pq", password="pass1234!")
    partner = User.objects.create_user(username="testuser_pq_partner")
    DialogueSessionRecord.objects.create(
        user=user,
        session_id="testsessionid123",
        topic_id=102,
        topic_title="測試議題",
        collection_name="nuclear_energy_all",
        session_state={"user_stance_score": 6.0, "history": []},
        last_activity_at=timezone.now(),
    )
    DialogueMatch.objects.create(
        topic_id=102,
        user_a=user,
        user_b=partner,
        user_a_score=6.0,
        user_b_score=2.0,
        room_id="testroomid456",
    )
    client = APIClient()
    client.force_authenticate(user=user)
    return client, user


@pytest.mark.django_db
class TestPostQuestionnaire:

    def test_normal_ai_submission(self, auth_client):
        """H-AI 組正常提交，回傳 201 並含 id 與 s_post。"""
        client, _ = auth_client
        payload = _make_ai_payload()
        response = client.post("/api/post-questionnaire/", payload, format="json")
        assert response.status_code == 201, response.data
        data = response.data
        assert "id" in data
        assert data["experiment_condition"] == "ai"
        assert data["opponent_judgment"] == 2
        assert data["consent_confirmed"] is None
        assert "s_post" in data
        assert data["pre_question_map"] == {1: 8, 2: 5, 3: 3, 4: 7, 5: 1, 6: 4, 7: 6, 8: 2}

    def test_hh_c4_null(self, auth_client):
        """H-H 組 opponent_judgment 傳 null，欄位應存 NULL。"""
        client, _ = auth_client
        payload = _make_hh_payload()
        response = client.post("/api/post-questionnaire/", payload, format="json")
        assert response.status_code == 201, response.data
        assert response.data["opponent_judgment"] is None
        assert response.data["experiment_condition"] == "hh"

    def test_hh_rejects_notnull_judgment(self, auth_client):
        """H-H 組傳 opponent_judgment 非 null 應被拒絕（400）。"""
        client, _ = auth_client
        payload = _make_hh_payload(opponent_judgment=1)
        response = client.post("/api/post-questionnaire/", payload, format="json")
        assert response.status_code == 400

    def test_ai_requires_judgment(self, auth_client):
        """H-AI 組 opponent_judgment=null 應被拒絕（400）。"""
        client, _ = auth_client
        payload = _make_ai_payload(opponent_judgment=None)
        response = client.post("/api/post-questionnaire/", payload, format="json")
        assert response.status_code == 400

    def test_d1_min_length(self, auth_client):
        """D1 少於 50 字應被拒絕（400）。"""
        client, _ = auth_client
        payload = _make_ai_payload(post_open_comprehension="短句")
        response = client.post("/api/post-questionnaire/", payload, format="json")
        assert response.status_code == 400

    def test_reverse_scoring(self, auth_client):
        """C1-2, C1-6, C1-7, C1-8 做 8-raw；s_post = Σadjusted / 8 計算正確。"""
        client, _ = auth_client
        # Use controlled values: all raw=4 (non-reverse stays 4, reverse → 8-4=4)
        all_four = {f"post_likert_{i}": 4 for i in range(1, 9)}
        payload = _make_ai_payload(**all_four)
        response = client.post("/api/post-questionnaire/", payload, format="json")
        assert response.status_code == 201, response.data
        # All items: raw=4, reversed: 8-4=4 → s_post = 4.0
        assert float(response.data["s_post"]) == pytest.approx(4.0)

        # Verify asymmetric case from BASE_C1:
        # post_likert_1=6 (no rev) → 6
        # post_likert_2=3 (rev)    → 5
        # post_likert_3=4 (no rev) → 4
        # post_likert_4=5 (no rev) → 5
        # post_likert_5=6 (no rev) → 6
        # post_likert_6=2 (rev)    → 6
        # post_likert_7=3 (rev)    → 5
        # post_likert_8=4 (rev)    → 4
        # Σ = 6+5+4+5+6+6+5+4 = 41 → s_post = 41/8 = 5.125
        payload2 = _make_ai_payload(**BASE_C1)
        response2 = client.post("/api/post-questionnaire/", payload2, format="json")
        assert response2.status_code == 201, response2.data
        assert float(response2.data["s_post"]) == pytest.approx(41 / 8)

    def test_topic_103_uses_its_own_reverse_scoring_rules(self, auth_client):
        """Women-in-service reverses Q2/Q4/Q6/Q8, not the nuclear item set."""
        client, user = auth_client
        DialogueSessionRecord.objects.create(
            user=user,
            session_id="topic103session",
            topic_id=103,
            topic_title="女性義務役",
            collection_name="military_service_women_news",
            session_state={"user_stance_score": 5.0, "history": []},
            last_activity_at=timezone.now(),
        )
        response = client.post(
            "/api/post-questionnaire/",
            _make_ai_payload(topic_id=103, session_id="topic103session"),
            format="json",
        )

        assert response.status_code == 201, response.data
        # Post order is Q8,Q5,Q3,Q7,Q1,Q4,Q6,Q2, so topic 103 reverses
        # post items 1,6,7,8: 2+3+4+5+6+6+5+4 = 35.
        assert float(response.data["s_post"]) == pytest.approx(35 / 8)

    def test_discomfort_creates_report(self, auth_client, db):
        """discomfort_flag=True 且 detail 填寫時，應建立 DiscomfortReport。"""
        from api.models import DiscomfortReport
        client, _ = auth_client
        payload = _make_ai_payload(
            discomfort_flag=True,
            discomfort_detail="對話中有不適切言論",
        )
        response = client.post("/api/post-questionnaire/", payload, format="json")
        assert response.status_code == 201
        assert DiscomfortReport.objects.filter(response_id=response.data["id"]).exists()

    def test_discomfort_flag_true_requires_detail(self, auth_client):
        """discomfort_flag=True 但 detail 空白應被拒絕（400）。"""
        client, _ = auth_client
        payload = _make_ai_payload(discomfort_flag=True, discomfort_detail="")
        response = client.post("/api/post-questionnaire/", payload, format="json")
        assert response.status_code == 400

    def test_unauthenticated_rejected(self):
        """未認證請求應回傳 401。"""
        client = APIClient()
        response = client.post("/api/post-questionnaire/", _make_ai_payload(), format="json")
        assert response.status_code == 401


@pytest.mark.django_db
class TestPostQuestionnaireStanceMetrics:
    def test_ai_metrics_use_session_snapshot(self, auth_client):
        client, user = auth_client
        # A later pre-survey profile must not replace this conversation's 6.0.
        from api.models import UserStanceProfile

        UserStanceProfile.objects.create(
            user=user,
            topic_id=102,
            stance_score=2.0,
            stance_category=UserStanceProfile.StanceCategory.OPPOSE,
        )
        all_four = {f"post_likert_{i}": 4 for i in range(1, 9)}

        response = client.post(
            "/api/post-questionnaire/",
            _make_ai_payload(**all_four),
            format="json",
        )

        assert response.status_code == 201, response.data
        assert response.data["s_pre"] == pytest.approx(6.0)
        assert response.data["delta_s"] == pytest.approx(-2.0)
        assert response.data["stance_centrism"] == pytest.approx(-2.0)

    def test_hh_metrics_use_participants_match_score(self, auth_client):
        client, _ = auth_client
        all_four = {f"post_likert_{i}": 4 for i in range(1, 9)}

        response = client.post(
            "/api/post-questionnaire/",
            _make_hh_payload(**all_four),
            format="json",
        )

        assert response.status_code == 201, response.data
        assert response.data["s_pre"] == pytest.approx(6.0)
        assert response.data["delta_s"] == pytest.approx(-2.0)

    def test_rejects_unknown_or_mismatched_conversation(self, auth_client):
        client, _ = auth_client

        unknown = client.post(
            "/api/post-questionnaire/",
            _make_ai_payload(session_id="not-owned"),
            format="json",
        )
        mismatched = client.post(
            "/api/post-questionnaire/",
            _make_ai_payload(room_id="testroomid456"),
            format="json",
        )

        assert unknown.status_code == 400
        assert "session_id" in unknown.data
        assert mismatched.status_code == 400


@pytest.mark.django_db
class TestPostQuestionnaireClosesSession:
    """提交後測問卷後，該筆 AI 對話 session 應結束——不該再被
    /api/dialogue/sessions/latest/ 當成「可繼續」的對話回傳。"""

    def _make_active_session(self, user, session_id="testsessionid123", topic_id=102):
        record, _ = DialogueSessionRecord.objects.update_or_create(
            session_id=session_id,
            defaults={
                "user": user,
                "topic_id": topic_id,
                "topic_title": "測試議題",
                "collection_name": "nuclear_energy_all",
                "session_state": {"user_stance_score": 6.0, "history": []},
                "status": DialogueSessionRecord.Status.ACTIVE,
                "last_activity_at": timezone.now(),
            },
        )
        return record

    def test_submitting_closes_matching_session_record(self, auth_client):
        from api.models import DialogueSessionRecord

        client, user = auth_client
        self._make_active_session(user)

        response = client.post(
            "/api/post-questionnaire/", _make_ai_payload(), format="json"
        )
        assert response.status_code == 201, response.data

        record = DialogueSessionRecord.objects.get(session_id="testsessionid123")
        assert record.status == DialogueSessionRecord.Status.CLOSED

    def test_closed_session_no_longer_offered_for_restore(self, auth_client):
        client, user = auth_client
        self._make_active_session(user)

        response = client.post(
            "/api/post-questionnaire/", _make_ai_payload(), format="json"
        )
        assert response.status_code == 201, response.data

        restore_response = client.get(
            "/api/dialogue/sessions/latest/?topic_id=102"
        )
        assert restore_response.status_code == 404

    def test_hh_submission_without_session_id_does_not_error(self, auth_client):
        """H-H 組送出時只有 room_id、沒有 session_id，不該因為找不到 session 而出錯。"""
        client, _ = auth_client
        response = client.post(
            "/api/post-questionnaire/", _make_hh_payload(), format="json"
        )
        assert response.status_code == 201, response.data


@pytest.mark.django_db
class TestPostQuestionnaireConsent:

    def _submit(self, client, payload=None):
        r = client.post("/api/post-questionnaire/", payload or _make_ai_payload(), format="json")
        assert r.status_code == 201
        return r.data["id"]

    def test_consent_confirmed(self, auth_client):
        """選擇同意 → consent_confirmed=True, withdrawn=False。"""
        client, _ = auth_client
        rid = self._submit(client)
        response = client.patch(
            f"/api/post-questionnaire/{rid}/consent/",
            {"consent_confirmed": True},
            format="json",
        )
        assert response.status_code == 200
        assert response.data["consent_confirmed"] is True
        assert response.data["withdrawn"] is False

    def test_withdrawal_marks_record(self, auth_client, db):
        """選擇撤回 → consent_confirmed=False, withdrawn=True，DB 欄位寫入正確。"""
        from api.models import PostDialogueResponse
        client, _ = auth_client
        rid = self._submit(client)
        response = client.patch(
            f"/api/post-questionnaire/{rid}/consent/",
            {"consent_confirmed": False},
            format="json",
        )
        assert response.status_code == 200
        assert response.data["consent_confirmed"] is False
        assert response.data["withdrawn"] is True
        # Verify DB
        record = PostDialogueResponse.objects.get(id=rid)
        assert record.consent_confirmed is False

    def test_consent_wrong_owner_returns_404(self, auth_client, db):
        """其他使用者不能更新別人的問卷。"""
        client, _ = auth_client
        rid = self._submit(client)
        other = User.objects.create_user(username="other_pq", password="pass1234!")
        other_client = APIClient()
        other_client.force_authenticate(user=other)
        response = other_client.patch(
            f"/api/post-questionnaire/{rid}/consent/",
            {"consent_confirmed": True},
            format="json",
        )
        assert response.status_code == 404
