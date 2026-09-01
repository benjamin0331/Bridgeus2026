import logging
import os
import re
from datetime import timedelta
from decimal import Decimal
from difflib import SequenceMatcher
from functools import lru_cache
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.db.models import Case, Count, FloatField, IntegerField, Q, Value, When
from django.db.models.functions import Coalesce
from django.utils import timezone
from rest_framework import exceptions, generics, permissions, status
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.pagination import PageNumberPagination
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.views import TokenObtainPairView

from apps.matching.services.anonymity import assign_anonymous_ids
from apps.matching.services.semantic import build_q9_embedding
from apps.matching.services.semantic_tree import get_topic_anchors
from apps.summary.models import VideoRecommendation, VideoWatchEvent, ViewpointNode

from .permissions import (
    IsGodotServiceToken,
    IsResearcher,
    RESEARCHER_GROUP_NAME,
    user_is_researcher,
)

from .models import (
    AIConversation,
    DialogueEntryAssignment,
    DialogueMatch,
    DialogueSessionRecord,
    DiscomfortReport,
    Favorite,
    Issue,
    IssueReaction,
    MatchMessage,
    MatchOpeningBrief,
    MatchQueueEntry,
    MatchStanceDrift,
    MessageReaction,
    PlatformDisplaySetting,
    PlatformFeedback,
    PolicyIdea,
    PostDialogueResponse,
    Title,
    TopicDisplayOverride,
    UserAchievement,
    UserStanceProfile,
    UserTitle,
)
from .achievement_rules import evaluate as evaluate_achievements
from .achievement_rules import unlock_knowledge_base_achievement
from .achievements import CATALOG, CATEGORY_TITLES
from .dialogue_topics import (
    TOPIC_CONFIGS,
    get_dialogue_survey,
)
from .display_settings import (
    default_stance_thresholds,
    get_entry_mode,
    get_match_fallback_timeout_seconds,
    get_stance_thresholds,
    get_survey_scoring_config,
    is_topic_visible,
    resolve_stance_category,
    visible_topics,
)
from .timeline_access import LOCKED_DETAIL, timeline_unlock_state
from .godot_tickets import issue_ticket, redeem_ticket
from api.godot_binding import (
    BINDING_STATS_KEY,
    binding_cancel_reason,
    godot_binding_info,
    match_pretest_state,
)
from .serializers import (
    AccountCreateSerializer,
    AccountListSerializer,
    AccountUpdateSerializer,
    PasswordResetSerializer,
    AIConversationSerializer,
    DialogueEntrySerializer,
    DialogueReplySerializer,
    DialogueSurveySerializer,
    DialogueTopicSerializer,
    DialogueSessionCreateSerializer,
    FavoriteToggleSerializer,
    GodotSurveySerializer,
    MatchMessageSerializer,
    MatchingJoinSerializer,
    MatchingRoomMessageCreateSerializer,
    MatchingRoomMessagesSerializer,
    MatchingRoomSemanticTreeSerializer,
    MatchingRoomSemanticTreeTimelineSerializer,
    MatchingStateSerializer,
    MatchingTopicSerializer,
    MessageReactionSerializer,
    PlatformDisplaySettingSerializer,
    PlatformFeedbackSerializer,
    PlatformFeedbackOutputSerializer,
    PolicyIdeaSerializer,
    PostDialogueResponseConsentSerializer,
    PostDialogueResponseOutputSerializer,
    PostDialogueResponseSerializer,
    BridgeUsTokenObtainPairSerializer,
    DialogueSummaryDetailSerializer,
    DialogueTopicTrendingSerializer,
    TopicDisplayOverrideSerializer,
    VideoRecommendationAdminSerializer,
    VideoRecommendationSerializer,
    ViewpointHighlightSerializer,
    ViewpointNodeReviewDecisionSerializer,
    ViewpointNodeReviewSerializer,
)

User = get_user_model()

SESSION_TTL_SECONDS = 60 * 60 * 12
logger = logging.getLogger(__name__)
DEFAULT_DIALOGUE_COLLECTION = os.getenv(
    "DEFAULT_DIALOGUE_COLLECTION",
    "general_knowledge",
)


class BridgeUsTokenObtainPairView(TokenObtainPairView):
    """跟 SimpleJWT 內建的 TokenObtainPairView 唯二差別：access token payload
    多帶一個 is_researcher claim（見 BridgeUsTokenObtainPairSerializer），
    以及加了速率限制。掛在 BridgeUs_Django/urls.py 的 /api/token/，取代原本的
    TokenObtainPairView。

    限流用 ScopedRateThrottle 而不是 AnonRateThrottle：後者是一個全域的匿名
    預算，會把登入跟其他未認證端點綁在一起；scope 讓登入（與日後的註冊）各自
    有獨立額度。

    ⚠️ 計數走 Django cache。多 worker 部署若沒開 Redis（USE_REDIS_CACHE=1），
    每個 worker 各自計數，實際上限會變成 N 倍——同 apps/matching/services/
    rate_limit.py 記載過的坑。
    """

    serializer_class = BridgeUsTokenObtainPairSerializer
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "login"


def _session_cache_key(session_id: str) -> str:
    return f"dialogue_session:{session_id}"


def _history_with_turn_ids(history: list[dict], turns) -> list[dict]:
    """Preserve session history and attach persisted AIConversation ids.

    AIConversation persistence is fail-open in the WebSocket consumer. The
    session record can therefore contain messages that have no database turn;
    those messages must remain visible even though they cannot be reacted to.
    """
    persisted_messages = []
    for turn in turns:
        if turn.user_prompt:
            persisted_messages.append(
                {
                    "role": "user",
                    "content": turn.user_prompt,
                    "turn_id": turn.id,
                }
            )
        if turn.ai_response:
            persisted_messages.append(
                {
                    "role": "agent",
                    "content": turn.ai_response,
                    "turn_id": turn.id,
                }
            )

    session_messages = [dict(message) for message in history]
    session_keys = [
        (message.get("role"), message.get("content"))
        for message in session_messages
    ]
    persisted_keys = [
        (message["role"], message["content"])
        for message in persisted_messages
    ]
    matcher = SequenceMatcher(
        a=session_keys,
        b=persisted_keys,
        autojunk=False,
    )

    merged_history = []
    for (
        tag,
        session_start,
        session_end,
        persisted_start,
        persisted_end,
    ) in matcher.get_opcodes():
        if tag == "equal":
            for offset, message in enumerate(
                session_messages[session_start:session_end]
            ):
                annotated = dict(message)
                annotated["turn_id"] = persisted_messages[
                    persisted_start + offset
                ]["turn_id"]
                merged_history.append(annotated)
            continue

        if tag in {"replace", "delete"}:
            merged_history.extend(session_messages[session_start:session_end])
        if tag in {"replace", "insert"}:
            merged_history.extend(persisted_messages[persisted_start:persisted_end])

    return merged_history


def _live_dialogue_history(
    *,
    session_id: str,
    user_id: int,
    history: list[dict],
) -> list[dict]:
    turns = AIConversation.objects.filter(
        user_id=user_id,
        session_id=session_id,
    ).order_by("created_at", "id")
    return _history_with_turn_ids(history, turns)


def _cache_dialogue_session_record(session_record: dict) -> None:
    cache.set(
        _session_cache_key(session_record["session_id"]),
        session_record,
        timeout=SESSION_TTL_SECONDS,
    )


def _rebuild_session_state_from_turns(record: DialogueSessionRecord) -> dict:
    session_state = (record.session_state or {}).copy()
    turns = list(
        AIConversation.objects.filter(
            user=record.user,
            session_id=record.session_id,
        ).order_by("created_at", "id")
    )
    latest_phase = session_state.get("dialogue_phase") or "engagement"

    for turn in turns:
        if turn.dialogue_phase:
            latest_phase = turn.dialogue_phase

    history = _history_with_turn_ids(session_state.get("history") or [], turns)
    if history:
        session_state["history"] = history
    session_state["dialogue_phase"] = latest_phase
    return session_state


def _dialogue_session_cache_payload_from_record(
    record: DialogueSessionRecord,
) -> dict:
    session_state = _rebuild_session_state_from_turns(record)
    payload = {
        "user_id": record.user_id,
        "session_id": record.session_id,
        "topic_id": record.topic_id,
        "topic_title": record.topic_title,
        "collection_name": record.collection_name,
        "survey_context": record.survey_context or {},
        "session": session_state,
    }
    if record.semantic_tree_state:
        payload["semantic_tree"] = record.semantic_tree_state
    return payload


def _persist_dialogue_session_record(session_record: dict) -> DialogueSessionRecord:
    current_time = timezone.now()
    record, _ = DialogueSessionRecord.objects.update_or_create(
        session_id=session_record["session_id"],
        defaults={
            "user_id": session_record["user_id"],
            "topic_id": session_record["topic_id"],
            "topic_title": session_record.get("topic_title")
            or session_record.get("session", {}).get("topic")
            or f"議題 {session_record['topic_id']}",
            "collection_name": session_record.get("collection_name")
            or DEFAULT_DIALOGUE_COLLECTION,
            "survey_context": session_record.get("survey_context") or {},
            "session_state": session_record.get("session") or {},
            "semantic_tree_state": session_record.get("semantic_tree") or {},
            "status": DialogueSessionRecord.Status.ACTIVE,
            "last_activity_at": current_time,
        },
    )
    return record


def _close_dialogue_session_record(*, session_id: str, user_id: int) -> None:
    """標記某個 AI 對話 session 為已結束，並清掉快取。

    後測問卷送出後呼叫——沒有這一步的話，該 session 在 DB 裡永遠是 ACTIVE，
    /api/dialogue/sessions/latest/ 會一直把它當成「可繼續」的對話回傳，使用者
    填完後測問卷後還是會看到「要繼續上次，還是開始新對話？」的提示。
    """
    updated = DialogueSessionRecord.objects.filter(
        session_id=session_id,
        user_id=user_id,
        status=DialogueSessionRecord.Status.ACTIVE,
    ).update(status=DialogueSessionRecord.Status.CLOSED)
    if updated:
        cache.delete(_session_cache_key(session_id))


def _close_superseded_dialogue_sessions(
    *, user_id: int, topic_id: int, keep_session_id: str
) -> None:
    """關掉同一位使用者、同一議題下除了 keep_session_id 以外的所有進行中 session。

    一個 user+topic 最多只該有一個「可恢復」的對話。舊的不關掉的話，使用者選了
    「開始新對話」之後，被丟下的那筆仍是 active，下次進來
    /api/dialogue/sessions/latest/ 又會撈到它，於是「要繼續上次，還是開始新對話？」
    永遠問不完——對話等於結束不掉，也永遠輪不到沿用上次立場的彈窗出現（那個彈窗
    只在沒有可恢復 session、showSurvey 為 true 時才會渲染）。
    """
    stale = list(
        DialogueSessionRecord.objects.filter(
            user_id=user_id,
            topic_id=topic_id,
            status=DialogueSessionRecord.Status.ACTIVE,
        )
        .exclude(session_id=keep_session_id)
        .values_list("session_id", flat=True)
    )
    if not stale:
        return

    DialogueSessionRecord.objects.filter(session_id__in=stale).update(
        status=DialogueSessionRecord.Status.CLOSED
    )
    cache.delete_many([_session_cache_key(s) for s in stale])


def _restore_dialogue_session_record_for_user(
    *,
    session_id: str,
    user_id: int,
) -> tuple[dict | None, str]:
    cached = cache.get(_session_cache_key(session_id))
    if cached and cached.get("user_id") == user_id:
        return cached, "cache"

    record = (
        DialogueSessionRecord.objects.filter(
            user_id=user_id,
            session_id=session_id,
            status=DialogueSessionRecord.Status.ACTIVE,
        )
        .order_by("-last_activity_at", "-id")
        .first()
    )
    if record is None:
        return None, ""

    session_record = _dialogue_session_cache_payload_from_record(record)
    _cache_dialogue_session_record(session_record)
    return session_record, "database"


def _dialogue_session_response_payload(
    *,
    session_record: dict,
    restored_from: str,
    user_id: int | None = None,
) -> dict:
    session_state = session_record.get("session") or {}
    # 帶 turn_id 的歷史，前端才能把 讚/倒讚 掛到對應的 AI 回覆上。
    history = session_state.get("history") or []
    if user_id is not None:
        history = _live_dialogue_history(
            session_id=session_record["session_id"],
            user_id=user_id,
            history=history,
        )
    stance_drift = session_state.get("stance_drift")
    stance_score = session_state.get("user_stance_score")
    stance_category = _display_stance_category(
        user_id=user_id,
        topic_id=session_record.get("topic_id"),
        stance_score=stance_score,
    )

    return {
        "session_id": session_record["session_id"],
        "topic_id": session_record.get("topic_id"),
        "topic_title": session_record.get("topic_title")
        or session_state.get("topic"),
        "dialogue_phase": session_state.get("dialogue_phase", "engagement"),
        "stance_score": stance_score,
        "stance_category": stance_category,
        "stance_label": session_state.get("user_stance_label", ""),
        "stance_drift": stance_drift,
        "history": history,
        "messages": history,
        "restored_from": restored_from,
        "status": DialogueSessionRecord.Status.ACTIVE,
    }


def _update_ai_session_stance_drift(
    *,
    session_record: dict,
    session_id: str,
    user_id: int,
) -> dict | None:
    from apps.matching.services.hh_analysis import calculate_ai_session_stance_drift

    try:
        return calculate_ai_session_stance_drift(
            session_record=session_record,
            session_id=session_id,
            user_id=user_id,
        )
    except Exception:
        logger.exception("AI stance drift failed for session %s.", session_id)
        return (session_record.get("session") or {}).get("stance_drift")


def _get_open_answer(
    survey_open_answers: dict[str, str],
    *,
    question_id: int,
    question_code: str,
) -> str:
    return (
        survey_open_answers.get(question_code)
        or survey_open_answers.get(str(question_id))
        or ""
    ).strip()


# 問卷分數邏輯
def _compute_user_stance_score(
    *,
    topic_id: int,
    survey_answers: dict[str, int],
) -> float:
    scoring_config = get_survey_scoring_config(topic_id)
    if not survey_answers:
        return round(scoring_config["neutral_score"], 2)

    adjusted_scores = []

    for question_id in scoring_config["likert_question_ids"]:
        raw_score = survey_answers.get(question_id)
        if raw_score is None:
            continue

        adjusted_score = float(raw_score)

        if question_id in scoring_config["reverse_question_ids"]:
            adjusted_score = (
                scoring_config["scale_min"]
                + scoring_config["scale_max"]
                - adjusted_score
            )

        adjusted_scores.append(adjusted_score)

    if not adjusted_scores:
        return round(scoring_config["neutral_score"], 2)

    return round(sum(adjusted_scores) / len(adjusted_scores), 2)


def _display_stance_category(
    *, user_id: int | None, topic_id, stance_score
) -> str | None:
    """顯示用的立場分類：優先取已儲存的值，取不到才即時重算。

    門檻是 Supervisor 可調的。若顯示時一律用當下門檻重算，改一次門檻就會
    回頭改變所有舊對話畫面上的立場分類——那不是「調設定」，那是改寫既有
    實驗資料的呈現。已存的分類才是這場對話當初實際被分到的組別。

    優先序：分流指派 > 立場問卷 > 即時重算。DialogueEntryAssignment 是分流
    當下的權威紀錄，還一併存了當時生效的門檻；UserStanceProfile 會在受試者
    為了新對話重填問卷時被覆寫，所以退為第二順位。
    """
    try:
        topic_id = int(topic_id)
    except (TypeError, ValueError):
        return None

    if user_id is not None:
        stored = (
            DialogueEntryAssignment.objects.filter(
                user_id=user_id, topic_id=topic_id
            )
            .values_list("stance_category", flat=True)
            .first()
        )
        if stored:
            return stored

        stored = (
            UserStanceProfile.objects.filter(user_id=user_id, topic_id=topic_id)
            .values_list("stance_category", flat=True)
            .first()
        )
        if stored:
            return stored

    try:
        return resolve_stance_category(
            topic_id=topic_id, user_stance_score=float(stance_score)
        )
    except (TypeError, ValueError):
        return None


def _resolve_stances(
    *,
    topic_id: int,
    user_stance_score: float,
) -> tuple[str, str, str]:
    stance_category = resolve_stance_category(
        topic_id=topic_id,
        user_stance_score=user_stance_score,
    )

    labels = TOPIC_CONFIGS.get(topic_id, {}).get("stance_labels", {})
    entry = labels.get(stance_category) or labels.get("neutral") or {}
    return (
        entry.get("user_label", "立場中立或尚未明確"),
        entry.get("agent_stance", "提出相反觀點"),
        entry.get("agent_stance_summary", ""),
    )


def _resolve_open_answers(
    *,
    topic_id: int,
    survey_open_answers: dict[str, str],
) -> dict[str, str]:
    scoring_config = get_survey_scoring_config(topic_id)
    resolved_answers = {}

    for question in scoring_config["open_question_mappings"]:
        resolved_answers[question["code"]] = _get_open_answer(
            survey_open_answers,
            question_id=question["id"],
            question_code=question["code"],
        )

    return resolved_answers


def _build_topic_config(
    *,
    topic_id: int,
    topic_title: str,
    topic_description: str,
    survey_answers: dict[str, int],
    survey_open_answers: dict[str, str],
    user_initial_argument: str,
) -> dict[str, str | float | dict]:
    topic_meta = TOPIC_CONFIGS.get(topic_id, {})
    user_stance_score = _compute_user_stance_score(
        topic_id=topic_id,
        survey_answers=survey_answers,
    )
    user_stance_label, agent_stance, agent_stance_summary = _resolve_stances(
        topic_id=topic_id,
        user_stance_score=user_stance_score,
    )
    survey_config = get_dialogue_survey(topic_id) or {}
    semantic_vector_interface = survey_config.get("semantic_vector_interface", {})
    resolved_open_answers = _resolve_open_answers(
        topic_id=topic_id,
        survey_open_answers=survey_open_answers,
    )
    resolved_initial_argument = (
        resolved_open_answers.get("Q9", "")
        or user_initial_argument
    )
    from apps.matching.services.ai_agent import infer_reasoning_mode
    user_reasoning_mode = infer_reasoning_mode(
        user_stance_score=user_stance_score,
        user_initial_argument=resolved_initial_argument,
        opponent_view_text=resolved_open_answers.get("Q10", ""),
    )
    q9_embedding = None
    if resolved_initial_argument:
        try:
            q9_embedding = build_q9_embedding({"Q9": resolved_initial_argument})
        except Exception:
            logger.exception("Failed to build AI session Q9 embedding.")

    return {
        "topic": topic_meta.get("title", topic_title),
        "topic_description": (
            topic_description
            or topic_meta.get("topic_description")
            or topic_meta.get("title")
            or topic_title
        ),
        "collection_name": topic_meta.get(
            "collection_name",
            DEFAULT_DIALOGUE_COLLECTION,
        ),
        "user_stance_label": user_stance_label,
        "user_stance_score": user_stance_score,
        "agent_stance": agent_stance,
        "agent_stance_summary": agent_stance_summary,
        "user_initial_argument": resolved_initial_argument,
        "user_reasoning_mode": user_reasoning_mode,
        "survey_open_answers": resolved_open_answers,
        "semantic_vector_interface": semantic_vector_interface,
        "q9_embedding": q9_embedding,
    }


def _upsert_user_stance_profile(
    *,
    user,
    topic_id: int,
    survey_answers: dict[str, int],
    survey_open_answers: dict[str, str],
    user_stance_score: float,
    q9_embedding,
) -> UserStanceProfile:
    """Persist the user's latest pre-survey stance for a topic.

    Shared canonical store (per user+topic) so a later "new dialogue" can offer
    to reuse the previous pre-survey answers instead of re-filling them. Matching
    already upserts this via ``enqueue_for_matching``; this keeps the AI-mode flow
    in sync so AI-only users also have a reusable profile.
    """
    stance_category = resolve_stance_category(
        topic_id=topic_id,
        user_stance_score=user_stance_score,
    )
    profile, _ = UserStanceProfile.objects.update_or_create(
        user=user,
        topic_id=topic_id,
        defaults={
            "stance_score": user_stance_score,
            "stance_category": stance_category,
            "survey_answers": survey_answers,
            "survey_open_answers": survey_open_answers,
            "q9_embedding": q9_embedding,
        },
    )
    return profile


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


def _fallback_offer_fields(*, topic_id: int, state, user_id: int) -> dict:
    """配對等太久要不要提示改跟 AI 對話。

    只有混合入口需要這個提示——分開入口的使用者本來就是自己選的模式，
    回傳 None 讓前端不要顯示對話框。
    """
    queue_entry = state.queue_entry
    if (
        queue_entry is None
        or queue_entry.status != MatchQueueEntry.Status.MATCHING
        or queue_entry.waiting_started_at is None
    ):
        return {"fallback_offer": None}

    user = User.objects.filter(pk=user_id).first()
    if user is None:
        return {"fallback_offer": None}

    entry_mode = get_entry_mode(is_researcher=user_is_researcher(user))
    if entry_mode != PlatformDisplaySetting.EntryMode.MIXED:
        return {"fallback_offer": None}

    timeout_seconds = get_match_fallback_timeout_seconds()
    waited_seconds = int(
        (timezone.now() - queue_entry.waiting_started_at).total_seconds()
    )
    available = waited_seconds >= timeout_seconds

    if available:
        # 記錄第一次被提示的時間（研究資料）。實際的 fallback 授權是在
        # fallback 端點當場重算等待時間，不依賴這個欄位。
        DialogueEntryAssignment.objects.filter(
            user_id=user_id,
            topic_id=topic_id,
            fallback_offered_at__isnull=True,
        ).update(fallback_offered_at=timezone.now())

    return {
        "fallback_offer": {
            "available": available,
            "waited_seconds": waited_seconds,
            "timeout_seconds": timeout_seconds,
        }
    }


def _godot_binding_fields(
    match, *, user_id: int, cancel_reason_override: str | None = None
) -> dict:
    """Godot 綁定房專屬欄位。非綁定房一律回中性值，前端只在 binding_source
    為 "godot" 時使用其餘欄位。

    binding_cancel_reason 只在裁決發生的那一次輪詢之後才會有值，而且房間一旦
    作廢，get_matching_state 之後回的可能已經是這位使用者的新狀態（重新排隊或
    改走 AI，match 不再是那間被作廢的房）。cancel_reason_override 就是為了這個
    情境存在：原因由呼叫端從 MatchingState.binding_cancel_reason 帶進來，而不
    是只看眼前這個 match 的 stats——那樣會漏掉「房間已經換了」的那一次回應。
    前端要在收到當下就反應，不能指望它一直存在。
    """
    binding = godot_binding_info(match)
    if binding is None:
        return {
            "binding_source": None,
            "survey_required": False,
            "survey_deadline": None,
            "partner_state": None,
            "binding_cancel_reason": cancel_reason_override,
        }
    cancel_reason = cancel_reason_override or binding.get("cancel_reason")
    pretest = match_pretest_state(match)
    is_user_a = match.user_a_id == user_id
    self_done = pretest["user_a_done"] if is_user_a else pretest["user_b_done"]
    partner_done = pretest["user_b_done"] if is_user_a else pretest["user_a_done"]
    if cancel_reason:
        partner_state = "left"
    elif partner_done:
        partner_state = "ready"
    else:
        partner_state = "pending"
    return {
        "binding_source": "godot",
        # 房已作廢就不該再叫人填問卷——填了也沒地方收（送出端點會回 409）。
        "survey_required": not self_done and not cancel_reason,
        "survey_deadline": binding.get("survey_deadline"),
        "partner_state": partner_state,
        "binding_cancel_reason": cancel_reason,
    }


def _godot_pretest_incomplete_response(match):
    """Godot 綁定房在雙方前測完成前不得進聊天室；未完成回 Response，完成或
    非綁定房回 None。

    前端已有 isMatchChatReady 擋著，但那只是 UI——繞過它（直接打 API 或開 WS）
    就能在前測資料齊全前產生對話文字，研究資料上會出現「對話早於 s_pre」的紀錄。
    把關要在後端。
    """
    if godot_binding_info(match) is None:
        return None
    if match_pretest_state(match)["both_done"]:
        return None
    return Response(
        {"detail": "雙方都完成前測問卷後才能開始對話。"},
        status=status.HTTP_409_CONFLICT,
    )


def _build_matching_state_payload(*, topic_id: int, state, user_id: int) -> dict:
    queue_entry = state.queue_entry
    match = state.match
    other_user_id = None
    other_user_name = None
    if match:
        other_user = _get_other_user(match, user_id=user_id)
        other_user_id = other_user.id
        anon_ids = assign_anonymous_ids(match.room_id, [match.user_a_id, match.user_b_id])
        other_user_name = anon_ids[other_user_id]

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
        **_fallback_offer_fields(topic_id=topic_id, state=state, user_id=user_id),
        **_godot_binding_fields(
            match,
            user_id=user_id,
            cancel_reason_override=getattr(state, "binding_cancel_reason", None),
        ),
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
    anon_ids = assign_anonymous_ids(match.room_id, [match.user_a_id, match.user_b_id])
    payload = {
        "room_id": match.room_id,
        "match_id": match.id,
        "topic_id": match.topic_id,
        "status": _room_match_state_status(match),
        "other_user_id": other_user.id,
        "other_user_name": anon_ids[other_user.id],
        "stance_drift": _get_latest_room_stance_drift(match=match, user_id=user_id),
        # 進房第一次載入就把 AI 開場帶回去；還沒生成時是 None，前端據此
        # 決定要不要打 POST /opening/ 觸發生成。
        "opening": _serialize_match_opening(match),
        **_match_presence_fields(match, user_id=user_id),
        "messages": messages,
    }
    return MatchingRoomMessagesSerializer(payload, context={"anon_ids": anon_ids}).data


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


def _semantic_tree_root_name(match: DialogueMatch) -> str:
    return _semantic_tree_root_name_for_topic_id(match.topic_id)


def _semantic_tree_root_name_for_topic_id(topic_id: int | None) -> str:
    return TOPIC_CONFIGS.get(topic_id, {}).get("title") or "核電"


def _get_dialogue_session_record_for_user(*, session_id: str, user_id: int):
    session_record, _ = _restore_dialogue_session_record_for_user(
        session_id=session_id,
        user_id=user_id,
    )
    if not session_record:
        return None, Response(
            {"detail": "找不到對話 session，請重新建立對話。"},
            status=status.HTTP_404_NOT_FOUND,
        )

    return session_record, None


def _get_history_ai_record_for_user(*, session_id: str, user_id: int):
    return (
        DialogueSessionRecord.objects.filter(
            user_id=user_id,
            session_id=session_id,
        )
        .order_by("-last_activity_at", "-id")
        .first()
    )


def _history_ai_turns(record: DialogueSessionRecord):
    return AIConversation.objects.filter(
        user_id=record.user_id,
        session_id=record.session_id,
    ).order_by("created_at", "id")


def _history_ai_messages(record: DialogueSessionRecord) -> list[dict]:
    messages = []
    for turn in _history_ai_turns(record):
        if turn.user_prompt:
            messages.append(
                {
                    "id": f"ai-{turn.id}-user",
                    "source_id": str(turn.id),
                    "role": "user",
                    "sender_label": "我",
                    "content": turn.user_prompt,
                    "created_at": turn.created_at,
                }
            )
        if turn.ai_response:
            messages.append(
                {
                    "id": f"ai-{turn.id}-agent",
                    "source_id": str(turn.id),
                    "role": "agent",
                    "sender_label": "BridgeUs",
                    "content": turn.ai_response,
                    "created_at": turn.created_at,
                }
            )
    return messages


def _history_match_messages(match: DialogueMatch, *, user_id: int) -> list[dict]:
    anon_ids = assign_anonymous_ids(match.room_id, [match.user_a_id, match.user_b_id])
    messages = []
    for message in match.messages.select_related("sender").order_by("created_at", "id"):
        is_current_user = message.sender_id == user_id
        messages.append(
            {
                "id": f"match-{message.id}",
                "source_id": str(message.id),
                "role": "user" if is_current_user else "partner",
                "sender_label": "我" if is_current_user else anon_ids[message.sender_id],
                "content": message.content,
                "created_at": message.created_at,
            }
        )
    return messages


def _approved_match_messages(match: DialogueMatch) -> list[dict]:
    """給知識庫『對話詳情』頁用的逐字稿：跟 _history_match_messages 不同，
    這裡沒有『目前使用者』（瀏覽的人不是對話當事人），一律用 A/B 方標示，
    不帶 sender_id/使用者名稱——不需要讓第三方看得出「這是誰說的」，直接按
    speaker side 標示更單純。"""
    messages = []
    for message in match.messages.select_related("sender").order_by("created_at", "id"):
        side = "a" if message.sender_id == match.user_a_id else "b"
        messages.append(
            {
                "id": f"match-{message.id}",
                "side": side,
                "sender_label": "A方" if side == "a" else "B方",
                "content": message.content,
                "created_at": message.created_at,
            }
        )
    return messages


def _last_message_content_by_role(messages: list[dict], role: str) -> str:
    """最後一則指定角色的訊息內容，找不到就回空字串（例如對方/AI 還沒回過）。"""
    for message in reversed(messages):
        if message["role"] == role:
            return message.get("content", "")
    return ""


def _is_latest_ai_session_for_topic(record: DialogueSessionRecord) -> bool:
    latest = (
        DialogueSessionRecord.objects.filter(user_id=record.user_id, topic_id=record.topic_id)
        .order_by("-last_activity_at", "-id")
        .first()
    )
    return latest is not None and latest.pk == record.pk


def _history_ai_summary(
    record: DialogueSessionRecord,
    *,
    completed_session_ids: set[str] | None = None,
    is_latest_for_topic: bool | None = None,
) -> dict:
    messages = _history_ai_messages(record)
    user_messages = [message for message in messages if message["role"] == "user"]
    preview_source = user_messages[-1] if user_messages else (messages[-1] if messages else None)
    last_overall_message = messages[-1] if messages else None
    # AI 對話沒有真正的「已結束」流程——record.status 在正式流程裡永遠是
    # active，不像真人配對房有明確的離開/關閉動作。改用兩個間接訊號判斷是否
    # 已結束：(1) 這個 topic 的對話後問卷已經填完，或 (2) 使用者後來又對同一
    # 個 topic 開了新的 session——沒填問卷就開新對話，代表舊的那場已經被放
    # 棄了，不該再被通知導回去。completed_session_ids／is_latest_for_topic
    # 由呼叫端一次查好整批傳進來，避免列表頁對每筆記錄各打好幾次 DB。
    if completed_session_ids is not None:
        has_post_response = record.session_id in completed_session_ids
    else:
        has_post_response = PostDialogueResponse.objects.filter(
            user_id=record.user_id,
            session_id=record.session_id,
        ).exists()
    if is_latest_for_topic is None:
        is_latest_for_topic = _is_latest_ai_session_for_topic(record)
    is_completed = has_post_response or not is_latest_for_topic
    return {
        "kind": "ai",
        "id": record.session_id,
        "session_id": record.session_id,
        # Alias of session_id — AI sessions don't have a real "room", but
        # exposing the same key as match conversations lets the frontend
        # treat both kinds uniformly instead of branching on kind. Computed
        # here rather than stored, so it's never missing for older records.
        "room_id": record.session_id,
        "topic_id": record.topic_id,
        "topic_title": record.topic_title,
        "status": DialogueSessionRecord.Status.CLOSED if is_completed else record.status,
        "message_count": len(messages),
        "last_message_preview": (preview_source or {}).get("content", "")[:120],
        # 通知列表專用：一定是「AI 最後回了什麼」，不是自己最後問了什麼——
        # last_message_preview 刻意留給歷史列表用（優先秀自己最後問的問題）。
        "last_partner_message_preview": _last_message_content_by_role(messages, "agent")[:120],
        # 通知鈴鐺用：最新一則若是 AI 回覆而非自己送出的，就算「未讀」——跟
        # match summary 的 last_message_from_partner 同一套判斷方式，讓前端
        # 不用分 kind 就能算未讀。
        "last_message_from_partner": bool(last_overall_message) and last_overall_message.get("role") == "agent",
        "last_activity_at": record.last_activity_at,
    }


def _history_match_summary(match: DialogueMatch, *, user_id: int) -> dict:
    messages = _history_match_messages(match, user_id=user_id)
    preview_source = messages[-1] if messages else None
    return {
        "kind": "match",
        "id": match.room_id,
        "room_id": match.room_id,
        "topic_id": match.topic_id,
        "topic_title": _semantic_tree_root_name(match),
        "status": match.status,
        "message_count": len(messages),
        "last_message_preview": (preview_source or {}).get("content", "")[:120],
        # 通知列表專用：一定是「對方最後傳了什麼」，不是自己最後傳了什麼。
        "last_partner_message_preview": _last_message_content_by_role(messages, "partner")[:120],
        # 通知鈴鐺用來判斷「未讀」：只有對方（非本人）發出的最新一則才算數，
        # 自己送出的最後一則不該讓自己的鈴鐺亮起紅點。
        "last_message_from_partner": bool(preview_source) and preview_source.get("role") == "partner",
        "last_activity_at": (
            (preview_source or {}).get("created_at")
            or match.closed_at
            or match.created_at
        ),
    }


def _timeline_access_fields(*, user_id: int, kind: str, conversation_id: str, conversation) -> dict:
    """Tell the frontend whether to offer the timeline slider at all, so a locked
    participant never even sees the entry point (the API enforces it anyway)."""
    state = timeline_unlock_state(
        user_id=user_id,
        kind=kind,
        conversation_id=conversation_id,
        conversation=conversation,
    )
    return {
        "timeline_unlocked": state["unlocked"],
        "timeline_lock_reason": state["reason"],
        "timeline_unlocks_at": state["unlocks_at"],
    }


def _history_ai_detail(record: DialogueSessionRecord, *, user_id: int) -> dict:
    from apps.matching.services.semantic_tree import semantic_tree_session_payload

    session_record = _dialogue_session_cache_payload_from_record(record)
    semantic_tree = semantic_tree_session_payload(
        session_record=session_record,
        session_id=record.session_id,
        root_name=_semantic_tree_root_name_for_topic_id(record.topic_id),
    )
    messages = _history_ai_messages(record)
    return {
        **_history_ai_summary(record),
        "messages": messages,
        "semantic_tree": MatchingRoomSemanticTreeSerializer(semantic_tree).data,
        **_timeline_access_fields(
            user_id=user_id,
            kind="ai",
            conversation_id=record.session_id,
            conversation=record,
        ),
    }


def _history_match_detail(match: DialogueMatch, *, user_id: int) -> dict:
    from apps.matching.services.semantic_tree import semantic_tree_payload

    semantic_tree = semantic_tree_payload(
        match=match,
        root_name=_semantic_tree_root_name(match),
        current_user_id=user_id,
    )
    messages = _history_match_messages(match, user_id=user_id)
    return {
        **_history_match_summary(match, user_id=user_id),
        "messages": messages,
        "semantic_tree": MatchingRoomSemanticTreeSerializer(semantic_tree).data,
        **_timeline_access_fields(
            user_id=user_id,
            kind="match",
            conversation_id=match.room_id,
            conversation=match,
        ),
    }


@lru_cache(maxsize=1)
def _get_dialogue_runtime():
    from apps.matching.services.ai_agent import (
        DialogueAgent,
        DialoguePhase,
        DialogueSession,
    )

    return DialogueAgent, DialoguePhase, DialogueSession


@lru_cache(maxsize=8)
def get_dialogue_agent(collection_name: str):
    DialogueAgent, _, _ = _get_dialogue_runtime()
    return DialogueAgent(collection_name=collection_name)


class AIConversationListCreate(generics.ListCreateAPIView):
    serializer_class = AIConversationSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return AIConversation.objects.filter(user=self.request.user).order_by(
            "-created_at"
        )

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)


class AIConversationDetail(generics.RetrieveUpdateDestroyAPIView):
    serializer_class = AIConversationSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return AIConversation.objects.filter(user=self.request.user)


class DialogueTopicListView(APIView):
    """議題清單，依請求者角色過濾。

    刻意不使用 JWTStatelessUserAuthentication：它回傳的 TokenUser.groups 是
    EmptyManager，user_is_researcher() 對它永遠是 False，研究者會被當成一般
    使用者而看不到只對研究者開放的議題。這裡需要真正的 User，所以吃 settings
    裡的預設 JWTAuthentication。
    """

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        topics = visible_topics(is_researcher=user_is_researcher(request.user))
        serializer = DialogueTopicSerializer(topics, many=True)
        return Response(serializer.data)


class DialogueTopicTrendingView(APIView):
    """GET /api/dialogue/topics/trending/

    「熱門度」= 該議題累計的 AI 對話數 + 真人配對數，由高到低排序。給知識庫
    首頁的「近期熱門」區塊用，不是嚴謹的統計指標，只是活動量代理值。跟
    DialogueTopicListView 一樣走 visible_topics()，被 Supervisor 關閉的議題
    不會出現在這裡。
    """

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        rows = []
        for topic in visible_topics(is_researcher=user_is_researcher(request.user)):
            topic_id = topic["id"]
            hits = (
                AIConversation.objects.filter(topic_id=topic_id).count()
                + DialogueMatch.objects.filter(topic_id=topic_id).count()
            )
            rows.append({"id": topic_id, "title": topic["title"], "hits": hits})

        rows.sort(key=lambda row: row["hits"], reverse=True)

        serializer = DialogueTopicTrendingSerializer(rows, many=True)
        return Response(serializer.data)


class PolicyIdeaListView(APIView):
    """GET /api/policy-ideas/?section=hot&limit=5

    公共政策網路參與平臺（join.gov.tw）的提案快照。資料由
    `manage.py import_join_ideas` 匯入，見 PolicyIdea 的 docstring。

    目前唯一的消費者是 Godot 大廳第二隻教學青蛙的台詞（npc_frog2.gd）——玩家
    不知道要貼什麼議題的時候，給幾個真實世界正在被討論的題目當引子。

    需要登入：跟 /issues/、/titles/me/ 一致，Godot client 拿的是主功能交接過來的
    同一個 JWT。桌面開發沒有 token，青蛙那邊有備援台詞（見 npc_frog2.gd），
    不會因為這支打不通就開不了對話。
    """

    permission_classes = [permissions.IsAuthenticated]

    # 一次最多給幾筆。上限存在的理由不是效能（快照本來就只有個位數筆），是
    # 消費者：對話框一行放一則，超過幾則就變成一直按空白鍵的酷刑。
    DEFAULT_LIMIT = 5
    MAX_LIMIT = 20

    def get(self, request):
        section = request.query_params.get("section", PolicyIdea.Section.HOT)
        if section not in PolicyIdea.Section.values:
            return Response(
                {"detail": "section 無效。"}, status=status.HTTP_400_BAD_REQUEST
            )

        raw_limit = request.query_params.get("limit")
        try:
            limit = int(raw_limit) if raw_limit is not None else self.DEFAULT_LIMIT
        except (TypeError, ValueError):
            return Response(
                {"detail": "limit 需為整數。"}, status=status.HTTP_400_BAD_REQUEST
            )
        limit = max(1, min(limit, self.MAX_LIMIT))

        # 排序照 rank，不重算——rank 就是平臺自己的「熱門」順序（見 PolicyIdea）。
        ideas = PolicyIdea.objects.filter(section=section).order_by("rank")[:limit]
        return Response(PolicyIdeaSerializer(ideas, many=True).data)


class DialogueSurveyView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    # 不用 JWTStatelessUserAuthentication：它回傳的 TokenUser 把 is_active
    # 寫死成 True，且 groups 是 EmptyManager（見 api/permissions.py 的說明）。
    # 省下一次 DB 查詢換來兩個安靜的錯誤行為，在這個規模不划算。

    def get(self, request, topic_id: int):
        survey = get_dialogue_survey(topic_id)
        if not survey:
            return Response(
                {"detail": "找不到這個議題的問卷設定。"},
                status=status.HTTP_404_NOT_FOUND,
            )

        serializer = DialogueSurveySerializer(survey)
        return Response(serializer.data)


class DialogueStanceProfileView(APIView):
    """GET /api/dialogue/topics/<topic_id>/stance-profile/

    Reports whether the user already has a saved pre-survey stance for this
    topic, and returns the stored answers so the frontend can offer to reuse
    them instead of re-filling the survey for a new dialogue.
    """

    permission_classes = [permissions.IsAuthenticated]
    # 不用 JWTStatelessUserAuthentication：它回傳的 TokenUser 把 is_active
    # 寫死成 True，且 groups 是 EmptyManager（見 api/permissions.py 的說明）。
    # 省下一次 DB 查詢換來兩個安靜的錯誤行為，在這個規模不划算。

    def get(self, request, topic_id: int):
        if not get_dialogue_survey(topic_id):
            return Response(
                {"detail": "找不到這個議題的問卷設定。"},
                status=status.HTTP_404_NOT_FOUND,
            )

        # request.user 是 stateless TokenUser：直接丟進 filter 會讓 Django 誤觸
        # TokenUser.__getattr__ 回傳的 resolve_expression=None 而崩潰，改用 id 過濾。
        profile = (
            UserStanceProfile.objects.filter(user_id=request.user.id, topic_id=topic_id)
            .order_by("-updated_at", "-id")
            .first()
        )
        if profile is None:
            return Response({"exists": False, "topic_id": topic_id})

        return Response(
            {
                "exists": True,
                "topic_id": topic_id,
                "stance_score": profile.stance_score,
                "stance_category": profile.stance_category,
                "survey_answers": profile.survey_answers or {},
                "survey_open_answers": profile.survey_open_answers or {},
                "updated_at": profile.updated_at,
            }
        )


def _create_ai_dialogue_session(
    *,
    user,
    topic_id: int,
    survey_answers: dict,
    survey_open_answers: dict,
    topic_title: str | None = None,
    topic_description: str | None = None,
    user_initial_argument: str | None = None,
) -> dict:
    """建立一場 AI 對話 session，回傳 API 回應用的 payload。

    分流端點、fallback 端點與 DialogueSessionCreateView 共用這一份。
    topic_title / topic_description / user_initial_argument 沒給時一律由後端
    從 TOPIC_CONFIGS 與問卷 Q9 補齊——混合入口不接受客戶端送這些欄位。
    """
    _, _, DialogueSession = _get_dialogue_runtime()

    topic_meta = TOPIC_CONFIGS.get(topic_id, {})
    resolved_open_answers = _resolve_open_answers(
        topic_id=topic_id,
        survey_open_answers=survey_open_answers,
    )
    if user_initial_argument is None:
        user_initial_argument = resolved_open_answers.get("Q9", "")

    # title 與 description 的 fallback 條件刻意不同：沒有標題的對話沒有意義，
    # 所以空字串也要補；但「這個議題沒有補充說明」是合法狀態，明確傳空字串
    # 就該保持空的，不能被 TOPIC_CONFIGS 蓋回去。
    topic_config = _build_topic_config(
        topic_id=topic_id,
        topic_title=topic_title or topic_meta.get("title", ""),
        topic_description=(
            topic_description
            if topic_description is not None
            else topic_meta.get("topic_description", "")
        ),
        survey_answers=survey_answers,
        survey_open_answers=survey_open_answers,
        user_initial_argument=user_initial_argument,
    )

    # 只有問卷真的填了才寫 profile，避免用空答案的中立預設值蓋掉真實立場。
    if survey_answers:
        _upsert_user_stance_profile(
            user=user,
            topic_id=topic_id,
            survey_answers=survey_answers,
            survey_open_answers=topic_config["survey_open_answers"],
            user_stance_score=topic_config["user_stance_score"],
            q9_embedding=topic_config["q9_embedding"],
        )

    session = DialogueSession(
        topic=topic_config["topic"],
        topic_description=topic_config["topic_description"],
        agent_stance=topic_config["agent_stance"],
        agent_stance_summary=topic_config["agent_stance_summary"],
        user_stance_label=topic_config["user_stance_label"],
        user_stance_score=topic_config["user_stance_score"],
        user_initial_argument=topic_config["user_initial_argument"],
        user_reasoning_mode=topic_config["user_reasoning_mode"],
    )

    session_id = uuid4().hex
    session_record = {
        "user_id": user.id,
        "session_id": session_id,
        "topic_id": topic_id,
        "topic_title": topic_config["topic"],
        "collection_name": topic_config["collection_name"],
        "survey_context": {
            "survey_answers": survey_answers,
            "survey_open_answers": topic_config["survey_open_answers"],
            "semantic_vector_interface": topic_config["semantic_vector_interface"],
            "q9_embedding": topic_config["q9_embedding"],
        },
        "session": session.to_dict(),
    }
    _cache_dialogue_session_record(session_record)
    _persist_dialogue_session_record(session_record)
    _close_superseded_dialogue_sessions(
        user_id=user.id,
        topic_id=topic_id,
        keep_session_id=session_id,
    )

    return {
        "session_id": session_id,
        "dialogue_phase": session.dialogue_phase.value,
        "stance_score": session.user_stance_score,
        "stance_category": resolve_stance_category(
            topic_id=topic_id,
            user_stance_score=session.user_stance_score,
        ),
        "stance_label": session.user_stance_label,
        "stance_drift": None,
        "history": session.to_dict()["history"],
    }


class DialogueSessionCreateView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        serializer = DialogueSessionCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        validated = serializer.validated_data

        gate = _entry_gate_response(
            user=request.user, topic_id=validated["topic_id"], target="ai"
        )
        if gate is not None:
            return gate

        payload = _create_ai_dialogue_session(
            user=request.user,
            topic_id=validated["topic_id"],
            survey_answers=validated.get("survey_answers", {}),
            survey_open_answers=validated.get("survey_open_answers", {}),
            topic_title=validated["topic_title"],
            topic_description=validated.get("topic_description", ""),
            user_initial_argument=validated.get("user_initial_argument", ""),
        )
        return Response(payload, status=status.HTTP_201_CREATED)


class DialogueEntryView(APIView):
    """一般使用者的唯一對話入口：填完問卷後由後端依立場分流。

    中立 → AI 對話；極端（support／oppose）→ 真人配對。分流規則直接用
    matcher.can_enter_human_matching()，與佇列內部同一份定義——兩邊分歧
    會造成「入口說你該配對、佇列說你不能配對」的死路。
    """

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        from apps.matching.services.matcher import (
            can_enter_human_matching,
            enqueue_for_matching,
        )

        serializer = DialogueEntrySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        validated = serializer.validated_data
        topic_id = validated["topic_id"]

        is_researcher = user_is_researcher(request.user)
        if not is_topic_visible(topic_id=topic_id, is_researcher=is_researcher):
            return Response(
                {"detail": "找不到這個議題。"}, status=status.HTTP_404_NOT_FOUND
            )

        survey_answers = validated["survey_answers"]
        survey_open_answers = validated.get("survey_open_answers", {})

        stance_score = _compute_user_stance_score(
            topic_id=topic_id, survey_answers=survey_answers
        )
        stance_category = resolve_stance_category(
            topic_id=topic_id, user_stance_score=stance_score
        )
        support_threshold, oppose_threshold = get_stance_thresholds(topic_id=topic_id)

        route = (
            DialogueEntryAssignment.Route.MATCH
            if can_enter_human_matching(stance_category)
            else DialogueEntryAssignment.Route.AI
        )

        DialogueEntryAssignment.objects.update_or_create(
            user=request.user,
            topic_id=topic_id,
            defaults={
                "route": route,
                "stance_score": Decimal(str(stance_score)),
                "stance_category": stance_category,
                "support_threshold": support_threshold,
                "oppose_threshold": oppose_threshold,
                "entry_mode_at_assignment": get_entry_mode(
                    is_researcher=is_researcher
                ),
                # 重填問卷＝重新分流，之前的逾時提示紀錄不再適用。
                "fallback_offered_at": None,
                "fallback_accepted_at": None,
            },
        )

        if route == DialogueEntryAssignment.Route.AI:
            payload = _create_ai_dialogue_session(
                user=request.user,
                topic_id=topic_id,
                survey_answers=survey_answers,
                survey_open_answers=survey_open_answers,
            )
            return Response(
                {"route": "ai", **payload}, status=status.HTTP_201_CREATED
            )

        state = enqueue_for_matching(
            user=request.user,
            topic_id=topic_id,
            stance_score=stance_score,
            stance_category=stance_category,
            survey_answers=survey_answers,
            survey_open_answers=_resolve_open_answers(
                topic_id=topic_id,
                survey_open_answers=survey_open_answers,
            ),
        )
        return Response(
            {
                "route": "match",
                **_build_matching_state_payload(
                    topic_id=topic_id,
                    state=state,
                    user_id=request.user.id,
                ),
            },
            status=status.HTTP_201_CREATED,
        )


class DialogueEntryFallbackView(APIView):
    """配對等太久，使用者同意改跟 AI 對話。

    授權條件當場從 MatchQueueEntry.waiting_started_at 重算，不看
    fallback_offered_at——後者會讓這個端點依賴前端「必須先輪詢過 status」，
    多一個沒必要的隱性順序耦合。
    """

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        from apps.matching.services.matcher import (
            MatchingAlreadyMatchedError,
            MatchingNotFoundError,
            cancel_matching,
        )

        try:
            topic_id = int(request.data.get("topic_id"))
        except (TypeError, ValueError):
            return Response(
                {"detail": "topic_id 必須是有效的議題編號。"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        assignment = DialogueEntryAssignment.objects.filter(
            user=request.user, topic_id=topic_id
        ).first()
        if (
            assignment is None
            or assignment.route != DialogueEntryAssignment.Route.MATCH
        ):
            return Response(
                {"detail": "目前沒有等待中的配對。"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        queue_entry = (
            MatchQueueEntry.objects.filter(
                user=request.user,
                topic_id=topic_id,
                status=MatchQueueEntry.Status.MATCHING,
            )
            .order_by("-waiting_started_at", "-id")
            .first()
        )
        if queue_entry is None:
            return Response(
                {"detail": "目前沒有等待中的配對。"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        waited_seconds = (
            timezone.now() - queue_entry.waiting_started_at
        ).total_seconds()
        if waited_seconds < get_match_fallback_timeout_seconds():
            return Response(
                {"detail": "尚未達到等待時間。"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        profile = UserStanceProfile.objects.filter(
            user=request.user, topic_id=topic_id
        ).first()
        if profile is None:
            return Response(
                {"detail": "找不到立場問卷紀錄，請重新填寫。"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            cancel_matching(user=request.user, topic_id=topic_id)
        except MatchingAlreadyMatchedError as exc:
            return Response(
                {"detail": str(exc)},
                status=status.HTTP_409_CONFLICT,
            )
        except MatchingNotFoundError as exc:
            return Response(
                {"detail": str(exc)},
                status=status.HTTP_404_NOT_FOUND,
            )

        now = timezone.now()
        assignment.fallback_accepted_at = now
        if assignment.fallback_offered_at is None:
            assignment.fallback_offered_at = now
        assignment.save(
            update_fields=["fallback_offered_at", "fallback_accepted_at"]
        )

        payload = _create_ai_dialogue_session(
            user=request.user,
            topic_id=topic_id,
            survey_answers=profile.survey_answers or {},
            survey_open_answers=profile.survey_open_answers or {},
        )
        return Response({"route": "ai", **payload}, status=status.HTTP_201_CREATED)


ENTRY_GATE_DETAIL = "請從議題頁面開始對話。"
# detail 對使用者刻意含糊（說清楚等於洩漏分組），所以前端無法從它判斷發生了
# 什麼。這個 code 是給前端看的：收到就代表「伺服器在混合入口、你還沒被分流」，
# 前端據此改走 /api/dialogue/entry/，而不是把死路丟給使用者。
ENTRY_GATE_CODE = "entry_gate"


def _entry_gate_denied() -> Response:
    return Response(
        {"detail": ENTRY_GATE_DETAIL, "code": ENTRY_GATE_CODE},
        status=status.HTTP_403_FORBIDDEN,
    )


def _entry_gate_response(*, user, topic_id: int, target: str):
    """混合入口下擋掉繞過分流的直接呼叫；回傳 Response 代表擋下，None 代表放行。

    ?mode= 只是 query string，不在後端擋的話受試者改個網址就能自己換組，
    實驗分組就不可信了。訊息刻意不說明分流規則——講了等於告訴受試者
    自己被分到哪一組，會影響後續作答。

    target: "match" 或 "ai"
    """
    if get_entry_mode(is_researcher=user_is_researcher(user)) != (
        PlatformDisplaySetting.EntryMode.MIXED
    ):
        return None

    assignment = DialogueEntryAssignment.objects.filter(
        user=user, topic_id=topic_id
    ).first()
    if assignment is None:
        return _entry_gate_denied()

    if target == "match":
        allowed = assignment.route == DialogueEntryAssignment.Route.MATCH
    else:
        allowed = (
            assignment.route == DialogueEntryAssignment.Route.AI
            or assignment.fallback_accepted_at is not None
        )

    if allowed:
        return None
    return _entry_gate_denied()


class MeView(APIView):
    """目前登入者的即時身分與入口模式。

    前端不從 JWT 的 is_researcher claim 讀這些：那個 claim 是簽發當下的快照，
    使用者被降級後仍會隨著 refresh token 存活最長 7 天。這裡每次都查 DB。
    """

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        is_researcher = user_is_researcher(request.user)
        return Response(
            {
                "id": request.user.id,
                "username": request.user.username,
                "display_name": request.user.display_name,
                "is_researcher": is_researcher,
                "entry_mode": get_entry_mode(is_researcher=is_researcher),
            }
        )


class DialogueSessionLatestView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        topic_id = request.query_params.get("topic_id")
        try:
            topic_id = int(topic_id)
        except (TypeError, ValueError):
            return Response(
                {"detail": "topic_id 必須是有效的議題編號。"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        record = (
            DialogueSessionRecord.objects.filter(
                user=request.user,
                topic_id=topic_id,
                status=DialogueSessionRecord.Status.ACTIVE,
            )
            .order_by("-last_activity_at", "-id")
            .first()
        )
        if record is None:
            return Response(
                {"detail": "找不到可恢復的對話 session。"},
                status=status.HTTP_404_NOT_FOUND,
            )

        session_record, restored_from = _restore_dialogue_session_record_for_user(
            session_id=record.session_id,
            user_id=request.user.id,
        )
        if session_record is None:
            return Response(
                {"detail": "找不到可恢復的對話 session。"},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(
            _dialogue_session_response_payload(
                session_record=session_record,
                restored_from=restored_from,
                user_id=request.user.id,
            )
        )


class DialogueSessionDetailView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, session_id: str):
        session_record, restored_from = _restore_dialogue_session_record_for_user(
            session_id=session_id,
            user_id=request.user.id,
        )
        if session_record is None:
            return Response(
                {"detail": "找不到對話 session，請重新建立對話。"},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(
            _dialogue_session_response_payload(
                session_record=session_record,
                restored_from=restored_from,
                user_id=request.user.id,
            )
        )


def _previous_ai_turn_is_question(*, user_id: int, session_id: str) -> bool:
    """input gate 規則 5 用的「上一則 AI 回覆是否以提問收尾」。

    正常情況讀 `AIConversation.ai_turn_is_question`（由策略層寫入）。還沒有
    任何 AI 回覆 turn 時，若這場有 AI 開場，就當作 True：開場本身就是在邀請
    對方挑一個方向開始講，這時回「第二個」不是低訊息量輸入。開場沒有對應的
    AIConversation turn（沒有使用者發言可配對），所以這裡要另外看 history。
    """
    flag = (
        AIConversation.objects.filter(
            user_id=user_id,
            session_id=session_id,
            ai_response__isnull=False,
        )
        .exclude(ai_response="")
        .order_by("-created_at", "-id")
        .values_list("ai_turn_is_question", flat=True)
        .first()
    )
    if flag is not None:
        return bool(flag)
    return _session_starts_with_ai_opening(user_id=user_id, session_id=session_id)


def _session_starts_with_ai_opening(*, user_id: int, session_id: str) -> bool:
    session_state = (
        DialogueSessionRecord.objects.filter(
            user_id=user_id,
            session_id=session_id,
        )
        .values_list("session_state", flat=True)
        .first()
    ) or {}
    history = session_state.get("history") or []
    return bool(history) and history[0].get("role") == "agent"


def _apply_reply_input_gate(*, session_id: str, user, user_message: str):
    """Run the input gate on the REST reply path.

    Returns a Response when the message must not reach the LLM, or None to let
    the caller carry on. Mirrors DialogueStreamConsumer._passes_input_gate; the
    payload shape matches the WebSocket events so the frontend can render both
    with the same code.
    """
    from apps.matching.services.input_gate import (
        COOLDOWN_NOTICE,
        InputVerdict,
        classify,
        fallback_message,
        rate_limit_notice,
        throttle_tier,
    )
    from apps.matching.services.input_gate_store import record_ai_attempt
    from apps.matching.services.rate_limit import (
        check_rate_limit,
        cooldown_remaining,
        start_cooldown,
    )

    scope = f"ai:{session_id}:{user.id}"
    remaining = cooldown_remaining(scope)
    if remaining:
        return Response(
            {
                "type": "input_cooldown",
                "seconds": remaining,
                "detail": COOLDOWN_NOTICE.format(seconds=remaining),
            },
            status=status.HTTP_429_TOO_MANY_REQUESTS,
        )

    rate = check_rate_limit(user.id)
    if not rate["allowed"]:
        return Response(
            {
                "type": "rate_limited",
                "reason": rate["reason"],
                "retry_after": rate["retry_after"],
                "detail": rate_limit_notice(rate["reason"]),
            },
            status=status.HTTP_429_TOO_MANY_REQUESTS,
        )

    prev_is_question = _previous_ai_turn_is_question(
        user_id=user.id,
        session_id=session_id,
    )
    verdict = classify(user_message, prev_ai_is_question=prev_is_question)
    if verdict is InputVerdict.VALID:
        record_ai_attempt(session_id, blocked=False)
        return None

    count = record_ai_attempt(
        session_id,
        blocked=True,
        profanity=verdict is InputVerdict.PROFANITY_ONLY,
    )
    tier = throttle_tier(count)
    if tier == "cooldown":
        seconds = start_cooldown(scope)
        return Response(
            {
                "type": "input_cooldown",
                "seconds": seconds,
                "invalid_input_count": count,
                "detail": COOLDOWN_NOTICE.format(seconds=seconds),
            },
            status=status.HTTP_429_TOO_MANY_REQUESTS,
        )

    # 200, not an error status: the participant gets a real (static, zero-token)
    # reply. Only the LLM call is skipped.
    return Response(
        {
            "type": "input_blocked",
            "presentation": tier,
            "reason": verdict.value,
            "reply": fallback_message(verdict, count),
            "chunks": [fallback_message(verdict, count)],
            "invalid_input_count": count,
        }
    )


class DialogueSessionReplyView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, session_id: str):
        _, DialoguePhase, DialogueSession = _get_dialogue_runtime()
        serializer = DialogueReplySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        session_record, _ = _restore_dialogue_session_record_for_user(
            session_id=session_id,
            user_id=request.user.id,
        )

        if not session_record:
            return Response(
                {"detail": "找不到對話 session，請重新建立對話。"},
                status=status.HTTP_404_NOT_FOUND,
            )

        user_message = serializer.validated_data["message"].strip()

        # The REST path is the frontend's fallback when the WebSocket cannot be
        # opened. It reaches the same RAG + Claude + AIConversation code, so the
        # input gate has to run here too — otherwise a participant on a flaky
        # connection bypasses it entirely.
        gate_response = _apply_reply_input_gate(
            session_id=session_id,
            user=request.user,
            user_message=user_message,
        )
        if gate_response is not None:
            return gate_response

        session = DialogueSession.from_dict(session_record["session"])
        session.add_user_message(user_message)
        session.dialogue_phase = DialoguePhase.from_turn_count(session.turn_count)

        prompt_embedding = None
        try:
            from chat.services.embedding import get_embedding

            prompt_embedding = get_embedding(user_message)
        except Exception:
            logger.exception(
                "Embedding failed for AI dialogue prompt session=%s user=%s.",
                session_id,
                request.user.id,
            )

        saved_turn = AIConversation.objects.create(
            user=request.user,
            session_id=session_id,
            topic_id=session_record.get("topic_id"),
            user_prompt=user_message,
            dialogue_phase=session.dialogue_phase.value,
            embedding=prompt_embedding,
        )

        # Neither this path nor the WebSocket path gets assistant prefill (the
        # model rejects it); DialogueAgent logs that once at construction. The
        # output contract is enforced below by ReplyStreamGate, which fails closed.
        agent = get_dialogue_agent(session_record["collection_name"])

        try:
            raw_reply = agent.respond(session)
        except Exception:
            logger.exception(
                "Dialogue reply failed for session %s with collection %s.",
                session_id,
                session_record["collection_name"],
            )
            return Response(
                {
                    "detail": "目前無法取得 AI 回覆，請稍後再試。"
                },
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        from apps.matching.services.ai_agent import ReplyStreamGate, split_into_chunks

        # respond() is non-streaming, so the gate parses the whole body in one feed.
        gate = ReplyStreamGate()
        gate.feed(raw_reply)
        _, contract_ok = gate.finish()

        if not contract_ok:
            # Fail closed: never hand the raw body back to the client. This path
            # has no corrective retry or salvage (respond() takes no correction
            # and the live UI uses the WebSocket path) — it just 503s.
            logger.error(
                "Output contract violated on REST reply session=%s turn=%s "
                "leak_pattern=%r buffer[:200]=%r reply[:200]=%r",
                session_id,
                saved_turn.id,
                gate.leak_pattern,
                gate.buffered_preview[:200],
                gate.reply[:200],
            )
            saved_turn.internal_judgment = (
                f"{gate.buffered_preview}\n\n"
                f"[reply leak_pattern={gate.leak_pattern!r}] {gate.reply}"
            )
            saved_turn.contract_violated = True
            saved_turn.dialogue_phase = session.dialogue_phase.value
            saved_turn.save(
                update_fields=[
                    "internal_judgment",
                    "contract_violated",
                    "dialogue_phase",
                ]
            )
            return Response(
                {
                    "detail": "目前無法取得 AI 回覆，請稍後再試。"
                },
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        reply = gate.reply
        chunks = split_into_chunks(reply)
        session.add_agent_message(reply)
        session_record["session"] = session.to_dict()
        saved_turn.ai_response = reply
        saved_turn.internal_judgment = gate.judgment
        saved_turn.contract_violated = False
        saved_turn.dialogue_phase = session.dialogue_phase.value
        saved_turn.save(
            update_fields=[
                "ai_response",
                "internal_judgment",
                "contract_violated",
                "dialogue_phase",
            ]
        )
        stance_drift = _update_ai_session_stance_drift(
            session_record=session_record,
            session_id=session_id,
            user_id=request.user.id,
        )
        _cache_dialogue_session_record(session_record)
        _persist_dialogue_session_record(session_record)

        return Response(
            {
                "reply": reply,
                "chunks": chunks,
                "dialogue_phase": session.dialogue_phase.value,
                "stance_score": session.user_stance_score,
                "stance_category": _display_stance_category(
                    user_id=request.user.id,
                    topic_id=session_record.get("topic_id"),
                    stance_score=session.user_stance_score,
                ),
                "stance_label": session.user_stance_label,
                "stance_drift": stance_drift,
                "history": _live_dialogue_history(
                    session_id=session_id,
                    user_id=request.user.id,
                    history=session_record["session"]["history"],
                ),
            }
        )


class DialogueSessionOpeningView(APIView):
    """H-AI 的 AI 開場：由代理人先發言，內容依前測 Q9/Q10 給出討論方向。

    開場寫進 session history 的第一則 agent 訊息，因此重連／還原時會跟著
    回來，不需要前端另外保存。它沒有對應的 `AIConversation` turn（沒有使用者
    發言可配對），所以不可被讚踩，也不計入 `turn_count`／對話階段。

    POST 是 idempotent 的：history 非空就直接回傳現況，不會再生一次。
    """

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, session_id: str):
        from apps.matching.services.opening import build_ai_opening

        _, _, DialogueSession = _get_dialogue_runtime()

        session_record, error_response = _get_dialogue_session_record_for_user(
            session_id=session_id,
            user_id=request.user.id,
        )
        if error_response is not None:
            return error_response

        session = DialogueSession.from_dict(session_record["session"])

        def _history_response(opening: dict | None) -> Response:
            return Response(
                {
                    "opening": opening,
                    "history": _live_dialogue_history(
                        session_id=session_id,
                        user_id=request.user.id,
                        history=session_record["session"]["history"],
                    ),
                }
            )

        if session.history:
            first = session.history[0]
            existing = (
                {"content": first.content, "directions": [], "source": "existing"}
                if first.role == "agent"
                else None
            )
            return _history_response(existing)

        survey_context = session_record.get("survey_context") or {}
        open_answers = survey_context.get("survey_open_answers") or {}
        try:
            opening = build_ai_opening(
                topic_id=session_record.get("topic_id"),
                q9=_get_open_answer(open_answers, question_id=9, question_code="Q9"),
                q10=_get_open_answer(open_answers, question_id=10, question_code="Q10"),
                stance_label=session.user_stance_label,
            )
        except Exception:
            logger.exception("AI opening generation failed for session %s.", session_id)
            return _history_response(None)

        if opening is None:
            return _history_response(None)

        session.add_agent_message(opening["text"])
        session_record["session"] = session.to_dict()
        _cache_dialogue_session_record(session_record)
        _persist_dialogue_session_record(session_record)
        return _history_response(
            {
                "content": opening["text"],
                "directions": opening["directions"],
                "source": opening["source"],
            }
        )


class DialogueSessionSemanticTreeView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, session_id: str):
        from apps.matching.services.semantic_tree import semantic_tree_session_payload

        session_record, error_response = _get_dialogue_session_record_for_user(
            session_id=session_id,
            user_id=request.user.id,
        )
        if error_response is not None:
            return error_response

        payload = semantic_tree_session_payload(
            session_record=session_record,
            session_id=session_id,
            root_name=_semantic_tree_root_name_for_topic_id(
                session_record.get("topic_id"),
            ),
        )
        return Response(MatchingRoomSemanticTreeSerializer(payload).data)


class DialogueSessionSemanticTreeAnalyzeView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, session_id: str):
        from apps.matching.services.semantic_tree import (
            SemanticTreeError,
            analyze_pending_ai_conversations,
        )

        session_record, error_response = _get_dialogue_session_record_for_user(
            session_id=session_id,
            user_id=request.user.id,
        )
        if error_response is not None:
            return error_response

        try:
            payload = analyze_pending_ai_conversations(
                session_record=session_record,
                session_id=session_id,
                user_id=request.user.id,
                root_name=_semantic_tree_root_name_for_topic_id(
                    session_record.get("topic_id"),
                ),
            )
        except SemanticTreeError as exc:
            return Response(
                {
                    "error": exc.code,
                    "message": str(exc),
                    "analysisStatus": exc.code,
                },
                status=exc.status_code,
            )

        _cache_dialogue_session_record(session_record)
        _persist_dialogue_session_record(session_record)
        return Response(MatchingRoomSemanticTreeSerializer(payload).data)


class HistoryConversationListView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        history_type = request.query_params.get("type", "all")
        if history_type not in {"all", "ai", "match"}:
            return Response(
                {"detail": "type 必須是 all、ai 或 match。"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        results = []
        if history_type in {"all", "ai"}:
            completed_session_ids = set(
                PostDialogueResponse.objects.filter(
                    user=request.user,
                    experiment_condition=PostDialogueResponse.ExperimentCondition.AI,
                ).values_list("session_id", flat=True)
            )
            ai_records = list(
                DialogueSessionRecord.objects.filter(user=request.user).order_by(
                    "-last_activity_at", "-id"
                )
            )
            # 依 (-last_activity_at, -id) 排序後，每個 topic 第一次遇到的
            # record 就是那個 topic 目前最新的 session。
            latest_session_id_by_topic: dict[int, str] = {}
            for record in ai_records:
                latest_session_id_by_topic.setdefault(record.topic_id, record.session_id)

            for record in ai_records:
                summary = _history_ai_summary(
                    record,
                    completed_session_ids=completed_session_ids,
                    is_latest_for_topic=(
                        latest_session_id_by_topic.get(record.topic_id) == record.session_id
                    ),
                )
                if summary["message_count"]:
                    results.append(summary)

        if history_type in {"all", "match"}:
            matches = (
                DialogueMatch.objects.select_related("user_a", "user_b")
                .filter(Q(user_a=request.user) | Q(user_b=request.user))
                .order_by("-created_at", "-id")
            )
            for match in matches:
                summary = _history_match_summary(match, user_id=request.user.id)
                if summary["message_count"]:
                    results.append(summary)

        results.sort(
            key=lambda item: item.get("last_activity_at") or timezone.datetime.min,
            reverse=True,
        )
        return Response({"count": len(results), "results": results})


class HistoryConversationDetailView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, kind: str, conversation_id: str):
        if kind == "ai":
            record = _get_history_ai_record_for_user(
                session_id=conversation_id,
                user_id=request.user.id,
            )
            if record is None:
                return Response(
                    {"detail": "找不到這筆 AI 對話紀錄。"},
                    status=status.HTTP_404_NOT_FOUND,
                )
            return Response(_history_ai_detail(record, user_id=request.user.id))

        if kind == "match":
            match = _get_room_match_for_user(
                room_id=conversation_id,
                user_id=request.user.id,
            )
            if match is None:
                return Response(
                    {"detail": "找不到這筆真人對話紀錄。"},
                    status=status.HTTP_404_NOT_FOUND,
                )
            return Response(_history_match_detail(match, user_id=request.user.id))

        return Response(
            {"detail": "kind 必須是 ai 或 match。"},
            status=status.HTTP_404_NOT_FOUND,
        )


def _timeline_locked_response(*, user_id: int, kind: str, conversation_id: str, conversation):
    """Return a 403 Response unless this participant has cleared the CCND
    timeline gate; None means "allowed, carry on".

    Replaying the CCND before the participant has answered the CCND self-report
    items (questionnaire C3 and Part F's F4 ux_ccnd) would contaminate them —
    see api.timeline_access for the full rule.
    """
    state = timeline_unlock_state(
        user_id=user_id,
        kind=kind,
        conversation_id=conversation_id,
        conversation=conversation,
    )
    if state["unlocked"]:
        return None

    return Response(
        {
            "detail": LOCKED_DETAIL,
            "timeline_unlocked": False,
            "timeline_lock_reason": state["reason"],
            "timeline_unlocks_at": state["unlocks_at"],
        },
        status=status.HTTP_403_FORBIDDEN,
    )


class HistoryConversationSemanticTreeTimelineView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, kind: str, conversation_id: str):
        from apps.matching.services.semantic_tree import (
            semantic_tree_session_timeline_payload,
            semantic_tree_timeline_payload,
        )

        as_of_message_id = (request.query_params.get("as_of_message_id") or "").strip()
        if not as_of_message_id:
            return Response(
                {"detail": "缺少 as_of_message_id 參數。"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if kind == "ai":
            record = _get_history_ai_record_for_user(
                session_id=conversation_id,
                user_id=request.user.id,
            )
            if record is None:
                return Response(
                    {"detail": "找不到這筆 AI 對話紀錄。"},
                    status=status.HTTP_404_NOT_FOUND,
                )

            locked = _timeline_locked_response(
                user_id=request.user.id,
                kind=kind,
                conversation_id=record.session_id,
                conversation=record,
            )
            if locked is not None:
                return locked

            session_record = _dialogue_session_cache_payload_from_record(record)
            payload = semantic_tree_session_timeline_payload(
                session_record=session_record,
                session_id=record.session_id,
                root_name=_semantic_tree_root_name_for_topic_id(record.topic_id),
                source_message_id=as_of_message_id,
            )
        elif kind == "match":
            match = _get_room_match_for_user(
                room_id=conversation_id,
                user_id=request.user.id,
            )
            if match is None:
                return Response(
                    {"detail": "找不到這個配對房間。"},
                    status=status.HTTP_404_NOT_FOUND,
                )

            locked = _timeline_locked_response(
                user_id=request.user.id,
                kind=kind,
                conversation_id=match.room_id,
                conversation=match,
            )
            if locked is not None:
                return locked

            payload = semantic_tree_timeline_payload(
                match=match,
                root_name=_semantic_tree_root_name(match),
                current_user_id=request.user.id,
                source_message_id=as_of_message_id,
            )
        else:
            return Response(
                {"detail": "kind 必須是 ai 或 match。"},
                status=status.HTTP_404_NOT_FOUND,
            )

        if payload is None:
            return Response(
                {"detail": "這則訊息還沒有被分析過。"},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(MatchingRoomSemanticTreeTimelineSerializer(payload).data)


class CCNDSnapshotAnalysisView(APIView):
    """Researcher-only aggregate CCND metrics for one finished conversation.

    IsAdminUser on purpose: per-segment new-concept counts and adjacent-snapshot
    Jaccard ARE the study's dependent variables. Handing them to a participant
    would show them the construct being measured, which is exactly what the
    timeline gate exists to prevent. Staff may inspect any conversation, so this
    deliberately does NOT scope the lookup to request.user.

    Returns one analysis per test subject (H-H yields two, one per participant;
    H-AI one), matching the export_ccnd_snapshots CLI.
    """

    permission_classes = [permissions.IsAdminUser]

    def get(self, request, kind: str, conversation_id: str):
        from apps.matching.services.ccnd_snapshot_analysis import iter_subject_analyses

        try:
            segments = max(1, int(request.query_params.get("segments", 3)))
        except (TypeError, ValueError):
            segments = 3

        if kind == "ai":
            conversation = DialogueSessionRecord.objects.filter(
                session_id=conversation_id,
            ).first()
        elif kind == "match":
            conversation = DialogueMatch.objects.filter(room_id=conversation_id).first()
        else:
            return Response(
                {"detail": "kind 必須是 ai 或 match。"},
                status=status.HTTP_404_NOT_FOUND,
            )

        if conversation is None:
            return Response(
                {"detail": "找不到這筆對話紀錄。"},
                status=status.HTTP_404_NOT_FOUND,
            )

        return Response(
            {
                "kind": kind,
                "conversation_id": conversation_id,
                "n_segments": segments,
                "subjects": list(
                    iter_subject_analyses(conversation, n_segments=segments)
                ),
            }
        )


class ViewpointReviewListView(generics.ListAPIView):
    """研究者專用：M6 觀點知識庫 Step 4 人工終審清單。

    用 IsResearcher（「研究者」Django Group）而不是 IsAdminUser：這裡列出的是
    尚未定案、可能被退回的候選觀點，權限該對應「有沒有研究者身分」，跟能不能
    登入 Django /admin/ 是兩件事。
    """

    permission_classes = [IsResearcher]
    serializer_class = ViewpointNodeReviewSerializer

    def get_queryset(self):
        status_param = self.request.query_params.get("status", ViewpointNode.ReviewStatus.PENDING)
        qs = ViewpointNode.objects.select_related("summary", "reviewed_by")
        if status_param != "all":
            qs = qs.filter(review_status=status_param)

        topic_id = self.request.query_params.get("topic_id")
        if topic_id:
            qs = qs.filter(topic_id=topic_id)

        return qs.order_by("-composite_score", "-created_at")


class ViewpointReviewDecisionView(APIView):
    """研究者專用：核准、標記未通過，或把已核准/未通過的節點重新送回待審核。"""

    permission_classes = [IsResearcher]

    _STATUS_BY_ACTION = {
        "approve": ViewpointNode.ReviewStatus.APPROVED,
        "reject": ViewpointNode.ReviewStatus.REJECTED,
        "reset": ViewpointNode.ReviewStatus.PENDING,
    }

    def post(self, request, pk: int):
        try:
            node = ViewpointNode.objects.get(pk=pk)
        except ViewpointNode.DoesNotExist:
            return Response({"detail": "找不到這筆觀點。"}, status=status.HTTP_404_NOT_FOUND)

        decision = ViewpointNodeReviewDecisionSerializer(data=request.data)
        decision.is_valid(raise_exception=True)

        node.review_status = self._STATUS_BY_ACTION[decision.validated_data["action"]]
        node.reviewed_by = request.user
        node.reviewed_at = timezone.now()
        node.review_notes = decision.validated_data["notes"]
        node.save(
            update_fields=["review_status", "reviewed_by", "reviewed_at", "review_notes"]
        )

        return Response(ViewpointNodeReviewSerializer(node).data)


def _parse_topic_id(raw: str | None) -> int | None:
    """把 query param 轉成 int；沒帶回傳 None，帶了但不是合法整數丟 ValueError。

    直接把字串塞進 `.filter(topic_id=raw)` 會在 Django 轉型 PositiveIntegerField
    時炸出 ValueError，DRF 不攔這個例外會變成 500 而不是 400——所有吃
    topic_id query param 的知識庫端點都要先過這裡。
    """
    if raw is None or raw == "":
        return None
    return int(raw)


def _approved_viewpoints_queryset(topic_id: int | None):
    qs = ViewpointNode.objects.filter(
        review_status=ViewpointNode.ReviewStatus.APPROVED
    ).select_related("summary")
    if topic_id is not None:
        qs = qs.filter(topic_id=topic_id)

    return qs.annotate(
        _score=Coalesce("composite_score", Value(0.0), output_field=FloatField())
    ).order_by("-citation_count", "-_score")


def _serialize_viewpoint_rows(nodes) -> list[dict]:
    """把 ViewpointNode queryset/list 轉成公開卡片用的 dict。

    帶 user_input_text/ai_response_text（使用者發言／對方回應）：只有走過
    人工審核通過（review_status=approved）的節點才會被傳進這裡，跟對話詳情
    頁（KnowledgeBaseConversationDetailView）已經在用的隱私範圍一致。

    帶 dialogue_summary_id：同一場對話（同一個聊天室）產生的多筆觀點會共用
    同一個 summary_id，前端知識庫頁面用這個欄位把它們歸類在同一組卡片下，
    而不是打散成互不相關的獨立卡片。
    """
    anchor_names_by_topic: dict[int, dict[str, str]] = {}
    rows = []
    for node in nodes:
        anchor_names = anchor_names_by_topic.setdefault(
            node.topic_id,
            {anchor["id"]: anchor["name"] for anchor in get_topic_anchors(node.topic_id)},
        )
        rows.append(
            {
                "id": node.id,
                "dialogue_summary_id": node.summary_id,
                "topic_id": node.topic_id,
                "topic_title": TOPIC_CONFIGS.get(node.topic_id, {}).get("title", ""),
                "dimension": node.dimension,
                "dimension_name": anchor_names.get(node.dimension, node.dimension),
                "speaker_side": node.speaker_side,
                "stance_direction": node.stance_direction,
                "viewpoint_summary": node.viewpoint_summary,
                "user_input_text": node.user_input_text,
                "ai_response_text": node.ai_response_text,
                "citation_count": node.citation_count,
                "composite_score": node.composite_score,
                "created_at": node.created_at,
            }
        )
    return rows


class KnowledgeBaseHighlightsView(APIView):
    """GET /api/summary/viewpoints/highlights/?topic_id=<id>&limit=<n>

    知識庫「熱門對話」區塊：所有登入使用者都能看，只回傳已通過人工審核
    （review_status=approved）的 ViewpointNode，依 citation_count（被去重
    比對命中的次數，等於這個觀點在多場對話中重複出現過幾次）排序，當作
    「熱門度」的代理指標。預設 limit=5（首頁選定議題後顯示前五名用），
    最多 20 筆——完整清單走 KnowledgeBaseViewpointBrowseView（有分頁）。
    跟 ViewpointReviewListView 不同：那個是研究者專用、預設列 PENDING、
    且會帶原始逐字稿欄位。
    """

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        try:
            topic_id = _parse_topic_id(request.query_params.get("topic_id"))
        except ValueError:
            return Response(
                {"detail": "topic_id 必須是整數。"}, status=status.HTTP_400_BAD_REQUEST
            )

        try:
            limit = int(request.query_params.get("limit", 5))
        except (TypeError, ValueError):
            limit = 5
        limit = max(1, min(limit, 20))

        # 埋點放在參數驗證之後：400 的請求沒真的看到知識庫，不該給成就。
        # 知識庫的「入口頁」打的是這支，browse/ 只有點進議題後的下一層才會用到，
        # 所以兩支都要掛，成就文案（首次開啟觀點知識庫）才對得上實際行為。
        # 第二次以後函式會直接短路 return，重複掛沒有額外成本。
        _safe_unlock_knowledge_base_achievement(request.user)

        qs = _approved_viewpoints_queryset(topic_id)[:limit]
        rows = _serialize_viewpoint_rows(qs)

        serializer = ViewpointHighlightSerializer(rows, many=True)
        return Response(serializer.data)


class KnowledgeBaseConversationDetailView(APIView):
    """GET /api/summary/viewpoints/<pk>/conversation/

    「熱門對話」卡片點進去看的對話紀錄。pk 是 ViewpointNode id，只接受已通過
    審核的節點（跟 highlights/browse 同一道 gate）。回傳它所屬 DialogueSummary
    已沉澱的摘要欄位、同一場對話底下其他已審核通過的觀點列表，並且直接把
    完整逐字稿（messages）跟雙方的 CCND 語意樹（semantic_tree）也一併回傳，
    讓前端能重用聊天室的「訊息串 + CCND 樹狀圖」畫面，不再是精簡摘要卡片。

    逐字稿只標示 A/B 方（見 _approved_match_messages），不帶 sender_id/使用者
    名稱——這裡開放給任何登入使用者看，但看到的仍然是「A 方說了什麼」，不是
    「誰說的」，跟匿名精神一致，只是把「摘要」換成「完整逐字稿」。

    最上方的 summary_text 是 AI 摘要（generate_ai_summary），不是逐字稿——第一次
    有人點開這筆對話時才即時呼叫 Claude 生成並存回 DialogueSummary.summary_text，
    之後都是直接讀快取，不會每次開頁都重打一次 API（見 is_raw_summary_text）。
    """

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, pk: int):
        from apps.matching.services.semantic_tree import approved_match_tree_payload
        from apps.summary.pipeline.assemble import generate_ai_summary, is_raw_summary_text

        try:
            node = ViewpointNode.objects.select_related("summary").get(
                pk=pk, review_status=ViewpointNode.ReviewStatus.APPROVED
            )
        except ViewpointNode.DoesNotExist:
            return Response(
                {"detail": "找不到這筆觀點，或尚未通過審核。"},
                status=status.HTTP_404_NOT_FOUND,
            )

        summary = node.summary
        try:
            match = DialogueMatch.objects.get(pk=int(summary.dialogue_id))
        except (DialogueMatch.DoesNotExist, ValueError, TypeError):
            return Response(
                {"detail": "找不到這場對話對應的配對房間紀錄。"},
                status=status.HTTP_404_NOT_FOUND,
            )

        topic_title = TOPIC_CONFIGS.get(summary.topic_id, {}).get("title", "")
        messages = _approved_match_messages(match)

        if is_raw_summary_text(summary.summary_text):
            ai_summary = generate_ai_summary(messages, topic_title=topic_title)
            if ai_summary:
                summary.summary_text = ai_summary
                summary.save(update_fields=["summary_text"])
            else:
                # 不存回資料庫：故意只改記憶體裡這個 response 用的值，讓
                # summary.summary_text 在 DB 裡繼續保持逐字稿格式，下次有人
                # 點開時 is_raw_summary_text() 才會再次嘗試生成——等到真的
                # 設定了 ANTHROPIC_API_KEY，不用手動清資料就會自動補上。
                summary.summary_text = "這裡是 AI 摘要，對話要加 API 金鑰才能顯示。"

        sibling_nodes = ViewpointNode.objects.filter(
            summary_id=summary.id,
            review_status=ViewpointNode.ReviewStatus.APPROVED,
        ).annotate(
            _score=Coalesce("composite_score", Value(0.0), output_field=FloatField())
        ).order_by("-citation_count", "-_score")

        data = {
            "dialogue_summary_id": summary.id,
            "topic_id": summary.topic_id,
            "topic_title": topic_title,
            "summary_text": summary.summary_text,
            "side_a_stance": summary.side_a_stance,
            "side_b_stance": summary.side_b_stance,
            "quality_score": summary.quality_score,
            "stance_shift_magnitude": summary.stance_shift_magnitude,
            "created_at": summary.created_at,
            "viewpoints": _serialize_viewpoint_rows(sibling_nodes),
            "messages": messages,
            "semantic_tree": approved_match_tree_payload(
                match=match, root_name=_semantic_tree_root_name(match)
            ),
        }
        serializer = DialogueSummaryDetailSerializer(data)
        return Response(serializer.data)


class ViewpointBrowsePagination(PageNumberPagination):
    page_size = 12
    page_size_query_param = "page_size"
    max_page_size = 50


class KnowledgeBaseViewpointBrowseView(APIView):
    """GET /api/summary/viewpoints/browse/?topic_id=<id>&page=<n>

    知識庫「觀看更多」頁面：列出某個議題底下所有已審核通過的觀點，依
    citation_count 排序，分頁回傳（DRF 標準 count/next/previous/results 格式）。
    topic_id 為必填——這裡設計上一定是使用者先在首頁選定一個議題後才會進來。
    """

    permission_classes = [permissions.IsAuthenticated]
    pagination_class = ViewpointBrowsePagination

    def get(self, request):
        try:
            topic_id = _parse_topic_id(request.query_params.get("topic_id"))
        except ValueError:
            return Response(
                {"detail": "topic_id 必須是整數。"}, status=status.HTTP_400_BAD_REQUEST
            )
        if topic_id is None:
            return Response(
                {"detail": "topic_id 為必填。"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # 埋點放在參數驗證之後：400 的請求沒真的看到知識庫，不該給成就。
        _safe_unlock_knowledge_base_achievement(request.user)

        qs = _approved_viewpoints_queryset(topic_id)

        paginator = self.pagination_class()
        page = paginator.paginate_queryset(qs, request, view=self)
        rows = _serialize_viewpoint_rows(page)
        serializer = ViewpointHighlightSerializer(rows, many=True)
        return paginator.get_paginated_response(serializer.data)


class FavoriteView(APIView):
    """觀點知識庫的「收藏」星星。

    GET  /api/favorites/
        → {"viewpoint": [...], "video": [...]}
        兩個清單分別用跟知識庫首頁完全相同的序列化器（ViewpointHighlightSerializer
        / VideoRecommendationSerializer），所以前端「我的收藏」頁可以直接重用
        知識庫的卡片元件，不用為了補標題摘要再逐筆打一次 API。

    POST /api/favorites/  body: {target_type, target_id}
        → {"target_type", "target_id", "favorited": bool}
        切換語意：已收藏就取消。

    只收「目前可公開」的目標——觀點必須 review_status=approved、影片必須
    is_published=True，跟 KnowledgeBaseHighlightsView / VideoRecommendationListView
    同一道 gate。這件事在讀跟寫兩邊都要做：寫的時候擋住，才不會有人靠猜 id 去
    收藏還沒過審的候選觀點（等於拿到一個「這筆存不存在」的探測器）；讀的時候
    也要擋，因為觀點可能在收藏之後才被退回審核（ViewpointReviewDecisionView 的
    reject/reset），那時候它就不該再出現在任何人的收藏頁上。
    """

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        rows = Favorite.objects.filter(user=request.user).order_by("-created_at", "-id")
        ids_by_type: dict[str, list[int]] = {
            Favorite.TargetType.VIEWPOINT: [],
            Favorite.TargetType.VIDEO: [],
        }
        for target_type, target_id in rows.values_list("target_type", "target_id"):
            if target_type in ids_by_type:
                ids_by_type[target_type].append(target_id)

        viewpoint_ids = ids_by_type[Favorite.TargetType.VIEWPOINT]
        nodes = {
            node.id: node
            for node in ViewpointNode.objects.filter(
                id__in=viewpoint_ids,
                review_status=ViewpointNode.ReviewStatus.APPROVED,
            ).select_related("summary")
        }
        # 依收藏時間（rows 的順序）排列，而不是 DB 回來的順序——使用者對「我的
        # 收藏」的預期是最近收藏的在最前面。查不到的 id（已被刪除或退回審核）
        # 在這一步自然被略過。
        ordered_nodes = [nodes[i] for i in viewpoint_ids if i in nodes]

        video_ids = ids_by_type[Favorite.TargetType.VIDEO]
        videos = {
            video.id: video
            for video in VideoRecommendation.objects.filter(
                id__in=video_ids,
                is_published=True,
            )
        }
        ordered_videos = [videos[i] for i in video_ids if i in videos]

        return Response(
            {
                "viewpoint": ViewpointHighlightSerializer(
                    _serialize_viewpoint_rows(ordered_nodes), many=True
                ).data,
                "video": VideoRecommendationSerializer(ordered_videos, many=True).data,
            }
        )

    def post(self, request):
        serializer = FavoriteToggleSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        target_type = serializer.validated_data["target_type"]
        target_id = serializer.validated_data["target_id"]

        if not self._target_is_visible(target_type, target_id):
            return Response(
                {"detail": "找不到這個可收藏的項目。"},
                status=status.HTTP_404_NOT_FOUND,
            )

        deleted, _ = Favorite.objects.filter(
            user=request.user,
            target_type=target_type,
            target_id=target_id,
        ).delete()
        if deleted:
            favorited = False
        else:
            # get_or_create 而不是 create：同一個人在兩個分頁同時按下星星時，
            # 唯一鍵會讓其中一次 create 丟 IntegrityError 變成 500。
            Favorite.objects.get_or_create(
                user=request.user,
                target_type=target_type,
                target_id=target_id,
            )
            favorited = True

        return Response(
            {
                "target_type": target_type,
                "target_id": target_id,
                "favorited": favorited,
            }
        )

    @staticmethod
    def _target_is_visible(target_type: str, target_id: int) -> bool:
        if target_type == Favorite.TargetType.VIEWPOINT:
            return ViewpointNode.objects.filter(
                id=target_id,
                review_status=ViewpointNode.ReviewStatus.APPROVED,
            ).exists()
        return VideoRecommendation.objects.filter(
            id=target_id, is_published=True
        ).exists()


class VideoRecommendationListView(generics.ListAPIView):
    """GET /api/summary/videos/?topic_id=<id>

    知識庫首頁「影片推薦」區塊。內容由 Django admin 後台人工維護
    （apps.summary.admin.VideoRecommendationAdmin），這裡只回傳
    is_published=True 的項目；topic_id 沒帶就回傳所有已發布項目（含不限
    議題的推薦），這種情況沒有單一議題可以判斷立場，一律走熱門排序。

    影片推薦演算法：
    - 初期（這個議題底下還沒有這位使用者的後測問卷 PostDialogueResponse，
      也就是還沒進行過對話）：依全部影片被觀看的次數（VideoWatchEvent，
      點擊率的代理指標）由高到低排序，取前 10 部。
    - 後期（已經填過這個議題的後測問卷）：用後測問卷算出的目前立場
      （resolve_stance_category）把「跟使用者立場相反」的影片（研究者在影片
      管理面板標記的 stance_direction）排到最前面優先推薦；同時持續統計這位
      使用者在這個議題底下已觀看的支持／反對影片比例，一旦落在 4:6～6:4 之間
      （含 5:5）就視為曝光已經均衡，改回跟初期一樣的熱門排序，不再單向推播。
      spec 說「每觀看 10 部影片為基準去判斷」——這裡改成每次請求都即時算
      比例，效果等價（達到均衡的當下就會反映在下一次請求），不需要另外
      排程一個「第 10 部」的檢查點。

    ⚠️ 後期是「相反立場排前面」而不是「只給相反立場」。曾經是用
    filter(stance_direction=...) 硬篩，那會造成死結：使用者看不到另一側的
    影片 → 另一側的 VideoWatchEvent 永遠是 0 → _is_exposure_balanced() 算出
    的比例永遠是 0 → 永遠達不到均衡、再也回不到熱門排序，上面那句「一旦落在
    4:6～6:4 就改回熱門排序」等於是死的。硬篩另外還有一個副作用：該議題底下
    如果根本沒有相反立場的影片，整個推薦區塊會變成空的。
    """

    permission_classes = [permissions.IsAuthenticated]
    serializer_class = VideoRecommendationSerializer

    POPULAR_LIMIT = 10
    # 「6:4／4:6／5:5」= 少數方佔比至少 40%。
    RATIO_BALANCE_THRESHOLD = 0.4

    def get_queryset(self):
        try:
            topic_id = _parse_topic_id(self.request.query_params.get("topic_id"))
        except ValueError:
            raise exceptions.ValidationError({"topic_id": "topic_id 必須是整數。"})

        qs = VideoRecommendation.objects.filter(is_published=True)
        if topic_id is not None:
            qs = qs.filter(topic_id=topic_id)

        if topic_id is None:
            return self._order_by_popularity(qs)

        target_stance = self._opposite_stance_for(self.request.user, topic_id)
        if target_stance is None or self._is_exposure_balanced(self.request.user, topic_id):
            return self._order_by_popularity(qs)

        return self._order_by_popularity(qs, prioritized_stance=target_stance)

    def _order_by_popularity(self, qs, *, prioritized_stance=None):
        """熱門排序（觀看次數 → display_order → 新到舊）。

        prioritized_stance 有值時，先照「是不是這個立場」分成兩群、該立場排在
        前面，群內仍然是熱門排序。用排序而不是過濾的理由見 class docstring。
        """
        qs = qs.annotate(watch_count=Count("watch_events"))
        ordering = ["-watch_count", "display_order", "-created_at"]
        if prioritized_stance is not None:
            qs = qs.annotate(
                stance_rank=Case(
                    When(stance_direction=prioritized_stance, then=Value(0)),
                    default=Value(1),
                    output_field=IntegerField(),
                )
            )
            ordering.insert(0, "stance_rank")
        return qs.order_by(*ordering)[: self.POPULAR_LIMIT]

    @staticmethod
    def _opposite_stance_for(user, topic_id):
        """使用者在這個議題的「相反立場」。還沒進入後期（沒有後測問卷）或
        立場中立時回傳 None（= 不特別過濾，走熱門排序）。"""
        response = (
            PostDialogueResponse.objects.filter(user=user, topic_id=topic_id)
            .order_by("-created_at")
            .first()
        )
        if response is None:
            return None

        category = resolve_stance_category(
            topic_id=topic_id, user_stance_score=response.s_post()
        )
        if category == UserStanceProfile.StanceCategory.SUPPORT:
            return VideoRecommendation.StanceDirection.OPPOSE
        if category == UserStanceProfile.StanceCategory.OPPOSE:
            return VideoRecommendation.StanceDirection.SUPPORT
        return None

    @classmethod
    def _is_exposure_balanced(cls, user, topic_id):
        counts = (
            VideoWatchEvent.objects.filter(user=user, topic_id=topic_id)
            .exclude(stance_direction=VideoRecommendation.StanceDirection.NEUTRAL)
            .values("stance_direction")
            .annotate(total=Count("id"))
        )
        by_stance = {row["stance_direction"]: row["total"] for row in counts}
        support = by_stance.get(VideoRecommendation.StanceDirection.SUPPORT, 0)
        oppose = by_stance.get(VideoRecommendation.StanceDirection.OPPOSE, 0)
        total = support + oppose
        if total == 0:
            return False
        return min(support, oppose) / total >= cls.RATIO_BALANCE_THRESHOLD


class VideoWatchEventCreateView(APIView):
    """POST /api/summary/videos/<pk>/watch/  body: {"topic_id": <id>}

    使用者在知識庫點開一部推薦影片播放時打這支 API，記一筆觀看紀錄——
    VideoRecommendationListView 的後期演算法需要這份紀錄才能算「支持／反對
    影片各看了幾部」的曝光比例。topic_id 必填且來自請求（不是
    video.topic_id）：影片本身可能是「不限議題」的共用推薦，但使用者一定是
    在某個特定議題頁面底下看的，比例要算在那個議題上。
    """

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk):
        try:
            topic_id = _parse_topic_id(str(request.data.get("topic_id", "")))
        except ValueError:
            topic_id = None
        if topic_id is None:
            return Response(
                {"detail": "topic_id 為必填，且必須是整數。"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            video = VideoRecommendation.objects.get(pk=pk, is_published=True)
        except VideoRecommendation.DoesNotExist:
            return Response(
                {"detail": "找不到這部影片。"}, status=status.HTTP_404_NOT_FOUND
            )

        VideoWatchEvent.objects.create(
            user=request.user,
            video=video,
            topic_id=topic_id,
            stance_direction=video.stance_direction,
        )
        return Response(status=status.HTTP_201_CREATED)


def _fill_video_url_from_file(instance, request):
    """video_file 有值、url 還是空的時候，自動補上這個檔案的存取網址。

    研究者本地上傳影片檔是現在的主要路徑，不該還要求另外手動填一個 url——
    但下游所有讀取路徑（公開清單、KB 首頁的播放器）都只認 url 欄位，這裡
    補完之後其他地方完全不用知道背後是本地檔案還是外部連結。

    存的是「根相對路徑」（instance.video_file.url，例如 /media/kb_videos/...），
    不是 build_absolute_uri 組出來的絕對網址。理由：
    - 絕對網址會把「上傳當下那個 request 的 host/scheme」寫死進 DB。開發機
      上傳存成 http://127.0.0.1:8005/...，換到正式站就指向不存在的主機；
      http 存進去、正式站走 https 又會被瀏覽器擋 mixed content。
    - 部署拓撲是前端、API、媒體同一個網域用路徑分流（dev 由 Vite proxy、
      docker 由 nginx、正式站由 Cloudflare Tunnel 把 /media/ 轉到後端），
      所以用頁面自己的 origin 解析 /media/... 一定對。
    request 參數保留是為了相容既有呼叫端；目前不需要用到。
    """
    if instance.video_file and not instance.url:
        instance.url = instance.video_file.url
        instance.save(update_fields=["url"])


class VideoRecommendationAdminListCreateView(generics.ListCreateAPIView):
    """研究者專用：知識庫影片管理面板（前端設定頁）的清單 + 新增。

    跟 VideoRecommendationListView 不同：這裡回傳所有影片（含未發布的），
    不只 is_published=True，讓研究者上傳新影片後、公開發布前可以先預覽。
    """

    permission_classes = [IsResearcher]
    serializer_class = VideoRecommendationAdminSerializer
    queryset = VideoRecommendation.objects.all()
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def perform_create(self, serializer):
        instance = serializer.save()
        _fill_video_url_from_file(instance, self.request)


class VideoRecommendationAdminDetailView(generics.RetrieveUpdateDestroyAPIView):
    """研究者專用：更新（含發布/取消發布、調整排序、重新上傳檔案）或刪除
    單一影片。"""

    permission_classes = [IsResearcher]
    serializer_class = VideoRecommendationAdminSerializer
    queryset = VideoRecommendation.objects.all()
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def perform_update(self, serializer):
        instance = serializer.save()
        _fill_video_url_from_file(instance, self.request)


class AccountListCreateView(generics.ListCreateAPIView):
    """研究者專用：帳號清單 + 新增帳號（前端設定頁）。"""

    permission_classes = [IsResearcher]

    def get_queryset(self):
        return User.objects.prefetch_related("groups").order_by("-date_joined")

    def get_serializer_class(self):
        if self.request.method == "POST":
            return AccountCreateSerializer
        return AccountListSerializer

    def create(self, request, *args, **kwargs):
        serializer = AccountCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        return Response(
            AccountListSerializer(user).data, status=status.HTTP_201_CREATED
        )


def _account_target_or_response(pk):
    """取目標帳號；找不到回 (None, 404 Response)。"""
    try:
        return User.objects.get(pk=pk), None
    except User.DoesNotExist:
        return None, Response(
            {"detail": "找不到這個帳號。"}, status=status.HTTP_404_NOT_FOUND
        )


class AccountDetailView(APIView):
    """研究者專用：更新單一帳號（停用/啟用、升/降研究者）。"""

    permission_classes = [IsResearcher]

    def patch(self, request, pk: int):
        target, error = _account_target_or_response(pk)
        if error:
            return error

        # 護欄 1：不能動 superuser。
        if target.is_superuser:
            return Response(
                {"detail": "不能對系統管理員帳號執行這個操作。"},
                status=status.HTTP_403_FORBIDDEN,
            )

        serializer = AccountUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        # 護欄 2：不能停用自己、不能取消自己的研究者身分。
        if target == request.user:
            if data.get("is_active") is False:
                return Response(
                    {"detail": "不能停用自己的帳號。"},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if data.get("is_researcher") is False:
                return Response(
                    {"detail": "不能取消自己的研究者身分。"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        if "is_active" in data:
            target.is_active = data["is_active"]
            target.save(update_fields=["is_active"])

        if "is_researcher" in data:
            group, _ = Group.objects.get_or_create(name=RESEARCHER_GROUP_NAME)
            if data["is_researcher"]:
                target.groups.add(group)  # signal 連動 is_staff
            else:
                target.groups.remove(group)

        return Response(AccountListSerializer(target).data)


class AccountPasswordResetView(APIView):
    """研究者專用：重設某帳號的密碼。"""

    permission_classes = [IsResearcher]

    def post(self, request, pk: int):
        target, error = _account_target_or_response(pk)
        if error:
            return error

        if target.is_superuser:
            return Response(
                {"detail": "不能重設系統管理員帳號的密碼。"},
                status=status.HTTP_403_FORBIDDEN,
            )

        serializer = PasswordResetSerializer(
            data=request.data, context={"target": target}
        )
        serializer.is_valid(raise_exception=True)
        target.set_password(serializer.validated_data["password"])
        target.save(update_fields=["password"])
        return Response({"detail": "密碼已重設。"})


def _topic_display_row(topic_id: int, *, override=None) -> dict:
    """設定頁用的單一議題狀態：目前生效值 + 程式碼預設值 + 是否被覆寫。

    override 可由呼叫端預先撈好一次性傳進來，避免逐議題各查一次
    （DisplaySettingsView.get 就是這樣批次載入的）。
    """
    if override is None:
        override = TopicDisplayOverride.objects.filter(topic_id=topic_id).first()
    support, oppose = get_stance_thresholds(topic_id=topic_id)
    default_support, default_oppose = default_stance_thresholds(topic_id=topic_id)

    return {
        "topic_id": topic_id,
        "title": TOPIC_CONFIGS.get(topic_id, {}).get("title", ""),
        "visible_to_participant": (
            override.visible_to_participant if override else True
        ),
        "visible_to_researcher": (
            override.visible_to_researcher if override else True
        ),
        "support_threshold": support,
        "oppose_threshold": oppose,
        "default_support_threshold": default_support,
        "default_oppose_threshold": default_oppose,
        "is_threshold_overridden": bool(
            override
            and (
                override.support_threshold is not None
                or override.oppose_threshold is not None
            )
        ),
    }


def _sorted_topic_ids() -> list[int]:
    return sorted(
        TOPIC_CONFIGS,
        key=lambda topic_id: TOPIC_CONFIGS[topic_id].get("display_order", topic_id),
    )


class DisplaySettingsView(APIView):
    """研究者專用：全站顯示設定 + 每個議題的目前狀態。"""

    permission_classes = [IsResearcher]

    def get(self, request):
        overrides = {
            override.topic_id: override
            for override in TopicDisplayOverride.objects.all()
        }
        return Response(
            {
                "platform": PlatformDisplaySettingSerializer(
                    PlatformDisplaySetting.load()
                ).data,
                "topics": [
                    _topic_display_row(topic_id, override=overrides.get(topic_id))
                    for topic_id in _sorted_topic_ids()
                ],
            }
        )

    def patch(self, request):
        setting = PlatformDisplaySetting.load()
        serializer = PlatformDisplaySettingSerializer(
            setting, data=request.data, partial=True
        )
        serializer.is_valid(raise_exception=True)
        serializer.save(updated_by=request.user)
        return Response(PlatformDisplaySettingSerializer(setting).data)


class DisplaySettingsTopicView(APIView):
    """研究者專用：單一議題的可見性與門檻覆寫。"""

    permission_classes = [IsResearcher]

    THRESHOLD_WARNING = "門檻變更只影響之後填寫的問卷，既有資料不會重算。"

    def patch(self, request, topic_id: int):
        if topic_id not in TOPIC_CONFIGS:
            return Response(
                {"detail": "找不到這個議題。"}, status=status.HTTP_404_NOT_FOUND
            )

        serializer = TopicDisplayOverrideSerializer(
            data=request.data, context={"topic_id": topic_id}
        )
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        # 「呼叫端有沒有提到這個欄位」一律看 request.data，不看 validated_data。
        # DRF 的 BooleanField.get_value() 在 form 編碼的請求裡，會把沒送到的欄位
        # 補成 False（因為 HTML 表單的未勾選 checkbox 不會出現在 payload）。若用
        # validated_data 判斷，一個只想改 visible_to_participant 的 form 請求會
        # 順手把 visible_to_researcher 靜默關掉。
        override, _ = TopicDisplayOverride.objects.get_or_create(topic_id=topic_id)
        for field in (
            "visible_to_participant",
            "visible_to_researcher",
            "support_threshold",
            "oppose_threshold",
        ):
            if field in request.data and field in data:
                setattr(override, field, data[field])
        override.updated_by = request.user
        override.save()

        payload = _topic_display_row(topic_id, override=override)
        if "support_threshold" in request.data or "oppose_threshold" in request.data:
            payload["warning"] = self.THRESHOLD_WARNING
        return Response(payload)


class CCNDInsightsView(APIView):
    """Participant-facing view of their OWN concept expansion for one conversation.

    Same numbers as the staff endpoint, but three things differ and all three
    matter:

    1. Gated behind the M6 flow (identical gate as the timeline). Only once the
       participant has answered C3 and Part F's F4 can they be shown what was
       measured, otherwise we contaminate those very items.
    2. The subject is the REQUESTING user, not analyze_conversation_ccnd's
       user_a default. Without this, user_b in an H-H match would be served
       user_a's analysis.
    3. partner_side is stripped. It carries the other participant's lit anchors
       and node names — their cognitive map — which a participant must never see.
    """

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, kind: str, conversation_id: str):
        from apps.matching.services.ccnd_snapshot_analysis import (
            analyze_conversation_ccnd,
        )
        from apps.matching.services.semantic_tree import (
            OWNER_AI_USER,
            _owner_key_for_user,
        )

        if kind == "ai":
            conversation = _get_history_ai_record_for_user(
                session_id=conversation_id,
                user_id=request.user.id,
            )
            if conversation is None:
                return Response(
                    {"detail": "找不到這筆 AI 對話紀錄。"},
                    status=status.HTTP_404_NOT_FOUND,
                )
            subject_owner_key = OWNER_AI_USER
            resolved_id = conversation.session_id
        elif kind == "match":
            conversation = _get_room_match_for_user(
                room_id=conversation_id,
                user_id=request.user.id,
            )
            if conversation is None:
                return Response(
                    {"detail": "找不到這個配對房間。"},
                    status=status.HTTP_404_NOT_FOUND,
                )
            # the requesting participant is the subject — never default to user_a
            subject_owner_key = _owner_key_for_user(conversation, request.user.id)
            resolved_id = conversation.room_id
        else:
            return Response(
                {"detail": "kind 必須是 ai 或 match。"},
                status=status.HTTP_404_NOT_FOUND,
            )

        locked = _timeline_locked_response(
            user_id=request.user.id,
            kind=kind,
            conversation_id=resolved_id,
            conversation=conversation,
        )
        if locked is not None:
            return locked

        analysis = analyze_conversation_ccnd(
            conversation,
            subject_owner_key=subject_owner_key,
        )
        return Response(
            {
                "kind": kind,
                "conversation_id": resolved_id,
                "n_segments": analysis["n_segments"],
                "summary": analysis["summary"],
                "novelty": analysis["novelty"],
                "similarity": analysis["similarity"],
                "snapshots": [
                    {
                        "label": snapshot["label"],
                        "macro_count": snapshot["macro_count"],
                        "micro_count": snapshot["micro_count"],
                        "cumulative_hit_count": snapshot["cumulative_hit_count"],
                    }
                    for snapshot in analysis["snapshots"]
                ],
                # NOTE: analysis["partner_side"] is deliberately NOT returned.
            }
        )


class HistoryConversationSemanticTreeAnalyzeView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, kind: str, conversation_id: str):
        from apps.matching.services.semantic_tree import (
            SemanticTreeError,
            analyze_pending_ai_conversations,
            analyze_pending_room_messages,
        )

        if kind == "ai":
            record = _get_history_ai_record_for_user(
                session_id=conversation_id,
                user_id=request.user.id,
            )
            if record is None:
                return Response(
                    {"detail": "找不到這筆 AI 對話紀錄。"},
                    status=status.HTTP_404_NOT_FOUND,
                )

            session_record = _dialogue_session_cache_payload_from_record(record)
            try:
                semantic_tree = analyze_pending_ai_conversations(
                    session_record=session_record,
                    session_id=record.session_id,
                    user_id=request.user.id,
                    root_name=_semantic_tree_root_name_for_topic_id(record.topic_id),
                )
            except SemanticTreeError as exc:
                return Response(
                    {
                        "error": exc.code,
                        "message": str(exc),
                        "analysisStatus": exc.code,
                    },
                    status=exc.status_code,
                )

            record.semantic_tree_state = session_record.get("semantic_tree") or {}
            record.save(update_fields=["semantic_tree_state", "updated_at"])
            _cache_dialogue_session_record(session_record)
            return Response(
                {
                    **_history_ai_detail(record, user_id=request.user.id),
                    "semantic_tree": MatchingRoomSemanticTreeSerializer(semantic_tree).data,
                }
            )

        if kind == "match":
            match = _get_room_match_for_user(
                room_id=conversation_id,
                user_id=request.user.id,
            )
            if match is None:
                return Response(
                    {"detail": "找不到這筆真人對話紀錄。"},
                    status=status.HTTP_404_NOT_FOUND,
                )

            try:
                semantic_tree = analyze_pending_room_messages(
                    match=match,
                    root_name=_semantic_tree_root_name(match),
                    current_user_id=request.user.id,
                )
            except SemanticTreeError as exc:
                return Response(
                    {
                        "error": exc.code,
                        "message": str(exc),
                        "analysisStatus": exc.code,
                    },
                    status=exc.status_code,
                )

            match.refresh_from_db()
            return Response(
                {
                    **_history_match_detail(match, user_id=request.user.id),
                    "semantic_tree": MatchingRoomSemanticTreeSerializer(semantic_tree).data,
                }
            )

        return Response(
            {"detail": "kind 必須是 ai 或 match。"},
            status=status.HTTP_404_NOT_FOUND,
        )


class MatchingJoinView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        from apps.matching.services.matcher import enqueue_for_matching

        serializer = MatchingJoinSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        validated = serializer.validated_data

        gate = _entry_gate_response(
            user=request.user, topic_id=validated["topic_id"], target="match"
        )
        if gate is not None:
            return gate

        stance_score = _compute_user_stance_score(
            topic_id=validated["topic_id"],
            survey_answers=validated["survey_answers"],
        )
        stance_category = resolve_stance_category(
            topic_id=validated["topic_id"],
            user_stance_score=stance_score,
        )
        resolved_open_answers = _resolve_open_answers(
            topic_id=validated["topic_id"],
            survey_open_answers=validated.get("survey_open_answers", {}),
        )

        state = enqueue_for_matching(
            user=request.user,
            topic_id=validated["topic_id"],
            stance_score=stance_score,
            stance_category=stance_category,
            survey_answers=validated["survey_answers"],
            survey_open_answers=resolved_open_answers,
            restart_existing_match=validated.get("restart_existing_match", False),
        )

        return Response(
            _build_matching_state_payload(
                topic_id=validated["topic_id"],
                state=state,
                user_id=request.user.id,
            )
        )


class MatchingStatusView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        from apps.matching.services.matcher import get_matching_state

        serializer = MatchingTopicSerializer(data=request.query_params)
        serializer.is_valid(raise_exception=True)
        topic_id = serializer.validated_data["topic_id"]

        state = get_matching_state(user=request.user, topic_id=topic_id)
        return Response(
            _build_matching_state_payload(
                topic_id=topic_id,
                state=state,
                user_id=request.user.id,
            )
        )


class GodotSurveyView(APIView):
    """POST /api/matching/godot-survey/ — Godot 綁定房的前測問卷回填。

    刻意**不**經過 _entry_gate_response：這條路徑的分組是遊戲內的木樁配對決定的，
    不是混合入口的立場分流決定的。中立立場的人也照樣 route=match（見 spec §D3）
    ——問卷是配對成立之後才填的，這時候再判定「你該去 AI」會把已經配好的兩人卡死。
    """

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        from apps.matching.services.matcher import record_godot_survey

        serializer = GodotSurveySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        validated = serializer.validated_data
        topic_id = validated["topic_id"]

        match = (
            DialogueMatch.objects.filter(
                topic_id=topic_id,
                status=DialogueMatch.Status.ACTIVE,
            )
            .filter(Q(user_a=request.user) | Q(user_b=request.user))
            .order_by("-created_at")
            .first()
        )
        if match is None or godot_binding_info(match) is None:
            # 一般配對房也走這裡會被擋掉——它的分數是配對演算法算的，不該被覆寫。
            return Response(
                {"detail": "找不到屬於你的 Godot 配對房間。"},
                status=status.HTTP_404_NOT_FOUND,
            )

        from apps.matching.services.matcher import resolve_godot_survey_gate

        # 先裁決再接受：房間若已因逾時或對方退出而作廢，不該再收問卷答案
        # （寫進去也沒有意義，而且會讓使用者以為送出成功）。見 spec §8.2。
        match = resolve_godot_survey_gate(
            match=match, viewer_user_id=request.user.id
        )
        if match.status != DialogueMatch.Status.ACTIVE:
            return Response(
                {
                    "detail": "這個配對房間已結束。",
                    "binding_cancel_reason": binding_cancel_reason(match),
                },
                status=status.HTTP_409_CONFLICT,
            )

        stance_score = _compute_user_stance_score(
            topic_id=topic_id, survey_answers=validated["survey_answers"]
        )
        stance_category = resolve_stance_category(
            topic_id=topic_id, user_stance_score=stance_score
        )
        resolved_open_answers = _resolve_open_answers(
            topic_id=topic_id,
            survey_open_answers=validated.get("survey_open_answers", {}),
        )

        recorded = record_godot_survey(
            user=request.user,
            match=match,
            topic_id=topic_id,
            stance_score=stance_score,
            stance_category=stance_category,
            survey_answers=validated["survey_answers"],
            survey_open_answers=resolved_open_answers,
        )
        if recorded is None:
            # 鎖內重驗擋下了——窗口期間房間被取消。回 409 跟前置檢查一致。
            # 這裡要重讀 match：呼叫端手上那份是過期快照，拿不到剛寫入的作廢原因。
            fresh = DialogueMatch.objects.filter(pk=match.pk).first()
            return Response(
                {
                    "detail": "這個配對房間已結束。",
                    "binding_cancel_reason": (
                        binding_cancel_reason(fresh) if fresh else None
                    ),
                },
                status=status.HTTP_409_CONFLICT,
            )

        support_threshold, oppose_threshold = get_stance_thresholds(topic_id=topic_id)
        DialogueEntryAssignment.objects.update_or_create(
            user=request.user,
            topic_id=topic_id,
            defaults={
                # 一律 match：見上方 docstring 與 spec §D3。
                "route": DialogueEntryAssignment.Route.MATCH,
                "stance_score": Decimal(str(stance_score)),
                "stance_category": stance_category,
                "support_threshold": support_threshold,
                "oppose_threshold": oppose_threshold,
                "entry_mode_at_assignment": get_entry_mode(
                    is_researcher=user_is_researcher(request.user)
                ),
                "fallback_offered_at": None,
                "fallback_accepted_at": None,
            },
        )

        from apps.matching.services.matcher import get_matching_state

        state = get_matching_state(user=request.user, topic_id=topic_id)
        return Response(
            _build_matching_state_payload(
                topic_id=topic_id, state=state, user_id=request.user.id
            )
        )


class MatchingCancelView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        from apps.matching.services.matcher import (
            MatchingAlreadyMatchedError,
            MatchingNotFoundError,
            cancel_matching,
            get_matching_state,
        )

        serializer = MatchingTopicSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        topic_id = serializer.validated_data["topic_id"]

        try:
            cancel_matching(user=request.user, topic_id=topic_id)
        except MatchingAlreadyMatchedError as exc:
            return Response(
                {"detail": str(exc)},
                status=status.HTTP_409_CONFLICT,
            )
        except MatchingNotFoundError as exc:
            return Response(
                {"detail": str(exc)},
                status=status.HTTP_404_NOT_FOUND,
            )

        state = get_matching_state(user=request.user, topic_id=topic_id)
        return Response(
            _build_matching_state_payload(
                topic_id=topic_id,
                state=state,
                user_id=request.user.id,
            )
        )


def _apply_match_rate_limit(*, user):
    """Keep anti-spam throttling on the match room's REST fallback path."""
    from apps.matching.services.input_gate import rate_limit_notice
    from apps.matching.services.rate_limit import check_rate_limit

    rate = check_rate_limit(user.id)
    if not rate["allowed"]:
        return Response(
            {
                "type": "rate_limited",
                "reason": rate["reason"],
                "retry_after": rate["retry_after"],
                "detail": rate_limit_notice(rate["reason"]),
            },
            status=status.HTTP_429_TOO_MANY_REQUESTS,
        )
    return None


class MatchingRoomMessagesView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, room_id: str):
        from apps.matching.services.matcher import get_room_messages

        match = _get_room_match_for_user(room_id=room_id, user_id=request.user.id)
        if not match:
            return Response(
                {"detail": "找不到這個配對房間。"},
                status=status.HTTP_404_NOT_FOUND,
            )
        gate = _godot_pretest_incomplete_response(match)
        if gate is not None:
            return gate

        match = _touch_room_match_for_user_activity(
            match=match,
            user_id=request.user.id,
        )
        messages = get_room_messages(match=match)
        return Response(
            _build_room_messages_payload(
                match=match,
                user_id=request.user.id,
                messages=messages,
            )
        )

    def post(self, request, room_id: str):
        from apps.matching.services.matcher import get_room_messages
        from .models import MatchMessage

        match = _get_room_match_for_user(room_id=room_id, user_id=request.user.id)
        if not match:
            return Response(
                {"detail": "找不到這個配對房間。"},
                status=status.HTTP_404_NOT_FOUND,
            )
        gate = _godot_pretest_incomplete_response(match)
        if gate is not None:
            return gate

        match = _touch_room_match_for_user_activity(
            match=match,
            user_id=request.user.id,
        )
        if match.status != DialogueMatch.Status.ACTIVE:
            return Response(
                {"detail": "這個配對房間目前無法傳送訊息。"},
                status=status.HTTP_409_CONFLICT,
            )

        serializer = MatchingRoomMessageCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        content = serializer.validated_data["content"].strip()

        # HTTP fallback for the match room, used when the WebSocket is not open.
        # H-H does not need the H-AI token-saving content gate. Keep the same
        # anti-spam rate limit as MatchRoomConsumer.
        gate_response = _apply_match_rate_limit(user=request.user)
        if gate_response is not None:
            return gate_response

        MatchMessage.objects.create(
            match=match,
            sender=request.user,
            content=content,
        )

        messages = get_room_messages(match=match)
        return Response(
            _build_room_messages_payload(
                match=match,
                user_id=request.user.id,
                messages=messages,
            ),
            status=status.HTTP_201_CREATED,
        )


class MatchingRoomOpeningView(APIView):
    """H-H 配對房的 AI 開場：GET 讀取、POST 產生（idempotent）。

    產生刻意不放在建房流程裡：那條路徑在配對成功的當下同步執行，多押一次
    LLM 呼叫會讓兩個人一起卡在等待畫面。改成進房後由前端補打一次，慢的是
    開場卡片而不是整間房。
    """

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, room_id: str):
        match = _get_room_match_for_user(room_id=room_id, user_id=request.user.id)
        if not match:
            return Response(
                {"detail": "找不到這個配對房間。"},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response({"opening": _serialize_match_opening(match)})

    def post(self, request, room_id: str):
        from apps.matching.services.opening import build_match_opening

        match = _get_room_match_for_user(room_id=room_id, user_id=request.user.id)
        if not match:
            return Response(
                {"detail": "找不到這個配對房間。"},
                status=status.HTTP_404_NOT_FOUND,
            )
        gate = _godot_pretest_incomplete_response(match)
        if gate is not None:
            # 前測還沒填完就沒有 Q9/Q10 可依據，開場也就無從生成。
            return gate

        existing = _serialize_match_opening(match)
        if existing is not None:
            return Response({"opening": existing, "pending": False})

        # 兩位參與者幾乎同時進房，會同時打這支端點。鎖只讓其中一位真的去呼叫
        # LLM；另一位拿到 pending=True，靠 WebSocket 廣播或下一次 GET 收斂。
        lock_key = f"match_opening_lock:{match.id}"
        if not cache.add(lock_key, "1", timeout=90):
            return Response({"opening": None, "pending": True})

        try:
            opening = build_match_opening(
                topic_id=match.topic_id,
                participant_a=_opening_participant_context(
                    user_id=match.user_a_id, topic_id=match.topic_id
                ),
                participant_b=_opening_participant_context(
                    user_id=match.user_b_id, topic_id=match.topic_id
                ),
            )
        except Exception:
            logger.exception("Match opening generation failed for room %s.", room_id)
            cache.delete(lock_key)
            return Response({"opening": None, "pending": False})

        if opening is None:
            # AI_OPENING_ENABLED=0：這場就是沒有開場，不要留鎖擋住之後開啟。
            cache.delete(lock_key)
            return Response({"opening": None, "pending": False})

        brief, _ = MatchOpeningBrief.objects.get_or_create(
            match=match,
            defaults={
                "content": opening["text"],
                "directions": opening["directions"],
                "source": opening["source"],
            },
        )
        payload = _serialize_match_opening_brief(brief)
        _broadcast_match_opening(room_id=room_id, opening=payload)
        return Response({"opening": payload, "pending": False})


def _opening_participant_context(*, user_id: int, topic_id: int) -> dict:
    profile = UserStanceProfile.objects.filter(
        user_id=user_id, topic_id=topic_id
    ).first()
    if not profile:
        return {"q9": "", "q10": "", "stance_label": ""}

    open_answers = profile.survey_open_answers or {}
    return {
        "q9": _get_open_answer(open_answers, question_id=9, question_code="Q9"),
        "q10": _get_open_answer(open_answers, question_id=10, question_code="Q10"),
        "stance_label": _stance_user_label(
            topic_id=topic_id,
            stance_category=profile.stance_category,
        ),
    }


def _stance_user_label(*, topic_id: int, stance_category: str) -> str:
    labels = TOPIC_CONFIGS.get(topic_id, {}).get("stance_labels", {})
    return (labels.get(stance_category) or {}).get("user_label", "")


def _serialize_match_opening(match: DialogueMatch) -> dict | None:
    brief = MatchOpeningBrief.objects.filter(match=match).first()
    return _serialize_match_opening_brief(brief) if brief else None


def _serialize_match_opening_brief(brief: MatchOpeningBrief) -> dict:
    return {
        "content": brief.content,
        "directions": brief.directions or [],
        "source": brief.source,
        "created_at": brief.created_at.isoformat(),
    }


def _broadcast_match_opening(*, room_id: str, opening: dict) -> None:
    """把開場推給房裡另一位——他不必等自己的輪詢。失敗不影響已落庫的開場。"""
    try:
        from asgiref.sync import async_to_sync
        from channels.layers import get_channel_layer

        channel_layer = get_channel_layer()
        if channel_layer is None:
            return
        async_to_sync(channel_layer.group_send)(
            f"match_room_{room_id}",
            {"type": "match_opening", "opening": opening},
        )
    except Exception:
        logger.exception("Failed to broadcast match opening for room %s.", room_id)


class MatchingRoomSemanticTreeView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, room_id: str):
        from apps.matching.services.semantic_tree import semantic_tree_payload

        match = _get_room_match_for_user(room_id=room_id, user_id=request.user.id)
        if not match:
            return Response(
                {"detail": "找不到這個配對房間。"},
                status=status.HTTP_404_NOT_FOUND,
            )

        match = _touch_room_match_for_user_activity(
            match=match,
            user_id=request.user.id,
        )
        payload = semantic_tree_payload(
            match=match,
            root_name=_semantic_tree_root_name(match),
            current_user_id=request.user.id,
        )
        return Response(MatchingRoomSemanticTreeSerializer(payload).data)


class MatchingRoomSemanticTreeTimelineView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, room_id: str):
        from apps.matching.services.semantic_tree import semantic_tree_timeline_payload

        as_of_message_id = (request.query_params.get("as_of_message_id") or "").strip()
        if not as_of_message_id:
            return Response(
                {"detail": "缺少 as_of_message_id 參數。"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        match = _get_room_match_for_user(room_id=room_id, user_id=request.user.id)
        if not match:
            return Response(
                {"detail": "找不到這個配對房間。"},
                status=status.HTTP_404_NOT_FOUND,
            )

        locked = _timeline_locked_response(
            user_id=request.user.id,
            kind="match",
            conversation_id=match.room_id,
            conversation=match,
        )
        if locked is not None:
            return locked

        match = _touch_room_match_for_user_activity(
            match=match,
            user_id=request.user.id,
        )
        payload = semantic_tree_timeline_payload(
            match=match,
            root_name=_semantic_tree_root_name(match),
            current_user_id=request.user.id,
            source_message_id=as_of_message_id,
        )
        if payload is None:
            return Response(
                {"detail": "這則訊息還沒有被分析過。"},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(MatchingRoomSemanticTreeTimelineSerializer(payload).data)


class MatchingRoomSemanticTreeAnalyzeView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, room_id: str):
        from apps.matching.services.semantic_tree import (
            SemanticTreeError,
            analyze_pending_room_messages,
        )

        match = _get_room_match_for_user(room_id=room_id, user_id=request.user.id)
        if not match:
            return Response(
                {"detail": "找不到這個配對房間。"},
                status=status.HTTP_404_NOT_FOUND,
            )

        match = _touch_room_match_for_user_activity(
            match=match,
            user_id=request.user.id,
        )
        if match.status != DialogueMatch.Status.ACTIVE:
            return Response(
                {"detail": "這個配對房間目前無法分析語意樹。"},
                status=status.HTTP_409_CONFLICT,
            )

        try:
            payload = analyze_pending_room_messages(
                match=match,
                root_name=_semantic_tree_root_name(match),
                current_user_id=request.user.id,
            )
        except SemanticTreeError as exc:
            return Response(
                {
                    "error": exc.code,
                    "message": str(exc),
                    "analysisStatus": exc.code,
                },
                status=exc.status_code,
            )

        return Response(MatchingRoomSemanticTreeSerializer(payload).data)


class MatchingRoomLeaveView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, room_id: str):
        from apps.matching.services.matcher import close_match, get_matching_state

        match = _get_room_match_for_user(room_id=room_id, user_id=request.user.id)
        if not match:
            return Response(
                {"detail": "找不到這個配對房間。"},
                status=status.HTTP_404_NOT_FOUND,
            )

        closed_match = close_match(match=match)
        state = get_matching_state(user=request.user, topic_id=closed_match.topic_id)
        return Response(
            _build_matching_state_payload(
                topic_id=closed_match.topic_id,
                state=state,
                user_id=request.user.id,
            )
        )


class MessageReactionView(APIView):
    """讚 / 倒讚 on an opponent's message, in either dialogue mode.

    GET  /api/message-reactions/?target_type=ai&conversation_id=<session_id>
    GET  /api/message-reactions/?target_type=match&conversation_id=<room_id>
        → { "reactions": [ {target_id, value}, ... ] } for the current user.

    POST /api/message-reactions/  body: {target_type, target_id, value}
        value = 1 (讚) / -1 (倒讚) / 0 (remove). Only the *opponent's* messages
        may be reacted to; reacting to your own is rejected.
    """

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        target_type = request.query_params.get("target_type")
        conversation_id = request.query_params.get("conversation_id")
        if target_type not in {
            MessageReaction.TargetType.AI,
            MessageReaction.TargetType.MATCH,
        }:
            return Response(
                {"detail": "target_type 必須是 ai 或 match。"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not conversation_id:
            return Response(
                {"detail": "conversation_id 為必填。"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        reactions = MessageReaction.objects.filter(
            user=request.user,
            target_type=target_type,
            conversation_id=conversation_id,
        ).only("target_id", "value")
        return Response(
            {
                "reactions": [
                    {"target_id": reaction.target_id, "value": reaction.value}
                    for reaction in reactions
                ]
            }
        )

    def post(self, request):
        serializer = MessageReactionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        target_type = serializer.validated_data["target_type"]
        target_id = serializer.validated_data["target_id"]
        value = serializer.validated_data["value"]

        # Resolve + authorize the target, and pull denormalized context.
        if target_type == MessageReaction.TargetType.AI:
            context = self._resolve_ai_target(request.user, target_id)
        else:
            context = self._resolve_match_target(request.user, target_id)
        if context is None:
            return Response(
                {"detail": "找不到可回應的對方發言，或你無權對其反應。"},
                status=status.HTTP_404_NOT_FOUND,
            )

        if value == 0:
            MessageReaction.objects.filter(
                user=request.user,
                target_type=target_type,
                target_id=target_id,
            ).delete()
            return Response(
                {
                    "target_type": target_type,
                    "target_id": target_id,
                    "value": None,
                }
            )

        MessageReaction.objects.update_or_create(
            user=request.user,
            target_type=target_type,
            target_id=target_id,
            defaults={
                "value": value,
                "topic_id": context["topic_id"],
                "conversation_id": context["conversation_id"],
            },
        )
        return Response(
            {
                "target_type": target_type,
                "target_id": target_id,
                "value": value,
            }
        )

    @staticmethod
    def _resolve_ai_target(user, target_id):
        """The AI reply belongs to the user's own session — reacting to the
        agent's turn. Require ai_response present (something to react to)."""
        turn = (
            AIConversation.objects.filter(id=target_id, user=user)
            .exclude(ai_response__isnull=True)
            .exclude(ai_response="")
            .first()
        )
        if turn is None:
            return None
        return {
            "topic_id": turn.topic_id,
            "conversation_id": turn.session_id,
        }

    @staticmethod
    def _resolve_match_target(user, target_id):
        """Only the partner's messages are reactable; the user's own are not."""
        message = (
            MatchMessage.objects.select_related("match")
            .filter(id=target_id)
            .first()
        )
        if message is None:
            return None

        match = message.match
        if user.id not in {match.user_a_id, match.user_b_id}:
            return None
        if message.sender_id == user.id:
            return None
        return {
            "topic_id": match.topic_id,
            "conversation_id": match.room_id,
        }


def _finalize_input_gate_metrics(*, user, session_id, room_id):
    """Compute and store invalid_ratio / substantive_turn_count at dialogue end.

    Never raises into the questionnaire response — a metrics failure must not
    cost the participant their submitted answers.
    """
    from apps.matching.services.input_gate_store import (
        finalize_ai_session_metrics,
        finalize_match_metrics,
    )

    try:
        if session_id:
            finalize_ai_session_metrics(session_id)
        if room_id:
            match = DialogueMatch.objects.filter(room_id=room_id).first()
            if match is not None:
                finalize_match_metrics(match.id, user.id)
    except Exception:
        logger.exception(
            "Input gate metrics finalization failed session=%s room=%s user=%s.",
            session_id,
            room_id,
            user.id,
        )


def _post_dialogue_stance_snapshot(*, user, validated: dict):
    """Resolve s_pre from the exact conversation named by the submission."""
    topic_id = validated["topic_id"]
    condition = validated["experiment_condition"]

    if condition == PostDialogueResponse.ExperimentCondition.AI:
        record = DialogueSessionRecord.objects.filter(
            user=user,
            session_id=validated["session_id"],
            topic_id=topic_id,
        ).first()
        if record is None:
            raise exceptions.ValidationError(
                {"session_id": "找不到屬於你的同議題 AI 對話 session。"}
            )
        return (record.session_state or {}).get("user_stance_score")

    match = (
        DialogueMatch.objects.filter(
            room_id=validated["room_id"],
            topic_id=topic_id,
        )
        .filter(Q(user_a=user) | Q(user_b=user))
        .first()
    )
    if match is None:
        raise exceptions.ValidationError(
            {"room_id": "找不到屬於你的同議題配對房間。"}
        )
    return match.user_a_score if match.user_a_id == user.id else match.user_b_score


def _safe_evaluate_achievements(user) -> list[str]:
    """在既有流程裡結算成就，永遠不把例外往上丟。

    成就是附加價值，不是那些流程的目的：一次評估失敗絕不該讓受試者的問卷答案
    送不出去，或讓玩家進不了 Godot 大廳。漏掉的解鎖會在下次打開成就頁時由
    AchievementMeView 的 evaluate 補上，所以吞掉例外沒有永久後果——
    但要留 log，否則規則寫壞了沒人會發現。
    """
    try:
        return evaluate_achievements(user)
    except Exception:
        logger.exception("Achievement evaluation failed for user=%s.", user.id)
        return []


def _safe_unlock_knowledge_base_achievement(user) -> list[str]:
    """在知識庫瀏覽流程裡解鎖「求知若渴」，永遠不把例外往上丟。

    理由同 _safe_evaluate_achievements：成就壞掉不該讓知識庫這個主要功能一起
    404/500。這個埋點漏掉的代價比其他觸發點高一點（AchievementMeView 的評估補
    不回來——那條規則只回報 UserAchievement 的既有狀態），但下次瀏覽就會再試一
    次，所以仍然不值得為它犧牲知識庫本身。
    """
    try:
        return unlock_knowledge_base_achievement(user)
    except Exception:
        logger.exception(
            "Knowledge base achievement unlock failed for user=%s.", user.id
        )
        return []


class PostDialogueResponseView(APIView):
    """POST /api/post-questionnaire/ — submit post-dialogue questionnaire."""

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        serializer = PostDialogueResponseSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        validated = serializer.validated_data

        discomfort_detail = validated.pop("discomfort_detail", "") or ""
        s_pre = _post_dialogue_stance_snapshot(
            user=request.user,
            validated=validated,
        )

        response_obj = PostDialogueResponse(
            user=request.user,
            topic_id=validated["topic_id"],
            session_id=validated.get("session_id") or None,
            room_id=validated.get("room_id") or None,
            experiment_condition=validated["experiment_condition"],
            post_likert_1=validated["post_likert_1"],
            post_likert_2=validated["post_likert_2"],
            post_likert_3=validated["post_likert_3"],
            post_likert_4=validated["post_likert_4"],
            post_likert_5=validated["post_likert_5"],
            post_likert_6=validated["post_likert_6"],
            post_likert_7=validated["post_likert_7"],
            post_likert_8=validated["post_likert_8"],
            exp_stance_change_1=validated["exp_stance_change_1"],
            exp_stance_change_2=validated["exp_stance_change_2"],
            exp_quality_1=validated["exp_quality_1"],
            exp_quality_2=validated["exp_quality_2"],
            exp_reflection_1=validated["exp_reflection_1"],
            exp_reflection_2=validated["exp_reflection_2"],
            exp_comprehension_1=validated["exp_comprehension_1"],
            ccnd_attention=validated["ccnd_attention"],
            ccnd_awareness=validated["ccnd_awareness"],
            ccnd_influence=validated["ccnd_influence"],
            opponent_judgment=validated.get("opponent_judgment"),
            post_open_comprehension=validated["post_open_comprehension"],
            post_open_feedback=validated.get("post_open_feedback", ""),
            discomfort_flag=validated.get("discomfort_flag", False),
        )
        response_obj.fill_stance_metrics(s_pre)

        try:
            with transaction.atomic():
                response_obj.save()
                if response_obj.session_id:
                    _close_dialogue_session_record(
                        session_id=response_obj.session_id,
                        user_id=request.user.id,
                    )

                if response_obj.discomfort_flag and discomfort_detail.strip():
                    DiscomfortReport.objects.create(
                        response=response_obj,
                        detail=discomfort_detail.strip(),
                    )
        except IntegrityError:
            # post_response_user_session_unique / post_response_user_room_unique
            # 撞到：這場對話已經送過後測了。不能讓它變成第二筆紀錄——等級/成就
            # 是直接數 PostDialogueResponse 筆數，多一筆就等於免費多算一場；
            # 同一場對話多一列也會汙染研究資料的立場位移統計。
            return Response(
                {"detail": "這場對話已經送出過後測問卷了，不能重複提交。"},
                status=status.HTTP_409_CONFLICT,
            )

        # 對話結束點：把 input gate 的完整性指標算出來落庫。
        # 只產生欄位，**不**在這裡排除任何樣本——排除規則由研究端另行決定。
        _finalize_input_gate_metrics(
            user=request.user,
            session_id=response_obj.session_id,
            room_id=response_obj.room_id,
        )

        # 放在 _finalize_input_gate_metrics 之後單純是求穩：評估在所有落庫動作
        # 都完成之後才跑，之後若有規則要讀那些指標也不必再調順序。目前沒有任何
        # 規則依賴它 —— clean_dialogue_* 讀的是 profanity_only_total（由
        # record_ai_attempt 即時累加）與 PostDialogueResponse。
        _safe_evaluate_achievements(request.user)

        out = PostDialogueResponseOutputSerializer(response_obj)
        return Response(out.data, status=status.HTTP_201_CREATED)


class PostDialogueResponseConsentView(APIView):
    """PATCH /api/post-questionnaire/<id>/consent/ — record debriefing consent."""

    permission_classes = [permissions.IsAuthenticated]

    def patch(self, request, response_id: int):
        try:
            response_obj = PostDialogueResponse.objects.get(
                id=response_id,
                user=request.user,
            )
        except PostDialogueResponse.DoesNotExist:
            return Response(
                {"detail": "找不到這筆問卷紀錄。"},
                status=status.HTTP_404_NOT_FOUND,
            )

        serializer = PostDialogueResponseConsentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        response_obj.consent_confirmed = serializer.validated_data["consent_confirmed"]
        response_obj.save(update_fields=["consent_confirmed", "updated_at"])

        withdrawn = not response_obj.consent_confirmed
        return Response(
            {
                "id": response_obj.id,
                "consent_confirmed": response_obj.consent_confirmed,
                "withdrawn": withdrawn,
                "message": (
                    "你的資料已標記為撤回，不會被納入研究分析。感謝你的參與。"
                    if withdrawn
                    else "感謝你同意繼續參與本研究。"
                ),
            }
        )


class PlatformFeedbackView(APIView):
    """POST /api/platform-feedback/ — submit Part F platform experience feedback."""

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        serializer = PlatformFeedbackSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        validated = serializer.validated_data

        try:
            response_obj = PostDialogueResponse.objects.get(
                id=validated["response_id"],
                user=request.user,
            )
        except PostDialogueResponse.DoesNotExist:
            return Response(
                {"detail": "找不到對應的問卷紀錄。"},
                status=status.HTTP_404_NOT_FOUND,
            )

        feedback, _created = PlatformFeedback.objects.update_or_create(
            response=response_obj,
            defaults={
                "ux_matching": validated["ux_matching"],
                "ux_chatroom": validated["ux_chatroom"],
                "ux_nlp_intervention": validated["ux_nlp_intervention"],
                "ux_ccnd": validated["ux_ccnd"],
                "ux_overall": validated["ux_overall"],
                "nps_score": validated["nps_score"],
                "ux_improvement": validated.get("ux_improvement") or "",
            },
        )

        out = PlatformFeedbackOutputSerializer(feedback)
        return Response(out.data, status=status.HTTP_201_CREATED)


class IssueListCreateView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        qs = Issue.objects.all()
        author_id = request.query_params.get("author")
        if author_id:
            qs = qs.filter(author_id=author_id)
        data = [
            {
                "id": i.id,
                "title": i.title,
                "body": i.body,
                "author_id": i.author_id,
                "created_at": i.created_at,
            }
            for i in qs
        ]
        return Response(data)

    def post(self, request):
        title = request.data.get("title", "").strip()
        if not title:
            return Response(
                {"detail": "title 必填"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        issue = Issue.objects.create(
            author=request.user,
            title=title,
            body=request.data.get("body", ""),
        )
        return Response(
            {
                "id": issue.id,
                "title": issue.title,
                "body": issue.body,
                "author_id": issue.author_id,
                "created_at": issue.created_at,
            },
            status=status.HTTP_201_CREATED,
        )


_HEX_COLOR_RE = re.compile(r"#[0-9a-fA-F]{6}")


# 等級門檻：index = 等級（Lv0–Lv6），值 = 該級最低累積完成場次。Godot 大廳用它決定
# 玩家青蛙的顏色（見 godot/Entities/player/player_00.gd 與 scripts/recolor_frog.py）。
#
# 「完成」= 送出後測，也就是 PostDialogueResponse 有一筆紀錄 —— 這與成就頁「完成前測
# 與後測」「完成完整聊天流程」是同一個定義，也是唯一每場對話都留下一筆的耐久紀錄。
# H-H 與 H-AI 都算（不看 experiment_condition）。
#
# 門檻由指導教授指定，改這個 tuple 就能調；等級數要跟 SpriteFrames 裡的 lvN 動畫數一致。
# 級距刻意遞增（+2 +3 +5 +7 +10 +13），所以後段升級愈來愈慢。
LEVEL_THRESHOLDS = (0, 2, 5, 10, 17, 27, 40)


def dialogue_level(count: int) -> int:
    """累積完成場次 → 等級（0–6）。

    Lv0 的門檻是 0，所以每個人一開始就有顏色（白青蛙）。回傳值預設 0 是防禦性的：
    就算哪天門檻表被改成 Lv0 > 0，新玩家也還是拿得到一個合法等級而不是沒有等級。
    """
    level = 0
    for i, need in enumerate(LEVEL_THRESHOLDS):
        if count >= need:
            level = i
    return level


class TitleMeView(APIView):
    """GET/POST /api/titles/me/ — 玩家在 Godot 大廳看/選自己擁有的頭銜，順便回等級。

    頭銜本身怎麼解鎖由主功能成就系統決定（見 UserTitle 模型註解），這裡只管
    「我有哪些、目前選哪個」。契約見 godot-backend-integration.md §3.1。

    等級（青蛙顏色）刻意掛在這支而不是新開端點：Godot 大廳本來就會打它拿頭銜，
    等級是純衍生值（不存欄位、無 migration），順路回傳就好。頭銜與等級語意分開 ——
    頭銜是玩家自選的展示文字，等級是客觀資歷。
    """

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        owned = UserTitle.objects.filter(user=request.user).select_related("title")
        selected = next((ut for ut in owned if ut.is_selected), None)
        count = PostDialogueResponse.objects.filter(user=request.user).count()
        return Response(
            {
                "owned": [{"id": ut.title_id, "name": ut.title.name} for ut in owned],
                "selected_id": selected.title_id if selected else None,
                "color": (selected.color or selected.title.color) if selected else None,
                "dialogue_count": count,
                "level": dialogue_level(count),
                # 讓 Godot 能顯示「再 N 場升級」而不必自己抄一份門檻表
                "level_thresholds": list(LEVEL_THRESHOLDS),
            }
        )

    def post(self, request):
        # 「沒帶 title_id」跟「明確傳 title_id: null」意義不同：後者是「取消顯示
        # 頭銜」，前者多半是呼叫端漏帶。不分辨的話，只想改顏色的請求會意外把
        # 使用者的頭銜選擇清掉，所以這裡要求一定要明確帶上。
        if "title_id" not in request.data:
            return Response(
                {"detail": "必須帶 title_id（要取消顯示請明確傳 null）。"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        title_id = request.data.get("title_id")
        color = request.data.get("color")

        # color 直接進 DB，但 Django 不會在 save() 時檢查 max_length——SQLite 會
        # 默默存進怪字串，PostgreSQL 則會丟 DataError 變成 500。在這裡擋掉。
        if color is not None and color != "" and not _HEX_COLOR_RE.fullmatch(str(color)):
            return Response(
                {"detail": "color 需為 #RRGGBB 格式。"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        target = None
        if title_id is not None:
            try:
                target = UserTitle.objects.get(user=request.user, title_id=title_id)
            except UserTitle.DoesNotExist:
                return Response(
                    {"detail": "尚未擁有這個頭銜。"},
                    status=status.HTTP_403_FORBIDDEN,
                )

        # 驗證擁有權先於任何寫入——避免「清掉舊選擇後才發現目標無效」把使用者
        # 的選擇狀態意外清空。
        with transaction.atomic():
            UserTitle.objects.filter(user=request.user, is_selected=True).update(
                is_selected=False
            )
            if target is not None:
                # ponytail: 傳空字串/null 不會清回 Title 預設色，只是「不改色」——
                # 目前沒有「重設為預設色」的需求，有再加。
                if color:
                    target.color = color
                target.is_selected = True
                target.save(update_fields=["is_selected", "color"])

        return self.get(request)


def _achievement_item(definition, row):
    """把目錄定義 + 解鎖紀錄（可能沒有）攤成前端要的一筆。"""
    return {
        "code": definition.code,
        "name": definition.name,
        "how": definition.how,
        "description": definition.description,
        "title": definition.title_name,
        "unlocked": row is not None,
        "unlocked_at": row.unlocked_at if row is not None else None,
    }


class AchievementMeView(APIView):
    """GET /api/achievements/me/ — 成就頁的全部內容，外加還沒跳過通知的新解鎖。

    這支 GET 有副作用（會呼叫 evaluate 落庫新解鎖），這是刻意的：它是規則的
    最終安全網，玩家只要打開成就頁就會結算，不必依賴每一個觸發點都沒漏掉。

    newly_unlocked 的判準是 notified_at IS NULL（資料庫），不是 evaluate() 的
    回傳值——兩個併發請求可能都算出同一個新解鎖，但只有一列會真的被建立。
    通知跳完之後由前端呼叫 POST /api/achievements/ack/ 標記。
    """

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        # 這裡刻意不吞例外：成就頁是規則的最終安全網，規則壞掉要在這裡炸出來，
        # 不然只會安靜地回一頁全部未解鎖，沒人會發現。另外兩個觸發點相反——
        # 那裡的主線任務（送問卷、進大廳）比成就重要，所以走 _safe_ 包裝。
        evaluate_achievements(request.user)
        rows = {
            row.code: row
            for row in UserAchievement.objects.filter(user=request.user)
        }

        categories = []
        for category_id, category_title in CATEGORY_TITLES.items():
            categories.append(
                {
                    "id": category_id,
                    "title": category_title,
                    "items": [
                        _achievement_item(d, rows.get(d.code))
                        for d in CATALOG
                        if d.category == category_id
                    ],
                }
            )

        # 帶完整文案而不只是 code：toast 要顯示名稱、描述與頭銜，讓前端再去
        # categories 裡撈一次只是多一層可能對不上的查表。
        newly_unlocked = [
            _achievement_item(d, rows[d.code])
            for d in CATALOG
            if d.code in rows and rows[d.code].notified_at is None
        ]

        return Response(
            {"categories": categories, "newly_unlocked": newly_unlocked}
        )


class AchievementAckView(APIView):
    """POST /api/achievements/ack/ — 標記「這些解鎖通知已經跳過了」。

    只更新 notified_at 還是 NULL 的列，所以重送不會累加、也不會把時間往後推。
    queryset 一律鎖在 request.user 底下——code 是全域字串，不做這個限制就等於
    讓任何登入者去標記別人的通知。
    """

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        codes = request.data.get("codes")
        if not isinstance(codes, list) or not all(
            isinstance(code, str) for code in codes
        ):
            return Response(
                {"detail": "codes 必須是字串陣列。"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # 合法呼叫端最多也只會送目錄裡有的那些 code，超過就是畸形請求。
        if len(codes) > len(CATALOG):
            return Response(
                {"detail": "codes 數量超出上限。"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        updated = UserAchievement.objects.filter(
            user=request.user, code__in=codes, notified_at__isnull=True
        ).update(notified_at=timezone.now())

        return Response({"acknowledged": updated})


class IssueReactionsView(APIView):
    """GET/POST /api/issues/<issue_id>/reactions/ — 議題表情回復（5 選 1）。
    upsert：同一 reactor 對同一 issue 再送 = 覆蓋，不是疊加。契約見
    godot-backend-integration.md §3.2。"""

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, issue_id: int):
        if not Issue.objects.filter(pk=issue_id).exists():
            return Response(
                {"detail": "找不到這個議題。"}, status=status.HTTP_404_NOT_FOUND
            )
        counts: dict[str, int] = {}
        for idx in IssueReaction.objects.filter(issue_id=issue_id).values_list(
            "emoji_index", flat=True
        ):
            counts[str(idx)] = counts.get(str(idx), 0) + 1
        mine = (
            IssueReaction.objects.filter(issue_id=issue_id, reactor=request.user)
            .values_list("emoji_index", flat=True)
            .first()
        )
        return Response({"counts": counts, "mine": mine})

    def post(self, request, issue_id: int):
        try:
            issue = Issue.objects.get(pk=issue_id)
        except Issue.DoesNotExist:
            return Response(
                {"detail": "找不到這個議題。"}, status=status.HTTP_404_NOT_FOUND
            )
        emoji_index = request.data.get("emoji_index")
        # 必須是真的 int：bool 是 int 的子類別，JSON 的 true 會被當成 1，所以
        # 額外排除 bool；字串 "3" 也不接受，避免前端型別漂移悄悄過關。
        if (
            isinstance(emoji_index, bool)
            or not isinstance(emoji_index, int)
            or not (0 <= emoji_index <= 4)
        ):
            return Response(
                {"detail": "emoji_index 必須是 0-4 的整數。"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        IssueReaction.objects.update_or_create(
            issue=issue,
            reactor=request.user,
            defaults={"emoji_index": emoji_index},
        )
        return self.get(request, issue_id)


class GodotTicketIssueView(APIView):
    """POST /api/godot/tickets/ — 主功能頁面替目前登入者換一張 Godot 大廳入場券。

    回傳的 ticket 由 GodotLobby.jsx 塞進 iframe 的 window.bridgeus_ticket，
    Godot client 再交給 headless server 兌換（見 integration spec §5）。

    券不做回收：一次入場一列，~60 人的研究規模下可接受；若日後對外開放要補一支
    清理指令。
    """

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        ticket = issue_ticket(user=request.user)
        expires_in = int((ticket.expires_at - timezone.now()).total_seconds())
        return Response(
            {"ticket": ticket.token, "expires_in": expires_in},
            status=status.HTTP_201_CREATED,
        )


class GodotTicketRedeemView(APIView):
    """POST /api/godot/tickets/redeem/ — 常駐 headless Godot server 用服務金鑰
    把入場券換成 user_id，藉此確認「這個 peer 是哪個使用者」。

    呼叫者是 Godot server、不是使用者，沒有也不該有 JWT。清空 authentication_classes
    是必要的：預設的 JWTAuthentication 遇到過期/損壞的 Authorization header 會
    直接丟 401，根本輪不到底下的服務金鑰驗證跑（同 GodotMatchRoomView）。

    回應只有 user_id，不含任何顯示用名稱：username 在這個研究規模下可能就是
    研究對象自己選的真名或學號，對話室本身也刻意隱藏身份（見
    ANONYMOUS_MATCH_USER_NAME、MatchMessageSerializer.get_sender_name），沒有
    理由把登入帳號的識別字串交給遊戲端。大廳日後若需要顯示名稱，應該從稱號系統
    （/api/titles/me/）另外取，而不是從這裡。
    """

    authentication_classes = []
    permission_classes = [IsGodotServiceToken]

    def post(self, request):
        user = redeem_ticket(token=request.data.get("ticket"))
        if user is None:
            # 不區分「不存在／已用過／逾期」——呼叫端用不到，區分了等於給探測者 oracle。
            return Response(
                {"detail": "入場券無效。"}, status=status.HTTP_400_BAD_REQUEST
            )
        # 兌換成功 = 這位玩家真的進了 Godot 世界。
        _safe_evaluate_achievements(user)

        return Response({"user_id": user.id})


# Godot 綁定房的前測問卷期限。本階段只用來顯示倒數；逾時作廢是階段五。
GODOT_SURVEY_WINDOW_SECONDS = 300


class GodotMatchRoomView(APIView):
    """POST /api/godot/match-rooms/ — 給常駐 headless Godot server 呼叫，把兩位
    已在主功能登入的玩家直接配成一間議題聊天室，不走 M3 立場配對佇列
    （MatchingJoinView/enqueue_for_matching）。契約見
    godot-backend-integration.md §3.3；身份用共用服務金鑰而非 user JWT，見
    godot-web-deployment-spec.md §4。"""

    # 呼叫者是 Godot server、不是使用者，沒有也不該有 JWT。清空 authentication_classes
    # 是必要的：預設的 JWTAuthentication 遇到過期/損壞的 Authorization header 會
    # 直接丟 401，根本輪不到底下的服務金鑰驗證跑。
    authentication_classes = []
    permission_classes = [IsGodotServiceToken]

    def post(self, request):
        topic_id = request.data.get("topic_id")
        user_ids = request.data.get("user_ids")

        if not isinstance(topic_id, int) or topic_id not in TOPIC_CONFIGS:
            return Response(
                {"detail": "topic_id 無效。"}, status=status.HTTP_400_BAD_REQUEST
            )
        # 元素型別也要擋：User.objects.get(pk="a") 丟的是 ValueError 不是
        # DoesNotExist，下面的 try/except 接不到，會變成 500。bool 一併排除
        # （bool 是 int 的子類別，True 會被當成 pk=1）。
        if (
            not isinstance(user_ids, list)
            or len(user_ids) != 2
            or any(isinstance(u, bool) or not isinstance(u, int) for u in user_ids)
            or user_ids[0] == user_ids[1]
        ):
            return Response(
                {"detail": "user_ids 需為兩個不同的使用者 id（整數）。"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            user_a = User.objects.get(pk=user_ids[0])
            user_b = User.objects.get(pk=user_ids[1])
        except User.DoesNotExist:
            return Response(
                {"detail": "找不到其中一位使用者。"},
                status=status.HTTP_404_NOT_FOUND,
            )

        # 冪等：同一對人、同一議題已經有進行中的房間就直接沿用，不再開一間。
        # Godot server 重試或玩家重複觸發都可能打第二次，而下游
        # _get_active_match() 是 .first() 且沒有 order_by——真的開出兩間 ACTIVE
        # 的話，兩位參與者可能各自被導到不同房間、看到空的聊天室。
        existing = (
            DialogueMatch.objects.filter(
                topic_id=topic_id,
                status=DialogueMatch.Status.ACTIVE,
            )
            .filter(
                Q(user_a=user_a, user_b=user_b) | Q(user_a=user_b, user_b=user_a)
            )
            .order_by("-created_at")
            .first()
        )
        if existing:
            return Response(
                {
                    "room_id": existing.room_id,
                    "topic_id": topic_id,
                    "redirect_url": f"/topic/{topic_id}?mode=match",
                },
                status=status.HTTP_200_OK,
            )

        # Godot 木樁配對只做「同議題湊一對」，不跑 M3 立場向量配對。前測問卷是
        # 跳轉到網頁之後才填的（spec §D4），所以建房當下沒有 s_pre——兩個分數
        # 欄位留 NULL，等 /api/matching/godot-survey/ 回填。
        # matching_algorithm_version 標成 "godot_manual"，方便日後分析時跟真正
        # 演算法配對的資料分開看。
        match = DialogueMatch.objects.create(
            topic_id=topic_id,
            user_a=user_a,
            user_b=user_b,
            matching_algorithm_version="godot_manual",
            room_id=uuid4().hex,
            status=DialogueMatch.Status.ACTIVE,
            stats={
                # key 由 godot_binding 模組擁有——它是唯一判讀這段結構的地方，
                # 這裡是唯一的寫入點，兩邊必須用同一個常數才不會各自漂移。
                BINDING_STATS_KEY: {
                    "source": "godot",
                    # 問卷期限。本階段只回傳給前端倒數用，逾時裁決在階段五。
                    "survey_deadline": (
                        timezone.now()
                        + timedelta(seconds=GODOT_SURVEY_WINDOW_SECONDS)
                    ).isoformat(),
                }
            },
        )

        return Response(
            {
                "room_id": match.room_id,
                "topic_id": topic_id,
                # 前端沒有獨立的 /dialogue/room/<id> 路由——配對聊天室其實是
                # TopicChat.jsx 掛在 /topic/<topic_id>?mode=match，內部再用
                # GET /api/matching/status/?topic_id= 找到這筆 DialogueMatch。
                "redirect_url": f"/topic/{topic_id}?mode=match",
            },
            status=status.HTTP_201_CREATED,
        )
