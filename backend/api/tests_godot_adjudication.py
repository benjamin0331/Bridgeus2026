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


GODOT_PRESENCE_TIMEOUT = 45


def _mark_seen(match, user, *, seconds_ago=0):
    """直接寫 presence，模擬「這個人最後一次輪詢是幾秒前」。"""
    from apps.matching.services.matcher import (
        _presence_state,
        _save_presence_state,
    )

    presence = _presence_state(match)
    presence["participants"][str(user.id)] = {
        "connected": True,
        "last_seen": (timezone.now() - timedelta(seconds=seconds_ago)).isoformat(),
        "disconnected_at": None,
    }
    _save_presence_state(match, presence)
    match.refresh_from_db()
    return match


@pytest.mark.django_db
def test_gate_keeps_room_while_both_recently_seen():
    from apps.matching.services.matcher import resolve_godot_survey_gate

    user_a, user_b = _make_users()
    match = _godot_match(user_a, user_b, room_id="room-gate-ok")
    _mark_seen(match, user_a, seconds_ago=2)
    _mark_seen(match, user_b, seconds_ago=3)

    resolved = resolve_godot_survey_gate(match=match)

    assert resolved.status == DialogueMatch.Status.ACTIVE


@pytest.mark.django_db
def test_never_seen_participant_is_not_treated_as_left():
    """房間剛建立時兩人的 last_seen 都是 None——那是「還沒出現」不是「離開」。

    誤判這個會讓剛跳轉進來、還沒開始輪詢的人被當場判出局，房間當場作廢。
    """
    from apps.matching.services.matcher import resolve_godot_survey_gate

    user_a, user_b = _make_users()
    match = _godot_match(user_a, user_b, room_id="room-gate-fresh")

    resolved = resolve_godot_survey_gate(match=match)

    assert resolved.status == DialogueMatch.Status.ACTIVE


@pytest.mark.django_db
def test_partner_gone_cancels_room():
    from apps.matching.services.matcher import resolve_godot_survey_gate

    user_a, user_b = _make_users()
    match = _godot_match(user_a, user_b, room_id="room-gate-left")
    _mark_seen(match, user_a, seconds_ago=1)
    _mark_seen(match, user_b, seconds_ago=GODOT_PRESENCE_TIMEOUT + 10)

    resolved = resolve_godot_survey_gate(match=match)

    assert resolved.status == DialogueMatch.Status.CANCELLED
    assert resolved.stats["binding"]["cancel_reason"] == "godot_partner_left"


@pytest.mark.django_db
def test_deadline_expiry_cancels_room():
    from apps.matching.services.matcher import resolve_godot_survey_gate

    user_a, user_b = _make_users()
    match = _godot_match(
        user_a, user_b, room_id="room-gate-timeout", deadline_offset_seconds=-10
    )
    _mark_seen(match, user_a, seconds_ago=1)
    _mark_seen(match, user_b, seconds_ago=1)

    resolved = resolve_godot_survey_gate(match=match)

    assert resolved.status == DialogueMatch.Status.CANCELLED
    assert resolved.stats["binding"]["cancel_reason"] == "godot_survey_timeout"


@pytest.mark.django_db
def test_deadline_expiry_does_not_cancel_when_both_completed():
    """逾時只針對「還沒填完」的房。都填完了就進聊天室，期限不再有意義。

    前置要照真實順序走：先在期限內讓兩人都送出，**之後**才把期限改成過去。
    若一開始就給過期的期限，第一個人送出時裁決就會正確地作廢房間（那時確實
    「期限已過且還有人沒填完」），第二個人根本送不進來——那驗的是另一回事。
    """
    from apps.matching.services.matcher import resolve_godot_survey_gate

    user_a, user_b = _make_users()
    match = _godot_match(user_a, user_b, room_id="room-gate-done")
    _submit_survey(user_a, SUPPORT_ANSWERS)
    _submit_survey(user_b, OPPOSE_ANSWERS)
    match.refresh_from_db()
    assert match.status == DialogueMatch.Status.ACTIVE   # 前置成立才有意義

    # 兩人都填完之後才讓期限過期。
    stats = dict(match.stats)
    binding = dict(stats["binding"])
    binding["survey_deadline"] = (timezone.now() - timedelta(seconds=10)).isoformat()
    stats["binding"] = binding
    match.stats = stats
    match.save(update_fields=["stats"])

    resolved = resolve_godot_survey_gate(match=match)

    assert resolved.status == DialogueMatch.Status.ACTIVE


@pytest.mark.django_db
def test_survivor_with_extreme_stance_returns_to_queue():
    """已填問卷、立場極端的人退回一般配對佇列（§D3 壓抑的分流在此恢復）。"""
    from apps.matching.services.matcher import resolve_godot_survey_gate

    user_a, user_b = _make_users()
    match = _godot_match(user_a, user_b, room_id="room-gate-survivor")
    _submit_survey(user_a, SUPPORT_ANSWERS)
    match.refresh_from_db()
    _mark_seen(match, user_a, seconds_ago=1)
    _mark_seen(match, user_b, seconds_ago=GODOT_PRESENCE_TIMEOUT + 10)

    resolve_godot_survey_gate(match=match)

    assert MatchQueueEntry.objects.filter(
        user=user_a, topic_id=102, status=MatchQueueEntry.Status.MATCHING
    ).exists()


@pytest.mark.django_db
def test_survivor_with_neutral_stance_routed_to_ai():
    from api.models import DialogueEntryAssignment
    from apps.matching.services.matcher import resolve_godot_survey_gate

    user_a, user_b = _make_users()
    match = _godot_match(user_a, user_b, room_id="room-gate-neutral")
    _submit_survey(user_a, NEUTRAL_ANSWERS)
    match.refresh_from_db()
    _mark_seen(match, user_a, seconds_ago=1)
    _mark_seen(match, user_b, seconds_ago=GODOT_PRESENCE_TIMEOUT + 10)

    resolve_godot_survey_gate(match=match)

    assignment = DialogueEntryAssignment.objects.get(user=user_a, topic_id=102)
    assert assignment.route == DialogueEntryAssignment.Route.AI


@pytest.mark.django_db
def test_gate_ignores_non_godot_match():
    from apps.matching.services.matcher import resolve_godot_survey_gate

    user_a, user_b = _make_users()
    match = DialogueMatch.objects.create(
        topic_id=102, user_a=user_a, user_b=user_b,
        user_a_score=5, user_b_score=3,
        room_id="room-normal-gate", status=DialogueMatch.Status.ACTIVE,
    )

    resolved = resolve_godot_survey_gate(match=match)

    assert resolved.status == DialogueMatch.Status.ACTIVE
