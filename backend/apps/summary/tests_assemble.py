"""Integration test for the M6 "組資料" assembly layer: DialogueMatch/MatchMessage
-> quality_filter.run_pipeline() input. Ties together get_message_drift_value
(hh_analysis.py) and get_lit_node_count (semantic_tree.py), which previously had
no caller anywhere in the project.
"""

from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone

from api.models import DialogueMatch, MatchMessage, MatchStanceDrift
from apps.matching.services.semantic_tree import (
    OWNER_USER_A,
    apply_analysis_items_to_tree,
    create_owner_tree_state,
)
from apps.summary.pipeline.assemble import MAX_LIT_NODES, build_messages_for_match
from apps.summary.pipeline.quality_filter import run_pipeline


def _item(claim_text, point_name, stance, anchor_id):
    return {
        "claimText": claim_text,
        "anchorId": anchor_id,
        "path": [],
        "pointName": point_name,
        "stance": stance,
        "confidence": 0.9,
        "rationale": "test",
    }


class BuildMessagesForMatchTests(TestCase):
    def setUp(self):
        self.user_a = User.objects.create_user(username="user_a", password="x")
        self.user_b = User.objects.create_user(username="user_b", password="x")
        self.match = DialogueMatch.objects.create(
            topic_id=102,
            user_a=self.user_a,
            user_b=self.user_b,
            user_a_score=6.0,
            user_b_score=2.0,
            room_id="test-room-assemble",
        )

        # user_a already had one drift recorded before any of these messages.
        MatchStanceDrift.objects.create(match=self.match, user=self.user_a, drift_value=0.2222)

        self.msg_a1 = MatchMessage.objects.create(
            match=self.match,
            sender=self.user_a,
            content="我認為核能發電在當前能源轉型過程中仍有其存在必要性，因為它的碳排放量相對低",
        )
        self.msg_b1 = MatchMessage.objects.create(
            match=self.match,
            sender=self.user_b,
            content="但是核廢料的處理問題確實是一大隱憂，目前全球都還沒有完善的解決方案",
        )

        owner_a_state = create_owner_tree_state(OWNER_USER_A, "核電")
        apply_analysis_items_to_tree(
            owner_a_state["treeData"],
            [_item("核能低碳排放優勢", "核能低碳優勢", "支持", "anchor_safety")],
            source_message={"id": str(self.msg_a1.id)},
        )
        owner_a_state["analysisHistory"].append(
            {"sourceId": str(self.msg_a1.id), "analyzedAt": timezone.now().isoformat()}
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

    def test_builds_one_dict_per_message_in_order(self):
        messages = build_messages_for_match(self.match)

        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[0]["message_id"], self.msg_a1.id)
        self.assertEqual(messages[1]["message_id"], self.msg_b1.id)

    def test_side_reflects_sender(self):
        messages = build_messages_for_match(self.match)

        self.assertEqual(messages[0]["side"], "a")
        self.assertEqual(messages[1]["side"], "b")

    def test_semantic_dist_uses_hh_analysis_drift(self):
        messages = build_messages_for_match(self.match)

        # user_a: the drift recorded before msg_a1 is the latest known value at that point.
        self.assertEqual(messages[0]["ccnd_semantic_dist"], 0.2222)
        # user_b: never had a drift recorded.
        self.assertEqual(messages[1]["ccnd_semantic_dist"], 0.0)

    def test_stance_shift_uses_ccnd_lit_node_count(self):
        messages = build_messages_for_match(self.match)

        # user_a lit exactly 1 node as of msg_a1 -> 100/36 * 1.
        self.assertEqual(messages[0]["ccnd_stance_shift"], round(100 / MAX_LIT_NODES, 4))
        # user_b has no CCND analysis at all.
        self.assertEqual(messages[1]["ccnd_stance_shift"], 0.0)

    def test_output_is_accepted_by_run_pipeline_without_error(self):
        messages = build_messages_for_match(self.match)

        # Only 2 short messages -> fails Step 1's minimum-turns gate, but the
        # point here is that the dict shape run_pipeline() expects is exactly
        # what build_messages_for_match() produces, with no KeyError/crash.
        self.assertEqual(run_pipeline(messages), [])
