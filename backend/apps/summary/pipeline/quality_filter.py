"""M6 觀點知識庫 — 品質篩選管線：Step 1 → Step 2 → Step 3

Step 1：整場對話品質篩選（passes_quality_filter）
Step 2：擷取有價值的發言 + 對方回應配對（extract_valuable_pairs）
Step 3：加權評分排序（score_and_rank）

收錄範圍為人對人（H-H）對話，雙方皆為真人發言者。

前置假設：
- Step 1 的 messages: list[dict]，每筆含 content（role/side 皆可，不影響篩選）
- Step 2/3 的 messages 每筆額外含 side / ccnd_semantic_dist / ccnd_stance_shift / message_id
  - side："a" | "b"，對應 DialogueSummary.side_a_stance / side_b_stance
- ccnd_semantic_dist（論述移動 / drift）請用
  apps.matching.services.hh_analysis.get_message_drift_value(match_id=, user_id=, as_of=該則訊息的 created_at)
  取得——讀取的是 api/consumers.py 每則發言後即時寫入的 MatchStanceDrift 記錄。
- ccnd_stance_shift（立場偏移量，用「CCND 點亮節點數」衡量：點亮節點越多代表
  這則發言帶出的觀點推進度越大，誰的亮點多選誰）請用
  100 / 36 * apps.matching.services.semantic_tree.get_lit_node_count(
      match, owner_key=, source_message_id=該則訊息 id
  )
  取得（36 為假設的滿分點亮節點數，可視實際資料調整；get_lit_node_count 回傳的
  是累積不重複點亮的 micro node 數）。
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

def passes_quality_filter(messages: list[dict]) -> bool:
    """
    輸入整場對話，回傳 True 代表整場對話品質合格。

    條件：
    1. 雙方發言輪數合計 >= 6
    2. 平均發言長度 >= 30 字
    3. 全場攻擊性詞彙比例 < 15%
    """
    human_messages = [m["content"] for m in messages]

    if len(human_messages) < 6:
        return False

    avg_len = sum(len(m) for m in human_messages) / len(human_messages)
    if avg_len < 30:
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


def _attack_ratio(text: str) -> float:
    if not text:
        return 0.0
    return _count_attack_hits(text) / len(text)


def extract_valuable_pairs(messages: list[dict]) -> list[dict]:
    """
    輸入：整場對話的訊息列表，每筆格式：
        {
            "side":               "a" | "b",  # 對應 DialogueSummary.side_a_stance / side_b_stance
            "content":            str,
            "ccnd_semantic_dist": float,   # 與前一則的 cosine distance（CCND 已算）
            "ccnd_stance_shift":  float,   # 對方立場偏移量（CCND 已算）
            "message_id":         int,     # 在這場對話中的序號
        }

    雙方皆為真人發言者，任一方發言只要通過篩選門檻，就會嘗試跟緊接其後、
    由另一方發出的下一則訊息配對成「發言方 + 回應方」。

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

        if len(content) < MIN_LENGTH:
            continue

        semantic_dist = msg.get("ccnd_semantic_dist", 0.0)
        if semantic_dist <= SEMANTIC_DIST_THRESHOLD:
            continue

        if _attack_ratio(content) > MAX_ATTACK_RATIO:
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
# Step 3：加權評分排序
# ──────────────────────────────────────────────

# 加權評分權重（初始等權）
W_SEMANTIC = 0.35
W_STANCE   = 0.35
W_LEXICAL  = 0.2
W_LENGTH   = 0.1

# 取幾筆進人工終審
TOP_N = 15


def _lexical_richness(text: str) -> float:
    """詞彙豐富度 = 不重複詞彙數 / 總詞彙數（jieba 斷詞）。"""
    tokens = [t for t in jieba.cut(text) if t.strip()]
    if not tokens:
        return 0.0
    return len(set(tokens)) / len(tokens)


def _normalize(values: list[float]) -> list[float]:
    """Min-max 正規化到 0-1。若所有值相同，回傳全 0。"""
    mn, mx = min(values), max(values)
    if mx == mn:
        return [0.0] * len(values)
    return [(v - mn) / (mx - mn) for v in values]


def score_and_rank(pairs: list[dict], top_n: int = TOP_N) -> list[dict]:
    """
    輸入：extract_valuable_pairs 回傳的配對列表
    輸出：加上 composite_score / score_detail，依分數降序排列，取 top_n 筆
    """
    if not pairs:
        return []

    raw_semantic = [p["ccnd_semantic_dist"]                 for p in pairs]
    raw_stance   = [p["ccnd_stance_shift"]                  for p in pairs]
    raw_lexical  = [_lexical_richness(p["user_input_text"]) for p in pairs]
    raw_length   = [len(p["user_input_text"])               for p in pairs]

    norm_semantic = _normalize(raw_semantic)
    norm_stance   = _normalize(raw_stance)
    norm_lexical  = _normalize(raw_lexical)
    norm_length   = _normalize(raw_length)

    scored = []
    for i, pair in enumerate(pairs):
        composite = (
            W_SEMANTIC * norm_semantic[i]
            + W_STANCE  * norm_stance[i]
            + W_LEXICAL * norm_lexical[i]
            + W_LENGTH  * norm_length[i]
        )
        scored.append({
            **pair,
            "composite_score": round(composite, 4),
            "score_detail": {
                "semantic_dist_raw":  round(raw_semantic[i], 4),
                "stance_shift_raw":   round(raw_stance[i],   4),
                "lexical_richness":   round(raw_lexical[i],  4),
                "length_chars":       raw_length[i],
                "semantic_dist_norm": round(norm_semantic[i], 4),
                "stance_shift_norm":  round(norm_stance[i],   4),
                "lexical_norm":       round(norm_lexical[i],  4),
                "length_norm":        round(norm_length[i],   4),
            },
        })

    scored.sort(key=lambda x: x["composite_score"], reverse=True)
    return _select_balanced_by_side(scored, top_n)


def _select_balanced_by_side(scored: list[dict], top_n: int) -> list[dict]:
    """在保留分數排序優先權的前提下，確保 top_n 筆結果盡量涵蓋這場對話裡出現
    過的每一種 speaker_side（例如支持方跟反對方）。

    背景：BridgeUs 的核心價值是呈現異質觀點，如果單純依 composite_score 排序，
    某一方發言的分數若系統性偏高（例如發言明顯較長、詞彙較豐富），另一方的
    觀點可能整場對話都擠不進 TOP_N，知識庫裡就只看得到單一立場——這正是
    我們想避免的同溫層效果。

    做法：scored 已依分數由高到低排序，所以每個 side 第一次出現的位置就是
    該 side 分數最高的配對；先各保留一筆，其餘名額再依分數高低補滿。
    """
    if top_n >= len(scored):
        return scored

    picked_indices: list[int] = []
    seen_sides: set[str] = set()
    for i, pair in enumerate(scored):
        if pair["speaker_side"] not in seen_sides:
            picked_indices.append(i)
            seen_sides.add(pair["speaker_side"])
        if len(picked_indices) >= top_n:
            break

    if len(picked_indices) < top_n:
        picked_set = set(picked_indices)
        for i in range(len(scored)):
            if i in picked_set:
                continue
            picked_indices.append(i)
            if len(picked_indices) >= top_n:
                break

    picked_indices.sort()  # scored 本身已依分數由高到低排序，索引升冪 = 分數降冪
    return [scored[i] for i in picked_indices]


# ──────────────────────────────────────────────
# 整合入口：對單場對話跑完 Step 1 → 2 → 3
# ──────────────────────────────────────────────

def run_pipeline(messages: list[dict], top_n: int = TOP_N) -> list[dict]:
    """
    輸入一場對話的訊息列表。
    先跑 Step 1 品質篩選，通過後執行 Step 2 擷取配對、Step 3 評分排序。
    回傳 Top N 配對，供 Step 4 人工終審使用。
    若整場對話未通過 Step 1，回傳空列表。
    """
    if not passes_quality_filter(messages):
        return []
    pairs  = extract_valuable_pairs(messages)
    ranked = score_and_rank(pairs, top_n=top_n)
    return ranked
