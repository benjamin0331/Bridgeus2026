"""M6 觀點知識庫 — 組資料層：把一場 H-H DialogueMatch 的 MatchMessage 組成
apps.summary.pipeline.quality_filter.run_pipeline() 需要的 messages: list[dict]。

ccnd_semantic_dist / ccnd_stance_shift 都是「這則發言本身帶來多少新東西」的
逐則訊號，不是累積量——早期版本曾經直接借用 hh_analysis.get_message_drift_value
（累積偏移量）跟 semantic_tree.get_lit_node_count（累積點亮節點數）的原始回傳值，
但那兩者都是單調趨勢的累積量：對話後段的發言分數會系統性偏高（不是因為內容
真的比較有價值，只是因為講得比較晚），Step 3 的加權評分因此被這個時間偏誤
主導。這裡改成逐則差值：
- ccnd_semantic_dist：這則訊息的 embedding 與「同一位發言者上一則發言」的
  cosine distance——衡量這則發言相對於自己前一次發言帶來多少新論述內容。
- ccnd_stance_shift：這則訊息新點亮的 CCND 節點數（get_lit_node_count 在這則
  訊息、跟這位發言者上一則訊息之間的差，不是累積總數）。
兩者都是「這位發言者自己前後兩則發言之間」的差值，第一則發言沒有「自己的
上一則」可比，記 0.0（跟這位發言者從未推進過論述是同一種狀態，Step 2 的
門檻本來就會把它篩掉，不需要特殊處理）。
"""

from api.models import DialogueMatch
from apps.matching.services.semantic_tree import (
    OWNER_USER_A,
    OWNER_USER_B,
    get_lit_node_count,
    get_message_dimension,
)
from apps.summary.pipeline.quality_filter import run_pipeline
from apps.summary.pipeline.write import write_dialogue_summary, write_viewpoint
from chat.services.embedding import cosine_distance

# ccnd_stance_shift 的縮放常數。Step 3 的 score_and_rank 會對整批候選配對做
# min-max 正規化，任何正的線性縮放對正規化後的排序結果沒有影響——這個常數
# 純粹是讓 score_detail 裡的原始值好讀，不影響評分結果。
MAX_LIT_NODES = 36


def build_messages_for_match(match: DialogueMatch) -> list[dict]:
    """把 match 底下所有 MatchMessage 依時間順序組成 run_pipeline() 要的格式。

    每則訊息：
    - side：發送者是 match.user_a 就是 "a"，否則 "b"
    - ccnd_semantic_dist：見模組 docstring，這位發言者跟自己上一則發言的
      cosine distance；任一則訊息缺 embedding（極少數情況，embedding 服務
      當下失敗）時記 0.0，不強行估算
    - ccnd_stance_shift：見模組 docstring，這位發言者這則訊息新點亮的
      CCND 節點數，換算成 100/MAX_LIT_NODES 分制
    - message_id：MatchMessage 的 id
    """
    messages = []
    last_embedding_by_side: dict[str, list[float]] = {}
    last_lit_count_by_side: dict[str, int] = {}

    for msg in match.messages.order_by("created_at", "id"):
        if msg.sender_id == match.user_a_id:
            side, owner_key = "a", OWNER_USER_A
        elif msg.sender_id == match.user_b_id:
            side, owner_key = "b", OWNER_USER_B
        else:
            continue  # 不屬於這場配對雙方的訊息，理論上不會發生，跳過不納入

        prev_embedding = last_embedding_by_side.get(side)
        if prev_embedding is not None and msg.embedding is not None:
            semantic_dist = round(float(cosine_distance(msg.embedding, prev_embedding)), 4)
        else:
            semantic_dist = 0.0
        if msg.embedding is not None:
            last_embedding_by_side[side] = msg.embedding

        lit_count = get_lit_node_count(
            match, owner_key=owner_key, source_message_id=str(msg.id)
        )
        # get_lit_node_count 本身是累積計數；new_lit_count 只取「比這位發言者
        # 上一則多點亮了幾個」。用 max(0, ...) 防禦：若這則訊息剛好沒有 CCND
        # 分析紀錄（get_lit_node_count 對「未分析過」的訊息一律回 0），不該
        # 讓差值變負，直接當作沒有新推進。
        prev_lit_count = last_lit_count_by_side.get(side, 0)
        new_lit_count = max(0, lit_count - prev_lit_count)
        last_lit_count_by_side[side] = lit_count

        messages.append(
            {
                "side": side,
                "content": msg.content,
                "ccnd_semantic_dist": semantic_dist,
                "ccnd_stance_shift": round(100 / MAX_LIT_NODES * new_lit_count, 4),
                "message_id": msg.id,
            }
        )
    return messages


def run_pipeline_for_match(match_id: int) -> int:
    """M6 觀點知識庫的自動觸發入口：配對房結束對話時呼叫這支函式，跑完整條
    品質篩選 → 去重 → 寫入流程，回傳實際寫入的 ViewpointNode 筆數。

    由 apps/matching/services/matcher.py 的 _close_locked_match() 透過
    transaction.on_commit() 呼叫，確保配對房狀態真的轉為 CLOSED（交易已提交、
    鎖已釋放）之後才觸發。任何一步失敗都不該讓配對房關不掉，所以呼叫端把整支
    函式包在自己的例外處理裡，這裡不特別 catch。

    side_a_stance / side_b_stance / summary_text / quality_score /
    stance_shift_magnitude 目前都沒有餵值（DialogueSummary 這幾個欄位本來就是
    nullable/blank，不影響寫入）——這些欄位要填什麼是還沒拍板的另一個問題，
    先不要在這裡自己發明公式。
    """
    match = DialogueMatch.objects.get(pk=match_id)
    messages = build_messages_for_match(match)
    ranked = run_pipeline(messages)
    if not ranked:
        return 0

    summary_id = write_dialogue_summary(
        {"dialogue_id": str(match.id), "topic_id": match.topic_id}
    )

    written = 0
    for pair in ranked:
        owner_key = OWNER_USER_A if pair["speaker_side"] == "a" else OWNER_USER_B
        dimension = get_message_dimension(
            match,
            owner_key=owner_key,
            source_message_id=str(pair["user_message_id"]),
        )
        if dimension is None:
            continue  # 這則發言沒有對應到任何 CCND anchor，無法分類，跳過不寫入

        if write_viewpoint(
            {
                "summary_id": summary_id,
                "dimension": dimension,
                "speaker_side": pair["speaker_side"],
                "user_input_text": pair["user_input_text"],
                "ai_response_text": pair["ai_response_text"],
                "source_message_ids": [pair["user_message_id"]],
                "composite_score": pair["composite_score"],
                "score_detail": pair["score_detail"],
            }
        ):
            written += 1
    return written
