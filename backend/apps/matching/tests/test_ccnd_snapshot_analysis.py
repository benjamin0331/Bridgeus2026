"""pytest for ccnd_snapshot_analysis — flatten / snapshot split / Jaccard / novelty.

Numeric-correctness tests (not just "it runs") on a synthetic tree built to
mirror the real append schema (semantic_tree._append_node_message):

  - timestamps are generated with ``timezone.now().isoformat()`` (tz-aware),
    NOT hand-typed strings;
  - the hit time-sequence is deliberately NON-uniform (a dense early cluster,
    then sparse, then a late cluster) to exercise count-based snapshot splitting
    independently of wall-clock gaps;
  - dirty boundary cases are baked in: one message MISSING its timestamp, one
    node hit MULTIPLE times, one node nested under a path-category (parent-anchor
    resolution), one message in ``merge-parent`` mode, and a 7th unexpected
    anchor for the anomaly path.

Run from backend/:
    pytest apps/matching/tests/test_ccnd_snapshot_analysis.py -v

NOTE (do not skip once real data exists): these tests validate the maths on a
synthetic tree. A real-data verification pass against a finished conversation is
still owed — see verify step 1.
"""

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.matching.services.ccnd_snapshot_analysis import (
    analyze_conversation_ccnd,
    build_snapshots,
    flatten_state,
    flatten_tree,
    iter_subject_analyses,
    novelty_timeline,
    snapshot_similarity,
)

# node ids used in the fixture tree (owner = "user")
A1 = "user::agent_1"  # 事故風險      (anchor_safety)   — hit x3 timed
A2 = "user::agent_2"  # 發電成本      (anchor_economy)  — hit x2 timed + 1 UNTIMED
A3 = "user::agent_3"  # 供電穩定      (anchor_energy)   — hit x1
A5 = "user::agent_5"  # 延役爭議      (anchor_safety, nested) — hit x3, one merge-parent


@pytest.fixture
def base_time():
    # tz-aware, real "now" — the same clock the live append path uses.
    return timezone.now()


def _msg(base, offset_s, mid, *, participant="user", stance="支持",
         conf=0.8, mode="new", text="claim"):
    """One message record, shaped like semantic_tree._append_node_message output.

    ``offset_s=None`` produces a record with NO sourceTimestamp (dirty case).
    """
    ts = None if offset_s is None else (base + timedelta(seconds=offset_s)).isoformat()
    return {
        "text": text,
        "stance": stance,
        "confidence": conf,
        "rationale": "",
        "mode": mode,
        "recordedAt": timezone.now().isoformat(),
        "source": "ai_user_prompt",
        "sourceMessageId": mid,
        "sourceTimestamp": ts,
        "sourceParticipant": participant,
    }


def _point(node_id, name, messages, children=None):
    return {
        "id": node_id,
        "name": name,
        "type": "point",
        "children": children or [],
        "messages": messages,
    }


def _ai_state(tree_data):
    return {
        "version": 2,
        "mode": "ai_user_tree",
        "anchors": [],
        "participants": {"user": {"ownerKey": "user", "treeData": tree_data}},
    }


@pytest.fixture
def fixture_tree(base_time):
    """Build the H-AI state. Physical order is intentionally scrambled; the true
    order is defined only by the timestamps, so sorting must recover it.

    True time order (t1..t9) -> [A1,A1,A2,A2,A3,A1,A5,A5,A5]:
        t1 A1  @ +0     t4 A2  @ +60    t7 A5  @ +600 (merge-parent)
        t2 A1  @ +1     t5 A3  @ +61    t8 A5  @ +601
        t3 A2  @ +2     t6 A1  @ +62    t9 A5  @ +602
    Non-uniform: dense [0,2]s, gap, [60,62]s, big gap, [600,602]s.
    Plus A2 carries one UNTIMED message (offset None).
    """
    b = base_time
    node_a1 = _point("agent_1", "事故風險", [
        _msg(b, 62, "m6"),   # t6  (out of physical order on purpose)
        _msg(b, 0, "m1"),    # t1
        _msg(b, 1, "m2"),    # t2  (same node hit multiple times)
    ])
    node_a2 = _point("agent_2", "發電成本", [
        _msg(b, 2, "m3"),
        _msg(b, 60, "m4"),
        _msg(b, None, "m_untimed"),  # DIRTY: missing timestamp
    ])
    node_a3 = _point("agent_3", "供電穩定", [
        _msg(b, 61, "m5"),
    ])
    # A5 nested under a mid-level path-category, to test parent-anchor resolution
    node_a5 = _point("agent_5", "延役爭議", [
        _msg(b, 600, "m7", mode="merge-parent"),  # DIRTY: merge-parent mode
        _msg(b, 601, "m8"),
        _msg(b, 602, "m9"),
    ])
    category = {
        "id": "agent_4", "name": "延役議題", "type": "point",
        "children": [node_a5], "messages": [],
    }
    tree_data = {
        "id": "root", "name": "核電", "type": "root",
        "children": [
            {"id": "anchor_safety", "name": "核能安全", "type": "anchor",
             "children": [node_a1, category]},
            {"id": "anchor_economy", "name": "經濟成本", "type": "anchor",
             "children": [node_a2]},
            {"id": "anchor_energy", "name": "能源問題", "type": "anchor",
             "children": [node_a3]},
            # an unused/hidden anchor with no messages — must yield no hits
            {"id": "anchor_waste", "name": "核廢處理", "type": "anchor",
             "hiddenUntilUsed": True, "children": []},
        ],
    }
    return _ai_state(tree_data)


# ─────────────────────────────────────────────────────────────────────────────
# flatten
# ─────────────────────────────────────────────────────────────────────────────

def test_flatten_counts_and_timestamp_coverage(fixture_tree):
    hits = flatten_state(fixture_tree)
    assert len(hits) == 10  # 9 timed + 1 untimed

    parseable = [h for h in hits if h["source_timestamp"] is not None]
    missing = [h for h in hits if h["source_timestamp_raw"] in (None, "")]
    assert len(parseable) == 9
    assert len(missing) == 1
    # the one untimed hit belongs to A2 and is still present
    assert missing[0]["_node_key"] == A2


def test_flatten_resolves_parent_anchor_through_nesting(fixture_tree):
    hits = flatten_state(fixture_tree)
    a5_hits = [h for h in hits if h["_node_key"] == A5]
    assert len(a5_hits) == 3
    # nested two levels under the anchor, yet parent anchor resolves to safety
    assert {h["parent_anchor_id"] for h in a5_hits} == {"anchor_safety"}


def test_flatten_preserves_merge_parent_mode(fixture_tree):
    hits = flatten_state(fixture_tree)
    modes = {h["mode"] for h in hits if h["_node_key"] == A5}
    assert "merge-parent" in modes  # dirty mode is not filtered out


def test_flatten_empty_anchor_yields_no_hits(fixture_tree):
    hits = flatten_state(fixture_tree)
    assert all(h["parent_anchor_id"] != "anchor_waste" for h in hits)


def test_flatten_tree_direct_owner_key():
    tree = {"id": "root", "name": "核電", "type": "root", "children": [
        {"id": "anchor_safety", "name": "核能安全", "type": "anchor",
         "children": [_point("agent_1", "x", [{"sourceTimestamp": None,
                                               "sourceMessageId": "z"}])]},
    ]}
    hits = flatten_tree(tree, owner_key="user_b")
    assert hits[0]["_node_key"] == "user_b::agent_1"


# ─────────────────────────────────────────────────────────────────────────────
# snapshots (count-based split, cumulative)
# ─────────────────────────────────────────────────────────────────────────────

def test_build_snapshots_cumulative_sets(fixture_tree):
    hits = flatten_state(fixture_tree)
    result = build_snapshots(hits, n_segments=3)

    assert result["timed_hit_count"] == 9
    assert result["untimed_hit_count"] == 1

    t1, t2, t3 = result["snapshots"]
    # split 9 hits -> 3/3/3 by COUNT despite non-uniform time gaps
    assert (t1["segment_hit_count"], t2["segment_hit_count"], t3["segment_hit_count"]) == (3, 3, 3)

    assert set(t1["macro_set"]) == {"anchor_safety", "anchor_economy"}
    assert set(t1["micro_set"]) == {A1, A2}

    assert set(t2["macro_set"]) == {"anchor_safety", "anchor_economy", "anchor_energy"}
    assert set(t2["micro_set"]) == {A1, A2, A3}

    # cumulative: T3 keeps everything from T2 and adds A5 (macro already covered)
    assert set(t3["macro_set"]) == {"anchor_safety", "anchor_economy", "anchor_energy"}
    assert set(t3["micro_set"]) == {A1, A2, A3, A5}


def test_build_snapshots_fewer_hits_than_segments():
    # 2 timed hits, 3 segments -> some empty segments, still 3 snapshots
    b = timezone.now()
    tree = _ai_state({"id": "root", "name": "核電", "type": "root", "children": [
        {"id": "anchor_safety", "name": "核能安全", "type": "anchor", "children": [
            _point("agent_1", "a", [_msg(b, 0, "m1")]),
            _point("agent_2", "b", [_msg(b, 5, "m2")]),
        ]},
    ]})
    result = build_snapshots(flatten_state(tree), n_segments=3)
    labels = [s["label"] for s in result["snapshots"]]
    assert labels == ["T1", "T2", "T3"]
    assert result["snapshots"][0]["micro_count"] == 0   # empty first segment
    assert result["snapshots"][-1]["micro_count"] == 2


# ─────────────────────────────────────────────────────────────────────────────
# Metric A — similarity
# ─────────────────────────────────────────────────────────────────────────────

def test_snapshot_similarity_jaccard_and_decomposition(fixture_tree):
    result = build_snapshots(flatten_state(fixture_tree), n_segments=3)
    sim = snapshot_similarity(result)
    p12, p23 = sim["pairs"]

    assert p12["pair"] == "T1-T2"
    assert p12["macro"]["jaccard"] == pytest.approx(2 / 3)
    assert [d["anchor_id"] for d in p12["macro"]["added"]] == ["anchor_energy"]
    assert p12["micro"]["jaccard"] == pytest.approx(2 / 3)
    assert [d["key"] for d in p12["micro"]["added"]] == [A3]

    assert p23["macro"]["jaccard"] == pytest.approx(1.0)   # macro plateaued
    assert p23["macro"]["added"] == []
    assert p23["micro"]["jaccard"] == pytest.approx(3 / 4)
    assert [d["key"] for d in p23["micro"]["added"]] == [A5]

    # cumulative model -> nothing ever dropped -> no anomalies
    assert sim["anomalies"] == []


def test_snapshot_similarity_flags_dropped_anomaly():
    # hand-built NON-cumulative snapshots: a node present in T1 vanishes in T2
    snapshots = {
        "snapshots": [
            {"label": "T1", "macro_set": ["anchor_safety"], "micro_set": ["u::n1", "u::n2"]},
            {"label": "T2", "macro_set": ["anchor_safety"], "micro_set": ["u::n1"]},
        ],
        "node_index": {},
    }
    sim = snapshot_similarity(snapshots)
    assert sim["pairs"][0]["micro"]["dropped_count"] == 1
    assert any(a["type"] == "dropped_in_cumulative_model" for a in sim["anomalies"])


# ─────────────────────────────────────────────────────────────────────────────
# Metric B — novelty
# ─────────────────────────────────────────────────────────────────────────────

def test_novelty_first_appearance_order(fixture_tree):
    nov = novelty_timeline(flatten_state(fixture_tree), n_segments=3)
    order = [(e["key"], e["first_hit_ordinal"]) for e in nov["first_appearance"]]
    assert order == [(A1, 1), (A2, 3), (A3, 5), (A5, 7)]


def test_novelty_per_segment_new_counts(fixture_tree):
    nov = novelty_timeline(flatten_state(fixture_tree), n_segments=3)
    assert nov["per_segment_new_count"]["new_macros"] == [2, 1, 0]
    assert nov["per_segment_new_count"]["new_micros"] == [2, 1, 1]


def test_novelty_hit_frequency_includes_untimed(fixture_tree):
    nov = novelty_timeline(flatten_state(fixture_tree), n_segments=3)
    micro = nov["hit_frequency"]["micro"]
    assert micro[A1]["count"] == 3
    assert micro[A2]["count"] == 3   # 2 timed + 1 untimed counted here
    assert micro[A3]["count"] == 1
    assert micro[A5]["count"] == 3
    # A2 still has timed appearances, so it's not orphaned
    assert nov["nodes_without_timed_appearance"] == []


# ─────────────────────────────────────────────────────────────────────────────
# Step 5 — entry point & dispatch
# ─────────────────────────────────────────────────────────────────────────────

def test_analyze_conversation_ccnd_summary(fixture_tree):
    result = analyze_conversation_ccnd(fixture_tree, n_segments=3)
    assert result["source_type"] == "H-AI"
    s = result["summary"]
    assert s["final_macro_coverage"] == pytest.approx(3 / 6)
    assert s["final_macro_count"] == 3
    assert s["final_micro_count"] == 4
    assert s["new_macros_per_segment"] == [2, 1, 0]
    assert s["jaccard_macro_T1T2"] == pytest.approx(2 / 3)
    assert s["jaccard_macro_T2T3"] == pytest.approx(1.0)
    assert s["untimed_hit_count"] == 1
    # the only anomaly is the intentional missing timestamp; nothing dropped,
    # no unexpected anchor
    assert [a["type"] for a in result["anomalies"]] == ["missing_timestamp"]


def test_analyze_flags_unexpected_seventh_anchor():
    b = timezone.now()
    tree = _ai_state({"id": "root", "name": "核電", "type": "root", "children": [
        {"id": "anchor_other", "name": "其他", "type": "anchor", "children": [
            _point("agent_1", "怪節點", [_msg(b, 0, "m1")]),
        ]},
    ]})
    result = analyze_conversation_ccnd(tree)
    assert any(a["type"] == "unexpected_anchor" for a in result["anomalies"])


def test_analyze_reports_missing_timestamp_anomaly(fixture_tree):
    result = analyze_conversation_ccnd(fixture_tree)
    assert any(a["type"] == "missing_timestamp" and a["count"] == 1
               for a in result["anomalies"])


def test_hh_flatten_namespaces_distinct_nodes():
    b = timezone.now()
    # both participants have a node with the SAME node_id "agent_1"
    def one_tree():
        return {"id": "root", "name": "核電", "type": "root", "children": [
            {"id": "anchor_safety", "name": "核能安全", "type": "anchor",
             "children": [_point("agent_1", "事故風險", [_msg(b, 0, "m")])]},
        ]}
    state = {
        "version": 2, "mode": "participant_trees", "anchors": [],
        "participants": {
            "user_a": {"ownerKey": "user_a", "treeData": one_tree()},
            "user_b": {"ownerKey": "user_b", "treeData": one_tree()},
        },
    }
    # flatten sees both sides, namespaced so the colliding ids stay distinct
    hits = flatten_state(state)
    assert {h["_node_key"] for h in hits} == {"user_a::agent_1", "user_b::agent_1"}


def _hh_two_sided_state(base):
    """H-H state: user_a lights safety+economy, user_b lights energy+waste."""
    def tree(anchor_defs):
        return {"id": "root", "name": "核電", "type": "root", "children": [
            {"id": aid, "name": aname, "type": "anchor",
             "children": [_point(nid, nname, [_msg(base, off, mid)])]}
            for (aid, aname, nid, nname, off, mid) in anchor_defs
        ]}
    return {
        "version": 2, "mode": "participant_trees", "anchors": [],
        "participants": {
            "user_a": {"ownerKey": "user_a", "treeData": tree([
                ("anchor_safety", "核能安全", "agent_1", "事故風險", 0, "a1"),
                ("anchor_economy", "經濟成本", "agent_2", "發電成本", 1, "a2"),
            ])},
            "user_b": {"ownerKey": "user_b", "treeData": tree([
                ("anchor_energy", "能源問題", "agent_1", "供電穩定", 2, "b1"),
                ("anchor_waste", "核廢處理", "agent_2", "最終處置", 3, "b2"),
            ])},
        },
    }


def test_hh_breadth_counts_subject_side_only():
    """A3: breadth counts ONLY the subject side; the partner is excluded from the
    main metrics but reported under partner_side."""
    state = _hh_two_sided_state(timezone.now())

    # subject = user_a -> only safety+economy count; energy/waste (user_b) excluded
    ra = analyze_conversation_ccnd(state, subject_owner_key="user_a", n_segments=2)
    assert ra["subject_owner_key"] == "user_a"
    assert ra["summary"]["final_macro_count"] == 2
    assert ra["summary"]["final_macro_coverage"] == pytest.approx(2 / 6)
    assert set(ra["snapshots"][-1]["macro_set"]) == {"anchor_safety", "anchor_economy"}
    assert set(ra["node_index"]) == {"user_a::agent_1", "user_a::agent_2"}
    # partner side (user_b) is listed for contrast, NOT folded into breadth
    assert ra["partner_side"]["owner_keys"] == ["user_b"]
    assert set(ra["partner_side"]["macro_set"]) == {"anchor_energy", "anchor_waste"}
    assert ra["partner_side"]["micro_count"] == 2

    # subject = user_b -> mirror image
    rb = analyze_conversation_ccnd(state, subject_owner_key="user_b", n_segments=2)
    assert set(rb["snapshots"][-1]["macro_set"]) == {"anchor_energy", "anchor_waste"}
    assert set(rb["node_index"]) == {"user_b::agent_1", "user_b::agent_2"}
    assert rb["partner_side"]["owner_keys"] == ["user_a"]


def test_iter_subject_analyses_one_row_per_subject():
    state = _hh_two_sided_state(timezone.now())
    results = list(iter_subject_analyses(state, n_segments=2))
    assert [r["subject_owner_key"] for r in results] == ["user_a", "user_b"]
    # each subject counts only its own 2 nodes
    assert all(r["summary"]["final_micro_count"] == 2 for r in results)


def test_analyze_dispatch_on_unsaved_model_instances(fixture_tree):
    """isinstance dispatch works on model instances (no DB save needed)."""
    from api.models import DialogueMatch, DialogueSessionRecord

    state = fixture_tree
    session = DialogueSessionRecord(semantic_tree_state=state)
    assert analyze_conversation_ccnd(session)["source_type"] == "H-AI"

    # H-H match: same trees wrapped as participant_trees under stats
    hh_state = dict(state, mode="participant_trees")
    hh_state["participants"] = {
        "user_a": state["participants"]["user"],
    }
    match = DialogueMatch(stats={"semantic_tree": hh_state})
    assert analyze_conversation_ccnd(match)["source_type"] == "H-H"


def test_analyze_rejects_unknown_type():
    with pytest.raises(TypeError):
        analyze_conversation_ccnd(12345)


# ─────────────────────────────────────────────────────────────────────────────
# CSV export helper (A2) — pure row serialization
# ─────────────────────────────────────────────────────────────────────────────

def test_export_build_csv_row(fixture_tree):
    from api.management.commands.export_ccnd_snapshots import build_csv_row

    result = analyze_conversation_ccnd(fixture_tree, n_segments=3)
    row = build_csv_row("session:test", result)

    assert row["conversation_id"] == "session:test"
    assert row["source_type"] == "H-AI"
    assert row["subject_owner_key"] == "user"
    assert row["final_macro_coverage"] == pytest.approx(3 / 6)
    assert row["new_macros_per_segment"] == "2|1|0"
    assert row["jaccard_macro_T1T2"] == pytest.approx(2 / 3)
    assert row["untimed_hit_count"] == 1
    assert "missing_timestamp" in row["anomalies"]
    # first_appearance is an ordered "name@ordinal@iso" series
    fa = row["first_appearance"].split(";")
    assert fa[0].startswith("事故風險@1@")
    assert [seg.split("@")[1] for seg in fa] == ["1", "3", "5", "7"]
