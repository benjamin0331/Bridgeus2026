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
    calculate_match_score,
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
    # Godot 綁定房剛被裁決作廢時的原因。一次性訊號：作廢之後
    # _godot_return_to_normal 會把人重新排隊或改走 AI，回傳的 state 就不再掛著
    # 那間房了，原因只能靠這裡帶出去。下一次輪詢就不會再有值。
    binding_cancel_reason: str | None = None


def _as_decimal(score: float | Decimal) -> Decimal:
    return Decimal(str(score)).quantize(Decimal("0.01"))


def _as_metric_decimal(score: float | Decimal) -> Decimal:
    return Decimal(str(score)).quantize(Decimal("0.0001"))


def _match_bound_profile(*, match, user_id: int, topic_id: int):
    """這場配對綁定的那一份前測；沒有綁定紀錄才退回這個人最新的一份。

    前測問卷是 append-only 的，所以「最新一份」跟「這場當初用的那份」在受試者
    事後重填之後就不是同一列了。退回 latest_for() 只是為了讓 MatchQueueEntry
    被清掉的舊資料仍算得出數字，不是預期路徑。
    """
    profile = UserStanceProfile.for_match(match_id=match.id, user_id=user_id)
    if profile is not None:
        return profile
    return UserStanceProfile.latest_for(user_id=user_id, topic_id=topic_id)


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
    reuse_profile: UserStanceProfile | None = None,
) -> MatchingState:
    """把這個人排進配對佇列，需要時建立配對。

    reuse_profile：呼叫端手上已經有「這一次作答」對應的那一列時傳進來，這支就
    不再新增一列。給的是內部重新排隊的路徑（例如 Godot 房作廢後退回一般模式）
    ——那不是受試者又填了一次問卷，憑空多一列會讓研究資料看起來像重填過。
    受試者真的送出問卷的路徑不要傳，讓它照常留下新的一列。
    """
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
            # 一定要走 _close_locked_match()，不能自己 set status 存檔——
            # 否則不會註冊 transaction.on_commit() 觸發 M6 觀點知識庫 pipeline，
            # 這場被放棄重配的對話就永遠不會產生 DialogueSummary/ViewpointNode。
            _close_locked_match(active_match, now=now)
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

        # append-only：每次填問卷都是新的一列，不覆寫上一次的作答（見
        # UserStanceProfile docstring）。這一場配對用的是哪一列，由下面
        # queue_entry.profile 綁死，所以之後再填幾次都不會動到已成立的配對。
        profile = reuse_profile or UserStanceProfile.objects.create(
            user=user,
            topic_id=topic_id,
            stance_score=decimal_score,
            stance_category=stance_category,
            survey_answers=survey_answers,
            survey_open_answers=survey_open_answers,
            q9_embedding=q9_embedding,
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


def record_godot_survey(
    *,
    user,
    match,
    topic_id: int,
    stance_score: float,
    stance_category: str,
    survey_answers: dict,
    survey_open_answers: dict,
):
    """Godot 綁定房的前測問卷落地。

    跟 enqueue_for_matching 的差別：不排隊、不找候選人——配對已經由遊戲內的木樁
    決定了。這裡只補「s_pre」這一塊：建 profile、建一筆 MATCHED 的 queue entry
    （它同時是「這個人填過問卷」的憑證，見 spec §D5），並回填 DialogueMatch 的分數。

    建 queue entry 不只是為了憑證：下游（get_matching_state 的 profile 欄位、
    M5 分析、M6 pipeline）本來就預期配對房兩邊都有這筆記錄，順手建起來比另外加
    一個布林欄位更不容易跟既有邏輯打架。

    回傳值語意：成功寫入回傳（重新讀取後的）match；鎖內重驗擋下則回傳 None。
    呼叫端（GodotSurveyView）必須檢查 None 並回應 409——呼叫端的裁決檢查發生在
    這個 transaction 之外，那之後到這裡取得鎖的窗口期間，清理指令或另一位的輪詢
    可能已經把房間取消，不能假設傳進來的 match 快照仍然有效。
    """
    from api.godot_binding import godot_binding_info

    decimal_score = _as_decimal(stance_score)
    q9_embedding = build_q9_embedding(survey_open_answers)

    with transaction.atomic():
        locked = DialogueMatch.objects.select_for_update().get(pk=match.pk)
        # 鎖內重驗：呼叫端的裁決檢查發生在 transaction 外，那之後到這裡取得鎖的
        # 窗口期間，清理指令或另一位的輪詢可能已經把房間取消。不重驗的話會寫出
        # 「問卷成功但房已作廢、人也沒被重新分流」的孤兒——因為退回一般模式是在
        # 這筆 MATCHED entry 存在之前跑的，當時會判定這個人沒填完而跳過他。
        if locked.status != DialogueMatch.Status.ACTIVE:
            return None
        if godot_binding_info(locked) is None:
            return None
        if user.id not in (locked.user_a_id, locked.user_b_id):
            return None
        # append-only，理由同 enqueue_for_matching。重填時下面那筆 entry 會改指
        # 到這一列，舊的那列留著但不再是任何一場對話的依據。
        profile = UserStanceProfile.objects.create(
            user=user,
            topic_id=topic_id,
            stance_score=decimal_score,
            stance_category=stance_category,
            survey_answers=survey_answers,
            survey_open_answers=survey_open_answers,
            q9_embedding=q9_embedding,
        )

        entry = MatchQueueEntry.objects.filter(
            user=user,
            topic_id=topic_id,
            match=locked,
            status=MatchQueueEntry.Status.MATCHED,
        ).first()
        if entry:
            # 重填問卷：覆寫同一筆，不要建第二筆（憑證必須是一對一）。
            entry.profile = profile
            entry.stance_score = decimal_score
            entry.save(update_fields=["profile", "stance_score", "updated_at"])
        else:
            MatchQueueEntry.objects.create(
                user=user,
                topic_id=topic_id,
                profile=profile,
                stance_score=decimal_score,
                status=MatchQueueEntry.Status.MATCHED,
                match=locked,
                matched_at=timezone.now(),
            )

        if locked.user_a_id == user.id:
            locked.user_a_score = decimal_score
        else:
            locked.user_b_score = decimal_score
        update_fields = ["user_a_score", "user_b_score"]
        if locked.user_a_score is not None and locked.user_b_score is not None:
            # 兩邊都填完才算得出來。這兩個指標對 Godot 房是**事後描述**，不是配對
            # 依據——配對是遊戲內的木樁決定的，不是演算法挑的。但不能永遠留欄位
            # 預設的 0：真實的 semantic_distance 也可能是 0，留著就跟階段四消滅的
            # 4.00 佔位值一樣，分析時分不出「沒算」還是「算出來是 0」。
            # 綁這場房的 entry.profile，不是「這個人最新的一份」——問卷現在是
            # append-only，兩者在對方之後又重填問卷時就不是同一列了，那會讓這場
            # 房的 semantic_distance 用到一份跟本場無關的 Q9 向量。
            profile_a = _match_bound_profile(
                match=locked, user_id=locked.user_a_id, topic_id=topic_id
            )
            profile_b = _match_bound_profile(
                match=locked, user_id=locked.user_b_id, topic_id=topic_id
            )
            metrics = calculate_match_score(
                requester_score=locked.user_a_score,
                candidate_score=locked.user_b_score,
                requester_embedding=profile_a.q9_embedding if profile_a else None,
                candidate_embedding=profile_b.q9_embedding if profile_b else None,
            )
            locked.likert_distance = _as_metric_decimal(metrics.likert_distance)
            locked.semantic_distance = _as_metric_decimal(metrics.semantic_distance)
            locked.match_score = _as_metric_decimal(metrics.match_score)
            update_fields += ["likert_distance", "semantic_distance", "match_score"]
        locked.save(update_fields=update_fields)
        return locked


# Godot 綁定房的問卷階段，對方多久沒有輪詢就判定離開。
# 階段四已把前端輪詢改成「一路輪到進聊天室為止」，所以問卷期間與等待對方期間
# 都會持續更新 last_seen；這個門檻是輪詢間隔的數倍，容忍網路抖動與換頁。
DEFAULT_GODOT_PRESENCE_TIMEOUT_SECONDS = 45


def godot_presence_timeout_seconds() -> int:
    try:
        return max(
            0,
            int(
                os.getenv(
                    "GODOT_PRESENCE_TIMEOUT_SECONDS",
                    str(DEFAULT_GODOT_PRESENCE_TIMEOUT_SECONDS),
                )
            ),
        )
    except (TypeError, ValueError):
        return DEFAULT_GODOT_PRESENCE_TIMEOUT_SECONDS


def _godot_participant_is_gone(match, user_id: int, *, now, timeout_seconds: int) -> bool:
    """這位參與者是不是已經離開（超過門檻沒有輪詢）。

    last_seen 為 None 代表「還沒出現過」——房間剛建立的頭幾秒兩個人都是這樣，
    **不能當成離開**，否則剛跳轉進來還沒開始輪詢的人會被當場判出局。這種情況
    改用房間的 created_at 起算寬限期。
    """
    presence = _presence_state(match)
    participant = presence["participants"].get(str(user_id), {})
    last_seen = _parse_presence_datetime(participant.get("last_seen"))
    reference = last_seen or match.created_at
    return (now - reference).total_seconds() > timeout_seconds


def resolve_godot_survey_gate(*, match, viewer_user_id=None, now=None):
    """Godot 綁定房在雙方完成前測問卷前的裁決。

    由 get_matching_state() 在輪詢路徑上呼叫——雙方在問卷階段與等待階段都會
    持續輪詢，那就是這裡用的存在訊號。兩人都關掉網頁時沒有人輪詢，裁決不會
    觸發，由 close_expired_godot_matches 指令兜底。

    先判離開再判逾時：離開的訊息（「對方已退出」）比「時間到了」對使用者具體。
    但「離開」要有人留下來才成立——兩位都不在時退回逾時判斷，見下方 gone/present。

    viewer_user_id 是正在發出這次請求的人。他顯然還在現場，所以不拿他的
    last_seen 去判斷——輪詢者自己的 last_seen 要到 mark_match_participant_connected
    才會更新，而那是在裁決之後。不排除他的話，一個載入較慢、超過門檻才第一次
    輪詢的人會在自己抵達的瞬間把房間判掉。清理指令沒有 viewer（傳 None），
    兩邊都檢查，那正是「兩個人都不在」該有的行為。
    """
    from api.godot_binding import (
        BINDING_STATS_KEY,
        godot_binding_info,
        match_pretest_state,
        survey_deadline_of,
    )

    if godot_binding_info(match) is None:
        return match
    if match.status != DialogueMatch.Status.ACTIVE:
        return match

    pretest = match_pretest_state(match)
    if pretest["both_done"]:
        # 都填完了就進聊天室，期限與存在偵測不再適用——之後改由既有的
        # close_match_if_idle / close_match_if_participant_absent 接手。
        return match

    current_time = now or timezone.now()
    timeout_seconds = godot_presence_timeout_seconds()
    reason = None

    if timeout_seconds > 0:
        gone = []
        present = []
        for user_id in (match.user_a_id, match.user_b_id):
            if user_id == viewer_user_id:
                # 發出這次請求的人顯然在現場（理由見 docstring：他自己的 last_seen
                # 要到裁決之後的 mark_match_participant_connected 才會更新）。
                present.append(user_id)
                continue
            if _godot_participant_is_gone(
                match, user_id, now=current_time, timeout_seconds=timeout_seconds
            ):
                gone.append(user_id)
            else:
                present.append(user_id)
        # 「對方已退出」的前提是還有人留在現場等他。兩位都不在的時候沒有「對方」
        # 這個角色，這句話對誰都不成立——那是逾時，交給下面的期限判斷。
        #
        # 這個條件不是理論上的邊界：close_expired_godot_matches 傳 viewer_user_id
        # =None，而它抓到的房依定義就是沒有人在輪詢的房（有人輪詢的話那個請求
        # 早就把房裁決掉了），兩位一定都判定為不在。沒有這一條的話
        # godot_survey_timeout 永遠不會從清理指令這條路發出，明明是問卷逾時卻
        # 對兩個人都說「對方已退出配對」，連 stats 裡記下的原因都是錯的。
        if gone and present:
            reason = "godot_partner_left"

    if reason is None:
        deadline = survey_deadline_of(match)
        if deadline is not None and current_time > deadline:
            reason = "godot_survey_timeout"

    if reason is None:
        return match

    with transaction.atomic():
        locked = DialogueMatch.objects.select_for_update().get(pk=match.pk)
        if locked.status != DialogueMatch.Status.ACTIVE:
            return locked
        # 在鎖內重讀填卷狀態：從上面那次鎖外讀取到取得鎖之間，對方可能剛送出
        # 問卷、把房間補成雙方都完成。沿用鎖外的 pretest 會作廢一間其實已經
        # 完成的房，而且那位剛送出的人還會因為過期狀態被跳過退回一般模式，
        # 變成手上有 s_pre 卻無路可走的孤兒。
        locked_pretest = match_pretest_state(locked)
        if locked_pretest["both_done"]:
            return locked
        stats = _stats_dict(locked).copy()
        binding = dict(stats.get(BINDING_STATS_KEY) or {})
        binding["cancel_reason"] = reason
        binding["cancelled_at"] = _isoformat(current_time)
        stats[BINDING_STATS_KEY] = binding
        locked.stats = stats
        locked.status = DialogueMatch.Status.CANCELLED
        locked.closed_at = current_time
        locked.save(update_fields=["stats", "status", "closed_at"])

    # 房間作廢後才退回一般模式：已填問卷的人有 stance 資料可以重新分流。
    # 這裡是階段四 §D3 刻意壓抑的分流規則恢復生效的地方。
    for user_id, done in (
        (locked.user_a_id, locked_pretest["user_a_done"]),
        (locked.user_b_id, locked_pretest["user_b_done"]),
    ):
        if done:
            try:
                _godot_return_to_normal(
                    user_id=user_id, topic_id=locked.topic_id, match=locked
                )
            except Exception:
                # 房間已經 CANCELLED 且不可重試（_get_active_match 找不到它了），
                # 一個人失敗不能拖累另一個人，也不該讓輪詢請求整個 500。
                logger.exception(
                    "Godot 房作廢後退回一般模式失敗 user=%s topic=%s match=%s",
                    user_id, locked.topic_id, locked.id,
                )
    return locked


def _godot_return_to_normal(*, user_id: int, topic_id: int, match) -> None:
    """把已填過問卷的參與者退回一般模式。

    §D3 在 Godot 房裡刻意不套用「中立→AI」分流（問卷是配對成立後才填的，這時
    判定某人該去 AI 會把已配好的兩人卡死）。但房間作廢之後那個顧慮消失了——
    這個人現在是單獨一個人，本來就該照他的立場走正常分流。
    """
    from api.models import DialogueEntryAssignment

    profile = UserStanceProfile.latest_for(user_id=user_id, topic_id=topic_id)
    if profile is None:
        return

    # 只取消「這間房」的 MATCHED entry。MATCHED entry 是 match_pretest_state
    # 判斷「填過前測問卷」的憑證（見 spec §D5），波及同使用者同議題的其他房，
    # 會讓那些房的前測紀錄憑空消失——歷史資料被靜默改寫，研究上讀不出來。
    MatchQueueEntry.objects.filter(
        user_id=user_id,
        topic_id=topic_id,
        match=match,
        status=MatchQueueEntry.Status.MATCHED,
    ).update(status=MatchQueueEntry.Status.CANCELLED, cancelled_at=timezone.now())

    if _can_enter_human_matching(profile.stance_category):
        enqueue_for_matching(
            user=profile.user,
            topic_id=topic_id,
            stance_score=float(profile.stance_score),
            stance_category=profile.stance_category,
            survey_answers=profile.survey_answers,
            survey_open_answers=profile.survey_open_answers,
            # 這是系統把人搬回一般佇列，不是受試者又填了一次問卷——沿用他原本
            # 那一列，不要在 append-only 的問卷歷史裡憑空多一筆假的重填紀錄。
            reuse_profile=profile,
        )
        route = DialogueEntryAssignment.Route.MATCH
    else:
        route = DialogueEntryAssignment.Route.AI

    DialogueEntryAssignment.objects.filter(
        user_id=user_id, topic_id=topic_id
    ).update(route=route)


def get_matching_state(*, user, topic_id: int) -> MatchingState:
    from api.godot_binding import mark_cancel_notice_seen, pending_cancel_notice_for

    active_match = _get_active_match(user_id=user.id, topic_id=topic_id)
    godot_cancel_reason = None
    if active_match:
        # Godot 綁定房在雙方填完問卷前，適用的是問卷裁決而不是一般的缺席/閒置關房
        # （那兩者的預設值分別是 180s／600s，跟問卷階段的語意不同）。
        resolved = resolve_godot_survey_gate(
            match=active_match, viewer_user_id=user.id
        )
        if resolved.status != DialogueMatch.Status.ACTIVE:
            # 作廢原因要在這裡抓下來：接下來 _godot_return_to_normal 已經把這個人
            # 重新排隊或改走 AI，底下回傳的 state 不會再掛著這間房，原因就消失了。
            # 觸發裁決的只會是其中一個請求，所以要走「逐人確認」名單——抓到之後
            # 立刻標記已通知，否則觸發者接下來會透過下面補查的路徑再收到一次。
            godot_cancel_reason = pending_cancel_notice_for(resolved, user.id)
            if godot_cancel_reason:
                mark_cancel_notice_seen(resolved, user.id)
        active_match = resolved

    if godot_cancel_reason is None:
        # 沒有從裁決拿到原因時，看看有沒有「最近作廢、這位使用者還沒被告知」的
        # Godot 房。觸發裁決的只會是其中一個請求（清理指令取消時一個都沒有），
        # 另一位要靠這條路徑才收得到通知。
        recent_cancelled = (
            DialogueMatch.objects.filter(
                topic_id=topic_id,
                status=DialogueMatch.Status.CANCELLED,
            )
            .filter(Q(user_a_id=user.id) | Q(user_b_id=user.id))
            .order_by("-closed_at", "-id")
            .first()
        )
        if recent_cancelled is not None:
            pending = pending_cancel_notice_for(recent_cancelled, user.id)
            if pending:
                mark_cancel_notice_seen(recent_cancelled, user.id)
                godot_cancel_reason = pending

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
            binding_cancel_reason=godot_cancel_reason,
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
            binding_cancel_reason=godot_cancel_reason,
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
            binding_cancel_reason=godot_cancel_reason,
        )

    profile = UserStanceProfile.latest_for(user_id=user.id, topic_id=topic_id)
    if profile and not _can_enter_human_matching(profile.stance_category):
        return MatchingState(
            status=AI_RECOMMENDED_STATUS,
            profile=profile,
            binding_cancel_reason=godot_cancel_reason,
        )

    if queue_entry:
        return MatchingState(
            status=queue_entry.status,
            profile=queue_entry.profile,
            queue_entry=queue_entry,
            match=queue_entry.match,
            binding_cancel_reason=godot_cancel_reason,
        )

    return MatchingState(status="idle", profile=profile, binding_cancel_reason=godot_cancel_reason)


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
