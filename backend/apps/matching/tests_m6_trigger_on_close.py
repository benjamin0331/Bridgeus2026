"""Tests that closing a DialogueMatch (either side leaving, or the idle-timeout
auto-close) triggers the M6 觀點知識庫 pipeline exactly once, after commit, and
that a pipeline failure never blocks the match from closing.
"""

from unittest import mock

from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone

from api.models import DialogueMatch, MatchMessage, MatchStanceDrift
from apps.matching.services.matcher import close_match
from apps.matching.services.semantic_tree import (
    OWNER_USER_A,
    apply_analysis_items_to_tree,
    create_owner_tree_state,
)
from apps.summary.models import DialogueSummary, ViewpointNode

# Six real turns, alternating sides, each comfortably over the 30-char /
# quality-filter thresholds so Step 1 passes and message index 2 (side "a")
# clears Step 2's MIN_LENGTH + SEMANTIC_DIST_THRESHOLD gates.
_TURNS = [
    ("a", "我認為核能發電在當前能源轉型過程中仍然有其存在的必要性，因為它的碳排放量相對其他化石燃料要來得低很多。"),
    ("b", "但是核廢料的最終處置問題確實是一大隱憂，目前全世界都還沒有找到真正完善且長久的解決方案，這點必須正視。"),
    ("a", "核能安全性在台灣地震頻繁的自然環境下，也是需要非常審慎評估的重要面向，絕對不能輕易忽視這個風險。"),
    ("b", "核廢料的長期管理責任不應該留給下一代承擔，這是世代正義的重要課題，值得我們深入討論與思考。"),
    ("a", "從供電穩定的角度來看，再生能源目前還沒辦法完全取代核能作為基載電力的角色，仍需要時間發展。"),
    ("b", "儲能技術與智慧電網的發展其實已經逐漸成熟，未來完全可以支撐再生能源成為穩定的基載選項。"),
]


class TriggerM6PipelineOnCloseTests(TestCase):
    def setUp(self):
        self.user_a = User.objects.create_user(username="user_a", password="x")
        self.user_b = User.objects.create_user(username="user_b", password="x")
        self.match = DialogueMatch.objects.create(
            topic_id=102,
            user_a=self.user_a,
            user_b=self.user_b,
            user_a_score=6.0,
            user_b_score=2.0,
            room_id="test-room-m6-trigger",
        )

        messages = []
        for index, (side, content) in enumerate(_TURNS):
            sender = self.user_a if side == "a" else self.user_b
            if index == 2:
                # Gives message index 2 (side "a") a real drift > SEMANTIC_DIST_THRESHOLD
                # so it clears Step 2 of quality_filter.extract_valuable_pairs().
                MatchStanceDrift.objects.create(
                    match=self.match, user=self.user_a, drift_value=0.5
                )
            messages.append(
                MatchMessage.objects.create(match=self.match, sender=sender, content=content)
            )

        owner_a_state = create_owner_tree_state(OWNER_USER_A, "核電")
        apply_analysis_items_to_tree(
            owner_a_state["treeData"],
            [
                {
                    "claimText": "核能安全性需審慎評估地震風險",
                    "anchorId": "anchor_safety",
                    "path": [],
                    "pointName": "地震風險評估",
                    "stance": "反對",
                    "confidence": 0.9,
                    "rationale": "test",
                }
            ],
            source_message={"id": str(messages[2].id)},
        )
        owner_a_state["analysisHistory"].append(
            {"sourceId": str(messages[2].id), "analyzedAt": timezone.now().isoformat()}
        )
        self.match.stats = {
            "semantic_tree": {
                "version": 2,
                "mode": "participant_trees",
                "anchors": [],
                "participants": {
                    OWNER_USER_A: owner_a_state,
                    "user_b": create_owner_tree_state("user_b", "核電"),
                },
            }
        }
        self.match.save()

    def test_closing_the_match_writes_a_dialogue_summary_and_viewpoint(self):
        with self.captureOnCommitCallbacks(execute=True):
            close_match(match=self.match)

        summary = DialogueSummary.objects.get(dialogue_id=str(self.match.id))
        self.assertEqual(summary.topic_id, 102)

        viewpoint = ViewpointNode.objects.get(summary=summary)
        self.assertEqual(viewpoint.dimension, "anchor_safety")
        self.assertIn("核能安全性", viewpoint.user_input_text)

    def test_match_still_closes_even_if_the_m6_pipeline_blows_up(self):
        def _boom(match_id):
            raise RuntimeError("pretend the pipeline crashed")

        with mock.patch(
            "apps.summary.pipeline.assemble.run_pipeline_for_match", side_effect=_boom
        ):
            with self.captureOnCommitCallbacks(execute=True):
                closed = close_match(match=self.match)

        self.assertEqual(closed.status, DialogueMatch.Status.CLOSED)
        self.assertFalse(DialogueSummary.objects.filter(dialogue_id=str(self.match.id)).exists())
