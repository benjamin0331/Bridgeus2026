"""
pytest tests for Godot 綁定房的前測問卷（階段四）。

Run from backend/:
    pytest api/tests_godot_survey.py -v
"""
from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APIClient

from api.models import DialogueMatch, MatchQueueEntry, UserStanceProfile

User = get_user_model()

SERVICE_TOKEN = "svc-token"


def _make_users():
    return (
        User.objects.create_user(username="ua", password="pw"),
        User.objects.create_user(username="ub", password="pw"),
    )


@pytest.mark.django_db
def test_godot_match_is_created_without_placeholder_scores():
    """建房時沒有真實 s_pre，分數留 NULL——4.00 佔位值跟真實分數無法區分。"""
    user_a, user_b = _make_users()

    match = DialogueMatch.objects.create(
        topic_id=102,
        user_a=user_a,
        user_b=user_b,
        matching_algorithm_version="godot_manual",
        room_id="room-null-scores",
        status=DialogueMatch.Status.ACTIVE,
    )

    match.refresh_from_db()
    assert match.user_a_score is None
    assert match.user_b_score is None


@pytest.mark.django_db
def test_score_check_constraint_still_rejects_out_of_range():
    """放寬成允許 NULL，但 1..7 的範圍檢查不能跟著失效。"""
    from django.db.utils import IntegrityError

    user_a, user_b = _make_users()

    with pytest.raises(IntegrityError):
        DialogueMatch.objects.create(
            topic_id=102,
            user_a=user_a,
            user_b=user_b,
            user_a_score=9,
            user_b_score=4,
            room_id="room-bad-score",
            status=DialogueMatch.Status.ACTIVE,
        )


def _godot_match(user_a, user_b, *, topic_id=102, room_id="room-binding"):
    """建一間跟 GodotMatchRoomView 產出形狀相同的房（含 binding stats）。"""
    return DialogueMatch.objects.create(
        topic_id=topic_id,
        user_a=user_a,
        user_b=user_b,
        matching_algorithm_version="godot_manual",
        room_id=room_id,
        status=DialogueMatch.Status.ACTIVE,
        stats={
            "binding": {
                "source": "godot",
                "survey_deadline": (
                    timezone.now() + timedelta(seconds=300)
                ).isoformat(),
            }
        },
    )


@pytest.mark.django_db
def test_pretest_state_reports_nobody_done_initially():
    from api.godot_binding import match_pretest_state

    user_a, user_b = _make_users()
    match = _godot_match(user_a, user_b)

    state = match_pretest_state(match)

    assert state["user_a_done"] is False
    assert state["user_b_done"] is False
    assert state["both_done"] is False


@pytest.mark.django_db
def test_pretest_state_counts_matched_queue_entry_as_done():
    """填過問卷的憑證是 MATCHED 的 queue entry，不是分數不等於某個值。"""
    from api.godot_binding import match_pretest_state

    user_a, user_b = _make_users()
    match = _godot_match(user_a, user_b)
    profile = UserStanceProfile.objects.create(
        user=user_a, topic_id=102, stance_score=6, stance_category="support"
    )
    MatchQueueEntry.objects.create(
        user=user_a,
        topic_id=102,
        profile=profile,
        stance_score=6,
        status=MatchQueueEntry.Status.MATCHED,
        match=match,
    )

    state = match_pretest_state(match)

    assert state["user_a_done"] is True
    assert state["user_b_done"] is False
    assert state["both_done"] is False


@pytest.mark.django_db
def test_binding_info_returns_none_for_non_godot_match():
    from api.godot_binding import godot_binding_info

    user_a, user_b = _make_users()
    match = DialogueMatch.objects.create(
        topic_id=102,
        user_a=user_a,
        user_b=user_b,
        user_a_score=5,
        user_b_score=3,
        room_id="room-normal",
        status=DialogueMatch.Status.ACTIVE,
    )

    assert godot_binding_info(match) is None


@pytest.mark.django_db
def test_matching_status_exposes_binding_fields():
    user_a, user_b = _make_users()
    _godot_match(user_a, user_b)
    client = APIClient()
    client.force_authenticate(user=user_a)

    response = client.get("/api/matching/status/?topic_id=102")

    assert response.status_code == 200
    assert response.data["binding_source"] == "godot"
    assert response.data["survey_required"] is True
    assert response.data["survey_deadline"] is not None
    assert response.data["partner_state"] == "pending"


@pytest.mark.django_db
def test_matching_status_binding_fields_absent_for_normal_match():
    user_a, user_b = _make_users()
    DialogueMatch.objects.create(
        topic_id=102,
        user_a=user_a,
        user_b=user_b,
        user_a_score=5,
        user_b_score=3,
        room_id="room-normal-2",
        status=DialogueMatch.Status.ACTIVE,
    )
    client = APIClient()
    client.force_authenticate(user=user_a)

    response = client.get("/api/matching/status/?topic_id=102")

    assert response.data["binding_source"] is None
    assert response.data["survey_required"] is False


# topic 102 的 reverse_question_ids 是 [2, 4, 5, 6]（見 dialogue_topics.py）。
# 統一填同一個數字（例如全部填 6）會讓正向題與反向題的調整分互相抵消，
# 算出來剛好是中立值 4——不管填的是 6 還是 2 都一樣，測不出真實分數的差異。
# 所以這裡改用「正向題填高分、反向題填低分」的組合，讓調整後全部一致地
# 偏向支持（或反對），才能穩定算出偏離中立值的真實分數。
_REVERSE_QUESTION_IDS = {2, 4, 5, 6}
SURVEY_ANSWERS = {
    str(i): (1 if i in _REVERSE_QUESTION_IDS else 7) for i in range(1, 9)
}  # 調整後全部等於 7（強烈支持）
OPPOSING_SURVEY_ANSWERS = {
    str(i): (7 if i in _REVERSE_QUESTION_IDS else 1) for i in range(1, 9)
}  # 調整後全部等於 1（強烈反對）
SURVEY_OPEN = {"Q9": "我支持這個議題，因為……"}


@pytest.mark.django_db
def test_godot_survey_writes_real_score_and_queue_entry():
    user_a, user_b = _make_users()
    match = _godot_match(user_a, user_b, room_id="room-survey-1")
    client = APIClient()
    client.force_authenticate(user=user_a)

    response = client.post(
        "/api/matching/godot-survey/",
        {
            "topic_id": 102,
            "survey_answers": SURVEY_ANSWERS,
            "survey_open_answers": SURVEY_OPEN,
        },
        format="json",
    )

    assert response.status_code == 200
    match.refresh_from_db()
    assert match.user_a_score is not None
    assert match.user_a_score != 4  # 真實分數，不是舊的佔位值
    assert match.user_b_score is None  # 對方還沒填
    assert MatchQueueEntry.objects.filter(
        user=user_a, match=match, status=MatchQueueEntry.Status.MATCHED
    ).exists()
    assert UserStanceProfile.objects.filter(user=user_a, topic_id=102).exists()


@pytest.mark.django_db
def test_godot_survey_marks_route_match_even_for_neutral_stance():
    """spec §D3：Godot 房不套用「中立→AI」分流，一律 route=match。"""
    from api.models import DialogueEntryAssignment

    user_a, user_b = _make_users()
    _godot_match(user_a, user_b, room_id="room-survey-neutral")
    client = APIClient()
    client.force_authenticate(user=user_a)

    client.post(
        "/api/matching/godot-survey/",
        {
            "topic_id": 102,
            "survey_answers": {str(i): 4 for i in range(1, 9)},
            "survey_open_answers": SURVEY_OPEN,
        },
        format="json",
    )

    assignment = DialogueEntryAssignment.objects.get(user=user_a, topic_id=102)
    assert assignment.route == DialogueEntryAssignment.Route.MATCH


@pytest.mark.django_db
def test_godot_survey_both_sides_fills_likert_distance():
    user_a, user_b = _make_users()
    match = _godot_match(user_a, user_b, room_id="room-survey-2")
    client = APIClient()

    client.force_authenticate(user=user_a)
    client.post(
        "/api/matching/godot-survey/",
        {"topic_id": 102, "survey_answers": SURVEY_ANSWERS,
         "survey_open_answers": SURVEY_OPEN},
        format="json",
    )
    client.force_authenticate(user=user_b)
    response = client.post(
        "/api/matching/godot-survey/",
        {"topic_id": 102, "survey_answers": OPPOSING_SURVEY_ANSWERS,
         "survey_open_answers": {"Q9": "我反對，因為……"}},
        format="json",
    )

    assert response.status_code == 200
    assert response.data["survey_required"] is False
    assert response.data["partner_state"] == "ready"
    match.refresh_from_db()
    assert match.user_a_score is not None
    assert match.user_b_score is not None
    assert match.likert_distance > 0


@pytest.mark.django_db
def test_godot_survey_rejects_non_participant():
    user_a, user_b = _make_users()
    _godot_match(user_a, user_b, room_id="room-survey-3")
    outsider = User.objects.create_user(username="uc", password="pw")
    client = APIClient()
    client.force_authenticate(user=outsider)

    response = client.post(
        "/api/matching/godot-survey/",
        {"topic_id": 102, "survey_answers": SURVEY_ANSWERS,
         "survey_open_answers": SURVEY_OPEN},
        format="json",
    )

    assert response.status_code == 404


@pytest.mark.django_db
def test_godot_survey_rejects_when_no_godot_room():
    """一般配對房不能走這支端點——它的分數是配對演算法算出來的。"""
    user_a, user_b = _make_users()
    DialogueMatch.objects.create(
        topic_id=102, user_a=user_a, user_b=user_b,
        user_a_score=5, user_b_score=3,
        room_id="room-normal-3", status=DialogueMatch.Status.ACTIVE,
    )
    client = APIClient()
    client.force_authenticate(user=user_a)

    response = client.post(
        "/api/matching/godot-survey/",
        {"topic_id": 102, "survey_answers": SURVEY_ANSWERS,
         "survey_open_answers": SURVEY_OPEN},
        format="json",
    )

    assert response.status_code == 404


@pytest.mark.django_db
def test_godot_survey_resubmit_updates_instead_of_duplicating():
    user_a, user_b = _make_users()
    match = _godot_match(user_a, user_b, room_id="room-survey-4")
    client = APIClient()
    client.force_authenticate(user=user_a)
    payload = {"topic_id": 102, "survey_answers": SURVEY_ANSWERS,
               "survey_open_answers": SURVEY_OPEN}

    client.post("/api/matching/godot-survey/", payload, format="json")
    client.post("/api/matching/godot-survey/", payload, format="json")

    assert MatchQueueEntry.objects.filter(
        user=user_a, match=match, status=MatchQueueEntry.Status.MATCHED
    ).count() == 1


@pytest.mark.django_db
def test_godot_survey_requires_authentication():
    """用真 JWT 路徑驗證未登入會被擋——force_authenticate 驗不到這件事。"""
    response = APIClient().post(
        "/api/matching/godot-survey/",
        {"topic_id": 102, "survey_answers": SURVEY_ANSWERS,
         "survey_open_answers": SURVEY_OPEN},
        format="json",
    )

    assert response.status_code == 401
