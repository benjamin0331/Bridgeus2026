"""Unit tests for CCND timeline reconstruction (0707 計劃書, 階段 2) —
pure functions, no DB.

Because nodes never change position once created (only their message
history grows), reconstructing "what the tree looked like at time T" is a
prune operation on the *current* tree, not a full replay from a flattened
event log: drop any node whose messages are all after the cutoff (it
hadn't been created yet), and trim the rest to only messages at/before the
cutoff.
"""
from apps.matching.services.semantic_tree import (
    apply_analysis_items_to_tree,
    create_initial_tree,
    reconstruct_tree_as_of,
    resolve_cutoff_for_message,
)


def _item(claim_text, point_name, stance, anchor_id="anchor_safety"):
    return {
        "claimText": claim_text,
        "anchorId": anchor_id,
        "path": [],
        "pointName": point_name,
        "stance": stance,
        "confidence": 0.9,
        "rationale": "test",
    }


def _build_dialogue_tree():
    """msg_001 creates a node, msg_002 is a stance-update merge on it,
    msg_003 creates a second, unrelated node."""
    tree = create_initial_tree("核電")
    apply_analysis_items_to_tree(
        tree,
        [_item("核四延役安全爭議", "核四延役爭議", "反對")],
        source_message={"id": "msg_001"},
    )
    apply_analysis_items_to_tree(
        tree,
        [_item("安全審查機制嚴謹", "核四延役爭議", "支持")],
        source_message={"id": "msg_002"},
    )
    apply_analysis_items_to_tree(
        tree,
        [_item("深地質處置可行", "深地質處置", "支持", anchor_id="anchor_waste")],
        source_message={"id": "msg_003"},
    )
    return tree


def _find_by_name(tree, name):
    def walk(node):
        for child in node.get("children") or []:
            if child.get("name") == name:
                return child
            found = walk(child)
            if found:
                return found
        return None
    return walk(tree)


def test_cutoff_before_any_message_prunes_everything():
    tree = _build_dialogue_tree()
    snapshot = reconstruct_tree_as_of(tree, "2000-01-01T00:00:00+00:00")

    assert _find_by_name(snapshot, "核四延役爭議") is None
    assert _find_by_name(snapshot, "深地質處置") is None


def test_cutoff_after_first_message_only_shows_first_node_with_original_stance():
    tree = _build_dialogue_tree()
    first_recorded_at = _find_by_name(tree, "核四延役爭議")["messages"][0]["recordedAt"]

    snapshot = reconstruct_tree_as_of(tree, first_recorded_at)

    node = _find_by_name(snapshot, "核四延役爭議")
    assert node is not None
    assert len(node["messages"]) == 1
    assert node["claimText"] == "核四延役安全爭議"
    assert _find_by_name(snapshot, "深地質處置") is None


def test_cutoff_after_stance_update_shows_updated_claim_and_both_messages():
    tree = _build_dialogue_tree()
    second_recorded_at = _find_by_name(tree, "核四延役爭議")["messages"][1]["recordedAt"]

    snapshot = reconstruct_tree_as_of(tree, second_recorded_at)

    node = _find_by_name(snapshot, "核四延役爭議")
    assert len(node["messages"]) == 2
    assert node["claimText"] == "安全審查機制嚴謹"
    assert _find_by_name(snapshot, "深地質處置") is None


def test_cutoff_after_everything_shows_full_tree():
    tree = _build_dialogue_tree()
    last_recorded_at = _find_by_name(tree, "深地質處置")["messages"][0]["recordedAt"]

    snapshot = reconstruct_tree_as_of(tree, last_recorded_at)

    assert _find_by_name(snapshot, "核四延役爭議") is not None
    assert _find_by_name(snapshot, "深地質處置") is not None


def test_reconstruction_does_not_mutate_the_original_tree():
    tree = _build_dialogue_tree()
    first_recorded_at = _find_by_name(tree, "核四延役爭議")["messages"][0]["recordedAt"]

    reconstruct_tree_as_of(tree, first_recorded_at)

    assert _find_by_name(tree, "深地質處置") is not None
    assert len(_find_by_name(tree, "核四延役爭議")["messages"]) == 2


def test_resolve_cutoff_for_message_looks_up_analyzed_at_by_source_id():
    analysis_history = [
        {"sourceId": "msg_001", "analyzedAt": "2026-07-07T10:00:00+00:00"},
        {"sourceId": "msg_002", "analyzedAt": "2026-07-07T10:05:00+00:00"},
    ]

    assert resolve_cutoff_for_message(analysis_history, "msg_002") == "2026-07-07T10:05:00+00:00"


def test_resolve_cutoff_for_message_returns_none_for_unknown_message():
    analysis_history = [{"sourceId": "msg_001", "analyzedAt": "2026-07-07T10:00:00+00:00"}]

    assert resolve_cutoff_for_message(analysis_history, "does_not_exist") is None
