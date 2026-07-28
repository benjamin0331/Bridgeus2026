"""
H-H 立場漂移追蹤。

計算方式：
  取「當前區間」內該用戶所有已算 embedding 的發言，計算平均向量，
  再與用戶初始立場向量（問卷開放式回答 embedding）比較 cosine_distance → drift_value。
  drift_value 越大 = 立場偏離初始越遠 = 趨向對方立場。

「當前區間」定義：
  上一筆 StanceDrift.measured_at 之後的訊息；若無歷史記錄則取全部。

Direction 判定（diff = current_drift - previous_drift）：
  diff >  DIRECTION_THRESHOLD  → "approaching"（偏移加大，立場趨近對方）
  diff < -DIRECTION_THRESHOLD  → "diverging" （偏移縮小，立場退回初始）
  else                         → "stable"
  第一次測量無比較基準 → "stable"

提前返回條件（不寫 DB、不拋例外）：
  - 初始立場 embedding 尚未設定 (None)
  - 當前區間無已計算 embedding 的訊息
"""

import numpy as np
from asgiref.sync import sync_to_async

from chat.services.embedding import cosine_distance

DIRECTION_THRESHOLD: float = 0.02


def calculate_drift(conversation_id: int, user_id: int) -> dict:
    """
    Compute drift_value for one user in one conversation and persist to DB.

    Returns:
        drift_value  float  — 0.0 if skipped
        direction    str    — "approaching" | "diverging" | "stable"
    """
    from chat.models import Conversation, Message, StanceDrift

    conv = Conversation.objects.get(id=conversation_id)

    initial_emb = (
        conv.user_a_initial_embedding
        if conv.user_a_id == user_id
        else conv.user_b_initial_embedding
    )
    if initial_emb is None:
        return {"drift_value": 0.0, "direction": "stable"}

    last_record = (
        StanceDrift.objects.filter(
            conversation_id=conversation_id,
            user_id=user_id,
        )
        .order_by("-measured_at")
        .first()
    )

    qs = Message.objects.filter(
        conversation_id=conversation_id,
        sender_id=user_id,
        embedding__isnull=False,
    )
    if last_record is not None:
        qs = qs.filter(timestamp__gt=last_record.measured_at)

    embeddings = [np.array(m.embedding, dtype=np.float32) for m in qs.order_by("timestamp")]

    if not embeddings:
        return {"drift_value": 0.0, "direction": "stable"}

    mean_emb = np.mean(embeddings, axis=0)
    drift_value = round(float(cosine_distance(mean_emb, initial_emb)), 4)

    if last_record is not None:
        diff = drift_value - last_record.drift_value
        if diff > DIRECTION_THRESHOLD:
            direction = "approaching"
        elif diff < -DIRECTION_THRESHOLD:
            direction = "diverging"
        else:
            direction = "stable"
    else:
        direction = "stable"

    StanceDrift.objects.create(
        conversation_id=conversation_id,
        user_id=user_id,
        drift_value=drift_value,
    )

    return {"drift_value": drift_value, "direction": direction}


acalculate_drift = sync_to_async(calculate_drift, thread_sensitive=False)
