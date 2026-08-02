"""Topic-specific semantic relevance policies for human matching rooms.

All product-facing knobs live in ``TOPIC_CONFIGS[topic_id]["off_topic_detection"]``.
This module owns validation, anchor embedding, message-window selection, and the
final threshold comparison so consumers do not need topic-specific conditions.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from functools import lru_cache

import numpy as np
from asgiref.sync import sync_to_async

from api.dialogue_topics import TOPIC_CONFIGS
from apps.matching.services.input_gate import is_substantive_message
from chat.services.embedding import cosine_similarity, get_embedding

DEFAULT_THRESHOLD = 0.35
DEFAULT_WINDOW_SIZE = 3
DEFAULT_MIN_MESSAGES = 2

# 過濾短回應需要在 Python 端讀 content，所以固定窗口的查詢要先多撈幾倍
# 才有機會湊滿 window_size 則實質發言。純防禦性倍率，不是實驗參數。
_SUBSTANTIVE_OVERFETCH = 5


@dataclass(frozen=True)
class TopicRelevancePolicy:
    anchor_text: str
    threshold: float = DEFAULT_THRESHOLD
    window_size: int = DEFAULT_WINDOW_SIZE
    min_messages: int = DEFAULT_MIN_MESSAGES


def get_topic_relevance_policy(
    topic_id: int,
    *,
    fallback_anchor_text: str = "",
) -> TopicRelevancePolicy:
    topic_config = TOPIC_CONFIGS.get(topic_id, {})
    config = topic_config.get("off_topic_detection", {})
    anchor_text = (
        str(config.get("anchor_text") or "").strip()
        or str(topic_config.get("topic_description") or "").strip()
        or fallback_anchor_text.strip()
    )
    threshold = float(config.get("threshold", DEFAULT_THRESHOLD))
    window_size = int(config.get("window_size", DEFAULT_WINDOW_SIZE))
    min_messages = int(config.get("min_messages", DEFAULT_MIN_MESSAGES))

    if not -1.0 <= threshold <= 1.0:
        raise ValueError(f"Invalid off-topic threshold for topic {topic_id}: {threshold}")
    if window_size < 1:
        raise ValueError(f"Invalid off-topic window size for topic {topic_id}: {window_size}")
    if not 1 <= min_messages <= window_size:
        raise ValueError(
            f"Invalid minimum off-topic messages for topic {topic_id}: {min_messages}"
        )

    return TopicRelevancePolicy(
        anchor_text=anchor_text,
        threshold=threshold,
        window_size=window_size,
        min_messages=min_messages,
    )


@lru_cache(maxsize=32)
def get_topic_anchor_embedding(anchor_text: str) -> list[float]:
    """Embed a configured anchor once per backend process."""
    if not anchor_text.strip():
        raise ValueError("Topic relevance anchor text cannot be empty.")
    return get_embedding(anchor_text)


def _mean_embedding(embeddings) -> np.ndarray | None:
    vectors = []
    for embedding in embeddings:
        if embedding is None:
            continue
        try:
            vector = np.array(embedding, dtype=np.float32)
        except (TypeError, ValueError):
            continue
        if vector.size and np.all(np.isfinite(vector)):
            vectors.append(vector)
    if not vectors:
        return None
    return np.mean(vectors, axis=0)


def check_match_topic_relevance(
    *,
    match_id: int,
    user_id: int,
    topic_id: int,
    topic_anchor_embedding,
) -> dict:
    from api.models import MatchMessage

    policy = get_topic_relevance_policy(topic_id)
    # 短回應（「好」「同意」）的 embedding 不帶議題資訊，混進滾動窗口只會把
    # 離題分數往中間拉。先多撈再過濾，才不會因為連說三次「好」就填滿窗口。
    candidates = MatchMessage.objects.filter(
        match_id=match_id,
        sender_id=user_id,
        embedding__isnull=False,
    ).order_by("-created_at", "-id")[: policy.window_size * _SUBSTANTIVE_OVERFETCH]
    messages = [
        message
        for message in candidates
        if is_substantive_message(message.content or "")
    ][: policy.window_size]
    if len(messages) < policy.min_messages:
        return {"relevance_score": 1.0, "is_off_topic": False}

    mean_embedding = _mean_embedding(message.embedding for message in messages)
    if mean_embedding is None or topic_anchor_embedding is None:
        return {"relevance_score": 1.0, "is_off_topic": False}

    score = float(cosine_similarity(mean_embedding, topic_anchor_embedding))
    if not math.isfinite(score):
        return {"relevance_score": 1.0, "is_off_topic": False}
    return {
        "relevance_score": round(score, 4),
        "is_off_topic": score < policy.threshold,
    }


aget_topic_anchor_embedding = sync_to_async(
    get_topic_anchor_embedding,
    thread_sensitive=False,
)
acheck_match_topic_relevance = sync_to_async(
    check_match_topic_relevance,
    thread_sensitive=False,
)
