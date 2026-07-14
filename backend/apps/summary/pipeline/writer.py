"""
ORM 版觀點寫入（Step 5 → 6 → 7）。

移植自獨立跑的 觀點知識庫/觀點寫入.py prototype：原本用 `psycopg.connect()` +
手寫 `INSERT`，現在改用 Django ORM（DialogueSummary.objects.create /
ViewpointNode.objects.create），embedding 也改用專案共用的
chat.services.embedding.get_embedding，不再自帶一份獨立的 SentenceTransformer
singleton。
"""

from chat.services.embedding import get_embedding

from apps.summary.models import DialogueSummary, ViewpointNode

from .dedup import increment_citation, is_duplicate


def write_dialogue_summary(data: dict) -> DialogueSummary:
    """
    data 包含：dialogue_id, topic_id, summary_text,
               side_a_stance, side_b_stance, quality_score, stance_shift_magnitude
    """
    return DialogueSummary.objects.create(
        dialogue_id=data["dialogue_id"],
        topic_id=data["topic_id"],
        summary_text=data.get("summary_text", ""),
        side_a_stance=data.get("side_a_stance", ""),
        side_b_stance=data.get("side_b_stance", ""),
        quality_score=data.get("quality_score"),
        stance_shift_magnitude=data.get("stance_shift_magnitude"),
    )


def write_viewpoint(data: dict) -> ViewpointNode | None:
    """
    data 包含：summary_id, topic_id, dimension, stance_direction,
               user_input_text, ai_response_text, viewpoint_summary,
               source_message_ids, composite_score, score_detail

    若偵測為重複觀點，回傳 None（既有節點的 citation_count 會 +1）；
    否則回傳新建的 ViewpointNode。
    """
    embedding = get_embedding(data["user_input_text"])

    is_dup, dup_id = is_duplicate(embedding, data["topic_id"], data["dimension"])
    if is_dup:
        increment_citation(dup_id)
        return None

    return ViewpointNode.objects.create(
        summary_id=data["summary_id"],
        topic_id=data["topic_id"],
        dimension=data["dimension"],
        stance_direction=data.get("stance_direction", ""),
        user_input_text=data["user_input_text"],
        ai_response_text=data.get("ai_response_text", ""),
        viewpoint_summary=data.get("viewpoint_summary", ""),
        source_message_ids=data.get("source_message_ids", []),
        embedding=embedding,
        composite_score=data.get("composite_score"),
        score_detail=data.get("score_detail", {}),
    )
