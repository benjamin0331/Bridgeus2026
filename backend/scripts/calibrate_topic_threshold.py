"""
離題偵測閾值校準腳本 — BridgeUs H-H 對話模組

【模組定位】
偵測使用者是否偏離議題，觸發時私下傳送「引導回主題」的 AI 建議。
屬於輔助建議，非強制攔截，邏輯與情緒模組相同：
  - 漏放（真正離題未偵測）> 誤觸（在題句被誤判）

【偵測機制】
  1. 取發言者最近 window（預設 5）則訊息的 embedding 平均
  2. 計算與議題錨點 embedding 的 cosine_similarity
  3. similarity < THRESHOLD → 判定離題

  使用視窗平均的目的：
  - 單則短句 embedding 不穩定（語義資訊不足）
  - 平均後可抵銷偶發的偏題措辭，避免一句偏題就觸發
  - window=3 vs window=5：較小視窗對近期偏離更敏感

【閾值設計依據】
  在「台灣是否應該擴大發展核能發電」錨點測試：
  - 直接相關句（核能政策、安全、成本）: 0.53–0.66
  - 間接相關句（福島、德國廢核、氣候）: 0.24–0.40
  - 完全無關句（天氣、飲食、娛樂）    : 0.01–0.08
  設 0.25 而非 0.30：確保「福島事件」類間接相關句不被誤判為離題

執行：
    cd backend
    python scripts/calibrate_topic_threshold.py
"""

import sys
import os
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from chat.services.embedding import cosine_similarity, get_embedding
from apps.matching.services.topic_relevance import get_topic_relevance_policy

TOPIC_ID = 102
POLICY = get_topic_relevance_policy(TOPIC_ID)
THRESHOLD = POLICY.threshold
TOPIC_ANCHOR = POLICY.anchor_text

# -----------------------------------------------------------------------
# 測試語料
# 格式: (分類, 預期結果, 句子)
# 預期: "on" = 在議題內, "off" = 應判定離題
# -----------------------------------------------------------------------
CORPUS = [
    # ── 直接相關 ── 明確討論台灣核能政策
    ("直接相關", "on",  "我認為台灣應該繼續使用核能，它是穩定且低碳的電力來源。"),
    ("直接相關", "on",  "核能的碳排放量比化石燃料低很多，對台灣的減碳目標有幫助。"),
    ("直接相關", "on",  "台灣核電廠的安全設計是否足夠應對地震與海嘯風險？"),
    ("直接相關", "on",  "廢核政策導致台灣電力供應不穩定，近年缺電問題更加嚴重。"),
    ("直接相關", "on",  "核廢料最終處置場的選址問題一直是台灣核能發展的主要障礙。"),
    ("直接相關", "on",  "核四廠封存多年，若重啟需要多少時間和成本才能達到安全標準？"),

    # ── 間接相關 ── 涉及核能但非直接討論台灣政策
    # 這類句子是設計的邊界測試，正確做法是視為「在題」
    ("間接相關", "on",  "日本福島事件讓很多人改變了對核能安全的看法。"),  # ← 設計原點
    ("間接相關", "on",  "德國已完全關閉所有核電廠，轉向再生能源的經驗值得參考。"),
    ("間接相關", "on",  "車諾比核災對周邊國家的長期輻射影響至今仍有爭議。"),
    ("間接相關", "on",  "太陽能和風力發電目前能否穩定取代核電，還需要更多數據支撐。"),
    ("間接相關", "on",  "全球氣候變遷加速，各國重新評估核能在淨零路徑中的角色。"),

    # ── 完全無關 ── 與能源、核能或政策毫無關聯
    ("完全無關", "off", "你昨天去哪裡吃飯？最近有什麼好吃的推薦嗎？"),
    ("完全無關", "off", "今天天氣真的很熱，感覺夏天越來越長了。"),
    ("完全無關", "off", "台灣的夜市文化非常有特色，值得向外國朋友介紹。"),
    ("完全無關", "off", "最近有什麼好看的電影或劇集推薦嗎？"),
    ("完全無關", "off", "我覺得台灣的交通壅塞問題比能源問題更需要優先解決。"),
]

# -----------------------------------------------------------------------
# 視窗平均情境模擬
# 模擬一個使用者說了數則訊息後進行評估
# -----------------------------------------------------------------------
WINDOW_SCENARIOS = [
    {
        "name": "全程在題（直接相關）",
        "expected": "on",
        "messages": [
            "核能在台灣的能源結構中佔了多大比例？",
            "核廢料問題確實棘手，但其他能源也有廢棄物問題。",
            "廢核後台灣的備用容量率是否仍在安全範圍內？",
        ],
    },
    {
        "name": "間接相關後回主題",
        "expected": "on",
        "messages": [
            "德國廢核後電費大幅上漲，這對台灣是個警示。",
            "回到核能本身，台灣的地質條件是否適合繼續運轉核電廠？",
            "我支持延役而非新建核電廠，風險和成本都相對低。",
        ],
    },
    {
        "name": "逐漸偏離（最後幾句完全離題）",
        "expected": "off",
        "messages": [
            "核能政策需要長期規劃，不能只看短期選舉利益。",
            "對了你有沒有看最近那部劇？",
            "今天吃了很好吃的滷肉飯。",
        ],
    },
    {
        "name": "完全偏離（全程離題）",
        "expected": "off",
        "messages": [
            "你有沒有推薦的消暑食物？",
            "台灣的夜市真的很多元，每次去都能發現新東西。",
            "上週末去爬山，景色很漂亮。",
        ],
    },
]


def main():
    print("=" * 72)
    print("BridgeUs — 離題偵測閾值校準報告")
    print(f"模型 : paraphrase-multilingual-MiniLM-L12-v2")
    print(f"機制 : cosine_similarity(mean_window_emb, anchor_emb) < {THRESHOLD} → 離題")
    print(f"錨點 : 「{TOPIC_ANCHOR}」")
    print("=" * 72)

    print("\n正在計算錨點 embedding…")
    anchor_emb = get_embedding(TOPIC_ANCHOR)

    # ── Part 1: 單句分數分佈 ────────────────────────────────────────
    print("\n" + "─" * 72)
    print("Part 1 — 單句相似度分佈")
    print(f"{'分類':<10} {'相似度':>6}  {'判定':<8}  {'結果'}")
    print("-" * 72)

    results = []
    for label, expected, text in CORPUS:
        emb = get_embedding(text)
        score = cosine_similarity(emb, anchor_emb)
        score = round(float(score), 4)
        is_off = score < THRESHOLD
        caught_correctly = (is_off and expected == "off") or (not is_off and expected == "on")

        status = ""
        if expected == "off" and is_off:
            status = "✓ 正確偵測離題"
        elif expected == "off" and not is_off:
            status = "✗ 漏放離題"
        elif expected == "on" and is_off:
            status = "△ 誤判在題句"
        else:
            status = "✓ 正確判為在題"

        results.append((label, expected, text, score, is_off, caught_correctly))
        verdict = "離題 ▶" if is_off else "在題  "
        print(f"[{label:<8}] {score:.4f}  {verdict}  {status}")
        print(f"  └ {text[:60]}{'…' if len(text)>60 else ''}")

    # 摘要統計
    on_group  = [r for r in results if r[1] == "on"]
    off_group = [r for r in results if r[1] == "off"]
    direct    = [r for r in results if r[0] == "直接相關"]
    indirect  = [r for r in results if r[0] == "間接相關"]
    unrelated = [r for r in results if r[0] == "完全無關"]

    print("\n各分類分數範圍：")
    for group, name in [(direct, "直接相關"), (indirect, "間接相關"), (unrelated, "完全無關")]:
        scores = [r[3] for r in group]
        print(f"  {name:<8}  avg={np.mean(scores):.4f}  min={min(scores):.4f}  max={max(scores):.4f}")

    fp = sum(1 for r in on_group if r[4])
    fn = sum(1 for r in off_group if not r[4])
    print(f"\n誤判（在題句被標為離題）: {fp}/{len(on_group)}")
    print(f"漏放（離題句未被偵測）  : {fn}/{len(off_group)}")

    # ── Part 2: 閾值比較 ────────────────────────────────────────────
    print("\n" + "─" * 72)
    print("Part 2 — 各閾值效果比較（單句）")
    print(f"\n{'閾值':>6}  {'離題偵測率':>12}  {'誤判率':>10}  備註")
    print("-" * 72)
    notes = {
        0.20: "離題偵測率偏低",
        0.25: "",
        0.30: "",
        0.35: "◀ 目前設定",
        0.40: "誤判率上升",
    }
    for t in [0.20, 0.25, 0.30, 0.35, 0.40]:
        detected = sum(1 for l, e, txt, s, *_ in results if e == "off" and s < t)
        fp_n     = sum(1 for l, e, txt, s, *_ in results if e == "on" and s < t)
        print(
            f"  {t:.2f}  {detected}/{len(off_group)} ({detected/len(off_group)*100:3.0f}%)         "
            f"{fp_n}/{len(on_group)} ({fp_n/len(on_group)*100:3.0f}%)    {notes.get(t,'')}"
        )

    # ── Part 3: 視窗平均效果 ────────────────────────────────────────
    print("\n" + "─" * 72)
    print("Part 3 — 視窗平均效果（模擬真實對話，window=3）")
    print("說明：取發言者最近 3 則訊息 embedding 的平均；較小視窗對近期偏離更敏感")
    print()

    for scenario in WINDOW_SCENARIOS:
        embeddings = [get_embedding(m) for m in scenario["messages"]]
        single_scores = [round(float(cosine_similarity(e, anchor_emb)), 4) for e in embeddings]
        mean_emb = np.mean([np.array(e, dtype=np.float32) for e in embeddings], axis=0)
        window_score = round(float(cosine_similarity(mean_emb, anchor_emb)), 4)
        is_off = window_score < THRESHOLD
        expected = scenario["expected"]

        verdict = "離題 ▶" if is_off else "在題  "
        correct = "✓" if (is_off == (expected == "off")) else "✗"

        print(f"情境：{scenario['name']}")
        print(f"  單句分數：{single_scores}")
        print(f"  視窗均值：{window_score:.4f}  →  {verdict}  {correct}")
        print()

    # ── 設計決策說明 ────────────────────────────────────────────────
    print("─" * 72)
    print("設計決策摘要")
    print("-" * 72)
    print(f"  閾值 0.25 vs 0.30 的關鍵差異：")

    pivot_text = "日本福島事件讓很多人改變了對核能安全的看法。"
    pivot_emb  = get_embedding(pivot_text)
    pivot_score = round(float(cosine_similarity(pivot_emb, anchor_emb)), 4)
    print(f"  「{pivot_text}」")
    print(f"  單句相似度 = {pivot_score}")
    print(f"  閾值 0.25 → {'離題 ✗' if pivot_score < 0.25 else '在題 ✓'}")
    print(f"  閾值 0.30 → {'離題 ✗' if pivot_score < 0.30 else '在題 ✓'}")
    print()
    print("  結論：0.30 會將「福島」等間接相關句誤判為離題，干擾合理辯論；")
    print("        0.25 保留這類邊界句，配合視窗平均進一步穩定判定。")


if __name__ == "__main__":
    main()
