"""Integration test for the M6 "組資料" assembly layer: DialogueMatch/MatchMessage
-> quality_filter.run_pipeline() input. Ties together MatchMessage.embedding
(for ccnd_semantic_dist) and get_lit_node_count (semantic_tree.py, for
ccnd_stance_shift) — both computed as the delta from the same speaker's
previous message, not a cumulative value (see assemble.py module docstring
for why: cumulative values would systematically favor whoever speaks later).
"""

from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone

from api.models import DialogueMatch, MatchMessage
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

        # Orthogonal unit vectors -> cosine_similarity=0 -> cosine distance=1.0,
        # a clean, exact value to assert on.
        embedding_a1 = [1.0] + [0.0] * 383
        embedding_a2 = [0.0, 1.0] + [0.0] * 382
        embedding_b1 = [1.0] + [0.0] * 383

        self.msg_a1 = MatchMessage.objects.create(
            match=self.match,
            sender=self.user_a,
            content="我認為核能發電在當前能源轉型過程中仍有其存在必要性，因為它的碳排放量相對低",
            embedding=embedding_a1,
        )
        self.msg_b1 = MatchMessage.objects.create(
            match=self.match,
            sender=self.user_b,
            content="但是核廢料的處理問題確實是一大隱憂，目前全球都還沒有完善的解決方案",
            embedding=embedding_b1,
        )
        self.msg_a2 = MatchMessage.objects.create(
            match=self.match,
            sender=self.user_a,
            content="核廢料處置技術其實已經有國際上的成功案例可以參考，並非無解的難題",
            embedding=embedding_a2,
        )

        owner_a_state = create_owner_tree_state(OWNER_USER_A, "核電")

        # analyzedAt 要在 apply_analysis_items_to_tree() 呼叫「之後」才取
        # timezone.now()：該函式內部會用自己呼叫當下的時間戳記錄樹節點，
        # 如果 analyzedAt 是預先算好的固定時間，可能早於節點實際寫入的時間，
        # 導致 reconstruct_tree_as_of 用這個 cutoff 重建時，把這則訊息自己
        # 的節點也當成「cutoff 之後才出現」而剪掉。
        apply_analysis_items_to_tree(
            owner_a_state["treeData"],
            [_item("核能低碳排放優勢", "核能低碳優勢", "支持", "anchor_safety")],
            source_message={"id": str(self.msg_a1.id)},
        )
        owner_a_state["analysisHistory"].append(
            {"sourceId": str(self.msg_a1.id), "analyzedAt": timezone.now().isoformat()}
        )

        apply_analysis_items_to_tree(
            owner_a_state["treeData"],
            [_item("核廢料處置有解方", "核廢料處置", "支持", "anchor_waste")],
            source_message={"id": str(self.msg_a2.id)},
        )
        owner_a_state["analysisHistory"].append(
            {"sourceId": str(self.msg_a2.id), "analyzedAt": timezone.now().isoformat()}
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

        self.assertEqual(len(messages), 3)
        self.assertEqual(
            [m["message_id"] for m in messages],
            [self.msg_a1.id, self.msg_b1.id, self.msg_a2.id],
        )

    def test_side_reflects_sender(self):
        messages = build_messages_for_match(self.match)

        self.assertEqual([m["side"] for m in messages], ["a", "b", "a"])

    def test_semantic_dist_is_zero_for_first_message_per_speaker(self):
        messages = build_messages_for_match(self.match)

        # msg_a1 and msg_b1 are each speaker's first message -> no prior
        # same-speaker message to diff against.
        self.assertEqual(messages[0]["ccnd_semantic_dist"], 0.0)
        self.assertEqual(messages[1]["ccnd_semantic_dist"], 0.0)

    def test_semantic_dist_uses_cosine_distance_from_same_speakers_previous_message(self):
        messages = build_messages_for_match(self.match)

        # msg_a2's embedding is orthogonal to msg_a1's (user_a's previous
        # message) -> cosine similarity 0 -> cosine distance 1.0.
        self.assertEqual(messages[2]["ccnd_semantic_dist"], 1.0)

    def test_stance_shift_is_lit_node_delta_not_cumulative_total(self):
        messages = build_messages_for_match(self.match)

        # msg_a1: user_a's first message, cumulative count 1, no prior -> delta 1.
        self.assertEqual(messages[0]["ccnd_stance_shift"], round(100 / MAX_LIT_NODES * 1, 4))
        # msg_b1: user_b has no CCND analysis at all -> 0.
        self.assertEqual(messages[1]["ccnd_stance_shift"], 0.0)
        # msg_a2: cumulative count becomes 2 (1 new node), previous was 1 ->
        # delta 1, NOT the cumulative total of 2.
        self.assertEqual(messages[2]["ccnd_stance_shift"], round(100 / MAX_LIT_NODES * 1, 4))

    def test_output_is_accepted_by_run_pipeline_without_error(self):
        messages = build_messages_for_match(self.match)

        # Only 3 short messages -> fails Step 1's minimum-turns gate, but the
        # point here is that the dict shape run_pipeline() expects is exactly
        # what build_messages_for_match() produces, with no KeyError/crash.
        self.assertEqual(run_pipeline(messages), [])
