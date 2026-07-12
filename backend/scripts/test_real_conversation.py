"""
真實對話輸入測試 — Take A Bridge H-H 模組全管道驗證

輸入來源：2026-05-30 benjamin 實際輸入紀錄（核能議題對話）
測試管道：
  1. 內容過濾（黑名單）
  2. 情緒偵測（0.65 閾值 + 第二人稱過濾）
  3. 離題偵測（cosine_similarity < 0.35，window=3）
  4. 累積字數觸發（200 字 → drift + stalemate 分析）

執行：
    cd backend
    python scripts/test_real_conversation.py
"""

import sys
import os
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from chat.services.emotion import analyze_emotion
from chat.services.embedding import cosine_similarity, get_embedding
from chat.services.filter import check_content_sync

EMOTION_THRESHOLD  = 0.65
SECOND_PERSON      = {"你", "您", "妳"}
TOPIC_THRESHOLD    = 0.35
TOPIC_ANCHOR_TEXT  = "台灣是否應該擴大發展核能發電"
CHAR_TRIGGER       = 200
WINDOW             = 3

# ── Benjamin 的實際輸入（依對話順序） ─────────────────────────────────
MESSAGES = [
    "其他能源方案的選擇，使用成本將大過於核能方案的選擇，舉凡：使用火力發電將立即對空氣品質造成汙染、水力發電有區域性限制、風力發電轉換效率差、離岸風機維修成本過高、太陽能發電爭議多佔土地等問題。上述的理由造成了核能發電是一個只要有燃料棒就可以產電的這個能源選項，是一個相對妥善的選項，沒有立即性的問題，XD只有遺留問題，沒有關於難以大的系統。沒有過高的成本等",
    "你說的沒錯，但人類最擅長的事就是只要是未來的問題，那通通都假設看不見，勇敢的人先享受當下，正如20世紀的塑膠製品的創造，就算會造成汙染，但是反正方便，所以到現在為止大量使用，18世紀的工業革命，英國因為蒸汽機，整個城市烏煙瘴氣，但是沒關係，有錢有便利，所以一去不回頭，人類的邏輯不就是這樣啊?",
    "說實話，我自己搞不太清楚我自己是支持亦或是反對，我知道核能的風險，但我更清楚，台灣目前的能源狀況，說實話，更糟的是，這樣的能源狀況並非是民眾的問題，而是企業與工廠所導致的，同時台電的低電價，也導致政府需補貼台灣的能源費用，必須說，台灣當今的能源局勢是個爛攤子",
    "這是個很幹的問題，缺電缺的是工業用電，而非民生用電，電價補貼政策會造成民眾對於執政黨的不滿，倘若你認真問我，我會想先處理後者，但這樣會導致財團對於執政黨的不支持，XD我只能說這樣聊下去會跑題了，你認我是支持核電的一方，亦或是反對的一方，因為我自己也搞不清楚?",
    "沒錯，我確實是屬於這方，回到核廢料的問題上吧，好像至今為止，我還沒聽人量化說過核廢料的問題，方便跟我說明下?",
    "痾，話說這東西埋起來就埋起來了，還能有甚麼問題?阿不能大不了就直接馬斯克火箭一發送上太空丟進太陽核融合掉麻?",
    "哦，我還得考慮1萬年後人類的死活啊?",
    "好吧這確實聽起來像個倫理議題，阿所以，我跟你討論可以達到甚麼去極化的效果，我感覺我也沒甚麼極化",
    "所以，感覺討論差不多了?",
]


def has_second_person(text: str) -> bool:
    return any(p in text for p in SECOND_PERSON)


def main():
    print("=" * 72)
    print("Take A Bridge — 真實對話輸入全管道測試")
    print(f"使用者  : benjamin（核能議題）")
    print(f"訊息數  : {len(MESSAGES)} 則")
    print(f"議題錨點: {TOPIC_ANCHOR_TEXT}")
    print("=" * 72)

    print("\n正在載入模型與錨點 embedding…")
    anchor_emb = get_embedding(TOPIC_ANCHOR_TEXT)

    # ── 逐則分析 ────────────────────────────────────────────────────
    print("\n" + "─" * 72)
    print("逐則管道分析")
    print("─" * 72)

    cumulative_chars = 0
    analysis_triggered_at = []
    embeddings = []

    for i, msg in enumerate(MESSAGES, 1):
        print(f"\n【訊息 {i}】({len(msg)} 字)")
        print(f"  {msg[:72]}{'…' if len(msg) > 72 else ''}")

        # Stage 1: 黑名單過濾
        try:
            filter_result = check_content_sync(msg)
        except Exception:
            filter_result = {"is_blocked": False}

        if filter_result["is_blocked"]:
            print(f"  ├ 黑名單   : ✗ 攔截 → 訊息不送出，管道中止")
            continue
        else:
            print(f"  ├ 黑名單   : ✓ 通過")

        # Stage 2: 情緒分析
        emotion = analyze_emotion(msg)
        score = emotion["score"]
        has_2p = has_second_person(msg)
        emotion_triggered = score >= EMOTION_THRESHOLD and has_2p

        emotion_verdict = ""
        if emotion_triggered:
            emotion_verdict = f"⚡ 觸發改述建議（score={score:.4f}, 含第二人稱）"
        elif score >= EMOTION_THRESHOLD and not has_2p:
            emotion_verdict = f"分數 {score:.4f} 超標但無第二人稱 → 放行"
        else:
            emotion_verdict = f"✓ 放行（score={score:.4f}）"

        print(f"  ├ 情緒偵測 : {emotion_verdict}")

        # Stage 3: 離題偵測（window=3 平均）
        emb = get_embedding(msg)
        embeddings.append(emb)
        window_embs = embeddings[-WINDOW:]
        mean_emb = np.mean([np.array(e, dtype=np.float32) for e in window_embs], axis=0)
        topic_score = round(float(cosine_similarity(mean_emb, anchor_emb)), 4)
        is_off_topic = topic_score < TOPIC_THRESHOLD

        topic_verdict = ""
        if is_off_topic:
            topic_verdict = f"⚡ 觸發回題建議（window均值={topic_score:.4f} < {TOPIC_THRESHOLD}）"
        else:
            topic_verdict = f"✓ 在題（window均值={topic_score:.4f}）"
        print(f"  ├ 離題偵測 : {topic_verdict}")

        # Stage 4: 累積字數
        cumulative_chars += len(msg)
        print(f"  └ 累積字數 : {cumulative_chars} 字", end="")
        if cumulative_chars >= CHAR_TRIGGER and (not analysis_triggered_at or analysis_triggered_at[-1] < i):
            analysis_triggered_at.append(i)
            over = cumulative_chars - CHAR_TRIGGER
            print(f"  → ⚡ 觸發準即時分析（drift + stalemate）超過 {CHAR_TRIGGER} 字")
            cumulative_chars = 0  # reset after trigger
        else:
            print()

    # ── 彙整報告 ────────────────────────────────────────────────────
    print("\n" + "=" * 72)
    print("彙整報告")
    print("=" * 72)

    # 重新跑一次收集數據
    emotions = [analyze_emotion(m) for m in MESSAGES]
    topic_scores = []
    embs_recap = []
    for m in MESSAGES:
        e = get_embedding(m)
        embs_recap.append(e)
        win = embs_recap[-WINDOW:]
        mean = np.mean([np.array(x, dtype=np.float32) for x in win], axis=0)
        topic_scores.append(round(float(cosine_similarity(mean, anchor_emb)), 4))

    print(f"\n{'#':<3} {'情緒分':>7}  {'二人':>4}  {'觸發':>6}  {'離題分':>7}  {'離題':>6}  摘要")
    print("-" * 72)
    for i, (msg, emo, ts) in enumerate(zip(MESSAGES, emotions, topic_scores), 1):
        s = emo["score"]
        h2p = has_second_person(msg)
        e_trig = "⚡改述" if (s >= EMOTION_THRESHOLD and h2p) else "　　  "
        t_trig = "⚡回題" if ts < TOPIC_THRESHOLD else "　　  "
        p2_str = "有" if h2p else "無"
        print(f"{i:<3} {s:>7.4f}  {p2_str:>4}  {e_trig}  {ts:>7.4f}  {t_trig}  {msg[:22]}…")

    # 統計
    e_triggered = sum(1 for emo, m in zip(emotions, MESSAGES)
                      if emo["score"] >= EMOTION_THRESHOLD and has_second_person(m))
    t_triggered = sum(1 for ts in topic_scores if ts < TOPIC_THRESHOLD)

    print(f"\n  情緒改述觸發 : {e_triggered} 次 / {len(MESSAGES)} 則")
    print(f"  離題回題觸發 : {t_triggered} 次 / {len(MESSAGES)} 則")
    print(f"  準即時分析   : 在訊息 {analysis_triggered_at} 處觸發（累積 {CHAR_TRIGGER} 字）")

    print("\n模型行為觀察：")
    for i, (msg, emo, ts) in enumerate(zip(MESSAGES, emotions, topic_scores), 1):
        s = emo["score"]
        h2p = has_second_person(msg)
        notes = []
        if s >= EMOTION_THRESHOLD and not h2p:
            notes.append(f"情緒分 {s:.4f} 超標，但無第二人稱故未攔截")
        if ts < TOPIC_THRESHOLD:
            notes.append(f"離題分 {ts:.4f}，視窗均值低於閾值")
        if notes:
            print(f"  訊息{i}: {' / '.join(notes)}")


if __name__ == "__main__":
    main()
