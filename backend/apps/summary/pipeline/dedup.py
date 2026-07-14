"""
ORM 版去重檢查（Step 6）。

移植自獨立跑的 觀點知識庫/去重檢查.py prototype：原本用
`psycopg.connect()` 手寫 `SELECT ... ORDER BY embedding <=> %s::vector LIMIT 5`，
現在改用 pgvector 的 Django ORM lookup（CosineDistance），查詢邏輯不變
（同一 topic_id + dimension 下最近 5 筆，相似度門檻 0.92）。
"""

from django.db.models import F
from pgvector.django import CosineDistance

from apps.summary.models import ViewpointNode

DUPLICATE_SIMILARITY_THRESHOLD = 0.92
NEAREST_NEIGHBOURS = 5


def is_duplicate(
    embedding: list[float],
    topic_id: int,
    dimension: str,
    threshold: float = DUPLICATE_SIMILARITY_THRESHOLD,
) -> tuple[bool, int | None]:
    """回傳 (is_dup, dup_id)：是否與同 topic/dimension 下既有觀點重複。"""
    candidates = (
        ViewpointNode.objects
        .filter(topic_id=topic_id, dimension=dimension)
        .annotate(distance=CosineDistance("embedding", embedding))
        .order_by("distance")[:NEAREST_NEIGHBOURS]
    )
    for node in candidates:
        similarity = 1 - node.distance
        if similarity > threshold:
            return True, node.id
    return False, None


def increment_citation(node_id: int) -> None:
    ViewpointNode.objects.filter(id=node_id).update(
        citation_count=F("citation_count") + 1
    )
