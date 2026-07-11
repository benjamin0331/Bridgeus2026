from api.dialogue_topics import TOPIC_CONFIGS
from api.models import AIConversation, DialogueMatch, DialogueSessionRecord
from api.serializers import MatchingRoomSemanticTreeSerializer
from api.services.dialogue_session import _dialogue_session_cache_payload_from_record
from api.services.room_state import ANONYMOUS_MATCH_USER_NAME


def _semantic_tree_root_name(match: DialogueMatch) -> str:
    return _semantic_tree_root_name_for_topic_id(match.topic_id)


def _semantic_tree_root_name_for_topic_id(topic_id: int | None) -> str:
    return TOPIC_CONFIGS.get(topic_id, {}).get("title") or "核電"


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
