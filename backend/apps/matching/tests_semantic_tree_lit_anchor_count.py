"""Unit tests for get_lit_anchor_count / get_ai_lit_anchor_count — the
read-only lookup that sources the settlement radar's「廣度」axis (see
docs/settlement_radar.md §5).

Not to be confused with get_lit_node_count: that one counts the outermost
說法 nodes (滿分 36); this one counts how many of the 6 depth-1 大分類
anchors have been lit (滿分 6).
"""

from copy import deepcopy

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from api.models import DialogueMatch
from apps.matching.services.semantic_tree import (
    AI_TREE_MODE,
    FIXED_ANCHORS,
    MATCH_TREE_MODE,
    OWNER_AI_USER,
    OWNER_USER_A,
    OWNER_USER_B,
    SEMANTIC_TREE_STATE_VERSION,
    apply_analysis_items_to_tree,
    create_owner_tree_state,
    get_ai_lit_anchor_count,
    get_lit_anchor_count,
    owner_key_for_user,
)

User = get_user_model()


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
    apply_analysis_items_to_tree(
        owner_state["treeData"], [item], source_message={"id": source_message_id}
    )
    owner_state["analysisHistory"].append(
        {"sourceId": source_message_id, "analyzedAt": timezone.now().isoformat()}
    )


class GetLitAnchorCountTests(TestCase):
    def setUp(self):
        self.user_a = User.objects.create_user(username="anchor_a", password="x")
        self.user_b = User.objects.create_user(username="anchor_b", password="x")
        self.match = DialogueMatch.objects.create(
            topic_id=102,
            user_a=self.user_a,
            user_b=self.user_b,
            user_a_score=6.0,
            user_b_score=2.0,
            room_id="test-room-lit-anchor-count",
        )

        owner_a_state = create_owner_tree_state(OWNER_USER_A, "核電")
        # Two distinct 說法 nodes but under the *same* anchor → 1 lit anchor.
        _analyze(owner_a_state, _item("核四延役安全爭議", "核四延役爭議", "反對", "anchor_safety"), "msg_001")
        _analyze(owner_a_state, _item("耐震係數不足", "耐震設計爭議", "反對", "anchor_safety"), "msg_002")
        # A second anchor → 2 lit anchors total.
        _analyze(owner_a_state, _item("延役成本過高", "延役成本爭議", "反對", "anchor_economy"), "msg_003")

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

    def test_counts_distinct_lit_anchors_not_leaf_nodes(self):
        count = get_lit_anchor_count(self.match, owner_key=OWNER_USER_A)
        self.assertEqual(count, 2)

    def test_returns_zero_for_owner_with_no_analysis(self):
        count = get_lit_anchor_count(self.match, owner_key=OWNER_USER_B)
        self.assertEqual(count, 0)

    def test_never_exceeds_the_six_fixed_anchors(self):
        owner_a_state = self.match.stats["semantic_tree"]["participants"][OWNER_USER_A]
        for i, anchor_id in enumerate(
            [a["id"] for a in FIXED_ANCHORS] + ["anchor_safety"]
        ):
            _analyze(
                owner_a_state,
                _item(f"claim {i}", f"point {i}", "反對", anchor_id),
                f"fill_{i}",
            )
        self.match.save()
        count = get_lit_anchor_count(self.match, owner_key=OWNER_USER_A)
        self.assertEqual(count, 6)

    def test_tolerates_a_missing_or_malformed_state(self):
        self.match.stats = {}
        self.match.save()
        self.assertEqual(
            get_lit_anchor_count(self.match, owner_key=OWNER_USER_A), 0
        )

    def test_owner_key_for_user_maps_participants(self):
        self.assertEqual(
            owner_key_for_user(self.match, self.user_a.id), OWNER_USER_A
        )
        self.assertEqual(
            owner_key_for_user(self.match, self.user_b.id), OWNER_USER_B
        )


class GetAiLitAnchorCountTests(TestCase):
    def _session_record(self, owner_state):
        return {
            "topic_id": 102,
            "semantic_tree": {
                "version": SEMANTIC_TREE_STATE_VERSION,
                "mode": AI_TREE_MODE,
                "anchors": deepcopy(FIXED_ANCHORS),
                "participants": {OWNER_AI_USER: owner_state},
            },
        }

    def test_counts_lit_anchors_for_the_ai_session_owner(self):
        owner_state = create_owner_tree_state(OWNER_AI_USER, "核電")
        _analyze(owner_state, _item("核廢處置無解", "最終處置爭議", "反對", "anchor_waste"), "t1")
        _analyze(owner_state, _item("除役成本", "除役成本爭議", "反對", "anchor_economy"), "t2")
        _analyze(owner_state, _item("再談核廢", "最終處置爭議", "支持", "anchor_waste"), "t3")

        count = get_ai_lit_anchor_count(self._session_record(owner_state))
        self.assertEqual(count, 2)

    def test_returns_zero_when_no_anchor_lit_yet(self):
        owner_state = create_owner_tree_state(OWNER_AI_USER, "核電")
        count = get_ai_lit_anchor_count(self._session_record(owner_state))
        self.assertEqual(count, 0)

    def test_tolerates_empty_session_record(self):
        self.assertEqual(get_ai_lit_anchor_count({}), 0)
