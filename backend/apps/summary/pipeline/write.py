"""M6 觀點知識庫 — 寫入 DialogueSummary / ViewpointNode。

原本的 觀點寫入.py 是獨立 psycopg 連線 + 手寫 SQL；這裡改走 Django ORM，
讓寫入跟專案其餘部分共用同一個資料庫連線/交易生命週期。
"""

from api.dialogue_topics import TOPIC_CONFIGS
from apps.matching.services.semantic_tree import get_topic_anchors
from apps.summary.models import DialogueSummary, ViewpointNode
from apps.summary.pipeline.dedup import increment_citation, is_duplicate
from chat.services.embedding import get_embedding


def _validate_topic_id(topic_id: int) -> None:
    """觀點知識庫只收錄 TOPIC_CONFIGS 已定義的議題，其餘一律拒絕寫入。"""
    if topic_id not in TOPIC_CONFIGS:
        raise ValueError(
            f"topic_id={topic_id} 不是 api.dialogue_topics.TOPIC_CONFIGS 裡定義的議題"
            f"（目前只有 {sorted(TOPIC_CONFIGS)}），觀點知識庫拒絕寫入未定義議題的資料。"
        )


def _validate_dimension(topic_id: int, dimension: str) -> None:
    """dimension 只收錄該議題底下 M5 CCND 已定義的 anchor id，其餘一律拒絕寫入。"""
    valid_ids = {anchor["id"] for anchor in get_topic_anchors(topic_id)}
    if dimension not in valid_ids:
        raise ValueError(
            f"dimension={dimension!r} 不是 topic_id={topic_id} 底下定義的 anchor"
            f"（合法值：{sorted(valid_ids)}），觀點知識庫拒絕寫入未定義分類的資料。"
        )


def write_dialogue_summary(data: dict) -> int:
    """
    data 包含：dialogue_id, topic_id, summary_text,
               side_a_stance, side_b_stance, quality_score, stance_shift_magnitude

    topic_id 必須是來源對話室/session 本身的議題（H-H 用 DialogueMatch.topic_id，
    H-AI 用 AIConversation.topic_id），呼叫端不可另外指定其他議題。
    回傳新建的 id。
    """
    _validate_topic_id(data["topic_id"])

    summary = DialogueSummary.objects.create(
        dialogue_id=data["dialogue_id"],
        topic_id=data["topic_id"],
        summary_text=data.get("summary_text", ""),
        side_a_stance=data.get("side_a_stance", ""),
        side_b_stance=data.get("side_b_stance", ""),
        quality_score=data.get("quality_score"),
        stance_shift_magnitude=data.get("stance_shift_magnitude"),
    )
    return summary.id


def write_viewpoint(data: dict) -> bool:
    """
    data 包含：summary_id, dimension, stance_direction, speaker_side,
               user_input_text, ai_response_text, viewpoint_summary,
               source_message_ids, composite_score, score_detail

    topic_id 不由呼叫端指定，一律繼承自 summary_id 對應的 DialogueSummary.topic_id，
    確保同一場對話產出的觀點永遠跟原始聊天室屬於同一個議題。
    回傳是否實際寫入新節點；偵測為重複時只累加既有節點的 citation_count 並回傳 False。
    """
    summary = DialogueSummary.objects.get(id=data["summary_id"])
    topic_id = summary.topic_id
    _validate_topic_id(topic_id)
    _validate_dimension(topic_id, data["dimension"])

    embedding = get_embedding(data["user_input_text"])

    is_dup, dup_id = is_duplicate(embedding, topic_id, data["dimension"])
    if is_dup:
        increment_citation(dup_id)
        return False

    ViewpointNode.objects.create(
        summary=summary,
        topic_id=topic_id,
        dimension=data["dimension"],
        stance_direction=data.get("stance_direction", ""),
        speaker_side=data.get("speaker_side", ""),
        user_input_text=data["user_input_text"],
        ai_response_text=data.get("ai_response_text", ""),
        viewpoint_summary=data.get("viewpoint_summary", ""),
        source_message_ids=data.get("source_message_ids", []),
        embedding=embedding,
        composite_score=data.get("composite_score"),
        score_detail=data.get("score_detail", {}),
    )
    return True
