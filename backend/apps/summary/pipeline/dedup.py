"""M6 觀點知識庫 — 觀點去重檢查。

沿用 chat.services.embedding 既有的 cosine_similarity（不重寫一份 numpy 版本），
候選集用 pgvector 的 cosine-distance 排序在資料庫端先篩出最近的幾筆，
再用同一套相似度函式在 Python 端做精確判斷，門檻與其他語意比對邏輯一致。
"""

from pgvector.django import CosineDistance
from django.db.models import F

from apps.summary.models import ViewpointNode
from chat.services.embedding import cosine_similarity

DUPLICATE_SIMILARITY_THRESHOLD = 0.92
CANDIDATE_POOL_SIZE = 5


def is_duplicate(
    new_embedding: list[float],
    topic_id: int,
    dimension: str,
    threshold: float = DUPLICATE_SIMILARITY_THRESHOLD,
) -> tuple[bool, int | None]:
    """回傳 (is_dup, 既有重複節點的 id | None)。"""
    candidates = (
        ViewpointNode.objects
        .filter(topic_id=topic_id, dimension=dimension)
        .order_by(CosineDistance("embedding", new_embedding))[:CANDIDATE_POOL_SIZE]
    )
    for node in candidates:
        if cosine_similarity(new_embedding, node.embedding) > threshold:
            return True, node.id
    return False, None


def increment_citation(node_id: int) -> None:
    ViewpointNode.objects.filter(id=node_id).update(
        citation_count=F("citation_count") + 1
    )
