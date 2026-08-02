"""Analysis helpers for current matching-room data models."""

from __future__ import annotations

import math
import random
import re

import numpy as np
from asgiref.sync import sync_to_async
from django.utils import timezone

from apps.matching.services.input_gate import is_substantive_message
from chat.services.embedding import cosine_distance

STALEMATE_THRESHOLD = 0.05
DIRECTION_THRESHOLD = 0.02

# 過濾短回應需要在 Python 端讀 content，所以固定窗口的查詢要先多撈幾倍
# 才有機會湊滿 N 則實質發言。純防禦性倍率，不是實驗參數。
_SUBSTANTIVE_OVERFETCH = 5

_STALEMATE_PROMPTS = [
    "對方提到了「{keyword}」，你怎麼看？",
    "你似乎還沒有回應關於「{keyword}」的論點。",
    "關於「{keyword}」，你有什麼不同的想法嗎？",
    "試著回應對方關於「{keyword}」的觀點。",
]


# 短回應（「好」「同意」「不確定」）合法通過 input gate 並進入 LLM，但對語義
# 分析只有稀釋作用：它們的 embedding 不帶議題資訊，混進平均向量會把離題分數
# 與論述移動度往中間拉。以下三處分析一律只採計實質發言。
# 三處共用 input_gate.is_substantive_message()，避免各自實作而漂移。
def _substantive(messages, *, attr: str = "content"):
    """只留下可供語義分析的訊息，保持原本的順序。"""
    return [
        message
        for message in messages
        if is_substantive_message(getattr(message, attr, "") or "")
    ]


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


def calculate_match_stance_drift(*, match_id: int, user_id: int) -> dict:
    from api.models import DialogueMatch, MatchMessage, MatchStanceDrift, UserStanceProfile

    match = DialogueMatch.objects.get(id=match_id)
    profile = (
        UserStanceProfile.objects.filter(user_id=user_id, topic_id=match.topic_id)
        .order_by("-updated_at", "-id")
        .first()
    )
    if not profile or profile.q9_embedding is None:
        return {"drift_value": 0.0, "direction": "stable"}

    last_record = (
        MatchStanceDrift.objects.filter(match_id=match_id, user_id=user_id)
        .order_by("-measured_at")
        .first()
    )
    messages = MatchMessage.objects.filter(
        match_id=match_id,
        sender_id=user_id,
        embedding__isnull=False,
    )
    # 論述移動度衡量的是「離初始 Q9 立場多遠」。短回應沒有論述內容，
    # 它造成的位移是雜訊，會污染這個過程指標。
    mean_embedding = _mean_embedding(
        message.embedding
        for message in _substantive(messages.order_by("created_at"))
    )
    if mean_embedding is None:
        return {"drift_value": 0.0, "direction": "stable"}

    drift_value = round(float(cosine_distance(mean_embedding, profile.q9_embedding)), 4)
    if not math.isfinite(drift_value):
        return {"drift_value": 0.0, "direction": "stable"}

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

    MatchStanceDrift.objects.create(
        match_id=match_id,
        user_id=user_id,
        drift_value=drift_value,
    )
    return {"drift_value": drift_value, "direction": direction}


def calculate_ai_session_stance_drift(
    *,
    session_record: dict,
    session_id: str,
    user_id: int,
) -> dict | None:
    from api.models import AIConversation

    survey_context = session_record.get("survey_context") or {}
    baseline_embedding = survey_context.get("q9_embedding")
    if baseline_embedding is None:
        return None

    turns = AIConversation.objects.filter(
        user_id=user_id,
        session_id=session_id,
        user_prompt__gt="",
        embedding__isnull=False,
    ).order_by("created_at", "id")

    # 同 H-H：短回應排除在論述移動度之外（見 _substantive）。
    mean_embedding = _mean_embedding(
        turn.embedding for turn in _substantive(turns, attr="user_prompt")
    )
    if mean_embedding is None:
        return None

    drift_value = round(float(cosine_distance(mean_embedding, baseline_embedding)), 4)
    if not math.isfinite(drift_value):
        return None

    session_state = session_record.setdefault("session", {})
    previous_drift = session_state.get("stance_drift") or {}
    previous_value = previous_drift.get("drift_value")
    if previous_value is None:
        direction = "stable"
    else:
        diff = drift_value - float(previous_value)
        if diff > DIRECTION_THRESHOLD:
            direction = "approaching"
        elif diff < -DIRECTION_THRESHOLD:
            direction = "diverging"
        else:
            direction = "stable"

    payload = {
        "drift_value": drift_value,
        "direction": direction,
        "measured_at": timezone.now().isoformat(),
    }
    session_state["stance_drift"] = payload
    return payload


def get_message_drift_value(*, match_id: int, user_id: int, as_of) -> float:
    """回傳某個時間點當下，該使用者最新一筆已持久化的立場偏移量（drift_value，
    前端標籤「論述移動」）。

    給 M6 觀點知識庫 pipeline（apps/summary/pipeline/quality_filter.py 的
    ccnd_semantic_dist）用來取得逐則的論述移動量：讀取 api/consumers.py 每則
    「發言者本人」新發言後透過 calculate_match_stance_drift() 寫入的
    MatchStanceDrift 記錄，只做唯讀查詢，不重算、不新增 drift 記錄——重算是
    即時對話室自己的職責，這裡只是替歷史訊息回填当時最近的已知值。
    """
    from api.models import MatchStanceDrift

    record = (
        MatchStanceDrift.objects.filter(
            match_id=match_id, user_id=user_id, measured_at__lte=as_of
        )
        .order_by("-measured_at")
        .first()
    )
    return record.drift_value if record else 0.0


def detect_match_stalemate(*, match_id: int, window_size: int = 5) -> dict:
    from api.models import DialogueMatch, MatchMessage

    match = DialogueMatch.objects.get(id=match_id)

    def _recent_substantive(sender_id: int):
        # 短回應不計入僵局判定的窗口：兩人互丟「嗯」「好」時語義距離當然
        # 穩定，那不是論點僵持，只是沒有論點。
        candidates = MatchMessage.objects.filter(
            match_id=match_id,
            sender_id=sender_id,
            embedding__isnull=False,
        ).order_by("-created_at")[: window_size * _SUBSTANTIVE_OVERFETCH]
        return _substantive(candidates)[:window_size]

    messages_a = _recent_substantive(match.user_a_id)
    messages_b = _recent_substantive(match.user_b_id)
    pair_count = min(len(messages_a), len(messages_b))
    if pair_count < 2:
        return {"is_stalemate": False, "distance_trend": [], "std_dev": 0.0}

    distances = [
        round(cosine_distance(messages_a[index].embedding, messages_b[index].embedding), 4)
        for index in range(pair_count)
    ]
    std_dev = round(float(np.std(distances)), 4)
    return {
        "is_stalemate": std_dev < STALEMATE_THRESHOLD and pair_count >= window_size,
        "distance_trend": list(reversed(distances)),
        "std_dev": std_dev,
    }


def extract_match_opponent_keywords(*, match_id: int, user_id: int, top_n: int = 2) -> list[str]:
    from api.models import DialogueMatch, MatchMessage

    match = DialogueMatch.objects.get(id=match_id)
    opponent_id = match.user_b_id if match.user_a_id == user_id else match.user_a_id
    messages = list(
        MatchMessage.objects.filter(match_id=match_id, sender_id=opponent_id)
        .order_by("-created_at")[:5]
    )
    text = " ".join(message.content for message in messages)
    candidates = [token for token in re.split(r"[\s，。！？、,.!?；;：:（）()]+", text) if len(token) >= 2]
    seen = []
    for token in candidates:
        if token not in seen:
            seen.append(token)
    return seen[:top_n]


def build_stalemate_prompt(keywords: list[str]) -> str:
    if not keywords:
        return "雙方的討論似乎陷入僵局，試著換個角度思考對方的論點。"
    return random.choice(_STALEMATE_PROMPTS).format(keyword=keywords[0])


acalculate_match_stance_drift = sync_to_async(calculate_match_stance_drift, thread_sensitive=False)
acalculate_ai_session_stance_drift = sync_to_async(calculate_ai_session_stance_drift, thread_sensitive=False)
aget_message_drift_value = sync_to_async(get_message_drift_value, thread_sensitive=False)
adetect_match_stalemate = sync_to_async(detect_match_stalemate, thread_sensitive=False)
aextract_match_opponent_keywords = sync_to_async(extract_match_opponent_keywords, thread_sensitive=False)
