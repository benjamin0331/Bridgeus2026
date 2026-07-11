import logging
import os
import uuid as _uuid_mod
from functools import lru_cache
from uuid import uuid4

from django.contrib.auth.models import User
from django.core.cache import cache
from django.db.models import (
    Case,
    Count,
    IntegerField,
    OuterRef,
    Q,
    Subquery,
    Sum,
    TextField,
    Value,
    When,
)
from django.db.models.functions import Coalesce
from django.utils import timezone
from rest_framework import generics, permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.authentication import JWTStatelessUserAuthentication
from rest_framework_simplejwt.tokens import RefreshToken

from apps.matching.services.semantic import build_q9_embedding

from .models import (
    AIConversation,
    DialogueMatch,
    DialogueSessionRecord,
    DiscomfortReport,
    Issue,
    MatchMessage,
    MatchStanceDrift,
    PlatformFeedback,
    PostDialogueResponse,
)
from .dialogue_topics import (
    TOPIC_CONFIGS,
    get_dialogue_survey,
    get_dialogue_topics,
)
from .serializers import (
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
)

SESSION_TTL_SECONDS = 60 * 60 * 12
logger = logging.getLogger(__name__)
DEFAULT_DIALOGUE_COLLECTION = os.getenv(
    "DEFAULT_DIALOGUE_COLLECTION",
    "general_knowledge",
)
ANONYMOUS_MATCH_USER_NAME = "匿名對話者"


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

    return {
        "scale_min": int(scale_config.get("min", 1)),
        "scale_max": int(scale_config.get("max", 7)),
        "reverse_question_ids": {
            str(question_id)
            for question_id in stance_rules.get("reverse_question_ids", [])
        },
        "support_threshold": float(stance_rules.get("support_threshold", 4.5)),
        "oppose_threshold": float(stance_rules.get("oppose_threshold", 3.5)),
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
    message_count = getattr(record, "summary_message_count", None)
    if message_count is None:
        turns = AIConversation.objects.filter(
            user=record.user,
            session_id=record.session_id,
        ).values("ai_response")
        message_count = sum(2 if turn["ai_response"] else 1 for turn in turns)
    preview = getattr(record, "summary_last_message_preview", None)
    if preview is None:
        messages = _history_ai_messages(record)
        user_messages = [message for message in messages if message["role"] == "user"]
        preview_source = user_messages[-1] if user_messages else (
            messages[-1] if messages else None
        )
        preview = (preview_source or {}).get("content", "")
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
        "message_count": message_count,
        "last_message_preview": preview[:120],
        "last_activity_at": record.last_activity_at,
    }


def _history_match_summary(match: DialogueMatch, *, user_id: int) -> dict:
    message_count = getattr(match, "summary_message_count", None)
    if message_count is None:
        message_count = match.messages.count()
    preview = getattr(match, "summary_last_message_preview", None)
    preview_created_at = getattr(match, "summary_last_message_created_at", None)
    if preview is None or preview_created_at is None:
        messages = _history_match_messages(match, user_id=user_id)
        preview_source = messages[-1] if messages else None
        preview = (preview_source or {}).get("content", "")
        preview_created_at = (preview_source or {}).get("created_at")
    return {
        "kind": "match",
        "id": match.room_id,
        "room_id": match.room_id,
        "topic_id": match.topic_id,
        "topic_title": _semantic_tree_root_name(match),
        "status": match.status,
        "message_count": message_count,
        "last_message_preview": preview[:120],
        "last_activity_at": (
            preview_created_at
            or match.closed_at
            or match.created_at
        ),
    }


def _history_ai_detail(record: DialogueSessionRecord) -> dict:
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
    }


def _history_ai_summary_queryset(user_id: int):
    ai_turns = AIConversation.objects.filter(
        user_id=user_id,
        session_id=OuterRef("session_id"),
    )
    ai_turn_counts = ai_turns.values("session_id").annotate(
        turn_count=Count("id"),
        ai_response_count=Sum(
            Case(
                When(
                    ai_response__isnull=False,
                    then=Case(
                        When(ai_response="", then=Value(0)),
                        default=Value(1),
                        output_field=IntegerField(),
                    ),
                ),
                default=Value(0),
                output_field=IntegerField(),
            )
        ),
    )
    return (
        DialogueSessionRecord.objects.filter(user_id=user_id)
        .annotate(
            summary_turn_count=Coalesce(
                Subquery(ai_turn_counts.values("turn_count")[:1]),
                0,
            ),
            summary_ai_response_count=Coalesce(
                Subquery(ai_turn_counts.values("ai_response_count")[:1]),
                0,
            ),
            summary_last_message_preview=Coalesce(
                Subquery(
                    ai_turns.order_by("-created_at", "-id").values("user_prompt")[:1],
                    output_field=TextField(),
                ),
                Value(""),
                output_field=TextField(),
            ),
        )
        .annotate(
            summary_message_count=(
                Coalesce("summary_turn_count", 0)
                + Coalesce("summary_ai_response_count", 0)
            )
        )
        .order_by("-last_activity_at", "-id")
    )


def _history_match_summary_queryset(user_id: int):
    latest_messages = MatchMessage.objects.filter(match_id=OuterRef("pk")).order_by(
        "-created_at",
        "-id",
    )
    return (
        DialogueMatch.objects.select_related("user_a", "user_b")
        .filter(Q(user_a_id=user_id) | Q(user_b_id=user_id))
        .annotate(
            summary_message_count=Count("messages"),
            summary_last_message_preview=Coalesce(
                Subquery(
                    latest_messages.values("content")[:1],
                    output_field=TextField(),
                ),
                Value(""),
                output_field=TextField(),
            ),
            summary_last_message_created_at=Subquery(
                latest_messages.values("created_at")[:1]
            ),
        )
        .order_by("-created_at", "-id")
    )


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
            for record in _history_ai_summary_queryset(request.user.id):
                summary = _history_ai_summary(record)
                if summary["message_count"]:
                    results.append(summary)

        if history_type in {"all", "match"}:
            for match in _history_match_summary_queryset(request.user.id):
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
            return Response(_history_ai_detail(record))

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
                    **_history_ai_detail(record),
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
