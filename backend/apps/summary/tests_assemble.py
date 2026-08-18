"""Integration test for the M6 "組資料" assembly layer: DialogueMatch/MatchMessage
-> quality_filter.run_pipeline() input. ccnd_semantic_dist / ccnd_stance_shift are
both per-speaker deltas (see apps.summary.pipeline.assemble module docstring for
why they're deltas and not cumulative values) -- these tests pin that behavior down.
"""

from django.contrib.auth import get_user_model
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

User = get_user_model()

# Orthogonal unit vectors -> cosine_distance(EMB_X, EMB_Y) == 1.0, a clean
# deterministic value for assertions without needing the real embedding model.
EMB_X = [1.0] + [0.0] * 383
EMB_Y = [0.0, 1.0] + [0.0] * 382


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

        self.msg_a1 = MatchMessage.objects.create(
            match=self.match,
            sender=self.user_a,
            content="我認為核能發電在當前能源轉型過程中仍有其存在必要性，因為它的碳排放量相對低",
            embedding=EMB_X,
        )
        self.msg_b1 = MatchMessage.objects.create(
            match=self.match,
            sender=self.user_b,
            content="但是核廢料的處理問題確實是一大隱憂，目前全球都還沒有完善的解決方案",
            # 沒有 embedding：模擬 embedding 服務當下失敗的情況
        )
        self.msg_a2 = MatchMessage.objects.create(
            match=self.match,
            sender=self.user_a,
            content="不過相比煤炭發電，核能在發電過程中產生的空污明顯少很多",
            embedding=EMB_Y,
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
        apply_analysis_items_to_tree(
            owner_a_state["treeData"],
            [
                _item("核能空污較少", "空污較少", "支持", "anchor_safety"),
                _item("核能減碳優勢", "減碳優勢", "支持", "anchor_economy"),
            ],
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

    def test_semantic_dist_is_relative_to_own_previous_message(self):
        messages = build_messages_for_match(self.match)

        # msg_a1 是 user_a 的第一則發言，沒有「自己的上一則」可比。
        self.assertEqual(messages[0]["ccnd_semantic_dist"], 0.0)
        # msg_b1 是 user_b 的第一則發言，同理是 0.0——不會去看 msg_a1 的內容。
        self.assertEqual(messages[1]["ccnd_semantic_dist"], 0.0)
        # msg_a2 對 msg_a1（EMB_Y vs EMB_X 為正交向量）cosine distance = 1.0。
        self.assertEqual(messages[2]["ccnd_semantic_dist"], 1.0)

    def test_stance_shift_is_new_nodes_lit_by_this_message(self):
        messages = build_messages_for_match(self.match)

        # msg_a1 累積點亮 1 個節點，這是 user_a 的第一則發言 -> 新增 1。
        self.assertEqual(messages[0]["ccnd_stance_shift"], round(100 / MAX_LIT_NODES * 1, 4))
        # msg_b1 沒有任何 CCND 分析紀錄。
        self.assertEqual(messages[1]["ccnd_stance_shift"], 0.0)
        # msg_a2 累積點亮到 3 個節點，相對 msg_a1 的 1 個 -> 新增 2 個，不是 3。
        self.assertEqual(messages[2]["ccnd_stance_shift"], round(100 / MAX_LIT_NODES * 2, 4))

    def test_output_is_accepted_by_run_pipeline_without_error(self):
        messages = build_messages_for_match(self.match)

        # 3 則短訊息一樣過不了 Step 1 的最低輪數門檻，重點是 dict 形狀能被
        # run_pipeline() 吃下去，不會 KeyError/crash。
        self.assertEqual(run_pipeline(messages), [])
