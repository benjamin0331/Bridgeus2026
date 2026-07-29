"""
pytest tests for Godot 綁定房的問卷裁決與收尾（階段五）。

Run from backend/:
    pytest api/tests_godot_adjudication.py -v
"""
from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APIClient

from api.models import DialogueMatch, MatchQueueEntry, UserStanceProfile

User = get_user_model()

SUPPORT_ANSWERS = {str(i): (1 if i in {2, 4, 5, 6} else 7) for i in range(1, 9)}
OPPOSE_ANSWERS = {str(i): (7 if i in {2, 4, 5, 6} else 1) for i in range(1, 9)}
NEUTRAL_ANSWERS = {str(i): 4 for i in range(1, 9)}


def _make_users():
    return (
        User.objects.create_user(username="ua", password="pw"),
        User.objects.create_user(username="ub", password="pw"),
    )


def _godot_match(user_a, user_b, *, topic_id=102, room_id="room-adj",
                 deadline_offset_seconds=300):
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
                    timezone.now() + timedelta(seconds=deadline_offset_seconds)
                ).isoformat(),
            }
        },
    )


def _submit_survey(user, answers, *, topic_id=102, open_text="我的看法是……"):
    client = APIClient()
    client.force_authenticate(user=user)
    return client.post(
        "/api/matching/godot-survey/",
        {
            "topic_id": topic_id,
            "survey_answers": answers,
            "survey_open_answers": {"Q9": open_text},
        },
        format="json",
    )


@pytest.mark.django_db
def test_metrics_stay_zero_until_both_sides_submit():
    user_a, user_b = _make_users()
    match = _godot_match(user_a, user_b, room_id="room-metrics-1")

    _submit_survey(user_a, SUPPORT_ANSWERS)

    match.refresh_from_db()
    assert match.match_score == 0    # 只有一邊，還算不出來


@pytest.mark.django_db
def test_metrics_computed_once_both_sides_submit():
    """補算是事後描述指標，不是配對依據——但不能永遠留 0，那跟真值無法區分。"""
    user_a, user_b = _make_users()
    match = _godot_match(user_a, user_b, room_id="room-metrics-2")

    _submit_survey(user_a, SUPPORT_ANSWERS, open_text="我強烈支持，因為安全無虞。")
    _submit_survey(user_b, OPPOSE_ANSWERS, open_text="我強烈反對，因為風險太高。")

    match.refresh_from_db()
    assert match.likert_distance > 0
    assert match.match_score > 0
    # semantic_distance 取決於 embedding 模型，不斷言確切值，只確認有被寫入
    assert match.semantic_distance is not None
