"""M6 觀點知識庫 — 組資料層：把一場 H-H DialogueMatch 的 MatchMessage 組成
apps.summary.pipeline.quality_filter.run_pipeline() 需要的 messages: list[dict]。

ccnd_semantic_dist / ccnd_stance_shift 都是「跟同一位發言者上一則發言之間的
差值」，不是全場累積值——Step 3 的加權評分（quality_filter.py 的
W_SEMANTIC/W_STANCE 合計 70% 權重）本意是量化「這則發言本身帶來多少新意」，
如果餵累積值（例如全場論述移動總量、累積點亮節點數），會系統性地讓「講得晚」
的發言分數偏高，而不是「講得好」的發言分數偏高，跟評分的本意不符。第一則發言
沒有「自己的上一則」可比，記 0.0——Step 2 的門檻本來就會篩掉低分發言，不需要
特殊處理。
"""

from api.models import DialogueMatch, MatchStanceDrift
from api.display_settings import resolve_stance_category
from apps.matching.services.semantic_tree import (
    OWNER_USER_A,
    OWNER_USER_B,
    get_lit_node_count,
    get_message_dimension,
    get_message_lit_nodes,
)
from apps.summary.pipeline.quality_filter import run_pipeline
from apps.summary.pipeline.write import write_dialogue_summary, write_viewpoint
from chat.services.embedding import cosine_similarity

# 假設的滿分點亮節點數，換算 get_lit_node_count() 的原始計數成 0-100 分；
# 與 apps/summary/pipeline/quality_filter.py 模組 docstring 記載的公式一致。
MAX_LIT_NODES = 36


def build_messages_for_match(match: DialogueMatch) -> list[dict]:
    """把 match 底下所有 MatchMessage 依時間順序組成 run_pipeline() 要的格式。

    每則訊息：
    - side：發送者是 match.user_a 就是 "a"，否則 "b"
    - ccnd_semantic_dist：跟「同一位發言者上一則發言」的 embedding cosine
      distance（1 - cosine_similarity）；第一則記 0.0
    - ccnd_stance_shift：跟「同一位發言者上一則發言」相比多點亮了幾個 CCND
      節點（累積計數的差，不是總數），換算成 100/MAX_LIT_NODES 分制
    - message_id：MatchMessage 的 id
    """
    messages = []
    prev_embedding_by_side: dict[str, list[float] | None] = {}
    prev_lit_count_by_side: dict[str, int] = {}

    for msg in match.messages.order_by("created_at", "id"):
        if msg.sender_id == match.user_a_id:
            side, owner_key = "a", OWNER_USER_A
        elif msg.sender_id == match.user_b_id:
            side, owner_key = "b", OWNER_USER_B
        else:
            continue  # 不屬於這場配對雙方的訊息，理論上不會發生，跳過不納入

        prev_embedding = prev_embedding_by_side.get(side)
        if prev_embedding is not None and msg.embedding is not None:
            semantic_dist = round(1 - cosine_similarity(msg.embedding, prev_embedding), 4)
        else:
            semantic_dist = 0.0

        lit_count = get_lit_node_count(
            match, owner_key=owner_key, source_message_id=str(msg.id)
        )
        lit_delta = max(0, lit_count - prev_lit_count_by_side.get(side, 0))

        messages.append(
            {
                "side": side,
                "content": msg.content,
                "ccnd_semantic_dist": semantic_dist,
                "ccnd_stance_shift": round(100 / MAX_LIT_NODES * lit_delta, 4),
                "message_id": msg.id,
            }
        )

        prev_embedding_by_side[side] = msg.embedding
        prev_lit_count_by_side[side] = lit_count

    return messages


def _stance_for_score(topic_id: int, stance_score) -> str:
    """DialogueMatch.user_a_score / user_b_score 換算成 support/neutral/oppose。

    對應 DialogueSummary.side_a_stance / side_b_stance。沿用配對當下記錄在
    DialogueMatch 上的分數（而不是重查 UserStanceProfile 的當前值，那可能在
    配對之後又被使用者填了新的問卷、跟這場對話當時的立場對不上），並且套用
    api.display_settings.resolve_stance_category() 同一套 topic 門檻，跟問卷
    結果頁、配對演算法用同一套判定標準，不再自己另立一份。
    """
    return resolve_stance_category(topic_id=topic_id, user_stance_score=float(stance_score))


def _quality_score(ranked: list[dict]) -> float | None:
    """整場對話的品質分數 = Step 3 選中的配對 composite_score 平均值。"""
    scores = [pair["composite_score"] for pair in ranked]
    if not scores:
        return None
    return round(sum(scores) / len(scores), 4)


def _build_summary_text(messages: list[dict]) -> str:
    """把整場對話雙方所有發言依時間順序串成純文字記錄。

    對應 DialogueSummary.summary_text；這裡只是逐則保留原始發言（不是 LLM
    摘要——那是另一個範圍更大、還沒拍板的任務），messages 用的是
    build_messages_for_match() 回傳的全部訊息，不是 Step 3 篩選後的
    ranked 子集。
    """
    return "\n".join(f"{msg['side'].upper()}: {msg['content']}" for msg in messages)


def _stance_shift_magnitude(match: DialogueMatch) -> float | None:
    """整場對話的立場偏移量 = 雙方 |最後一筆 drift_value − 第一筆| 的平均。

    只在至少一方有 MatchStanceDrift 記錄時才有值；一方完全沒發言、沒觸發過
    drift 重算，就不計入平均（而不是當成 0 拉低整體）。雙方都沒有記錄時回傳
    None。
    """
    magnitudes = []
    for user_id in (match.user_a_id, match.user_b_id):
        drifts = list(
            MatchStanceDrift.objects.filter(match_id=match.id, user_id=user_id)
            .order_by("measured_at")
            .values_list("drift_value", flat=True)
        )
        if drifts:
            magnitudes.append(abs(drifts[-1] - drifts[0]))
    if not magnitudes:
        return None
    return round(sum(magnitudes) / len(magnitudes), 4)


def run_pipeline_for_match(match_id: int) -> int:
    """M6 觀點知識庫的自動觸發入口：配對房結束對話時呼叫這支函式，跑完整條
    品質篩選 → 去重 → 寫入流程，回傳實際寫入的 ViewpointNode 筆數。

    由 apps/matching/services/matcher.py 的 _close_locked_match() 透過
    transaction.on_commit() 呼叫，確保配對房狀態真的轉為 CLOSED（交易已提交、
    鎖已釋放）之後才觸發。任何一步失敗都不該讓配對房關不掉，所以呼叫端把整支
    函式包在自己的例外處理裡，這裡不特別 catch。

    summary_text 存的是雙方所有發言的純文字紀錄（見 _build_summary_text()），
    不是 LLM 摘要——後者範圍較大，另外處理。
    """
    match = DialogueMatch.objects.get(pk=match_id)
    messages = build_messages_for_match(match)
    ranked = run_pipeline(messages)
    if not ranked:
        return 0

    summary_id = write_dialogue_summary(
        {
            "dialogue_id": str(match.id),
            "topic_id": match.topic_id,
            "summary_text": _build_summary_text(messages),
            "side_a_stance": _stance_for_score(match.topic_id, match.user_a_score),
            "side_b_stance": _stance_for_score(match.topic_id, match.user_b_score),
            "quality_score": _quality_score(ranked),
            "stance_shift_magnitude": _stance_shift_magnitude(match),
        }
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

        lit_nodes = get_message_lit_nodes(
            match,
            owner_key=owner_key,
            source_message_id=str(pair["user_message_id"]),
        )

        if write_viewpoint(
            {
                "summary_id": summary_id,
                "dimension": dimension,
                "speaker_side": pair["speaker_side"],
                "user_input_text": pair["user_input_text"],
                "ai_response_text": pair["ai_response_text"],
                "viewpoint_summary": "、".join(node["name"] for node in lit_nodes),
                "stance_direction": lit_nodes[0]["stance"] if lit_nodes else "",
                "source_message_ids": [pair["user_message_id"]],
                "composite_score": pair["composite_score"],
                "score_detail": pair["score_detail"],
            }
        ):
            written += 1
    return written
