import os
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from api.models import DialogueMatch, MatchMessage, MatchQueueEntry, UserStanceProfile

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

    return close_match(match=match)


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
            active_match = close_match_if_idle(match=active_match, now=now)
            if active_match.status != DialogueMatch.Status.ACTIVE:
                active_match = None

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


def close_match(*, match: DialogueMatch) -> DialogueMatch:
    with transaction.atomic():
        locked_match = DialogueMatch.objects.select_for_update().get(pk=match.pk)
        if locked_match.status != DialogueMatch.Status.ACTIVE:
            return locked_match

        locked_match.status = DialogueMatch.Status.CLOSED
        locked_match.closed_at = timezone.now()
        locked_match.save(update_fields=["status", "closed_at"])
        return locked_match
