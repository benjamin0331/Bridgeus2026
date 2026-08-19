"""Live smoke test for semantic_tree.classify_stance_with_openai's prompt —
the 立場分類 Agent prompt at semantic_tree.py:1037-1073 that decides
支持/反對/中立/無關 for a message against a CCND node.
"""

import pytest
from django.test import TestCase

from apps.matching.services import semantic_tree

pytestmark = pytest.mark.live

CURATED_CASES = [
    # --- real anti-nuclear arguments used to hand-check this prompt ---
    (
        "雖然核電廠發生事故的機率極低，但歷史上的切爾諾貝利（Chernobyl）與福島（Fukushima）"
        "核事故證明，一旦發生核洩漏，將造成長達數十年、影響範圍廣大的生態與社會災難。",
        "核能安全",
        "反對",
    ),
    (
        "核電到底哪裡便宜了？把除役、核廢處理的隱藏成本算進去，你敢說它划算嗎？",
        "經濟成本",
        "反對",  # rhetorical question, not a genuine query
    ),
    (
        "核能發電的過程不會排放二氧化碳，這是它相較於火力發電的優勢之一。",
        "環境保護",
        "支持",  # plain, literal support
    ),
    (
        "目前台灣核能發電占總發電量的比例，各界估計數字不太一致。",
        "能源問題",
        "中立",  # factual statement, no discernible lean
    ),
    (
        "今天午餐吃什麼好呢，好餓喔。",
        "核能安全",
        "無關",  # off-topic
    ),
]


class ClassifyStanceWithOpenAIPromptTests(TestCase):

    def test_prints_and_asserts_stance_for_curated_cases(self):
        self.assertTrue(
            semantic_tree.get_openai_api_key(),
            "OPENAI_API_KEY is not configured in backend/.env — stance would "
            "silently fall back to '中立' for every case, making this test "
            "meaningless.",
        )

        mismatches = []
        for text, context_label, expected in CURATED_CASES:
            with self.subTest(text=text):
                stance = semantic_tree.classify_stance_with_openai(
                    text=text, context_label=context_label
                )
                mark = "OK" if stance == expected else "MISMATCH"
                print(
                    f"\n節點:「{context_label}」 預期:{expected}  實際:{stance}  [{mark}]"
                    f"\n輸入: {text}"
                )
                self.assertIn(stance, semantic_tree.STANCE_LABELS)
                if stance != expected:
                    mismatches.append((context_label, expected, stance, text))

        self.assertFalse(
            mismatches,
            "prompt regressed on known-good cases:\n"
            + "\n".join(
                f"  節點「{label}」預期 {exp}，實際 {got} — {text}"
                for label, exp, got, text in mismatches
            ),
        )