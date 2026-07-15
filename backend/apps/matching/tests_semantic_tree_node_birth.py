"""Unit tests for the message -> node-birth lookup used by the CCND
timeline feature (0707 計劃書, 階段 1) — pure functions, no DB.

Goal: given a source message id, find every node whose *first* message
(mode == "new") was recorded for that source message, i.e. the nodes that
were "born" when that message was analyzed.
"""
from apps.matching.services.semantic_tree import (
    apply_analysis_items_to_tree,
    create_initial_tree,
    find_nodes_born_from_message,
)


def _apply(tree, claim_text, point_name, stance, source_message_id, anchor_id="anchor_safety"):
    item = {
        "claimText": claim_text,
        "anchorId": anchor_id,
        "path": [],
        "pointName": point_name,
        "stance": stance,
        "confidence": 0.9,
        "rationale": "test",
    }
    source_message = {"id": source_message_id}
    return apply_analysis_items_to_tree(tree, [item], source_message=source_message)


def test_finds_node_born_from_given_message():
    tree = create_initial_tree("核電")
    _apply(tree, "核四延役安全爭議", "核四延役爭議", "反對", "msg_001")

    born = find_nodes_born_from_message(tree, "msg_001")

    assert len(born) == 1
    assert born[0]["name"] == "核四延役爭議"


def test_stance_update_merge_does_not_count_as_a_birth():
    tree = create_initial_tree("核電")
    _apply(tree, "核四延役安全爭議", "核四延役爭議", "反對", "msg_001")
    _apply(tree, "安全審查機制嚴謹", "核四延役爭議", "支持", "msg_002")

    assert find_nodes_born_from_message(tree, "msg_001") != []
    assert find_nodes_born_from_message(tree, "msg_002") == []


def test_returns_empty_list_for_unknown_message_id():
    tree = create_initial_tree("核電")
    _apply(tree, "核四延役安全爭議", "核四延役爭議", "反對", "msg_001")

    assert find_nodes_born_from_message(tree, "does_not_exist") == []


def test_finds_multiple_nodes_born_from_the_same_message():
    tree = create_initial_tree("核電")
    item_safety = {
        "claimText": "核四延役安全爭議",
        "anchorId": "anchor_safety",
        "path": [],
        "pointName": "核四延役爭議",
        "stance": "反對",
        "confidence": 0.9,
        "rationale": "test",
    }
    item_economy = {
        "claimText": "延役成本過高",
        "anchorId": "anchor_economy",
        "path": [],
        "pointName": "延役成本爭議",
        "stance": "反對",
        "confidence": 0.9,
        "rationale": "test",
    }
    apply_analysis_items_to_tree(tree, [item_safety, item_economy], source_message={"id": "msg_001"})

    born = find_nodes_born_from_message(tree, "msg_001")

    assert {n["name"] for n in born} == {"核四延役爭議", "延役成本爭議"}
