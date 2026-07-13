import numpy as np

def cosine_similarity(a, b):
    return np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b))

def is_duplicate(conn, new_embedding, topic_id, dimension, threshold=0.92):
    """
    回傳 (is_dup: bool, dup_id: int | None)
    """
    cur = conn.cursor()
    cur.execute("""
        SELECT id, embedding
        FROM summary_viewpointnode
        WHERE topic_id = %s AND dimension = %s
        ORDER BY embedding <=> %s::vector
        LIMIT 5
    """, (topic_id, dimension, new_embedding))
    
    rows = cur.fetchall()
    for row_id, existing_emb in rows:
        if isinstance(existing_emb, str):
            existing_emb = [float(x) for x in existing_emb.strip('[]').split(',')]
        sim = cosine_similarity(new_embedding, existing_emb)
        if sim > threshold:
            return True, row_id
    return False, None

def increment_citation(conn, node_id):
    cur = conn.cursor()
    cur.execute(
        "UPDATE summary_viewpointnode SET citation_count = citation_count + 1 WHERE id = %s",
        (node_id,)
    )
    conn.commit()