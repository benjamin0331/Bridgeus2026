"""Unit tests for the AI-session equivalent of the CCND timeline (pure
functions, no DB) — mirrors tests_semantic_tree_timeline.py but for a
single-participant AI dialogue session instead of a two-person match room.
"""
from apps.matching.services.semantic_tree import (
    apply_analysis_items_to_tree,
    create_owner_tree_state,
    get_ai_semantic_tree_state,
    OWNER_AI_USER,
    semantic_tree_session_timeline_payload,
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


def _build_session_record():
    """One AI turn (id "7") analyzed into the session's tree, one turn
    ("8") not yet analyzed."""
    session_record = {"topic_id": 102, "semantic_tree": None}
    state = get_ai_semantic_tree_state(session_record, root_name="核電")
    session_record["semantic_tree"] = state
    owner_state = state["participants"][OWNER_AI_USER]

    apply_analysis_items_to_tree(
        owner_state["treeData"],
        [_item("核四延役安全爭議", "核四延役爭議", "反對")],
        source_message={"id": "7"},
    )
    owner_state["analysisHistory"].append({"sourceId": "7", "analyzedAt": _recorded_at(owner_state)})
    return session_record


def _recorded_at(owner_state):
    node = owner_state["treeData"]["children"][0]["children"][0]
    return node["messages"][0]["recordedAt"]


def test_returns_snapshot_for_an_analyzed_turn():
    session_record = _build_session_record()

    payload = semantic_tree_session_timeline_payload(
        session_record=session_record,
        session_id="session-abc",
        root_name="核電",
        source_message_id="7",
    )

    assert payload is not None
    assert payload["session_id"] == "session-abc"
    assert payload["room_id"] == "session-abc"
    waste_or_safety_anchor = next(
        a for a in payload["treeData"]["children"] if a["id"] == "anchor_safety"
    )
    assert waste_or_safety_anchor["children"][0]["name"] == "核四延役爭議"


def test_returns_none_for_a_turn_that_has_not_been_analyzed_yet():
    session_record = _build_session_record()

    payload = semantic_tree_session_timeline_payload(
        session_record=session_record,
        session_id="session-abc",
        root_name="核電",
        source_message_id="8",
    )

    assert payload is None
