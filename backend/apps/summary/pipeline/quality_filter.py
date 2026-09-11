"""M6 觀點知識庫 — 品質篩選管線：Step 1 → Step 2

Step 1：整場對話品質篩選（passes_quality_filter）
Step 2：擷取有價值的發言 + 對方回應配對（extract_valuable_pairs）

（原本的 Step 3「加權評分排序 / 雙方平衡 / 取 Top N」已移除：現在 Step 2
通過門檻的配對會全部原樣送進 Step 4 去重與人工終審，不再排序、不截斷、
也不再保證雙方立場都有代表。）

收錄範圍為人對人（H-H）對話，雙方皆為真人發言者。

前置假設：
- Step 1 的 messages: list[dict]，每筆含 content（role/side 皆可，不影響篩選）
- Step 2 的 messages 每筆額外含 side / ccnd_semantic_dist / ccnd_stance_shift /
  message_id，並可選填 is_off_topic（bool，組資料層判定這則發言離題）
  - side："a" | "b"，對應 DialogueSummary.side_a_stance / side_b_stance
  - ccnd_semantic_dist / ccnd_stance_shift 都是「這則發言相對於同一位發言者
    上一則發言」的差值，不是累積量——組資料層
    apps.summary.pipeline.assemble.build_messages_for_match() 負責算好這兩個
    欄位（該檔案模組 docstring 有完整說明兩者為什麼要用差值，不是累積量）。
    這裡的 SEMANTIC_DIST_THRESHOLD / MIN_LENGTH 等門檻都是針對差值設計的。
"""

import jieba
from typing import Optional

from chat.services._blacklist import BLACKLIST

# ──────────────────────────────────────────────
# 共用設定
# ──────────────────────────────────────────────


def _count_attack_hits(text: str) -> int:
    """子字串比對攻擊詞出現次數，Step 1 / Step 2 共用同一種比對方式。

    詞典跟 M4 對話室即時攔截（chat.services.filter）共用同一份
    chat.services._blacklist.BLACKLIST，不再各自維護一份，避免 M6 品質篩選
    對攻擊性內容的偵測比 M4 正式對話室寬鬆。

    不用 jieba 斷詞比對，因為攻擊詞不一定會被斷成獨立 token，
    子字串比對才能確保兩步驟的判定邏輯一致。
    """
    return sum(text.count(w) for w in BLACKLIST)

# ──────────────────────────────────────────────
# Step 1：整場對話品質篩選
# ──────────────────────────────────────────────

def passes_quality_filter(
    messages: list[dict], *, turn_count_side: str | None = None
) -> bool:
    """
    輸入整場對話，回傳 True 代表整場對話品質合格。

    條件（turn_count_side=None 為 H-H，"a" 為 H-AI）：
    1. 發言輪數 >= 6
       - H-H：雙方發言合計 >= 6
       - H-AI：只數使用者（side "a"）發言 >= 6
    2. 平均發言長度 >= 30 字
       - H-H：整場所有訊息的平均
       - H-AI：只算使用者發言的平均（不含 AI 回覆）
    3. 全場攻擊性詞彙比例 < 15%（一律以整場對話所有訊息計算，含對方 / AI 回覆）
    """
    human_messages = [m["content"] for m in messages]

    if turn_count_side is None:
        counted_messages = human_messages
    else:
        counted_messages = [
            m["content"] for m in messages if m.get("side") == turn_count_side
        ]

    # 1. 輪數
    if len(counted_messages) < 6:
        return False

    # 2. 平均發言長度（H-AI 一樣只看使用者發言）
    avg_len = sum(len(m) for m in counted_messages) / len(counted_messages)
    if avg_len < 30:
        return False

    # 3. 攻擊性詞彙比例：仍以整場對話（含對方 / AI 回覆）計算
    if not human_messages:
        return False

    all_text = " ".join(human_messages)
    tokens = list(jieba.cut(all_text))
    attack_hits = _count_attack_hits(all_text)
    if len(tokens) > 0 and attack_hits / len(tokens) >= 0.15:
        return False

    return True


# ──────────────────────────────────────────────
# Step 2：篩選有價值的發言，配對另一方（真人）回應
# ──────────────────────────────────────────────

# 觀點推進度門檻（cosine distance，越大代表越不重複）
SEMANTIC_DIST_THRESHOLD = 0.15

# 發言長度門檻（字數）
MIN_LENGTH = 30

# 攻擊性詞彙比例上限（單則發言，比 Step 1 稍嚴）
MAX_ATTACK_RATIO = 0.10

# 詞彙豐富度下限（不重複詞 / 總詞，jieba 斷詞）：>= 30% 才收錄。低於這個值
# 代表大量重複字詞（「不要不要不要」「我覺得我覺得」這類灌水發言），資訊量
# 不足。這是防灌水的地板，不是「文筆好不好」的品質線，可依實際語料再校準。
MIN_LEXICAL_RICHNESS = 0.3


def _attack_ratio(text: str) -> float:
    if not text:
        return 0.0
    return _count_attack_hits(text) / len(text)


def _lexical_richness(text: str) -> float:
    """詞彙豐富度 = 不重複詞彙數 / 總詞彙數（jieba 斷詞）。
    1.0 = 完全沒有重複；越低代表越多重複字詞。"""
    tokens = [t for t in jieba.cut(text) if t.strip()]
    if not tokens:
        return 0.0
    return len(set(tokens)) / len(tokens)


def extract_valuable_pairs(messages: list[dict]) -> list[dict]:
    """
    輸入：整場對話的訊息列表，每筆格式：
        {
            "side":               "a" | "b",  # 對應 DialogueSummary.side_a_stance / side_b_stance
            "content":            str,
            "ccnd_semantic_dist": float,   # 與前一則的 cosine distance（CCND 已算）
            "ccnd_stance_shift":  float,   # 對方立場偏移量（CCND 已算）
            "message_id":         int,     # 在這場對話中的序號
            "is_off_topic":       bool,    # 選填：組資料層判定這則發言離題
                                          #       （沿用 M4 對話室即時離題偵測的
                                          #        議題錨點與門檻，見 assemble.py）
        }

    雙方皆為真人發言者，任一方發言只要通過篩選門檻，就會嘗試跟緊接其後、
    由另一方發出的下一則訊息配對成「發言方 + 回應方」。

    逐則篩掉：離題（is_off_topic）、太短（< MIN_LENGTH）、觀點沒推進
    （ccnd_semantic_dist <= SEMANTIC_DIST_THRESHOLD）、攻擊性過高
    （> MAX_ATTACK_RATIO）、詞彙重複灌水（詞彙豐富度 < MIN_LEXICAL_RICHNESS）、
    以及後面沒有對方回應可配對的。

    輸出：通過篩選的配對列表，每筆格式：
        {
            "user_message_id":    int,
            "speaker_side":       "a" | "b",
            "responder_side":     "a" | "b",
            "user_input_text":    str,
            "ai_response_text":   str,   # 對方緊接的回應內容（欄位名沿用既有 DB schema）
            "ccnd_semantic_dist": float,
            "ccnd_stance_shift":  float,
        }
    """
    pairs = []

    for i, msg in enumerate(messages):
        content = msg["content"]

        # M4 對話室即時離題偵測在這則發言時判定為離題 → 不收錄成觀點。
        # 由組資料層（assemble.build_messages_for_*）用議題錨點標好，這裡只讀旗標。
        if msg.get("is_off_topic"):
            continue

        if len(content) < MIN_LENGTH:
            continue

        semantic_dist = msg.get("ccnd_semantic_dist", 0.0)
        if semantic_dist <= SEMANTIC_DIST_THRESHOLD:
            continue

        if _attack_ratio(content) > MAX_ATTACK_RATIO:
            continue

        # jieba 斷詞較貴，放在其他便宜的門檻都過了之後才算。
        if _lexical_richness(content) < MIN_LEXICAL_RICHNESS:
            continue

        response: Optional[str] = None
        responder_side: Optional[str] = None
        if i + 1 < len(messages) and messages[i + 1]["side"] != msg["side"]:
            response = messages[i + 1]["content"]
            responder_side = messages[i + 1]["side"]

        if response is None:
            continue

        pairs.append({
            "user_message_id":    msg.get("message_id", i),
            "speaker_side":       msg["side"],
            "responder_side":     responder_side,
            "user_input_text":    content,
            "ai_response_text":   response,
            "ccnd_semantic_dist": semantic_dist,
            "ccnd_stance_shift":  msg.get("ccnd_stance_shift", 0.0),
        })

    return pairs


# ──────────────────────────────────────────────
# 整合入口：對單場對話跑完 Step 1 → 2
# ──────────────────────────────────────────────

def run_pipeline(
    messages: list[dict], *, turn_count_side: str | None = None
) -> list[dict]:
    """
    輸入一場對話的訊息列表。
    先跑 Step 1 品質篩選，通過後執行 Step 2 擷取配對，直接回傳 Step 2 所有
    通過門檻的配對，供 Step 4 去重與人工終審使用。
    若整場對話未通過 Step 1，回傳空列表。

    （原本這裡還會跑 Step 3：加權評分、依 composite_score 降序、取 Top 15、
    並用 _select_balanced_by_side 保證雙方立場都有代表。Step 3 已移除，所以
    回傳的配對不含 composite_score / score_detail，數量不設上限，順序就是
    對話中的出現順序，也不再保證少數方的觀點會被保留。）

    turn_count_side：見 passes_quality_filter()。H-AI 傳 "a"，讓「輪數 >= 6」
    只數使用者發言。
    """
    if not passes_quality_filter(messages, turn_count_side=turn_count_side):
        return []
    return extract_valuable_pairs(messages)
