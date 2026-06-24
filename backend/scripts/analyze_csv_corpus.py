"""
語料庫數值分析腳本 — BridgeUs H-H 模組

輸入：h:/P_BridgeUS/handmade_without_100_101拷貝.csv（單欄 content，10753 筆）
輸出：h:/P_BridgeUS/corpus_analysis_result.csv

分析項目（每筆）：
  - 情緒分數（negative-class prob）、是否超過閾值、是否含第二人稱、是否觸發改述
  - 離題相似度（cosine_similarity vs 核能錨點）、是否觸發回題

執行（預設抽樣 500 筆）：
    cd backend
    python scripts/analyze_csv_corpus.py

全量（慢，約 1 小時）：
    python scripts/analyze_csv_corpus.py --all

自訂樣本數：
    python scripts/analyze_csv_corpus.py --sample 1000
"""

import sys
import os
import csv
import io
import random
import argparse
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from chat.services.emotion import analyze_emotion
from chat.services.embedding import cosine_similarity, get_embedding

TOPIC_ANCHOR_TEXT  = "台灣是否應該擴大發展核能發電"
EMOTION_THRESHOLD  = 0.65
TOPIC_THRESHOLD    = 0.35
SECOND_PERSON      = {"你", "您", "妳"}

CSV_INPUT  = os.path.join("h:/P_BridgeUS", "handmade_without_100_101拷貝.csv")
CSV_OUTPUT = os.path.join("h:/P_BridgeUS", "corpus_analysis_result.csv")


def has_second_person(text: str) -> bool:
    return any(p in text for p in SECOND_PERSON)


def load_corpus(path: str) -> list[str]:
    with open(path, "rb") as f:
        raw = f.read()
    text = raw.decode("utf-8", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    rows = []
    for row in reader:
        content = (row.get("content") or row.get("﻿content") or "").strip()
        if content:
            rows.append(content)
    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--all", action="store_true", help="全量分析（不抽樣）")
    parser.add_argument("--sample", type=int, default=500, help="抽樣數量（預設 500）")
    parser.add_argument("--seed", type=int, default=42, help="隨機種子（預設 42，確保可重現）")
    args = parser.parse_args()

    print("=" * 68)
    print("BridgeUs — 語料庫數值分析")
    print(f"時間 : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"輸入 : {CSV_INPUT}")
    print(f"輸出 : {CSV_OUTPUT}")
    print("=" * 68)

    print("\n載入語料庫…")
    corpus = load_corpus(CSV_INPUT)
    print(f"總筆數：{len(corpus)}")

    if args.all:
        sample = corpus
        print(f"模式：全量分析（{len(sample)} 筆）")
    else:
        n = min(args.sample, len(corpus))
        random.seed(args.seed)
        sample = random.sample(corpus, n)
        print(f"模式：隨機抽樣（seed={args.seed}，{n} 筆）")

    print("\n載入模型與錨點 embedding…")
    anchor_emb = get_embedding(TOPIC_ANCHOR_TEXT)
    print("模型載入完成，開始分析…\n")

    results = []
    start = time.time()

    for i, content in enumerate(sample, 1):
        # 情緒分析
        emo = analyze_emotion(content)
        score      = emo["score"]
        label      = emo["label"]
        over_thresh = emo["is_over_threshold"]
        has_2p     = has_second_person(content)
        trigger_rephrase = over_thresh and has_2p

        # 離題分析（單句）
        emb = get_embedding(content)
        sim = round(float(cosine_similarity(emb, anchor_emb)), 4)
        off_topic = sim < TOPIC_THRESHOLD

        results.append({
            "content":           content[:200],  # 截斷避免 CSV 過寬
            "emotion_score":     round(score, 4),
            "emotion_label":     label,
            "emotion_over_threshold": over_thresh,
            "has_second_person": has_2p,
            "trigger_rephrase":  trigger_rephrase,
            "topic_similarity":  sim,
            "is_off_topic":      off_topic,
        })

        # 進度
        if i % 50 == 0 or i == len(sample):
            elapsed = time.time() - start
            eta = elapsed / i * (len(sample) - i)
            print(f"  {i:>5}/{len(sample)}  elapsed={elapsed:.0f}s  eta={eta:.0f}s")

    # 寫出 CSV
    fieldnames = [
        "content", "emotion_score", "emotion_label",
        "emotion_over_threshold", "has_second_person", "trigger_rephrase",
        "topic_similarity", "is_off_topic",
    ]
    with open(CSV_OUTPUT, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results)

    print(f"\n結果已寫入：{CSV_OUTPUT}")

    # 彙整統計
    n = len(results)
    e_over   = sum(1 for r in results if r["emotion_over_threshold"])
    e_trig   = sum(1 for r in results if r["trigger_rephrase"])
    has2p    = sum(1 for r in results if r["has_second_person"])
    off      = sum(1 for r in results if r["is_off_topic"])
    scores   = [r["emotion_score"] for r in results]
    sims     = [r["topic_similarity"] for r in results]

    print("\n" + "=" * 68)
    print("彙整統計")
    print("=" * 68)
    print(f"\n樣本數                       : {n}")
    print(f"\n【情緒分析】")
    print(f"  情緒分 avg / min / max      : {sum(scores)/n:.4f} / {min(scores):.4f} / {max(scores):.4f}")
    print(f"  超閾值（>= {EMOTION_THRESHOLD}）          : {e_over}/{n} ({e_over/n*100:.1f}%)")
    print(f"  含第二人稱                  : {has2p}/{n} ({has2p/n*100:.1f}%)")
    print(f"  實際觸發改述（超閾+有你）   : {e_trig}/{n} ({e_trig/n*100:.1f}%)")

    # 情緒分布分桶
    buckets = [(0.0,0.2),(0.2,0.4),(0.4,0.6),(0.6,0.8),(0.8,1.0)]
    print(f"\n  分數分布：")
    for lo, hi in buckets:
        cnt = sum(1 for s in scores if lo <= s < hi)
        bar = "█" * int(cnt / n * 40)
        print(f"    {lo:.1f}-{hi:.1f}  {cnt:>5} ({cnt/n*100:4.1f}%)  {bar}")

    print(f"\n【離題分析】")
    print(f"  相似度 avg / min / max      : {sum(sims)/n:.4f} / {min(sims):.4f} / {max(sims):.4f}")
    print(f"  判定離題（< {TOPIC_THRESHOLD}）          : {off}/{n} ({off/n*100:.1f}%)")

    sim_buckets = [(0.0,0.2),(0.2,0.35),(0.35,0.5),(0.5,0.7),(0.7,1.0)]
    print(f"\n  相似度分布（閾值 {TOPIC_THRESHOLD} 以下為離題）：")
    for lo, hi in sim_buckets:
        cnt = sum(1 for s in sims if lo <= s < hi)
        label_str = " ← 離題區" if hi <= TOPIC_THRESHOLD else ""
        bar = "█" * int(cnt / n * 40)
        print(f"    {lo:.2f}-{hi:.2f}  {cnt:>5} ({cnt/n*100:4.1f}%)  {bar}{label_str}")

    total_time = time.time() - start
    print(f"\n總耗時：{total_time:.1f} 秒（{total_time/n:.2f} 秒/筆）")


if __name__ == "__main__":
    main()
