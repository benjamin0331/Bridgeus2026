"""
對話結束處理服務。

end_session: 標記 Conversation 為 completed，收集並持久化統計數據。
generate_summary: 呼叫 Claude API 生成對話摘要，存入 Conversation.summary。
"""

import logging
import os

from asgiref.sync import sync_to_async

logger = logging.getLogger(__name__)


def end_session(
    conversation_id: int,
    *,
    blocked_count: int = 0,
    system_prompts_triggered: int = 0,
) -> dict:
    """
    Mark conversation as completed, persist ended_at, collect and persist stats.

    Returns stats dict:
        conversation_id, session_number, duration_minutes,
        total_messages, messages_per_user, avg_emotion_score_per_user,
        blocked_count, system_prompts_triggered, stance_drift_final
    """
    from django.utils import timezone as dj_tz

    from chat.models import Conversation, Message, StanceDrift

    conv = Conversation.objects.get(id=conversation_id)

    now = dj_tz.now()
    duration_minutes = (
        round((now - conv.started_at).total_seconds() / 60, 2)
        if conv.started_at
        else 0.0
    )

    conv.status = Conversation.Status.COMPLETED
    conv.ended_at = now
    conv.save(update_fields=["status", "ended_at"])

    messages = list(
        Message.objects.filter(conversation_id=conversation_id).order_by("timestamp")
    )

    user_a_id = conv.user_a_id
    user_b_id = conv.user_b_id

    messages_per_user = {user_a_id: 0, user_b_id: 0}
    emotion_scores_per_user: dict[int, list[float]] = {user_a_id: [], user_b_id: []}

    for msg in messages:
        if msg.sender_id in messages_per_user:
            messages_per_user[msg.sender_id] += 1
        if msg.emotion_score is not None and msg.sender_id in emotion_scores_per_user:
            emotion_scores_per_user[msg.sender_id].append(msg.emotion_score)

    avg_emotion_score_per_user: dict[int, float | None] = {}
    for uid, scores in emotion_scores_per_user.items():
        avg_emotion_score_per_user[uid] = (
            round(sum(scores) / len(scores), 4) if scores else None
        )

    stance_drift_final: dict[int, float | None] = {}
    for uid in (user_a_id, user_b_id):
        last_drift = (
            StanceDrift.objects.filter(
                conversation_id=conversation_id, user_id=uid
            )
            .order_by("-measured_at")
            .first()
        )
        stance_drift_final[uid] = (
            round(last_drift.drift_value, 4) if last_drift else None
        )

    stats = {
        "conversation_id": conversation_id,
        "session_number": conv.session_number,
        "duration_minutes": duration_minutes,
        "total_messages": len(messages),
        "messages_per_user": messages_per_user,
        "avg_emotion_score_per_user": avg_emotion_score_per_user,
        "blocked_count": blocked_count,
        "system_prompts_triggered": system_prompts_triggered,
        "stance_drift_final": stance_drift_final,
    }

    conv.stats = stats
    conv.save(update_fields=["stats"])

    return stats


def generate_summary(conversation_id: int) -> str:
    """
    Generate a conversation summary using Claude API and persist to Conversation.summary.
    Returns empty string when ANTHROPIC_API_KEY is not set or on any error.
    """
    from chat.models import Conversation, Message

    messages = list(
        Message.objects.filter(conversation_id=conversation_id)
        .select_related("sender")
        .order_by("timestamp")
    )

    if not messages:
        return ""

    transcript = "\n".join(
        f"User {msg.sender_id}: {msg.content}" for msg in messages
    )

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    summary = ""

    if not api_key:
        logger.warning(
            "ANTHROPIC_API_KEY not set — skipping summary generation conv=%s",
            conversation_id,
        )
    else:
        try:
            import anthropic

            client = anthropic.Anthropic(api_key=api_key)
            response = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=512,
                messages=[
                    {
                        "role": "user",
                        "content": (
                            "以下是一段關於公共議題的對話紀錄。"
                            "請用繁體中文，在150字以內，"
                            "簡要摘要雙方的主要論點與立場變化：\n\n"
                            f"{transcript}"
                        ),
                    }
                ],
            )
            summary = response.content[0].text
        except Exception as exc:
            logger.error(
                "Summary generation failed conv=%s: %s", conversation_id, exc
            )

    conv = Conversation.objects.get(id=conversation_id)
    conv.summary = summary
    conv.save(update_fields=["summary"])

    return summary


aend_session = sync_to_async(end_session, thread_sensitive=False)
agenerate_summary = sync_to_async(generate_summary, thread_sensitive=False)
