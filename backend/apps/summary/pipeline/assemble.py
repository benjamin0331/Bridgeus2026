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

import logging
import os

from api.display_settings import resolve_stance_category
from api.models import DialogueMatch, MatchStanceDrift
from apps.matching.services.semantic_tree import (
    OWNER_USER_A,
    OWNER_USER_B,
    get_lit_node_count,
    get_message_dimension,
    get_message_lit_nodes,
)
from apps.summary.pipeline.quality_filter import run_pipeline
from apps.summary.pipeline.write import write_dialogue_summary, write_viewpoint
from chat.services.embedding import cosine_distance

logger = logging.getLogger(__name__)

# ccnd_stance_shift 的縮放常數。Step 3 的 score_and_rank 會對整批候選配對做
# min-max 正規化，任何正的線性縮放對正規化後的排序結果沒有影響——這個常數
# 純粹是讓 score_detail 裡的原始值好讀，不影響評分結果。
MAX_LIT_NODES = 42


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
    """把整場對話雙方所有發言依時間順序串成純文字記錄，當作 AI 摘要
    （generate_ai_summary）失敗時的備援，以及還沒被知識庫頁面觸發過摘要生成
    前的暫時內容。

    對應 DialogueSummary.summary_text；messages 用的是 build_messages_for_match()
    回傳的全部訊息，不是 Step 3 篩選後的 ranked 子集。
    """
    return "\n".join(f"{msg['side'].upper()}: {msg['content']}" for msg in messages)


def is_raw_summary_text(summary_text: str) -> bool:
    """判斷 DialogueSummary.summary_text 是不是還停留在 _build_summary_text()
    的原始逐字稿格式（還沒被 generate_ai_summary() 換成真正的 AI 摘要）。

    用「開頭是不是 'A: '/'B: '」這個簡單字串特徵判斷，不新增一個布林欄位
    （不想為這個小事再加一次 migration）：真正的 AI 摘要是一段連貫的中文
    描述，幾乎不可能剛好以這個固定英文字母+冒號組合開頭。
    """
    stripped = (summary_text or "").lstrip()
    return stripped.startswith("A: ") or stripped.startswith("B: ")


def generate_ai_summary(messages: list[dict], *, topic_title: str) -> str | None:
    """呼叫 Claude 幫這場已審核通過的對話寫一段簡短摘要，給知識庫「對話詳情」
    頁最上方用（取代原本逐字稿直接複製貼上的 _build_summary_text）。

    沒設 ANTHROPIC_API_KEY，或呼叫失敗（額度、逾時、API 錯誤等），回傳 None，
    由呼叫端自行 fallback 回 _build_summary_text() 的逐字稿版本——不能讓知識庫
    頁面因為 AI 摘要生成失敗就整頁掛掉。
    """
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return None

    transcript = "\n".join(f"{msg['side'].upper()}: {msg['content']}" for msg in messages)
    prompt = (
        f"以下是一場關於「{topic_title}」的雙人討論逐字稿，A、B 分別代表兩位匿名參與者。"
        "請用繁體中文寫一段 150 字以內的摘要，客觀描述雙方各自的立場與討論重點、"
        "分歧所在，不要加任何前言、標題或說明文字，只回傳摘要本文。\n\n"
        f"{transcript}"
    )
    try:
        import anthropic

        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model=os.getenv("CLAUDE_CHAT_MODEL", "claude-sonnet-4-6"),
            max_tokens=400,
            messages=[{"role": "user", "content": prompt}],
        )
        text = response.content[0].text.strip()
        return text or None
    except Exception:
        logger.exception("Claude API call failed while generating KB conversation AI summary.")
        return None


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
