import logging
import os
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

from django.db import transaction
from django.db.models import Q
from django.utils.dateparse import parse_datetime
from django.utils import timezone

from api.models import DialogueMatch, MatchMessage, MatchQueueEntry, UserStanceProfile

logger = logging.getLogger(__name__)

from .matching_algorithm import (
    MATCHING_ALGORITHM_VERSION,
    ScoredCandidate,
    candidate_categories_for,
    choose_best_candidate,
)
from .semantic import build_q9_embedding


MATCHING_QUEUE_HEARTBEAT_TIMEOUT_SECONDS = int(
    os.getenv("MATCHING_QUEUE_HEARTBEAT_TIMEOUT_SECONDS", "15")
)
AI_RECOMMENDED_STATUS = "ai_recommended"
DEFAULT_MATCH_ROOM_IDLE_TIMEOUT_SECONDS = 600
DEFAULT_MATCH_ROOM_ABSENCE_TIMEOUT_SECONDS = 180
MATCH_PRESENCE_STATS_KEY = "presence"


class MatchingError(Exception):
    """Base exception for queue and match lifecycle errors."""


class ActiveMatchExistsError(MatchingError):
    """Raised when the user already has an active match for the topic."""


class MatchingNotFoundError(MatchingError):
    """Raised when there is no active matching request to operate on."""


class MatchingAlreadyMatchedError(MatchingError):
    """Raised when a matching request already became a live match."""


@dataclass
class MatchingState:
    status: str
    profile: UserStanceProfile | None = None
    queue_entry: MatchQueueEntry | None = None
    match: DialogueMatch | None = None


def _as_decimal(score: float | Decimal) -> Decimal:
    return Decimal(str(score)).quantize(Decimal("0.01"))


def _as_metric_decimal(score: float | Decimal) -> Decimal:
    return Decimal(str(score)).quantize(Decimal("0.0001"))


def _can_enter_human_matching(stance_category: str) -> bool:
    return bool(candidate_categories_for(stance_category))


def can_enter_human_matching(stance_category: str) -> bool:
    """公開版本，給混合入口決定分流方向用。

    刻意包一層而不是直接把 _can_enter_human_matching 改名：佇列內部已有
    多處呼叫，而分流規則必須只有一份定義——兩邊分歧的話，會出現「入口說
    你該配對、佇列說你不能配對」的死路。
    """
    return _can_enter_human_matching(stance_category)


def _active_match_queryset(*, user_id: int, topic_id: int):
    return DialogueMatch.objects.select_related("user_a", "user_b").filter(
        topic_id=topic_id,
        status=DialogueMatch.Status.ACTIVE,
    ).filter(Q(user_a_id=user_id) | Q(user_b_id=user_id))


def _get_active_match(*, user_id: int, topic_id: int) -> DialogueMatch | None:
    return _active_match_queryset(user_id=user_id, topic_id=topic_id).first()


def _stale_queue_cutoff(now=None):
    current_time = now or timezone.now()
    return current_time - timedelta(seconds=MATCHING_QUEUE_HEARTBEAT_TIMEOUT_SECONDS)


def match_room_idle_timeout_seconds() -> int:
    try:
        timeout = int(
            os.getenv(
                "MATCH_ROOM_IDLE_TIMEOUT_SECONDS",
                str(DEFAULT_MATCH_ROOM_IDLE_TIMEOUT_SECONDS),
            )
        )
    except (TypeError, ValueError):
        return DEFAULT_MATCH_ROOM_IDLE_TIMEOUT_SECONDS
    return max(0, timeout)


def match_room_absence_timeout_seconds() -> int:
    try:
        timeout = int(
            os.getenv(
                "MATCH_ROOM_ABSENCE_TIMEOUT_SECONDS",
                str(DEFAULT_MATCH_ROOM_ABSENCE_TIMEOUT_SECONDS),
            )
        )
    except (TypeError, ValueError):
        return DEFAULT_MATCH_ROOM_ABSENCE_TIMEOUT_SECONDS
    return max(0, timeout)


def _stats_dict(match: DialogueMatch) -> dict:
    return match.stats if isinstance(match.stats, dict) else {}


def _isoformat(value) -> str | None:
    return value.isoformat() if value else None


def _parse_presence_datetime(value):
    if not value:
        return None
    if hasattr(value, "utcoffset"):
        parsed = value
    elif isinstance(value, str):
        parsed = parse_datetime(value)
    else:
        return None

    if parsed is None:
        return None
    if timezone.is_naive(parsed):
        return timezone.make_aware(parsed, timezone.get_current_timezone())
    return parsed


def _presence_state(match: DialogueMatch) -> dict:
    stats = _stats_dict(match)
    existing = stats.get(MATCH_PRESENCE_STATS_KEY)
    if not isinstance(existing, dict):
        existing = {}

    existing_participants = existing.get("participants")
    if not isinstance(existing_participants, dict):
        existing_participants = {}

    participants = {}
    for user_id in (match.user_a_id, match.user_b_id):
        participant = existing_participants.get(str(user_id))
        if not isinstance(participant, dict):
            participant = {}
        participants[str(user_id)] = {
            "connected": bool(participant.get("connected", False)),
            "last_seen": participant.get("last_seen"),
            "disconnected_at": participant.get("disconnected_at"),
        }

    return {
        "version": 1,
        "participants": participants,
    }


def _save_presence_state(match: DialogueMatch, presence: dict) -> None:
    stats = _stats_dict(match).copy()
    stats[MATCH_PRESENCE_STATS_KEY] = presence
    match.stats = stats
    match.save(update_fields=["stats"])


def _participant_key_for_user(match: DialogueMatch, user_id: int) -> str | None:
    if user_id in {match.user_a_id, match.user_b_id}:
        return str(user_id)
    return None


def _absence_deadline_for_participant(participant: dict, *, timeout_seconds: int):
    if participant.get("connected") or timeout_seconds <= 0:
        return None

    disconnected_at = _parse_presence_datetime(participant.get("disconnected_at"))
    if disconnected_at is None:
        return None
    return disconnected_at + timedelta(seconds=timeout_seconds)


def _absence_deadline(match: DialogueMatch, *, now=None):
    if match.status != DialogueMatch.Status.ACTIVE:
        return None

    timeout_seconds = match_room_absence_timeout_seconds()
    if timeout_seconds <= 0:
        return None

    deadlines = [
        deadline
        for participant in _presence_state(match)["participants"].values()
        for deadline in [
            _absence_deadline_for_participant(
                participant,
                timeout_seconds=timeout_seconds,
            )
        ]
        if deadline is not None
    ]
    if not deadlines:
        return None
    return min(deadlines)


def _trigger_m6_pipeline_for_closed_match(match_id: int) -> None:
    """對話雙方結束對話（配對房轉為 CLOSED）後觸發 M6 觀點知識庫 pipeline。

    只註冊在 transaction.on_commit()，確保配對房關閉真的落地、鎖也釋放之後才
    跑（pipeline 會呼叫 embedding 模型、寫 DB，不該佔著關房當下的行鎖）。
    pipeline 本身失敗絕對不能讓配對房關不掉，所以這裡整個包住吃掉例外，只記
    log；呼叫端（_close_locked_match）不需要、也不應該知道 M6 這邊的結果。
    """
    try:
        from apps.summary.pipeline.assemble import run_pipeline_for_match

        run_pipeline_for_match(match_id)
    except Exception:
        logger.exception(
            "M6 觀點知識庫 pipeline 觸發失敗 match_id=%s（不影響配對房關閉）",
            match_id,
        )


def _close_locked_match(locked_match: DialogueMatch, *, now=None) -> DialogueMatch:
    if locked_match.status != DialogueMatch.Status.ACTIVE:
        return locked_match

    current_time = now or timezone.now()
    locked_match.status = DialogueMatch.Status.CLOSED
    locked_match.closed_at = current_time
    locked_match.save(update_fields=["status", "closed_at"])
    transaction.on_commit(
        lambda: _trigger_m6_pipeline_for_closed_match(locked_match.id)
    )
    return locked_match


def close_match_if_participant_absent(
    *,
    match: DialogueMatch,
    now=None,
) -> DialogueMatch:
    if match.status != DialogueMatch.Status.ACTIVE:
        return match

    timeout_seconds = match_room_absence_timeout_seconds()
    if timeout_seconds <= 0:
        return match

    current_time = now or timezone.now()
    with transaction.atomic():
        locked_match = DialogueMatch.objects.select_for_update().get(pk=match.pk)
        if locked_match.status != DialogueMatch.Status.ACTIVE:
            return locked_match

        deadline = _absence_deadline(locked_match, now=current_time)
        if deadline is None or current_time < deadline:
            return locked_match

        return _close_locked_match(locked_match, now=current_time)


def mark_match_participant_connected(
    *,
    match: DialogueMatch,
    user_id: int,
    now=None,
) -> DialogueMatch:
    current_time = now or timezone.now()
    with transaction.atomic():
        locked_match = DialogueMatch.objects.select_for_update().get(pk=match.pk)
        if locked_match.status != DialogueMatch.Status.ACTIVE:
            return locked_match

        participant_key = _participant_key_for_user(locked_match, user_id)
        if participant_key is None:
            return locked_match

        presence = _presence_state(locked_match)
        participant = presence["participants"][participant_key]
        participant["connected"] = True
        participant["last_seen"] = _isoformat(current_time)
        participant["disconnected_at"] = None
        _save_presence_state(locked_match, presence)
        return locked_match


def mark_match_participant_disconnected(
    *,
    match: DialogueMatch,
    user_id: int,
    now=None,
) -> DialogueMatch:
    current_time = now or timezone.now()
    with transaction.atomic():
        locked_match = DialogueMatch.objects.select_for_update().get(pk=match.pk)
        if locked_match.status != DialogueMatch.Status.ACTIVE:
            return locked_match

        participant_key = _participant_key_for_user(locked_match, user_id)
        if participant_key is None:
            return locked_match

        presence = _presence_state(locked_match)
        participant = presence["participants"][participant_key]
        was_already_disconnected = (
            not participant.get("connected")
            and participant.get("disconnected_at")
        )
        participant["connected"] = False
        participant["last_seen"] = participant.get("last_seen") or _isoformat(current_time)
        if not was_already_disconnected:
            participant["disconnected_at"] = _isoformat(current_time)
        _save_presence_state(locked_match, presence)
        return locked_match


def get_match_presence_payload(
    *,
    match: DialogueMatch,
    current_user_id: int,
    now=None,
) -> dict:
    presence = _presence_state(match)
    current_key = str(current_user_id)
    other_user_id = match.user_b_id if match.user_a_id == current_user_id else match.user_a_id
    other_key = str(other_user_id)
    deadline = _absence_deadline(match, now=now)

    def participant_payload(user_id: int) -> dict:
        participant = presence["participants"].get(str(user_id), {})
        return {
            "user_id": user_id,
            "connected": bool(participant.get("connected", False)),
            "last_seen": participant.get("last_seen"),
            "disconnected_at": participant.get("disconnected_at"),
        }

    return {
        "current_user": participant_payload(current_user_id),
        "other_user": participant_payload(other_user_id),
        "absence_timeout_seconds": match_room_absence_timeout_seconds(),
        "absence_deadline": deadline,
        "participants": {
            current_key: participant_payload(current_user_id),
            other_key: participant_payload(other_user_id),
        },
    }


def get_match_last_activity_at(*, match: DialogueMatch):
    latest_message_at = (
        MatchMessage.objects.filter(match=match)
        .order_by("-created_at", "-id")
        .values_list("created_at", flat=True)
        .first()
    )
    return latest_message_at or match.created_at


def close_match_if_idle(*, match: DialogueMatch, now=None) -> DialogueMatch:
    if match.status != DialogueMatch.Status.ACTIVE:
        return match

    timeout_seconds = match_room_idle_timeout_seconds()
    if timeout_seconds <= 0:
        return match

    current_time = now or timezone.now()
    last_activity_at = get_match_last_activity_at(match=match)
    if current_time - last_activity_at < timedelta(seconds=timeout_seconds):
        return match

    return close_match(match=match, now=current_time)


def expire_stale_matching_entries(*, topic_id: int, now=None) -> int:
    current_time = now or timezone.now()
    return MatchQueueEntry.objects.filter(
        topic_id=topic_id,
        status=MatchQueueEntry.Status.MATCHING,
        updated_at__lt=_stale_queue_cutoff(current_time),
    ).update(
        status=MatchQueueEntry.Status.CANCELLED,
        cancelled_at=current_time,
        updated_at=current_time,
    )


def touch_matching_queue_entry(queue_entry: MatchQueueEntry, *, now=None) -> None:
    current_time = now or timezone.now()
    queue_entry.updated_at = current_time
    queue_entry.save(update_fields=["updated_at"])


def _cancel_active_queue_for_ai_recommendation(*, user, topic_id: int, now) -> None:
    MatchQueueEntry.objects.filter(
        user=user,
        topic_id=topic_id,
        status=MatchQueueEntry.Status.MATCHING,
    ).update(
        status=MatchQueueEntry.Status.CANCELLED,
        cancelled_at=now,
        updated_at=now,
    )


def _find_best_candidate(
    *,
    topic_id: int,
    requester_user_id: int,
    requester_profile: UserStanceProfile,
    stance_score: Decimal,
    stance_category: str,
) -> ScoredCandidate | None:
    candidate_categories = candidate_categories_for(stance_category)
    if not candidate_categories:
        return None

    now = timezone.now()
    expire_stale_matching_entries(topic_id=topic_id, now=now)

    candidates = list(
        MatchQueueEntry.objects.select_for_update()
        .select_related("profile", "user")
        .filter(
            topic_id=topic_id,
            status=MatchQueueEntry.Status.MATCHING,
            updated_at__gte=_stale_queue_cutoff(now),
            profile__stance_category__in=candidate_categories,
        )
        .exclude(user_id=requester_user_id)
    )
    return choose_best_candidate(
        candidates=candidates,
        requester_score=stance_score,
        requester_category=stance_category,
        requester_embedding=requester_profile.q9_embedding,
    )


def enqueue_for_matching(
    *,
    user,
    topic_id: int,
    stance_score: float,
    stance_category: str,
    survey_answers: dict,
    survey_open_answers: dict,
    restart_existing_match: bool = False,
) -> MatchingState:
    decimal_score = _as_decimal(stance_score)
    q9_embedding = build_q9_embedding(survey_open_answers)
    now = timezone.now()

    with transaction.atomic():
        active_match = _get_active_match(user_id=user.id, topic_id=topic_id)
        if active_match:
            active_match = close_match_if_participant_absent(
                match=active_match,
                now=now,
            )
            active_match = close_match_if_idle(match=active_match, now=now)
            if active_match.status != DialogueMatch.Status.ACTIVE:
                active_match = None
            else:
                active_match = mark_match_participant_connected(
                    match=active_match,
                    user_id=user.id,
                    now=now,
                )

        if active_match and restart_existing_match:
            active_match.status = DialogueMatch.Status.CLOSED
            active_match.closed_at = now
            active_match.save(update_fields=["status", "closed_at"])
            active_match = None

        if active_match:
            queue_entry = (
                MatchQueueEntry.objects.select_related("profile")
                .filter(
                    user=user,
                    topic_id=topic_id,
                    match=active_match,
                    status=MatchQueueEntry.Status.MATCHED,
                )
                .order_by("-matched_at", "-id")
                .first()
            )
            return MatchingState(
                status=MatchQueueEntry.Status.MATCHED,
                profile=queue_entry.profile if queue_entry else None,
                queue_entry=queue_entry,
                match=active_match,
            )

        profile, _ = UserStanceProfile.objects.update_or_create(
            user=user,
            topic_id=topic_id,
            defaults={
                "stance_score": decimal_score,
                "stance_category": stance_category,
                "survey_answers": survey_answers,
                "survey_open_answers": survey_open_answers,
                "q9_embedding": q9_embedding,
            },
        )

        if not _can_enter_human_matching(stance_category):
            _cancel_active_queue_for_ai_recommendation(
                user=user,
                topic_id=topic_id,
                now=now,
            )
            return MatchingState(status=AI_RECOMMENDED_STATUS, profile=profile)

        queue_entry = (
            MatchQueueEntry.objects.select_for_update()
            .select_related("profile")
            .filter(
                user=user,
                topic_id=topic_id,
                status=MatchQueueEntry.Status.MATCHING,
            )
            .first()
        )

        if queue_entry:
            queue_entry.profile = profile
            queue_entry.stance_score = decimal_score
            queue_entry.save(update_fields=["profile", "stance_score", "updated_at"])
        else:
            queue_entry = MatchQueueEntry.objects.create(
                user=user,
                topic_id=topic_id,
                profile=profile,
                stance_score=decimal_score,
                status=MatchQueueEntry.Status.MATCHING,
            )

        scored_candidate = _find_best_candidate(
            topic_id=topic_id,
            requester_user_id=user.id,
            requester_profile=profile,
            stance_score=decimal_score,
            stance_category=stance_category,
        )
        if not scored_candidate:
            return MatchingState(
                status=MatchQueueEntry.Status.MATCHING,
                profile=profile,
                queue_entry=queue_entry,
            )

        candidate = scored_candidate.candidate
        match_metrics = scored_candidate.score
        match = DialogueMatch.objects.create(
            topic_id=topic_id,
            user_a=user,
            user_b=candidate.user,
            user_a_score=decimal_score,
            user_b_score=candidate.stance_score,
            likert_distance=_as_metric_decimal(match_metrics.likert_distance),
            semantic_distance=_as_metric_decimal(match_metrics.semantic_distance),
            match_score=_as_metric_decimal(match_metrics.match_score),
            matching_algorithm_version=MATCHING_ALGORITHM_VERSION,
            room_id=uuid4().hex,
            status=DialogueMatch.Status.ACTIVE,
        )

        queue_entry.status = MatchQueueEntry.Status.MATCHED
        queue_entry.match = match
        queue_entry.matched_at = now
        queue_entry.cancelled_at = None
        queue_entry.save(
            update_fields=[
                "status",
                "match",
                "matched_at",
                "cancelled_at",
                "updated_at",
            ]
        )

        candidate.status = MatchQueueEntry.Status.MATCHED
        candidate.match = match
        candidate.matched_at = now
        candidate.cancelled_at = None
        candidate.save(
            update_fields=[
                "status",
                "match",
                "matched_at",
                "cancelled_at",
                "updated_at",
            ]
        )

        return MatchingState(
            status=MatchQueueEntry.Status.MATCHED,
            profile=profile,
            queue_entry=queue_entry,
            match=match,
        )


def get_matching_state(*, user, topic_id: int) -> MatchingState:
    active_match = _get_active_match(user_id=user.id, topic_id=topic_id)
    if active_match:
        active_match = close_match_if_participant_absent(match=active_match)
    if active_match and active_match.status == DialogueMatch.Status.ACTIVE:
        active_match = mark_match_participant_connected(
            match=active_match,
            user_id=user.id,
        )
        active_match = close_match_if_idle(match=active_match)

    if active_match and active_match.status == DialogueMatch.Status.ACTIVE:
        queue_entry = (
            MatchQueueEntry.objects.select_related("profile")
            .filter(
                user=user,
                topic_id=topic_id,
                match=active_match,
                status=MatchQueueEntry.Status.MATCHED,
            )
            .order_by("-matched_at", "-id")
            .first()
        )
        return MatchingState(
            status=MatchQueueEntry.Status.MATCHED,
            profile=queue_entry.profile if queue_entry else None,
            queue_entry=queue_entry,
            match=active_match,
        )

    queue_entry = (
        MatchQueueEntry.objects.select_related("profile")
        .filter(
            user=user,
            topic_id=topic_id,
            status=MatchQueueEntry.Status.MATCHING,
        )
        .order_by("-waiting_started_at", "-id")
        .first()
    )
    if queue_entry:
        touch_matching_queue_entry(queue_entry)
        return MatchingState(
            status=MatchQueueEntry.Status.MATCHING,
            profile=queue_entry.profile,
            queue_entry=queue_entry,
        )

    queue_entry = (
        MatchQueueEntry.objects.select_related("profile", "match")
        .filter(user=user, topic_id=topic_id)
        .order_by("-updated_at", "-id")
        .first()
    )
    if queue_entry and queue_entry.match and queue_entry.match.status == DialogueMatch.Status.CLOSED:
        return MatchingState(
            status="closed",
            profile=queue_entry.profile,
            queue_entry=queue_entry,
            match=queue_entry.match,
        )

    profile = (
        UserStanceProfile.objects.filter(user=user, topic_id=topic_id)
        .order_by("-updated_at", "-id")
        .first()
    )
    if profile and not _can_enter_human_matching(profile.stance_category):
        return MatchingState(status=AI_RECOMMENDED_STATUS, profile=profile)

    if queue_entry:
        return MatchingState(
            status=queue_entry.status,
            profile=queue_entry.profile,
            queue_entry=queue_entry,
            match=queue_entry.match,
        )

    return MatchingState(status="idle", profile=profile)


def cancel_matching(*, user, topic_id: int) -> MatchQueueEntry:
    with transaction.atomic():
        active_match = _get_active_match(user_id=user.id, topic_id=topic_id)
        if active_match:
            raise MatchingAlreadyMatchedError(
                "使用者已經匹配成功，不能取消等待中的匹配。"
            )

        queue_entry = (
            MatchQueueEntry.objects.select_for_update()
            .select_related("profile")
            .filter(
                user=user,
                topic_id=topic_id,
                status=MatchQueueEntry.Status.MATCHING,
            )
            .order_by("-waiting_started_at", "-id")
            .first()
        )
        if not queue_entry:
            raise MatchingNotFoundError("目前沒有進行中的匹配請求。")

        queue_entry.status = MatchQueueEntry.Status.CANCELLED
        queue_entry.cancelled_at = timezone.now()
        queue_entry.save(update_fields=["status", "cancelled_at", "updated_at"])
        return queue_entry


def get_room_messages(*, match: DialogueMatch):
    return MatchMessage.objects.select_related("sender").filter(match=match).order_by(
        "created_at", "id"
    )


def close_match(*, match: DialogueMatch, now=None) -> DialogueMatch:
    with transaction.atomic():
        locked_match = DialogueMatch.objects.select_for_update().get(pk=match.pk)
        return _close_locked_match(locked_match, now=now)
