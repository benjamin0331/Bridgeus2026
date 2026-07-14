"""Unit tests for node-creation metadata (created_at / message_id) — pure
functions, no DB.

Every node created by apply_analysis_items_to_tree() (both the leaf point
node and any intermediate category node built to satisfy `path`) should
record when it was created and which chat message triggered it, directly
on the node — not just buried in a leaf node's `messages` history, since
category nodes never get a `messages` entry at all.
"""
from api.dialogue_topics import get_topic_anchors
from apps.matching.services.semantic_tree import (
    apply_analysis_items_to_tree,
    create_initial_tree,
    find_node_by_id,
)

NUCLEAR_ANCHORS = get_topic_anchors(102)


def test_new_point_node_records_created_at_and_message_id():
    tree = create_initial_tree("核電", NUCLEAR_ANCHORS)
    item = {
        "claimText": "核四延役安全爭議",
        "anchorId": "anchor_safety",
        "path": [],
        "pointName": "核四延役爭議",
        "stance": "反對",
        "confidence": 0.9,
        "rationale": "test",
    }

    result = apply_analysis_items_to_tree(tree, [item], source_message={"id": 42})

    node = find_node_by_id(tree, result["appliedItems"][0]["nodeId"])
    assert node["message_id"] == "42"
    assert node["created_at"]  # non-empty ISO timestamp


def test_new_category_node_also_records_created_at_and_message_id():
    tree = create_initial_tree("核電", NUCLEAR_ANCHORS)
    item = {
        "claimText": "地下處置已證實可行",
        "anchorId": "anchor_waste",
        "path": ["處置方案"],
        "pointName": "瑞典地下處置方案",
        "stance": "支持",
        "confidence": 0.9,
        "rationale": "test",
    }

    apply_analysis_items_to_tree(tree, [item], source_message={"id": 7})

    anchor = find_node_by_id(tree, "anchor_waste")
    category_node = next(child for child in anchor["children"] if child["name"] == "處置方案")
    assert category_node["message_id"] == "7"
    assert category_node["created_at"]


def test_merging_into_an_existing_node_does_not_change_its_original_creation_metadata():
    tree = create_initial_tree("核電", NUCLEAR_ANCHORS)
    item = {
        "claimText": "核四延役安全爭議",
        "anchorId": "anchor_safety",
        "path": [],
        "pointName": "核四延役爭議",
        "stance": "反對",
        "confidence": 0.9,
        "rationale": "test",
    }
    result = apply_analysis_items_to_tree(tree, [item], source_message={"id": "msg_001"})
    node_id = result["appliedItems"][0]["nodeId"]
    original_created_at = find_node_by_id(tree, node_id)["created_at"]

    stance_update = {**item, "claimText": "安全審查機制嚴謹", "stance": "支持"}
    apply_analysis_items_to_tree(tree, [stance_update], source_message={"id": "msg_002"})

    node = find_node_by_id(tree, node_id)
    assert node["created_at"] == original_created_at
    assert node["message_id"] == "msg_001"


def test_missing_source_message_leaves_message_id_blank():
    tree = create_initial_tree("核電", NUCLEAR_ANCHORS)
    item = {
        "claimText": "核四延役安全爭議",
        "anchorId": "anchor_safety",
        "path": [],
        "pointName": "核四延役爭議",
        "stance": "反對",
        "confidence": 0.9,
        "rationale": "test",
    }

    result = apply_analysis_items_to_tree(tree, [item], source_message=None)

    node = find_node_by_id(tree, result["appliedItems"][0]["nodeId"])
    assert node["message_id"] == ""
    assert node["created_at"]
