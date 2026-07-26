import logging
import os
import re
import uuid as _uuid_mod
from decimal import Decimal
from functools import lru_cache
from uuid import uuid4

from django.contrib.auth.models import Group, User
from django.core.cache import cache
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework import generics, permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.authentication import JWTStatelessUserAuthentication
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenObtainPairView

from apps.matching.services.semantic import build_q9_embedding
from apps.summary.models import ViewpointNode

from .permissions import IsGodotServiceToken, IsResearcher, RESEARCHER_GROUP_NAME

from .models import (
    AIConversation,
    DialogueMatch,
    DialogueSessionRecord,
    DiscomfortReport,
    Issue,
    IssueReaction,
    MatchStanceDrift,
    PlatformFeedback,
    PostDialogueResponse,
    Title,
    UserStanceProfile,
    UserTitle,
)
from .dialogue_topics import (
    TOPIC_CONFIGS,
    get_dialogue_survey,
    get_dialogue_topics,
)
from .display_settings import get_stance_thresholds
from .timeline_access import LOCKED_DETAIL, timeline_unlock_state
from .serializers import (
    AccountCreateSerializer,
    AccountListSerializer,
    AccountUpdateSerializer,
    PasswordResetSerializer,
    AIConversationSerializer,
    DialogueReplySerializer,
    DialogueSurveySerializer,
    DialogueTopicSerializer,
    DialogueSessionCreateSerializer,
    MatchMessageSerializer,
    MatchingJoinSerializer,
    MatchingRoomMessageCreateSerializer,
    MatchingRoomMessagesSerializer,
    MatchingRoomSemanticTreeSerializer,
    MatchingRoomSemanticTreeTimelineSerializer,
    MatchingStateSerializer,
    MatchingTopicSerializer,
    PlatformFeedbackSerializer,
    PlatformFeedbackOutputSerializer,
    PostDialogueResponseConsentSerializer,
    PostDialogueResponseOutputSerializer,
    PostDialogueResponseSerializer,
    BridgeUsTokenObtainPairSerializer,
    ViewpointNodeReviewDecisionSerializer,
    ViewpointNodeReviewSerializer,
)

SESSION_TTL_SECONDS = 60 * 60 * 12
logger = logging.getLogger(__name__)
DEFAULT_DIALOGUE_COLLECTION = os.getenv(
    "DEFAULT_DIALOGUE_COLLECTION",
    "general_knowledge",
)
ANONYMOUS_MATCH_USER_NAME = "匿名對話者"


class BridgeUsTokenObtainPairView(TokenObtainPairView):
    """跟 SimpleJWT 內建的 TokenObtainPairView 唯一差別是 access token payload
    多帶一個 is_researcher claim（見 BridgeUsTokenObtainPairSerializer）。
    掛在 BridgeUs_Django/urls.py 的 /api/token/，取代原本的 TokenObtainPairView。
    """

    serializer_class = BridgeUsTokenObtainPairSerializer


def _session_cache_key(session_id: str) -> str:
    return f"dialogue_session:{session_id}"


def _cache_dialogue_session_record(session_record: dict) -> None:
    cache.set(
        _session_cache_key(session_record["session_id"]),
        session_record,
        timeout=SESSION_TTL_SECONDS,
    )


def _rebuild_session_state_from_turns(record: DialogueSessionRecord) -> dict:
    session_state = (record.session_state or {}).copy()
    turns = AIConversation.objects.filter(
        user=record.user,
        session_id=record.session_id,
    ).order_by("created_at", "id")
    history = []
    latest_phase = session_state.get("dialogue_phase") or "engagement"

    for turn in turns:
        if turn.user_prompt:
            history.append({"role": "user", "content": turn.user_prompt})
        if turn.ai_response:
            history.append({"role": "agent", "content": turn.ai_response})
        if turn.dialogue_phase:
            latest_phase = turn.dialogue_phase

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
) -> dict:
    session_state = session_record.get("session") or {}
    history = session_state.get("history") or []
    stance_drift = session_state.get("stance_drift")
    stance_score = session_state.get("user_stance_score")
    try:
        stance_category = _resolve_stance_category(
            topic_id=int(session_record.get("topic_id")),
            user_stance_score=float(stance_score),
        )
    except (TypeError, ValueError):
        stance_category = None

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


def _get_survey_scoring_config(topic_id: int) -> dict:
    survey_config = get_dialogue_survey(topic_id) or {}
    scale_config = survey_config.get("scale", {})
    stance_rules = survey_config.get("stance_rules", {})
    likert_questions = survey_config.get("questions", [])
    # 門檻走覆寫層：Supervisor 在設定頁調過的值優先於 SURVEY_CONFIGS。
    # 這裡刻意不加快取，否則改設定要重啟服務才生效。
    support_threshold, oppose_threshold = get_stance_thresholds(topic_id=topic_id)

    return {
        "scale_min": int(scale_config.get("min", 1)),
        "scale_max": int(scale_config.get("max", 7)),
        "reverse_question_ids": {
            str(question_id)
            for question_id in stance_rules.get("reverse_question_ids", [])
        },
        "support_threshold": support_threshold,
        "oppose_threshold": oppose_threshold,
        "neutral_score": float(
            (
                float(scale_config.get("min", 1))
                + float(scale_config.get("max", 7))
            )
            / 2
        ),
        "likert_question_ids": {
            str(question["id"]) for question in likert_questions
        },
        "open_question_mappings": [
            {
                "id": question["id"],
                "code": question["code"],
            }
            for question in survey_config.get("open_questions", [])
        ],
    }


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
    scoring_config = _get_survey_scoring_config(topic_id)
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


def _resolve_stance_category(*, topic_id: int, user_stance_score: float) -> str:
    scoring_config = _get_survey_scoring_config(topic_id)

    if user_stance_score > scoring_config["support_threshold"]:
        return "support"
    if user_stance_score < scoring_config["oppose_threshold"]:
        return "oppose"
    return "neutral"


def _resolve_stances(
    *,
    topic_id: int,
    user_stance_score: float,
) -> tuple[str, str, str]:
    stance_category = _resolve_stance_category(
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
    scoring_config = _get_survey_scoring_config(topic_id)
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

    from apps.matching.services.ai_agent import infer_reasoning_mode
    user_reasoning_mode = infer_reasoning_mode(
        user_stance_score=user_stance_score,
        user_initial_argument=resolved_initial_argument,
        opponent_view_text=resolved_open_answers.get("Q10", ""),
    )

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
    stance_category = _resolve_stance_category(
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
    messages = []
    for message in match.messages.select_related("sender").order_by("created_at", "id"):
        is_current_user = message.sender_id == user_id
        messages.append(
            {
                "id": f"match-{message.id}",
                "source_id": str(message.id),
                "role": "user" if is_current_user else "partner",
                "sender_label": "我" if is_current_user else ANONYMOUS_MATCH_USER_NAME,
                "content": message.content,
                "created_at": message.created_at,
            }
        )
    return messages


def _history_ai_summary(record: DialogueSessionRecord) -> dict:
    messages = _history_ai_messages(record)
    user_messages = [message for message in messages if message["role"] == "user"]
    preview_source = user_messages[-1] if user_messages else (messages[-1] if messages else None)
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
        "status": record.status,
        "message_count": len(messages),
        "last_message_preview": (preview_source or {}).get("content", "")[:120],
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
    permission_classes = [permissions.IsAuthenticated]
    authentication_classes = [JWTStatelessUserAuthentication]

    def get(self, request):
        serializer = DialogueTopicSerializer(get_dialogue_topics(), many=True)
        return Response(serializer.data)


class DialogueSurveyView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    authentication_classes = [JWTStatelessUserAuthentication]

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
    authentication_classes = [JWTStatelessUserAuthentication]

    def get(self, request, topic_id: int):
        if not get_dialogue_survey(topic_id):
            return Response(
                {"detail": "找不到這個議題的問卷設定。"},
                status=status.HTTP_404_NOT_FOUND,
            )

        profile = (
            UserStanceProfile.objects.filter(user=request.user, topic_id=topic_id)
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


class DialogueSessionCreateView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        _, _, DialogueSession = _get_dialogue_runtime()
        serializer = DialogueSessionCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        validated = serializer.validated_data

        topic_config = _build_topic_config(
            topic_id=validated["topic_id"],
            topic_title=validated["topic_title"],
            topic_description=validated.get("topic_description", ""),
            survey_answers=validated.get("survey_answers", {}),
            survey_open_answers=validated.get("survey_open_answers", {}),
            user_initial_argument=validated.get("user_initial_argument", ""),
        )

        # Persist the pre-survey stance so a later "new dialogue" can reuse it.
        # Only when the survey was actually filled, to avoid overwriting a real
        # profile with the neutral default of an empty answer set.
        if validated.get("survey_answers"):
            _upsert_user_stance_profile(
                user=request.user,
                topic_id=validated["topic_id"],
                survey_answers=validated["survey_answers"],
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
            "user_id": request.user.id,
            "session_id": session_id,
            "topic_id": validated["topic_id"],
            "topic_title": topic_config["topic"],
            "collection_name": topic_config["collection_name"],
            "survey_context": {
                "survey_answers": validated.get("survey_answers", {}),
                "survey_open_answers": topic_config["survey_open_answers"],
                "semantic_vector_interface": topic_config[
                    "semantic_vector_interface"
                ],
                "q9_embedding": topic_config["q9_embedding"],
            },
            "session": session.to_dict(),
        }
        _cache_dialogue_session_record(session_record)
        _persist_dialogue_session_record(session_record)
        _close_superseded_dialogue_sessions(
            user_id=request.user.id,
            topic_id=validated["topic_id"],
            keep_session_id=session_id,
        )

        return Response(
            {
                "session_id": session_id,
                "dialogue_phase": session.dialogue_phase.value,
                "stance_score": session.user_stance_score,
                "stance_category": _resolve_stance_category(
                    topic_id=validated["topic_id"],
                    user_stance_score=session.user_stance_score,
                ),
                "stance_label": session.user_stance_label,
                "stance_drift": None,
                "history": session.to_dict()["history"],
            },
            status=status.HTTP_201_CREATED,
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
            )
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

        try:
            reply = get_dialogue_agent(session_record["collection_name"]).respond(
                session
            )
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

        from apps.matching.services.ai_agent import split_into_chunks

        chunks = split_into_chunks(reply)
        session.add_agent_message(reply)
        session_record["session"] = session.to_dict()
        saved_turn.ai_response = reply
        saved_turn.dialogue_phase = session.dialogue_phase.value
        saved_turn.save(update_fields=["ai_response", "dialogue_phase"])
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
                "stance_category": _resolve_stance_category(
                    topic_id=session_record.get("topic_id"),
                    user_stance_score=session.user_stance_score,
                ),
                "stance_label": session.user_stance_label,
                "stance_drift": stance_drift,
                "history": session_record["session"]["history"],
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
            for record in DialogueSessionRecord.objects.filter(
                user=request.user,
            ).order_by("-last_activity_at", "-id"):
                summary = _history_ai_summary(record)
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
    """研究者專用：核准或退回單一 ViewpointNode。"""

    permission_classes = [IsResearcher]

    def post(self, request, pk: int):
        try:
            node = ViewpointNode.objects.get(pk=pk)
        except ViewpointNode.DoesNotExist:
            return Response({"detail": "找不到這筆觀點。"}, status=status.HTTP_404_NOT_FOUND)

        decision = ViewpointNodeReviewDecisionSerializer(data=request.data)
        decision.is_valid(raise_exception=True)

        node.review_status = (
            ViewpointNode.ReviewStatus.APPROVED
            if decision.validated_data["action"] == "approve"
            else ViewpointNode.ReviewStatus.REJECTED
        )
        node.reviewed_by = request.user
        node.reviewed_at = timezone.now()
        node.review_notes = decision.validated_data["notes"]
        node.save(
            update_fields=["review_status", "reviewed_by", "reviewed_at", "review_notes"]
        )

        return Response(ViewpointNodeReviewSerializer(node).data)


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

        serializer = PasswordResetSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        target.set_password(serializer.validated_data["password"])
        target.save(update_fields=["password"])
        return Response({"detail": "密碼已重設。"})


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

        stance_score = _compute_user_stance_score(
            topic_id=validated["topic_id"],
            survey_answers=validated["survey_answers"],
        )
        stance_category = _resolve_stance_category(
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

        MatchMessage.objects.create(
            match=match,
            sender=request.user,
            content=serializer.validated_data["content"].strip(),
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


class PostDialogueResponseView(APIView):
    """POST /api/post-questionnaire/ — submit post-dialogue questionnaire."""

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        serializer = PostDialogueResponseSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        validated = serializer.validated_data

        discomfort_detail = validated.pop("discomfort_detail", "") or ""

        response_obj = PostDialogueResponse.objects.create(
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
            ccnd_attention=validated["ccnd_attention"],
            ccnd_awareness=validated["ccnd_awareness"],
            ccnd_influence=validated["ccnd_influence"],
            opponent_judgment=validated.get("opponent_judgment"),
            post_open_comprehension=validated["post_open_comprehension"],
            post_open_feedback=validated.get("post_open_feedback", ""),
            discomfort_flag=validated.get("discomfort_flag", False),
        )

        if response_obj.session_id:
            _close_dialogue_session_record(
                session_id=response_obj.session_id, user_id=request.user.id
            )

        if response_obj.discomfort_flag and discomfort_detail.strip():
            DiscomfortReport.objects.create(
                response=response_obj,
                detail=discomfort_detail.strip(),
            )

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
class GuestLoginView(APIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        nickname = (request.data.get("nickname") or "Guest")[:30]
        username = f"guest_{_uuid_mod.uuid4().hex[:8]}"
        user = User.objects.create_user(username=username, password=None)
        user.first_name = nickname
        user.save(update_fields=["first_name"])
        refresh = RefreshToken.for_user(user)
        return Response(
            {
                "access": str(refresh.access_token),
                "refresh": str(refresh),
                "user_id": user.id,
                "username": username,
            },
            status=status.HTTP_201_CREATED,
        )


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


class TitleMeView(APIView):
    """GET/POST /api/titles/me/ — 玩家在 Godot 大廳看/選自己擁有的頭銜。
    頭銜本身怎麼解鎖由主功能成就系統決定（見 UserTitle 模型註解），這裡只管
    「我有哪些、目前選哪個」。契約見 godot-backend-integration.md §3.1。"""

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        owned = UserTitle.objects.filter(user=request.user).select_related("title")
        selected = next((ut for ut in owned if ut.is_selected), None)
        return Response(
            {
                "owned": [{"id": ut.title_id, "name": ut.title.name} for ut in owned],
                "selected_id": selected.title_id if selected else None,
                "color": (selected.color or selected.title.color) if selected else None,
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
                    "redirect_url": f"/topic/{topic_id}?mode=match",
                },
                status=status.HTTP_200_OK,
            )

        # Godot 木樁配對只做「同議題湊一對」，不跑 M3 立場向量配對，所以沒有
        # 真實 stance score 可用——user_a_score/user_b_score 只是滿足 DB 1-7
        # constraint 的中性佔位值。matching_algorithm_version 標成
        # "godot_manual"，方便日後分析時跟真正演算法配對的資料分開看。
        match = DialogueMatch.objects.create(
            topic_id=topic_id,
            user_a=user_a,
            user_b=user_b,
            user_a_score=Decimal("4.00"),
            user_b_score=Decimal("4.00"),
            matching_algorithm_version="godot_manual",
            room_id=uuid4().hex,
            status=DialogueMatch.Status.ACTIVE,
        )

        return Response(
            {
                "room_id": match.room_id,
                # 前端沒有獨立的 /dialogue/room/<id> 路由——配對聊天室其實是
                # TopicChat.jsx 掛在 /topic/<topic_id>?mode=match，內部再用
                # GET /api/matching/status/?topic_id= 找到這筆 DialogueMatch。
                "redirect_url": f"/topic/{topic_id}?mode=match",
            },
            status=status.HTTP_201_CREATED,
        )
