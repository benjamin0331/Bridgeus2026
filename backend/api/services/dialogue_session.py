import logging
import os
from functools import lru_cache

from django.core.cache import cache
from django.db import transaction
from django.utils import timezone

from apps.matching.services.semantic import build_q9_embedding

from api.dialogue_topics import TOPIC_CONFIGS, get_dialogue_survey
from api.models import AIConversation, DialogueSessionRecord
from api.services.stance_scoring import (
    _compute_user_stance_score,
    _resolve_open_answers,
    _resolve_stance_category,
    _resolve_stances,
)

SESSION_TTL_SECONDS = 60 * 60 * 12
logger = logging.getLogger(__name__)
DEFAULT_DIALOGUE_COLLECTION = os.getenv(
    "DEFAULT_DIALOGUE_COLLECTION",
    "general_knowledge",
)


def _session_cache_key(session_id: str) -> str:
    return f"dialogue_session:{session_id}"


def _cache_dialogue_session_record(session_record: dict) -> None:
    cache.set(
        _session_cache_key(session_record["session_id"]),
        session_record,
        timeout=SESSION_TTL_SECONDS,
    )


def invalidate_dialogue_session_cache(session_id: str) -> None:
    cache.delete(_session_cache_key(session_id))


def session_metadata(session, *, stance_drift: dict | None) -> dict:
    """DB-persisted projection of a DialogueSession: to_dict() minus history,
    plus the last-measured stance_drift. History lives only in AIConversation
    turns; this is what update_session_metadata writes to session_state."""
    data = session.to_dict()
    data.pop("history", None)
    data["stance_drift"] = stance_drift
    return data


def build_session_state_from_turns(record: DialogueSessionRecord) -> dict:
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
    elif session_state.get("history"):
        logger.warning(
            "Session %s has no AIConversation turns; falling back to legacy "
            "session_state history (%d entries).",
            record.session_id,
            len(session_state["history"]),
        )
    session_state["dialogue_phase"] = latest_phase
    return session_state


def _dialogue_session_cache_payload_from_record(
    record: DialogueSessionRecord,
) -> dict:
    session_state = build_session_state_from_turns(record)
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


def create_dialogue_session_record(
    *,
    user_id: int,
    session_id: str,
    topic_id: int,
    topic_title: str,
    collection_name: str,
    survey_context: dict,
    metadata: dict,
) -> DialogueSessionRecord:
    return DialogueSessionRecord.objects.create(
        user_id=user_id,
        session_id=session_id,
        topic_id=topic_id,
        topic_title=topic_title or f"議題 {topic_id}",
        collection_name=collection_name or DEFAULT_DIALOGUE_COLLECTION,
        survey_context=survey_context or {},
        session_state=metadata,
        status=DialogueSessionRecord.Status.ACTIVE,
        last_activity_at=timezone.now(),
    )


def update_session_metadata(*, session_id: str, user_id: int, metadata: dict) -> dict:
    """Reply/WS-only write. Locks the row, re-reads current session_state, and
    merges the caller's fresh metadata onto it — only dialogue_phase,
    focus_signal_count and user_reasoning_mode need special merge rules
    (everything else is a fixed baseline set once at creation, so taking the
    caller's value is safe). Never touches semantic_tree_state or history."""
    _, DialoguePhase, _ = _get_dialogue_runtime()
    with transaction.atomic():
        record = DialogueSessionRecord.objects.select_for_update().get(
            session_id=session_id,
            user_id=user_id,
        )
        current = record.session_state or {}
        turn_count = AIConversation.objects.filter(
            user_id=user_id,
            session_id=session_id,
            user_prompt__gt="",
        ).count()

        merged = dict(metadata)
        merged["dialogue_phase"] = DialoguePhase.from_turn_count(turn_count).value
        merged["focus_signal_count"] = max(
            current.get("focus_signal_count", 0) or 0,
            metadata.get("focus_signal_count", 0) or 0,
        )
        current_mode = current.get("user_reasoning_mode", "unknown")
        merged["user_reasoning_mode"] = (
            current_mode
            if current_mode != "unknown"
            else metadata.get("user_reasoning_mode", "unknown")
        )

        record.session_state = merged
        record.last_activity_at = timezone.now()
        record.save(update_fields=["session_state", "last_activity_at"])
    return merged


def update_semantic_tree_state(
    *,
    session_id: str,
    user_id: int,
    semantic_tree: dict,
) -> None:
    """Targeted semantic_tree_state write for the AI-session analyze path.

    P1's own locked transaction (semantic_tree.py) doesn't exist on this
    branch yet, so this stands in for it at the field-ownership boundary this
    design assumes: only semantic_tree_state, never session_state/history.
    Should collapse once P1 lands (see HANDOFF_P5)."""
    with transaction.atomic():
        record = DialogueSessionRecord.objects.select_for_update().get(
            session_id=session_id,
            user_id=user_id,
        )
        record.semantic_tree_state = semantic_tree or {}
        record.save(update_fields=["semantic_tree_state"])


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
