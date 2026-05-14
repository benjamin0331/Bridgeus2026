"""
H-H 僵局偵測 + 對方關鍵詞抽取。

僵局判定：
  最近 window_size 輪中，雙方訊息 embedding 的成對 cosine_distance 標準差 < STALEMATE_THRESHOLD。
  雙方立場距離不再變化 → 陷入僵局。

關鍵詞抽取：
  KeyBERT（底層複用 embedding.py 的 MiniLM singleton）+ jieba 中文分詞。
  從對方最近 n_messages 則發言中抽取候選詞，
  選出與「當前用戶最近發言」語義距離最遠的 top_n 個詞
  （= 用戶最少回應的論點）。

STALEMATE_THRESHOLD = 0.05：
  標準差閾值，先給粗值；待真實對話數據調整。
  cosine_distance 值域 [0, 2]，std_dev < 0.05 表示對話陷入僵局。
"""

import random
import threading

import numpy as np
from asgiref.sync import sync_to_async

from chat.services.embedding import _get_model, cosine_distance, get_embedding

STALEMATE_THRESHOLD: float = 0.05

_STALEMATE_PROMPTS = [
    "對方提到了「{keyword}」，你怎麼看？",
    "你似乎還沒有回應關於「{keyword}」的論點。",
    "關於「{keyword}」，你有什麼不同的想法嗎？",
    "試著回應對方關於「{keyword}」的觀點。",
]

# KeyBERT singleton — reuse the SentenceTransformer loaded by embedding.py
_kw_model = None
_kw_lock = threading.Lock()


def _get_kw_model():
    global _kw_model
    if _kw_model is None:
        with _kw_lock:
            if _kw_model is None:
                from keybert import KeyBERT
                _kw_model = KeyBERT(model=_get_model())
    return _kw_model


# ---------- public API ----------

def detect_stalemate(conversation_id: int, window_size: int = 5) -> dict:
    """
    Fetch the last window_size messages with embeddings from each participant,
    pair them by recency, compute pairwise cosine_distance per round, then flag
    a stalemate if the standard deviation of those distances is below
    STALEMATE_THRESHOLD AND we have a full window of data.

    Returns:
        is_stalemate    bool
        distance_trend  list[float]  — pairwise distances, oldest first
        std_dev         float
    """
    from chat.models import Conversation, Message

    conv = Conversation.objects.get(id=conversation_id)

    msgs_a = list(
        Message.objects.filter(
            conversation_id=conversation_id,
            sender_id=conv.user_a_id,
            embedding__isnull=False,
        ).order_by("-timestamp")[:window_size]
    )
    msgs_b = list(
        Message.objects.filter(
            conversation_id=conversation_id,
            sender_id=conv.user_b_id,
            embedding__isnull=False,
        ).order_by("-timestamp")[:window_size]
    )

    n_pairs = min(len(msgs_a), len(msgs_b))

    if n_pairs < 2:
        return {"is_stalemate": False, "distance_trend": [], "std_dev": 0.0}

    distances = [
        round(cosine_distance(msgs_a[i].embedding, msgs_b[i].embedding), 4)
        for i in range(n_pairs)
    ]

    std_dev = round(float(np.std(distances)), 4)
    is_stalemate = std_dev < STALEMATE_THRESHOLD and n_pairs >= window_size

    return {
        "is_stalemate": is_stalemate,
        "distance_trend": list(reversed(distances)),  # chronological order
        "std_dev": std_dev,
    }


def extract_opponent_keywords(
    conversation_id: int,
    user_id: int,
    n_messages: int = 5,
    top_n: int = 2,
) -> list[str]:
    """
    Extract top_n keywords from the opponent's recent messages, ranked by
    how little the current user has engaged with them (highest cosine distance
    to user's most recent message).

    Steps:
      1. Concatenate opponent's last n_messages.
      2. Segment with jieba → join with spaces (so KeyBERT can tokenise Chinese).
      3. KeyBERT extracts top_n * 4 candidate keyphrases with MMR diversity.
      4. Embed each candidate; rank by cosine_distance to user's latest message.
      5. Return top_n highest-distance candidates.
    """
    import jieba

    from chat.models import Conversation, Message

    conv = Conversation.objects.get(id=conversation_id)
    opponent_id = conv.user_b_id if conv.user_a_id == user_id else conv.user_a_id

    opp_msgs = list(
        Message.objects.filter(
            conversation_id=conversation_id,
            sender_id=opponent_id,
        ).order_by("-timestamp")[:n_messages]
    )
    if not opp_msgs:
        return []

    opp_text = " ".join(m.content for m in reversed(opp_msgs))

    # Jieba-segment → join with spaces so KeyBERT can tokenize word-by-word
    segmented = " ".join(jieba.cut(opp_text))

    try:
        raw_candidates = _get_kw_model().extract_keywords(
            segmented,
            keyphrase_ngram_range=(1, 2),
            top_n=top_n * 4,
            use_mmr=True,
            diversity=0.6,
        )
    except Exception:
        return []

    if not raw_candidates:
        return []

    # Strip spaces that jieba may have inserted into multi-word phrases
    keyword_strs = ["".join(kw.split()) for kw, _ in raw_candidates if kw.strip()]

    # Filter out single-character keywords and duplicates
    seen: set[str] = set()
    filtered = []
    for kw in keyword_strs:
        if len(kw) >= 2 and kw not in seen:
            seen.add(kw)
            filtered.append(kw)

    if not filtered:
        return []

    # Rank by distance to current user's last message (most unengaged argument first)
    user_msg = (
        Message.objects.filter(
            conversation_id=conversation_id,
            sender_id=user_id,
        )
        .order_by("-timestamp")
        .first()
    )

    if user_msg is None:
        return filtered[:top_n]

    user_emb = get_embedding(user_msg.content)

    def _dist(kw: str) -> float:
        try:
            return cosine_distance(get_embedding(kw), user_emb)
        except Exception:
            return 0.0

    ranked = sorted(filtered, key=_dist, reverse=True)
    return ranked[:top_n]


def build_stalemate_prompt(keywords: list[str]) -> str:
    """Fill a random prompt template with the first keyword.
    Falls back to a generic message when keywords is empty.
    """
    if not keywords:
        return "雙方的討論似乎陷入僵局，試著換個角度思考對方的論點。"
    return random.choice(_STALEMATE_PROMPTS).format(keyword=keywords[0])


# ---------- async wrappers ----------

adetect_stalemate = sync_to_async(detect_stalemate, thread_sensitive=False)
aextract_opponent_keywords = sync_to_async(extract_opponent_keywords, thread_sensitive=False)
