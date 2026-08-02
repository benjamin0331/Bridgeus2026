"""Unit tests for get_lit_node_count — the read-only lookup M6's
quality_filter.py uses to source ccnd_stance_shift ("誰的亮點多選誰", see
quality_filter.py module docstring) from the CCND semantic tree.
"""

from copy import deepcopy

from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone

from api.models import DialogueMatch
from apps.matching.services.semantic_tree import (
    FIXED_ANCHORS,
    MATCH_TREE_MODE,
    OWNER_USER_A,
    OWNER_USER_B,
    SEMANTIC_TREE_STATE_VERSION,
    apply_analysis_items_to_tree,
    create_owner_tree_state,
    get_lit_node_count,
)


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


def _analyze(owner_state, item, source_message_id):
    """Mirrors the production sequence in semantic_tree.py: apply the item to
    the tree, then record the analysis in analysisHistory (analyzedAt is always
    taken *after* the tree message's own recordedAt, same as production)."""
    apply_analysis_items_to_tree(
        owner_state["treeData"], [item], source_message={"id": source_message_id}
    )
    owner_state["analysisHistory"].append(
        {"sourceId": source_message_id, "analyzedAt": timezone.now().isoformat()}
    )


class GetLitNodeCountTests(TestCase):
    def setUp(self):
        self.user_a = User.objects.create_user(username="user_a", password="x")
        self.user_b = User.objects.create_user(username="user_b", password="x")
        self.match = DialogueMatch.objects.create(
            topic_id=102,
            user_a=self.user_a,
            user_b=self.user_b,
            user_a_score=6.0,
            user_b_score=2.0,
            room_id="test-room-lit-node-count",
        )

        owner_a_state = create_owner_tree_state(OWNER_USER_A, "核電")
        _analyze(owner_a_state, _item("核四延役安全爭議", "核四延役爭議", "反對", "anchor_safety"), "msg_001")
        _analyze(owner_a_state, _item("延役成本過高", "延役成本爭議", "反對", "anchor_economy"), "msg_002")

        self.match.stats = {
            "semantic_tree": {
                "version": SEMANTIC_TREE_STATE_VERSION,
                "mode": MATCH_TREE_MODE,
                "anchors": deepcopy(FIXED_ANCHORS),
                "participants": {
                    OWNER_USER_A: owner_a_state,
                    OWNER_USER_B: create_owner_tree_state(OWNER_USER_B, "核電"),
                },
            }
        }
        self.match.save()

    def test_counts_only_nodes_lit_as_of_the_given_message(self):
        count = get_lit_node_count(
            self.match, owner_key=OWNER_USER_A, source_message_id="msg_001"
        )
        self.assertEqual(count, 1)

    def test_counts_accumulate_across_later_messages(self):
        count = get_lit_node_count(
            self.match, owner_key=OWNER_USER_A, source_message_id="msg_002"
        )
        self.assertEqual(count, 2)

    def test_returns_zero_for_owner_with_no_analysis(self):
        count = get_lit_node_count(
            self.match, owner_key=OWNER_USER_B, source_message_id="msg_001"
        )
        self.assertEqual(count, 0)

    def test_returns_zero_for_unknown_message_id(self):
        count = get_lit_node_count(
            self.match, owner_key=OWNER_USER_A, source_message_id="does_not_exist"
        )
        self.assertEqual(count, 0)

    def test_repeated_hits_on_the_same_node_are_not_double_counted(self):
        owner_a_state = self.match.stats["semantic_tree"]["participants"][OWNER_USER_A]
        # Second mention of the *same* point node (merge, not a new node).
        _analyze(
            owner_a_state,
            _item("安全審查機制嚴謹", "核四延役爭議", "支持", "anchor_safety"),
            "msg_003",
        )
        self.match.save()

        count = get_lit_node_count(
            self.match, owner_key=OWNER_USER_A, source_message_id="msg_003"
        )
        self.assertEqual(count, 2)  # still just the 2 distinct nodes, not 3
