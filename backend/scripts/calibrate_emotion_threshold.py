"""
情緒偵測閾值校準腳本 — Take A Bridge H-H 對話模組

【模組定位】
本模組為「AI 改述建議」模組，屬於輔助介入，非強制攔截。
- 觸發時：系統私下向發訊者提供改述建議，對方不會收到原文
- 使用者可選擇接受、自行修改或忽略建議
- 誤觸發（False Positive）：使用者收到不必要的建議 → 點「忽略」，輕微干擾
- 漏觸發（False Negative）：攻擊性發言未被攔截，直接送出 → 對話品質下降

【設計優先順序】
  Recall（攔截到攻擊）> Precision（不誤觸平和句）
  因為漏放攻擊的傷害遠大於多給一次不必要的建議。

【攔截規則】
  負面情緒分數 >= THRESHOLD  AND  發言含第二人稱（你／您／妳）
  ↑ 分數過濾攻擊強度          ↑ 人稱過濾排除事實陳述句

執行：
    cd backend
    python scripts/calibrate_emotion_threshold.py
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from chat.services.emotion import analyze_emotion

THRESHOLD = 0.65          # 目前設定值
SECOND_PERSON = {"你", "您", "妳"}

# -----------------------------------------------------------------------
# 測試語料
# 格式: (分類, 預期結果, 句子)
# 預期: "pass" = 不觸發建議, "block" = 應觸發建議
# -----------------------------------------------------------------------
CORPUS = [
    # ── 正常辯論句 × 有「你」──────────────────────────────────────────
    # 這類句子有第二人稱但屬於正常討論，分數應低於閾值自然放行
    ("正常辯論/有你", "pass", "你說的有些道理，但我覺得再生能源的發展潛力更大。"),
    ("正常辯論/有你", "pass", "你有沒有考慮過從經濟角度來分析這個議題？"),
    ("正常辯論/有你", "pass", "我認同你的部分觀點，但在核廢料問題上我有不同看法。"),
    ("正常辯論/有你", "pass", "你提出的數據很有說服力，不過我想補充幾點。"),
    ("正常辯論/有你", "pass", "你怎麼看待政府目前的能源轉型政策？"),

    # ── 事實陳述 × 無「你」──────────────────────────────────────────
    # 討論負面議題時模型分數可能偏高，但無第二人稱 → 規則直接放行，不進入情緒判斷
    ("事實陳述/無你", "pass", "核能事故的風險不容忽視，車諾比和福島都是前車之鑑。"),
    ("事實陳述/無你", "pass", "這個政策的缺點在於缺乏長期規劃，可能導致能源短缺。"),
    ("事實陳述/無你", "pass", "反對核能的人通常擔心核廢料的長期儲存問題。"),
    ("事實陳述/無你", "pass", "數據顯示台灣的能源自給率長期偏低，這是結構性問題。"),
    ("事實陳述/無你", "pass", "再生能源的間歇性問題至今仍未被有效解決。"),

    # ── 情緒邊界句 × 有「你」──────────────────────────────────────────
    # 有負面情緒但尚未到攻擊程度；若被觸發，使用者收到建議後可選擇忽略，損害有限
    ("情緒邊界/有你", "pass", "你說的這些讓我感到有些困惑，可以再解釋一下嗎？"),
    ("情緒邊界/有你", "pass", "我覺得你的論點跳得有點快，讓我有些跟不上。"),
    ("情緒邊界/有你", "pass", "你似乎沒有考慮到另一方的立場，這樣的討論有點單方面。"),
    ("情緒邊界/有你", "pass", "說真的我有點不耐煩了，你說的跟上次的論點前後矛盾。"),
    ("情緒邊界/有你", "pass", "你這個說法讓人難以信服，能提供數據支持嗎？"),

    # ── 明確攻擊 × 有「你」── 主要偵測目標 ────────────────────────────
    # 直接針對對方人身或言論的攻擊，應觸發 AI 改述建議
    ("攻擊/有你", "block", "你這種說法根本就是在鬼扯，完全沒有邏輯！"),
    ("攻擊/有你", "block", "你說的這些廢話讓人很失望，根本不值得討論。"),
    ("攻擊/有你", "block", "你根本就是在胡說八道，浪費大家的時間。"),
    ("攻擊/有你", "block", "你這個人完全沒有理性，繼續說下去也是白費工夫。"),
    ("攻擊/有你", "block", "你憑什麼這樣說？根本是在惡意誤導大家。"),
    ("攻擊/有你", "block", "你就是喜歡斷章取義，這種行為真的很令人不齒。"),

    # ── 明確攻擊 × 無「你」── 規則已知盲點 ───────────────────────────
    # 無第二人稱的攻擊句由第一道防線（黑名單過濾）負責，情緒模組不介入
    ("攻擊/無你[黑名單]", "block", "這麼簡單的道理都不懂，真的很蠢。"),
    ("攻擊/無你[黑名單]", "block", "說這種話的人根本沒資格討論這個議題。"),
    ("攻擊/無你[黑名單]", "block", "這種邏輯簡直是在開玩笑，浪費所有人的時間。"),
]


def has_second_person(text: str) -> bool:
    return any(p in text for p in SECOND_PERSON)


def is_intercepted(score: float, has_2p: bool, threshold: float) -> bool:
    return score >= threshold and has_2p


def main():
    print("=" * 72)
    print("Take A Bridge — 情緒偵測閾值校準報告")
    print(f"模型 : lxyuan/distilbert-base-multilingual-cased-sentiments-student")
    print(f"規則 : 情緒分數 >= {THRESHOLD}  AND  含第二人稱（你／您／妳）")
    print(f"優先 : Recall（攔截攻擊）> Precision（避免誤觸）")
    print("=" * 72)

    results = []
    for label, expected, text in CORPUS:
        r = analyze_emotion(text)
        score = r["score"]
        has_2p = has_second_person(text)
        caught = is_intercepted(score, has_2p, THRESHOLD)
        results.append((label, expected, text, score, has_2p, caught))

    # ── 詳細分數表 ───────────────────────────────────────────────────
    print(f"\n{'分類':<16} {'分數':>6}  {'二人稱':<5}  {'觸發':<5}  {'結果'}")
    print("-" * 72)
    for label, expected, text, score, has_2p, caught in results:
        p2_str  = "有 ✓" if has_2p else "無  "
        trig    = "觸發 ▶" if caught else "放行  "
        correct = ""
        if expected == "block" and caught:
            correct = "✓ 正確攔截"
        elif expected == "block" and not caught:
            correct = "✗ 漏放"
        elif expected == "pass" and caught:
            correct = "△ 誤觸（可忽略）"
        else:
            correct = "✓ 正確放行"

        print(f"[{label:<14}] {score:.4f}  {p2_str}  {trig}  {correct}")
        print(f"  └ {text[:60]}{'…' if len(text)>60 else ''}")

    # ── 核心指標（目前閾值） ──────────────────────────────────────────
    target_group   = [r for r in results if r[0].startswith("攻擊/有你")]
    blindspot_group = [r for r in results if r[0].startswith("攻擊/無你")]
    pass_group     = [r for r in results if r[1] == "pass"]

    recall_main  = sum(1 for *_, caught in target_group if caught) / len(target_group)
    fp_count     = sum(1 for *_, caught in pass_group if caught)
    fp_rate      = fp_count / len(pass_group)

    print("\n" + "=" * 72)
    print(f"核心指標（閾值 {THRESHOLD}，第二人稱過濾）")
    print("-" * 72)
    print(f"  主要目標（攻擊/有你）攔截率 : {sum(1 for *_,c in target_group if c)}/{len(target_group)}  =  {recall_main*100:.0f}%")
    print(f"  誤觸率（正常句被觸發）      : {fp_count}/{len(pass_group)}  =  {fp_rate*100:.0f}%")
    print(f"  規則盲點（攻擊/無你）       : {len(blindspot_group)} 句，由黑名單過濾模組負責")
    print(f"\n  誤觸後果 : 使用者收到不必要的改述建議 → 點「忽略」即可，體驗影響輕微")
    print(f"  漏放後果 : 攻擊性發言直接送達對方，對話品質受損")

    # ── 各閾值比較（決策依據） ───────────────────────────────────────
    print("\n" + "=" * 72)
    print("閾值比較（輔助決策，第二人稱過濾已套用）")
    print(f"\n{'閾值':>6}  {'攻擊/有你 攔截率':>18}  {'誤觸率':>10}  {'建議'}")
    print("-" * 72)
    notes = {0.60: "誤觸略高", 0.65: "◀ 目前設定", 0.70: "", 0.75: "漏放過多", 0.80: "漏放嚴重"}
    for t in [0.60, 0.65, 0.70, 0.75, 0.80]:
        rec  = sum(1 for l, e, txt, s, h2, _ in results if l.startswith("攻擊/有你") and is_intercepted(s, h2, t))
        fp   = sum(1 for l, e, txt, s, h2, _ in results if e == "pass" and is_intercepted(s, h2, t))
        print(f"  {t:.2f}  {rec}/{len(target_group)} ({rec/len(target_group)*100:3.0f}%)               {fp}/{len(pass_group)} ({fp/len(pass_group)*100:3.0f}%)    {notes.get(t,'')}")

    # ── 漏放明細（主要目標） ──────────────────────────────────────────
    missed = [(label, text, score) for label, expected, text, score, has_2p, caught in results
              if label.startswith("攻擊/有你") and not caught]
    if missed:
        print(f"\n漏放明細（攻擊/有你，{THRESHOLD} 閾值下未攔截）：")
        for label, text, score in missed:
            print(f"  {score:.4f}  {text}")
        print("  → 分數偏低原因：模型為通用情感分析，對含蓄攻擊詞辨識力有限")
        print("  → 此為模型本身限制，可透過補強黑名單或換用攻擊性語言專用模型改善")


if __name__ == "__main__":
    main()
