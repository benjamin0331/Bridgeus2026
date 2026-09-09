"""
觀點知識庫（M6）種子資料腳本。
執行：cd backend && uv run python scripts/seed_knowledge_base.py

用 4 場模擬的 H-H 對話（topic 102 核能 x2、topic 103 女性兵役 x2）跑過完整
pipeline：quality_filter.run_pipeline()（Step 1 品質篩選 → Step 2 擷取配對）
→ write_dialogue_summary() / write_viewpoint()，寫進真正的
DialogueSummary / ViewpointNode 資料表。

正常流程 ViewpointNode 建立後是 PENDING，要走 /viewpoint-review 人工終審才會
變 APPROVED。這裡是測試用種子資料，跑完直接把新建的節點標成 APPROVED、給予不
同的 citation_count，讓 /kb 首頁的「熱門對話 Top 5」排序看起來有變化——不是
真的在模擬審核流程。

重複執行前會先清掉上一次由本腳本建立的資料（dialogue_id 以 [SEED] 開頭），
避免每跑一次就多一份重複資料。
"""

import os
import sys

import django

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "BridgeUs_Django.settings")
django.setup()

from django.utils import timezone

from apps.summary.models import DialogueSummary, ViewpointNode
from apps.summary.pipeline.quality_filter import run_pipeline
from apps.summary.pipeline.write import write_dialogue_summary, write_viewpoint

SEED_PREFIX = "[SEED]"

# ── 對話樣本：每則訊息附上模擬的 CCND 語意距離 / 立場偏移值 ──────────────────
# （正式流程這兩個值是從 MatchStanceDrift / semantic_tree 算出來的，這裡直接
#  給定，因為種子資料不會真的跑一場即時對話。）

DIALOGUES = [
    {
        "dialogue_id": f"{SEED_PREFIX}-nuclear-1",
        "topic_id": 102,
        "side_a_stance": "pro",
        "side_b_stance": "con",
        "anchors": ["anchor_safety", "anchor_waste"],
        "messages": [
            ("a", "我認為核能發電目前仍是台灣能源結構中不可或缺的一環，因為它能提供穩定的基載電力，不像太陽能跟風力會受天氣影響。", 0.28, 0.42),
            ("b", "核能的安全風險不能忽視，福島核災之後全球都在重新評估核電廠的耐震與應變能力，台灣地震頻繁更需要謹慎。", 0.31, 0.55),
            ("a", "我同意安全是核心考量，但現行機組已經加裝多重保護系統，而且除役後的核廢料處理技術也在持續進步。", 0.22, 0.30),
            ("b", "核廢料至今仍缺乏長期最終處置場，這個問題留給下一代承擔，我認為這是核能最大的道德爭議。", 0.35, 0.61),
            ("a", "確實核廢料處置是懸而未決的難題，不過相較於燃煤發電造成的空氣污染，核能在減碳上仍有明顯優勢。", 0.19, 0.25),
            ("b", "減碳固然重要，但我認為應該把資源投入再生能源與儲能系統的研發，長期來看才是更永續的方向。", 0.40, 0.70),
        ],
    },
    {
        "dialogue_id": f"{SEED_PREFIX}-nuclear-2",
        "topic_id": 102,
        "side_a_stance": "pro",
        "side_b_stance": "neutral",
        "anchors": ["anchor_economy", "anchor_energy"],
        "messages": [
            ("a", "從經濟層面來看，核能發電的單位成本其實比許多人想像中更具競爭力，尤其是長期運轉之後的邊際成本很低。", 0.26, 0.38),
            ("b", "但核電廠的除役與核廢料處理成本經常被低估，如果把這些隱藏成本算進去，核能的經濟優勢就沒有那麼明顯。", 0.33, 0.52),
            ("a", "這點我認同，除役成本確實容易被忽略，不過穩定的電力供應對台灣的產業競爭力來說仍然非常關鍵。", 0.21, 0.29),
            ("b", "我認為與其依賴單一電力來源，不如加速發展多元的再生能源組合，分散能源安全的風險。", 0.37, 0.58),
            ("a", "多元能源組合是好方向，但在儲能技術還不夠成熟之前，核能可以作為銜接的過渡電力。", 0.24, 0.33),
            ("b", "只要持續投入研發，儲能技術的進步速度可能比我們預期得更快，不需要把核能視為必要選項。", 0.29, 0.45),
        ],
    },
    {
        "dialogue_id": f"{SEED_PREFIX}-conscription-1",
        "topic_id": 103,
        "side_a_stance": "pro",
        "side_b_stance": "con",
        "anchors": ["anchor_equality", "anchor_autonomy"],
        "messages": [
            ("a", "我支持女性也納入義務兵役範圍，這樣才能真正落實性別平等，國防義務不應該只由男性承擔。", 0.30, 0.44),
            ("b", "性別平等的立場我理解，但女性與男性在體能條件上本來就有差異，直接套用相同的役期安排未必公平。", 0.27, 0.40),
            ("a", "體能標準確實可以依照職務性質分級規劃，但我認為納入義務役本身就是一種尊重女性能力的展現。", 0.23, 0.34),
            ("b", "我比較擔心的是這類政策若缺乏配套，反而會造成訓練資源排擠，影響整體戰力的提升。", 0.32, 0.48),
            ("a", "配套措施當然需要審慎規劃，但我相信只要循序漸進，女性義務役能為國防注入更多元的能量。", 0.20, 0.27),
            ("b", "我認為現階段應該先以自願役方式擴大女性參與，等制度成熟後再考慮全面義務化。", 0.36, 0.56),
        ],
    },
    {
        "dialogue_id": f"{SEED_PREFIX}-conscription-2",
        "topic_id": 103,
        "side_a_stance": "pro",
        "side_b_stance": "neutral",
        "anchors": ["anchor_defense", "anchor_social"],
        "messages": [
            ("a", "從國防戰力的角度來看，擴大兵役基礎能有效提升後備動員的量能，女性納入義務役有其戰略價值。", 0.25, 0.36),
            ("b", "我認為戰力提升不只看人數，訓練品質與裝備投資可能比單純擴大徵兵範圍更關鍵。", 0.34, 0.50),
            ("a", "訓練品質固然重要，但兩者並不衝突，擴大兵源基礎同時搭配裝備升級才是更完整的國防規劃。", 0.22, 0.31),
            ("b", "社會層面也要考量，女性義務役牽涉到家庭照顧分工等現實問題，需要更廣泛的社會溝通。", 0.28, 0.43),
            ("a", "社會溝通的過程確實需要時間，但我認為這正是推動性別平等分工的契機，不該因為困難就迴避。", 0.31, 0.47),
            ("b", "我同意長期方向值得討論，但短期內我還是傾向採取漸進式的政策設計，避免社會衝擊過大。", 0.18, 0.24),
        ],
    },
]


def _clear_previous_seed_data():
    deleted, _ = DialogueSummary.objects.filter(dialogue_id__startswith=SEED_PREFIX).delete()
    print(f"清掉上次的種子資料：{deleted} 筆關聯紀錄（DialogueSummary + 連帶的 ViewpointNode）")


def _side_to_stance(side: str, dialogue: dict) -> str:
    return dialogue["side_a_stance"] if side == "a" else dialogue["side_b_stance"]


def main():
    _clear_previous_seed_data()

    created_node_ids: list[int] = []

    for dialogue in DIALOGUES:
        pipeline_messages = [
            {
                "side": side,
                "content": content,
                "ccnd_semantic_dist": semantic_dist,
                "ccnd_stance_shift": stance_shift,
                "message_id": i,
            }
            for i, (side, content, semantic_dist, stance_shift) in enumerate(dialogue["messages"])
        ]

        # Step 3 已移除：run_pipeline 現在回傳 Step 2 通過門檻的全部配對，
        # 不含 composite_score / score_detail，也沒有 top_n。
        ranked_pairs = run_pipeline(pipeline_messages)
        if not ranked_pairs:
            print(f"⚠ {dialogue['dialogue_id']} 沒有通過品質篩選，略過")
            continue

        summary_id = write_dialogue_summary({
            "dialogue_id": dialogue["dialogue_id"],
            "topic_id": dialogue["topic_id"],
            "summary_text": "".join(m[1] for m in dialogue["messages"]),
            "side_a_stance": dialogue["side_a_stance"],
            "side_b_stance": dialogue["side_b_stance"],
            "quality_score": None,
            "stance_shift_magnitude": round(
                sum(m[3] for m in dialogue["messages"]) / len(dialogue["messages"]), 4
            ),
        })

        anchors = dialogue["anchors"]
        for i, pair in enumerate(ranked_pairs):
            dimension = anchors[i % len(anchors)]
            written = write_viewpoint({
                "summary_id": summary_id,
                "dimension": dimension,
                "speaker_side": pair["speaker_side"],
                "stance_direction": _side_to_stance(pair["speaker_side"], dialogue),
                "user_input_text": pair["user_input_text"],
                "ai_response_text": pair["ai_response_text"],
                "viewpoint_summary": pair["user_input_text"][:40],
                "source_message_ids": [pair["user_message_id"]],
            })
            if written:
                node = ViewpointNode.objects.filter(summary_id=summary_id, dimension=dimension).latest("created_at")
                created_node_ids.append(node.id)
                print(f"  寫入觀點 #{node.id}（{dialogue['dialogue_id']} / {dimension}）")
            else:
                print(f"  ⚠ 與既有節點重複，改累加 citation_count（{dialogue['dialogue_id']} / {dimension}）")

    # 種子資料直接標成已審核通過，並給遞減的 citation_count 讓「熱門對話 Top 5」排序有變化。
    for rank, node_id in enumerate(created_node_ids):
        ViewpointNode.objects.filter(id=node_id).update(
            review_status=ViewpointNode.ReviewStatus.APPROVED,
            reviewed_at=timezone.now(),
            review_notes="seed_knowledge_base.py 種子資料，直接核准。",
            citation_count=max(len(created_node_ids) - rank, 1),
        )

    print("\n" + "=" * 60)
    print(f"完成，共建立並核准 {len(created_node_ids)} 筆觀點節點")
    print("=" * 60)
    print("清掉本次種子資料：")
    print(f'  DialogueSummary.objects.filter(dialogue_id__startswith="{SEED_PREFIX}").delete()')
    print("=" * 60 + "\n")


if __name__ == "__main__":
    main()
