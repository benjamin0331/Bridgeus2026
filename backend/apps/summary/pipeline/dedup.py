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
    """回傳 (is_dup, 既有重複節點的 id | None)。

    候選集排除 REJECTED：被研究者判定「品質不好、退回」的節點不該再拿來當
    去重比對基準——否則之後任何跟它相似的新觀點（可能是同一個論點但表達得
    更好）都會被判為重複，只會幫一個已經被退回、永遠不會再被人看到的節點
    累加 citation_count，白白錯失收錄機會。PENDING 節點仍然要留在候選集：
    還沒審過不代表品質不好，一樣該當作去重基準。
    """
    candidates = (
        ViewpointNode.objects
        .filter(topic_id=topic_id, dimension=dimension)
        .exclude(review_status=ViewpointNode.ReviewStatus.REJECTED)
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
