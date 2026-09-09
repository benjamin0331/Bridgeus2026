"""M6 觀點知識庫 pipeline 測試（品質篩選 → 去重 → 寫入）。

改寫自 feat/polarbear 分支的獨立腳本 scripts/test_pipeline.py：原本自己開
psycopg 連線、手寫 SQL 驗證，這裡改用 Django TestCase，走 apps.summary.pipeline
的 ORM 版本，DB 連線/交易由測試框架管理，不需要另外接 pgvector 連線字串。
"""

from django.test import SimpleTestCase, TestCase

from apps.summary.models import DialogueSummary, ViewpointNode
from apps.summary.pipeline.dedup import is_duplicate
from apps.summary.pipeline.quality_filter import (
    extract_valuable_pairs,
    passes_quality_filter,
)
from apps.summary.pipeline.write import write_dialogue_summary, write_viewpoint
from chat.services.embedding import get_embedding


GOOD_DIALOGUE = [
    {"role": "user", "content": "我認為核能發電在當前能源轉型過程中仍有其存在必要性，因為它的碳排放量相對低"},
    {"role": "user", "content": "但是核廢料的處理問題確實是一大隱憂，目前全球都還沒有完善的解決方案"},
    {"role": "user", "content": "不過相比煤炭發電，核能在發電過程中產生的空污明顯少很多"},
    {"role": "user", "content": "台灣地震頻繁也是一個需要考量的安全因素，這點我承認是核能的弱點"},
    {"role": "user", "content": "但從電力穩定性來看，再生能源目前還無法完全取代基載電力"},
    {"role": "user", "content": "所以我認為在儲能技術成熟之前，核能是必要的過渡選項"},
]

SHORT_DIALOGUE = [
    {"role": "user", "content": "核能不好"},
    {"role": "user", "content": "會爆炸"},
    {"role": "user", "content": "不要蓋"},
]

ATTACK_DIALOGUE = GOOD_DIALOGUE + [
    {"role": "user", "content": "你這個白痴根本不懂能源問題"},
]

FAKE_DIALOGUE_SUMMARY = {
    "dialogue_id": "9001",
    "topic_id": 102,
    "summary_text": "使用者討論了核能發電的優缺點，包含碳排放、核廢料處理與安全性等面向，最終認為核能是儲能技術成熟前的必要過渡選項。",
    "side_a_stance": "pro",
    "side_b_stance": "neutral",
    "quality_score": 0.82,
    "stance_shift_magnitude": 0.35,
}

FAKE_VIEWPOINT = {
    "dimension": "anchor_safety",
    "stance_direction": "pro",
    "user_input_text": "核能在低碳排放這個面向確實有其優勢，但安全疑慮不能忽視",
    "ai_response_text": "您提到了核能的兩個核心矛盾：低碳優勢與安全風險，這正是當前辯論的焦點",
    "viewpoint_summary": "使用者承認核能低碳優勢，但對安全性持保留態度",
    "source_message_ids": [101, 102],
    "composite_score": 0.74,
    "score_detail": {"semantic_dist": 0.73, "stance_shift": 0.81, "lexical_richness": 0.65},
}


class QualityFilterTests(TestCase):
    def test_normal_dialogue_passes(self):
        self.assertTrue(passes_quality_filter(GOOD_DIALOGUE))

    def test_short_dialogue_fails(self):
        self.assertFalse(passes_quality_filter(SHORT_DIALOGUE))

    def test_attack_words_fail(self):
        self.assertFalse(passes_quality_filter(ATTACK_DIALOGUE))


class EmbeddingDimensionTests(TestCase):
    def test_embedding_is_384_dim_floats(self):
        emb = get_embedding("核能發電對台灣能源安全的重要性")
        self.assertEqual(len(emb), 384)
        self.assertIsInstance(emb[0], float)


class DedupTests(TestCase):
    def setUp(self):
        self.summary = DialogueSummary.objects.create(
            dialogue_id="[TEST_ONLY]-dedup", topic_id=102,
            side_a_stance="pro", side_b_stance="neutral",
        )
        text_a = "核能發電的碳排放量相對低，是能源轉型的重要選項"
        ViewpointNode.objects.create(
            summary=self.summary, topic_id=102, dimension="anchor_safety",
            stance_direction="pro", user_input_text=text_a,
            embedding=get_embedding(text_a),
        )

    def test_similar_text_is_duplicate(self):
        emb_similar = get_embedding("核能的碳排放量比較低，對能源轉型有幫助")
        is_dup, _ = is_duplicate(emb_similar, topic_id=102, dimension="anchor_safety")
        self.assertTrue(is_dup)

    def test_different_text_is_not_duplicate(self):
        emb_diff = get_embedding("太陽能板的光電轉換效率近年有顯著提升")
        is_dup, _ = is_duplicate(emb_diff, topic_id=102, dimension="anchor_safety")
        self.assertFalse(is_dup)

    def test_rejected_node_is_not_used_as_dedup_baseline(self):
        # setUp 建的那筆節點預設是 PENDING；改成 REJECTED 之後，即使新文字
        # 跟它幾乎一樣，也不該再被判定為重複——被退回的節點不能繼續擋路。
        node = ViewpointNode.objects.get(summary=self.summary)
        node.review_status = ViewpointNode.ReviewStatus.REJECTED
        node.save(update_fields=["review_status"])

        emb_similar = get_embedding("核能的碳排放量比較低，對能源轉型有幫助")
        is_dup, dup_id = is_duplicate(emb_similar, topic_id=102, dimension="anchor_safety")

        self.assertFalse(is_dup)
        self.assertIsNone(dup_id)

    def test_approved_node_is_still_used_as_dedup_baseline(self):
        node = ViewpointNode.objects.get(summary=self.summary)
        node.review_status = ViewpointNode.ReviewStatus.APPROVED
        node.save(update_fields=["review_status"])

        emb_similar = get_embedding("核能的碳排放量比較低，對能源轉型有幫助")
        is_dup, dup_id = is_duplicate(emb_similar, topic_id=102, dimension="anchor_safety")

        self.assertTrue(is_dup)
        self.assertEqual(dup_id, node.id)


class WritePipelineTests(TestCase):
    def test_write_dialogue_summary_roundtrip(self):
        self.assertTrue(passes_quality_filter(GOOD_DIALOGUE))

        summary_id = write_dialogue_summary(FAKE_DIALOGUE_SUMMARY)
        summary = DialogueSummary.objects.get(id=summary_id)

        self.assertEqual(summary.dialogue_id, FAKE_DIALOGUE_SUMMARY["dialogue_id"])
        self.assertEqual(summary.topic_id, FAKE_DIALOGUE_SUMMARY["topic_id"])
        self.assertEqual(summary.side_a_stance, FAKE_DIALOGUE_SUMMARY["side_a_stance"])

    def test_write_viewpoint_then_repeat_increments_citation(self):
        summary_id = write_dialogue_summary(FAKE_DIALOGUE_SUMMARY)
        viewpoint = {**FAKE_VIEWPOINT, "summary_id": summary_id}

        written_first = write_viewpoint(viewpoint)
        node = ViewpointNode.objects.get(summary_id=summary_id)
        self.assertTrue(written_first)
        self.assertEqual(node.citation_count, 0)
        # topic_id 繼承自 summary，不是呼叫端指定的
        self.assertEqual(node.topic_id, FAKE_DIALOGUE_SUMMARY["topic_id"])

        written_second = write_viewpoint(viewpoint)
        node.refresh_from_db()
        self.assertFalse(written_second)
        self.assertEqual(node.citation_count, 1)


class TopicValidationTests(TestCase):
    """觀點知識庫只收錄 TOPIC_CONFIGS 裡已定義的議題（目前是 102、103）。"""

    def test_write_dialogue_summary_rejects_unknown_topic(self):
        with self.assertRaises(ValueError):
            write_dialogue_summary({**FAKE_DIALOGUE_SUMMARY, "topic_id": 9999})

    def test_write_viewpoint_rejects_summary_with_unknown_topic(self):
        # topic_id 由 summary 繼承而來，所以拿一筆繞過驗證手動塞進去的髒資料
        # （模擬舊資料/手動建表）來確認 write_viewpoint 自己也會擋，不會照單全收。
        bad_summary = DialogueSummary.objects.create(
            dialogue_id="[TEST_ONLY]-bad-topic", topic_id=9999,
            side_a_stance="pro", side_b_stance="neutral",
        )
        with self.assertRaises(ValueError):
            write_viewpoint({**FAKE_VIEWPOINT, "summary_id": bad_summary.id})


class DimensionValidationTests(TestCase):
    """dimension 只能是該議題底下 M5 CCND anchors 定義的 anchor id。"""

    def test_write_viewpoint_rejects_unknown_dimension(self):
        summary_id = write_dialogue_summary(FAKE_DIALOGUE_SUMMARY)  # topic_id=102
        with self.assertRaises(ValueError):
            write_viewpoint({**FAKE_VIEWPOINT, "summary_id": summary_id, "dimension": "not_a_real_anchor"})

    def test_write_viewpoint_rejects_dimension_from_other_topic(self):
        # anchor_equality 是 topic 103（兵役議題）底下的 anchor，不屬於 topic 102（核能）。
        summary_id = write_dialogue_summary(FAKE_DIALOGUE_SUMMARY)  # topic_id=102
        with self.assertRaises(ValueError):
            write_viewpoint({**FAKE_VIEWPOINT, "summary_id": summary_id, "dimension": "anchor_equality"})

    def test_write_viewpoint_accepts_valid_anchor_for_topic(self):
        summary_id = write_dialogue_summary(FAKE_DIALOGUE_SUMMARY)  # topic_id=102
        written = write_viewpoint({**FAKE_VIEWPOINT, "summary_id": summary_id, "dimension": "anchor_economy"})
        self.assertTrue(written)

# Step 3（score_and_rank / _select_balanced_by_side）已移除，對應的
# ScoreAndRankBalanceTests 一併刪除。Step 2 通過門檻的配對現在全部原樣進入
# Step 4，沒有排序、截斷或雙方平衡可測。


def _msg(side, content, *, message_id, is_off_topic=False):
    return {
        "side": side,
        "content": content,
        "ccnd_semantic_dist": 0.4,  # 高於 SEMANTIC_DIST_THRESHOLD
        "ccnd_stance_shift": 0.3,
        "message_id": message_id,
        "is_off_topic": is_off_topic,
    }


_LONG_ON_TOPIC = "我認為核能發電在能源轉型過程中仍有必要性因為它的碳排放相對低且發電穩定"
_LONG_REPLY = "我理解你的觀點但核廢料處理問題到現在還沒有讓人放心的解決方案這點很關鍵"


class ExtractValuablePairsGateTests(SimpleTestCase):
    """Step 2 逐則門檻：詞彙豐富度（jieba）與離題旗標。"""

    def test_repetitive_message_is_dropped_by_lexical_richness(self):
        spam = "不要不要不要不要不要不要不要不要不要不要不要不要不要不要不要不要"
        messages = [
            _msg("a", spam, message_id=1),
            _msg("b", _LONG_REPLY, message_id=2),
        ]
        self.assertEqual(extract_valuable_pairs(messages), [])

    def test_off_topic_flagged_message_is_dropped(self):
        messages = [
            _msg("a", _LONG_ON_TOPIC, message_id=1, is_off_topic=True),
            _msg("b", _LONG_REPLY, message_id=2),
        ]
        self.assertEqual(extract_valuable_pairs(messages), [])

    def test_rich_on_topic_message_is_kept(self):
        messages = [
            _msg("a", _LONG_ON_TOPIC, message_id=1),
            _msg("b", _LONG_REPLY, message_id=2),
        ]
        pairs = extract_valuable_pairs(messages)
        self.assertEqual([p["user_message_id"] for p in pairs], [1])

    def test_missing_off_topic_key_is_treated_as_on_topic(self):
        # 舊組資料層若沒帶 is_off_topic，不能因此把發言擋掉。
        message = _msg("a", _LONG_ON_TOPIC, message_id=1)
        del message["is_off_topic"]
        pairs = extract_valuable_pairs([message, _msg("b", _LONG_REPLY, message_id=2)])
        self.assertEqual(len(pairs), 1)
