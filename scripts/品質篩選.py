"""
品質篩選模組：Step 1 → Step 2 → Step 3

Step 1：整場對話品質篩選（passes_quality_filter）
Step 2：擷取有價值的發言 + 對方回應配對（extract_valuable_pairs）
Step 3：加權評分排序（score_and_rank）

收錄範圍為人對人（H-H）對話，雙方皆為真人發言者。

前置假設：
- Step 1 的 messages: list[dict]，每筆含 content（role/side 皆可，不影響篩選）
- Step 2/3 的 messages 每筆額外含 side / ccnd_semantic_dist / ccnd_stance_shift / message_id
  - side："a" | "b"，對應 DialogueSummary.side_a_stance / side_b_stance
- CCND 已算好 semantic_dist（與前一則的 cosine distance）與 stance_shift（對方立場偏移量）
"""

import jieba
from typing import Optional

# ──────────────────────────────────────────────
# 共用設定
# ──────────────────────────────────────────────

# 攻擊性詞典（Step 1 / Step 2 共用）
ATTACK_WORDS = {"幹", "白痴", "智障", "廢物", "去死", "賤人", "混蛋"}


def _count_attack_hits(text: str) -> int:
    """子字串比對攻擊詞出現次數，Step 1 / Step 2 共用同一種比對方式。

    不用 jieba 斷詞比對，因為攻擊詞不一定會被斷成獨立 token，
    子字串比對才能確保兩步驟的判定邏輯一致。
    """
    return sum(text.count(w) for w in ATTACK_WORDS)

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
    return scored[:top_n]


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


# ──────────────────────────────────────────────
# 簡易測試（開發用）
# ──────────────────────────────────────────────

if __name__ == "__main__":
    # 雙方皆為真人（H-H），side "a" 傾向支持核能、side "b" 傾向支持再生能源
    mock_messages = [
        {"message_id": 1, "side": "a",
         "content": "我認為核能發電是目前最穩定的低碳電力來源，台灣應該重新考慮延役既有機組。",
         "ccnd_semantic_dist": 0.0, "ccnd_stance_shift": 0.0},
        {"message_id": 2, "side": "b",
         "content": "核能作為低碳基載電力確實是許多能源專家的立場，不過台灣的核安規範與地震風險是關鍵考量……",
         "ccnd_semantic_dist": 0.0, "ccnd_stance_shift": 0.0},
        {"message_id": 3, "side": "a",
         "content": "好",
         "ccnd_semantic_dist": 0.05, "ccnd_stance_shift": 0.02},
        {"message_id": 4, "side": "b",
         "content": "沒問題，我們繼續討論。",
         "ccnd_semantic_dist": 0.0, "ccnd_stance_shift": 0.0},
        {"message_id": 5, "side": "b",
         "content": "再生能源的間歇性問題確實存在，但搭配抽蓄水電與電池儲能系統，加上跨區域電網互聯，可以大幅改善供電穩定性，這方面德國已有實際運作的案例。",
         "ccnd_semantic_dist": 0.42, "ccnd_stance_shift": 0.31},
        {"message_id": 6, "side": "a",
         "content": "您提出的儲能搭配方案是目前能源轉型的主流技術路徑之一……",
         "ccnd_semantic_dist": 0.0, "ccnd_stance_shift": 0.0},
        {"message_id": 7, "side": "a",
         "content": "從經濟面來看，核電的建設成本與工期不確定性極高，芬蘭 Olkiluoto 3 號機延宕超過 14 年才商轉，成本超支三倍以上，這對台灣這樣資源有限的島嶼而言風險難以承受。",
         "ccnd_semantic_dist": 0.38, "ccnd_stance_shift": 0.45},
        {"message_id": 8, "side": "b",
         "content": "您引用的芬蘭案例是核電工期風險的代表性論據，確實影響了許多國家的評估決策……",
         "ccnd_semantic_dist": 0.0, "ccnd_stance_shift": 0.0},
        {"message_id": 9, "side": "b",
         "content": "核廢料的最終處置問題在全球範圍內都尚未完全解決，這是長達數萬年的責任，我認為不應該留給下一代承擔。",
         "ccnd_semantic_dist": 0.29, "ccnd_stance_shift": 0.22},
        {"message_id": 10, "side": "a",
         "content": "核廢料的世代倫理確實是核能討論中最難迴避的議題……",
         "ccnd_semantic_dist": 0.0, "ccnd_stance_shift": 0.0},
        {"message_id": 11, "side": "b",
         "content": "因此我認為台灣應該加速佈建分散式再生能源，而非將資源押注在單一的核能延役方案上，這樣的能源多元化策略才能真正降低風險。",
         "ccnd_semantic_dist": 0.33, "ccnd_stance_shift": 0.28},
        {"message_id": 12, "side": "a",
         "content": "您提出的分散式能源策略確實符合現代電網韌性的設計原則……",
         "ccnd_semantic_dist": 0.0, "ccnd_stance_shift": 0.0},
    ]

    # Step 1 單獨測試
    passed = passes_quality_filter(mock_messages)
    print(f"Step 1 品質篩選：{'通過' if passed else '未通過'}\n")

    # Step 1 → 2 → 3 整合
    results = run_pipeline(mock_messages, top_n=5)
    print(f"通過 Step 2 篩選後進入評分：{len(results)} 筆\n")
    for r in results:
        print(f"[msg_id={r['user_message_id']}] score={r['composite_score']} speaker={r['speaker_side']} responder={r['responder_side']}")
        print(f"  detail: {r['score_detail']}")
        print(f"  speaker: {r['user_input_text'][:60]}...")
        print()
