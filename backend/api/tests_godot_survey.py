"""
pytest tests for Godot 綁定房的前測問卷（階段四）。

Run from backend/:
    pytest api/tests_godot_survey.py -v
"""
import pytest
from django.contrib.auth import get_user_model

from api.models import DialogueMatch

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
