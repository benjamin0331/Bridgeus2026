import json

from 去重檢查 import is_duplicate, increment_citation
from embedding import generate_embedding


def write_dialogue_summary(conn, data: dict) -> int:
    """
    data 包含：dialogue_id, topic_id, summary_text,
               side_a_stance, side_b_stance, quality_score, stance_shift_magnitude
    回傳新建的 id。
    """
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO summary_dialoguesummary
            (dialogue_id, topic_id, summary_text, side_a_stance, side_b_stance,
             quality_score, stance_shift_magnitude, created_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
        RETURNING id
    """, (
        data["dialogue_id"], data["topic_id"], data.get("summary_text"),
        data.get("side_a_stance"), data.get("side_b_stance"),
        data.get("quality_score"), data.get("stance_shift_magnitude"),
    ))
    new_id = cur.fetchone()[0]
    conn.commit()
    return new_id


def write_viewpoint(conn, data: dict):
    """
    data 包含：summary_id, topic_id, dimension, stance_direction,
               user_input_text, ai_response_text, viewpoint_summary,
               source_message_ids, composite_score, score_detail
    """
    embedding = generate_embedding(data["user_input_text"])

    # 去重檢查
    is_dup, dup_id = is_duplicate(conn, embedding, data["topic_id"], data["dimension"])
    if is_dup:
        increment_citation(conn, dup_id)
        print(f"    [WARN] 重複觀點，citation_count +1（id={dup_id}）")
        return False

    cur = conn.cursor()
    cur.execute("""
        INSERT INTO summary_viewpointnode
            (summary_id, topic_id, dimension, stance_direction,
             user_input_text, ai_response_text, viewpoint_summary,
             source_message_ids, embedding, composite_score, score_detail,
             citation_count, created_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::vector, %s, %s, 0, NOW())
    """, (
        data["summary_id"], data["topic_id"], data["dimension"],
        data["stance_direction"], data["user_input_text"], data["ai_response_text"],
        data["viewpoint_summary"], data["source_message_ids"],
        embedding, data["composite_score"], json.dumps(data["score_detail"])
    ))
    conn.commit()
    return True
