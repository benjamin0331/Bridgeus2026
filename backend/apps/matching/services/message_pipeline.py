"""Shared H-H message pipeline.

Used by both the REST fallback path (``MatchingRoomMessagesView.post``) and the
WebSocket path (``MatchRoomConsumer``), so a message posted via either route goes
through the same blacklist filter, is persisted and broadcast the same way, and
gets the same embedding/emotion-score scheduling.

Decision (HANDOFF_P2): the WS path additionally runs an interactive
rephrase-suggestion round trip when emotion intensity is over threshold (see
``MatchRoomConsumer._handle_ai_assisted_message``) -- that needs a live socket to
negotiate the suggestion over. REST has no such channel, so ``post_match_message``
only ever blocks on the synchronous blacklist stage; an over-threshold emotion
score is recorded on the message but does not block it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer

from apps.matching.services.hh_ai import hh_ai_assist_enabled
from chat.services.embedding import get_embedding
from chat.services.emotion import analyze_emotion
from chat.services.filter import check_content_sync

logger = logging.getLogger(__name__)

BLOCKED_MESSAGE = "這則訊息包含可能冒犯對方的用語，請修改後重新發送。"


@dataclass
class MessagePostResult:
    message: Any | None
    blocked: bool
    filter_result: dict | None
    emotion: dict | None


def message_payload(match, message) -> dict:
    return {
        "id": message.id,
        "match_id": match.id,
        "room_id": match.room_id,
        "sender_id": message.sender_id,
        "sender_name": "匿名使用者",
        "content": message.content,
        "created_at": message.created_at.isoformat(),
    }


def broadcast_message(match, message) -> None:
    channel_layer = get_channel_layer()
    if channel_layer is None:
        return
    async_to_sync(channel_layer.group_send)(
        f"match_room_{match.room_id}",
        {"type": "match.message", "message": message_payload(match, message)},
    )


def finalize_match_message(*, match, sender, content: str, emotion_score=None):
    """Create + broadcast a message. Shared core for the REST pipeline below and
    for ``MatchRoomConsumer._relay_and_persist`` (wrapped in ``database_sync_to_async``
    there, since it touches the DB and the channel layer)."""
    from api.models import MatchMessage

    message = MatchMessage.objects.create(
        match=match,
        sender=sender,
        content=content,
        emotion_score=emotion_score,
    )
    broadcast_message(match, message)
    return message


def _schedule_embedding_and_emotion(
    message_id: int, content: str, *, emotion_score=None
) -> None:
    from api.models import MatchMessage

    try:
        embedding = get_embedding(content)
        MatchMessage.objects.filter(id=message_id).update(embedding=embedding)
    except Exception:
        logger.exception("Embedding failed for match message %s.", message_id)

    if emotion_score is not None:
        try:
            MatchMessage.objects.filter(id=message_id).update(
                emotion_score=emotion_score
            )
        except Exception:
            logger.exception("Emotion score save failed for match message %s.", message_id)


def post_match_message(*, match, sender, content: str) -> MessagePostResult:
    """Synchronous end-to-end pipeline for the REST fallback path: blacklist filter
    -> emotion score -> create -> broadcast -> schedule embedding/emotion persistence.
    """
    ai_assist = hh_ai_assist_enabled()
    filter_result = None
    emotion = None

    if ai_assist:
        filter_result = check_content_sync(content)
        if filter_result.get("is_blocked"):
            return MessagePostResult(
                message=None,
                blocked=True,
                filter_result=filter_result,
                emotion=None,
            )
        emotion = analyze_emotion(content)

    emotion_score = emotion.get("score") if emotion else None
    message = finalize_match_message(
        match=match,
        sender=sender,
        content=content,
        emotion_score=emotion_score,
    )

    if ai_assist:
        _schedule_embedding_and_emotion(message.id, content, emotion_score=emotion_score)

    return MessagePostResult(
        message=message,
        blocked=False,
        filter_result=filter_result,
        emotion=emotion,
    )
