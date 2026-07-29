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
