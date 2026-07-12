from django.db.models import Q

from api.models import DialogueMatch, MatchStanceDrift
from api.serializers import MatchingRoomMessagesSerializer, MatchingStateSerializer

ANONYMOUS_MATCH_USER_NAME = "匿名對話者"


def _get_other_user(match: DialogueMatch, *, user_id: int):
    return match.user_b if match.user_a_id == user_id else match.user_a


def _match_presence_fields(match: DialogueMatch | None, *, user_id: int) -> dict:
    if not match:
        return {
            "presence": None,
            "absence_deadline": None,
        }

    from apps.matching.services.matcher import get_match_presence_payload

    presence = get_match_presence_payload(match=match, current_user_id=user_id)
    absence_deadline = presence.get("absence_deadline")
    presence = {
        **presence,
        "absence_deadline": (
            absence_deadline.isoformat() if absence_deadline else None
        ),
    }
    return {
        "presence": presence,
        "absence_deadline": absence_deadline,
    }


def _build_matching_state_payload(*, topic_id: int, state, user_id: int) -> dict:
    queue_entry = state.queue_entry
    match = state.match
    other_user_id = None
    other_user_name = None
    if match:
        other_user = _get_other_user(match, user_id=user_id)
        other_user_id = other_user.id
        other_user_name = ANONYMOUS_MATCH_USER_NAME

    payload = {
        "topic_id": topic_id,
        "status": state.status,
        "stance_score": (
            state.profile.stance_score if state.profile else None
        ),
        "stance_category": (
            state.profile.stance_category if state.profile else None
        ),
        "queue_entry_id": queue_entry.id if queue_entry else None,
        "waiting_started_at": (
            queue_entry.waiting_started_at if queue_entry else None
        ),
        "matched_at": queue_entry.matched_at if queue_entry else None,
        "cancelled_at": queue_entry.cancelled_at if queue_entry else None,
        "closed_at": match.closed_at if match else None,
        "match_id": match.id if match else None,
        "room_id": match.room_id if match else None,
        "other_user_id": other_user_id,
        "other_user_name": other_user_name,
        **_match_presence_fields(match, user_id=user_id),
    }
    return MatchingStateSerializer(payload).data


def _room_match_state_status(match: DialogueMatch) -> str:
    if match.status == DialogueMatch.Status.ACTIVE:
        return "matched"
    return match.status


def _get_latest_room_stance_drift(*, match: DialogueMatch, user_id: int) -> dict | None:
    latest = (
        MatchStanceDrift.objects.filter(match=match, user_id=user_id)
        .order_by("-measured_at", "-id")
        .first()
    )
    if latest is None:
        return None

    return {
        "drift_value": latest.drift_value,
        "measured_at": latest.measured_at,
    }


def _build_room_messages_payload(*, match: DialogueMatch, user_id: int, messages) -> dict:
    other_user = _get_other_user(match, user_id=user_id)
    payload = {
        "room_id": match.room_id,
        "match_id": match.id,
        "topic_id": match.topic_id,
        "status": _room_match_state_status(match),
        "other_user_id": other_user.id,
        "other_user_name": ANONYMOUS_MATCH_USER_NAME,
        "stance_drift": _get_latest_room_stance_drift(match=match, user_id=user_id),
        **_match_presence_fields(match, user_id=user_id),
        "messages": messages,
    }
    return MatchingRoomMessagesSerializer(payload).data


def _get_room_match_for_user(*, room_id: str, user_id: int) -> DialogueMatch | None:
    return (
        DialogueMatch.objects.select_related("user_a", "user_b")
        .filter(room_id=room_id)
        .filter(Q(user_a_id=user_id) | Q(user_b_id=user_id))
        .first()
    )


def _touch_room_match_for_user_activity(
    *,
    match: DialogueMatch,
    user_id: int,
) -> DialogueMatch:
    from apps.matching.services.matcher import (
        close_match_if_idle,
        close_match_if_participant_absent,
        mark_match_participant_connected,
    )

    match = close_match_if_participant_absent(match=match)
    if match.status == DialogueMatch.Status.ACTIVE:
        match = mark_match_participant_connected(match=match, user_id=user_id)
        match = close_match_if_idle(match=match)
    return match
