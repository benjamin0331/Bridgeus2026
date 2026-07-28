"""
H-H 離題偵測。

機制：取該用戶最近 window 則訊息的 embedding 平均向量，
      與議題錨點 embedding 計算 cosine_similarity。
      低於閾值 → 判定離題，透過 WebSocket 推送 system_prompt 給發言者。

「當前視窗」：sender 最近 window 則有 embedding 的訊息；不足時用實際數量。
無訊息（含 embedding）時 fail-open，視為在議題內。

已知限制：
  - 閾值 0.25：實測「日本福島事件讓很多人改變想法」單則得分 0.2938，
    window 平均後通常更穩定。待真實對話數據進一步調整。
  - 短發言（< 5 字）embedding 語意不穩定，靠視窗平均稀釋。
"""

import numpy as np
from asgiref.sync import sync_to_async

from chat.services.embedding import cosine_similarity, get_embedding

TOPIC_RELEVANCE_THRESHOLD: float = 0.35


def get_topic_anchor_embedding(topic_description: str) -> list[float]:
    """Embed a topic description string; call once at conversation creation."""
    return get_embedding(topic_description)


def check_topic_relevance(
    conversation_id: int,
    user_id: int,
    topic_anchor_embedding: list[float],
    window: int = 3,
) -> dict:
    """
    Compare the user's recent messages against the topic anchor embedding.

    Args:
        conversation_id: DB id of the conversation.
        user_id:         DB id of the sender whose messages to check.
        topic_anchor_embedding: Pre-computed anchor vector.
        window:          How many recent messages to average (default 5).

    Returns:
        relevance_score  float — cosine_similarity(mean_emb, anchor_embedding)
        is_off_topic     bool  — relevance_score < TOPIC_RELEVANCE_THRESHOLD
    """
    from chat.models import Message

    msgs = list(
        Message.objects.filter(
            conversation_id=conversation_id,
            sender_id=user_id,
            embedding__isnull=False,
        ).order_by("-timestamp")[:window]
    )

    if not msgs:
        return {"relevance_score": 1.0, "is_off_topic": False}

    mean_emb = np.mean(
        [np.array(m.embedding, dtype=np.float32) for m in msgs], axis=0
    )
    score = cosine_similarity(mean_emb, topic_anchor_embedding)
    return {
        "relevance_score": round(float(score), 4),
        "is_off_topic": score < TOPIC_RELEVANCE_THRESHOLD,
    }


aget_topic_anchor_embedding = sync_to_async(get_topic_anchor_embedding, thread_sensitive=False)
acheck_topic_relevance = sync_to_async(check_topic_relevance, thread_sensitive=False)
