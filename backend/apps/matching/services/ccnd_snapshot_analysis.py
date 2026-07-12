"""Offline CCND snapshot analysis for a *finished* conversation.

Pure read + pure compute. This module NEVER writes to the DB, mutates a model,
or touches the consumer / frontend / dialogue flow. It is meant to be run by a
researcher after a conversation is over, to produce two families of snapshot
metrics over the CCND (概念認知網路圖) semantic tree:

  Metric A — snapshot_similarity: Jaccard between adjacent time snapshots
             (T1-T2, T2-T3) for both the micro-node set and the macro-anchor
             set, *plus* the added/retained/dropped decomposition (more
             interpretable than a bare scalar — this is the point).
  Metric B — novelty_timeline: when/how often new nodes light up (first
             appearance, per-segment new-node counts, hit frequency).

Data model recap (verified against source, see semantic_tree.py):
  - The CCND is a FIXED-topology tree: root -> 6 fixed anchors (macro / 大類) ->
    point nodes (micro / 小類, possibly nested under path-category nodes).
  - Every time a message hits a node, a record is appended to that node's
    ``messages`` list carrying ``sourceTimestamp`` / ``sourceMessageId`` /
    ``sourceParticipant`` (see semantic_tree._append_node_message).
  - H-H trees live in ``DialogueMatch.stats["semantic_tree"]`` (two participants
    user_a/user_b); H-AI trees in ``DialogueSessionRecord.semantic_tree_state``
    (single participant "user"). BOTH share the identical state shape
    ``{version, mode, anchors, participants:{ownerKey:{treeData,...}}}`` so a
    single walker reads both.

Design decisions (documented on purpose — they affect research semantics):
  - MACRO identity  = ``parent_anchor_id``. The 6 fixed anchors are a shared
    taxonomy, so if EITHER participant lights ``anchor_safety`` the macro
    category "核能安全" counts as covered. Denominator is fixed at 6.
  - MICRO identity  = ``(owner_key, node_id)``. A lit point-node in a specific
    participant's tree (node ids are per-tree counters and would otherwise
    collide across participants). If the professor later prefers concept-level
    dedup, switch the key to ``(parent_anchor_id, node_name)``; everything
    downstream is key-agnostic.
  - SUBJECT SIDE ONLY for breadth. The breadth indicators (macro coverage, micro
    count, novelty, snapshots, similarity) measure ONE test subject's personal
    view expansion, so the whole pipeline runs on the subject's own hits and
    EXCLUDES the partner/AI side. For H-AI the subject is the single human
    ("user"); for H-H each participant is a subject, so the match is analysed
    once per side (user_a, then user_b). The partner/AI nodes stay in the tree
    and are reported separately under ``partner_side`` for contrast, but never
    enter the subject's breadth metrics — this keeps H-H and H-AI comparable
    (both count one person's own concepts).
  - Snapshots are strictly TIME-based and built cumulatively (a node, once lit,
    never goes dark), so ``dropped`` is mathematically always empty — we still
    compute it as a self-check and flag any non-empty result as an anomaly.
  - Hits whose ``sourceTimestamp`` is missing/unparseable cannot be placed on
    the timeline: they are EXCLUDED from snapshot splitting and first-appearance
    ordering (and reported), but still counted in ``hit_frequency`` because the
    node genuinely was hit.

No LLM. No inter-node "distance" (meaningless on a fixed tree) — only set
operations and timestamps.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable

from api.dialogue_topics import get_topic_anchors

from .semantic_tree import (
    OWNER_AI_USER,
    OWNER_USER_A,
    OWNER_USER_B,
)

# The macro taxonomy is closed and fixed at 6. A 7th ("其他") must never appear
# on the tree; if one does we flag it rather than silently widen the denominator.
NUCLEAR_ANCHORS = get_topic_anchors(102)
FIXED_ANCHOR_IDS: list[str] = [anchor["id"] for anchor in NUCLEAR_ANCHORS]
FIXED_ANCHOR_NAME_BY_ID: dict[str, str] = {a["id"]: a["name"] for a in NUCLEAR_ANCHORS}
MACRO_DENOMINATOR: int = len(NUCLEAR_ANCHORS)  # == 6

DEFAULT_N_SEGMENTS = 3


# ─────────────────────────────────────────────────────────────────────────────
# timestamp helpers
# ─────────────────────────────────────────────────────────────────────────────

def parse_timestamp(value: Any) -> datetime | None:
    """Parse an ISO-8601 timestamp (as produced by ``datetime.isoformat()``).

    Returns a ``datetime`` or ``None`` if missing/unparseable. Never raises.
    """
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _as_utc(dt: datetime) -> datetime:
    """Coerce to an aware UTC datetime for a total, deterministic ordering.

    Real records are timezone-aware (Django isoformat). A naive value is assumed
    UTC so that a mixed batch still sorts without raising.
    """
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _order_key(hit: dict[str, Any]) -> tuple:
    """Total order over timed hits: time, then stable tie-breakers."""
    return (
        _as_utc(hit["source_timestamp"]),
        str(hit.get("source_message_id") or ""),
        hit["_node_key"],
    )


# ─────────────────────────────────────────────────────────────────────────────
# node identity
# ─────────────────────────────────────────────────────────────────────────────

def _node_key(owner_key: str | None, node_id: Any) -> str:
    """Globally-unique micro-node key, namespaced by participant.

    node ids are per-tree counters (``agent_1`` etc.) so they collide across the
    two H-H participant trees; the owner prefix keeps them distinct.
    """
    return f"{owner_key or 'default'}::{node_id}"


# ─────────────────────────────────────────────────────────────────────────────
# Step 2.1 — flatten
# ─────────────────────────────────────────────────────────────────────────────

def flatten_tree(tree: dict[str, Any], *, owner_key: str | None = None) -> list[dict[str, Any]]:
    """Flatten one ``treeData`` root into one record per message hit.

    Each record contains (spec fields):
        node_id, node_name, parent_anchor_id, parent_anchor_name, stance,
        confidence, source_message_id, source_timestamp (parsed datetime|None),
        source_participant
    plus a few internal/debug extras (``owner_key``, ``mode``,
    ``source_timestamp_raw``, ``_node_key``) that downstream steps rely on.

    The nearest depth-1 ancestor is used as the parent anchor, regardless of how
    deeply the point node is nested under path-category nodes.
    """
    hits: list[dict[str, Any]] = []
    if not isinstance(tree, dict):
        return hits

    for anchor in tree.get("children") or []:
        if not isinstance(anchor, dict):
            continue
        anchor_id = anchor.get("id")
        anchor_name = anchor.get("name")

        # DFS every descendant of this anchor (the anchor itself may, in a
        # degenerate case, carry messages too — handle it rather than assume).
        stack: list[dict[str, Any]] = [anchor]
        while stack:
            node = stack.pop()
            if not isinstance(node, dict):
                continue
            node_id = node.get("id")
            node_name = node.get("name")
            for msg in node.get("messages") or []:
                if not isinstance(msg, dict):
                    continue
                raw_ts = msg.get("sourceTimestamp")
                hits.append(
                    {
                        "node_id": node_id,
                        "node_name": node_name,
                        "parent_anchor_id": anchor_id,
                        "parent_anchor_name": anchor_name,
                        "stance": msg.get("stance"),
                        "confidence": msg.get("confidence"),
                        "source_message_id": msg.get("sourceMessageId"),
                        "source_timestamp": parse_timestamp(raw_ts),
                        "source_participant": msg.get("sourceParticipant"),
                        # extras
                        "owner_key": owner_key,
                        "mode": msg.get("mode"),
                        "source_timestamp_raw": raw_ts,
                        "_node_key": _node_key(owner_key, node_id),
                    }
                )
            for child in node.get("children") or []:
                stack.append(child)

    return hits


def flatten_state(state: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten every participant tree in a semantic-tree *state* dict.

    Works for both H-H (participants user_a/user_b) and H-AI (participant user)
    because the state shape is identical.
    """
    hits: list[dict[str, Any]] = []
    if not isinstance(state, dict):
        return hits
    participants = state.get("participants")
    if not isinstance(participants, dict):
        return hits
    for owner_key, owner_state in participants.items():
        if isinstance(owner_state, dict) and isinstance(owner_state.get("treeData"), dict):
            hits.extend(flatten_tree(owner_state["treeData"], owner_key=owner_key))
    return hits


# ─────────────────────────────────────────────────────────────────────────────
# shared node-index / descriptor helpers
# ─────────────────────────────────────────────────────────────────────────────

def _build_node_index(hits: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Map micro node_key -> descriptor {node_name, anchor_id, anchor_name}."""
    index: dict[str, dict[str, Any]] = {}
    for hit in hits:
        key = hit["_node_key"]
        if key not in index:
            index[key] = {
                "key": key,
                "node_id": hit.get("node_id"),
                "node_name": hit.get("node_name"),
                "anchor_id": hit.get("parent_anchor_id"),
                "anchor_name": hit.get("parent_anchor_name"),
                "owner_key": hit.get("owner_key"),
            }
    return index


def _micro_descriptor(key: str, node_index: dict[str, dict[str, Any]]) -> dict[str, Any]:
    return node_index.get(key, {"key": key})


def _macro_descriptor(anchor_id: str) -> dict[str, Any]:
    return {
        "anchor_id": anchor_id,
        "anchor_name": FIXED_ANCHOR_NAME_BY_ID.get(anchor_id, anchor_id),
    }


def _segment_end_indices(n_hits: int, n_segments: int) -> list[int]:
    """Cumulative end index for each segment, split by hit COUNT (not time).

    e.g. n_hits=10, n_segments=3 -> [3, 6, 10]. Empty segments are allowed when
    there are fewer hits than segments.
    """
    return [((k + 1) * n_hits) // n_segments for k in range(n_segments)]


# ─────────────────────────────────────────────────────────────────────────────
# Step 2.2 — snapshots
# ─────────────────────────────────────────────────────────────────────────────

def build_snapshots(
    hits: list[dict[str, Any]],
    n_segments: int = DEFAULT_N_SEGMENTS,
) -> dict[str, Any]:
    """Split the timed hit stream into ``n_segments`` by event count and build a
    *cumulative* snapshot at the end of each segment.

    Returns ``{"snapshots": [...], "n_segments", "timed_hit_count",
    "untimed_hit_count", "node_index"}``. Each snapshot is cumulative: T2
    contains everything lit in T1 (nodes never go dark).
    """
    if n_segments < 1:
        raise ValueError("n_segments must be >= 1")

    node_index = _build_node_index(hits)
    timed = [h for h in hits if h["source_timestamp"] is not None]
    untimed_count = len(hits) - len(timed)
    ordered = sorted(timed, key=_order_key)
    n = len(ordered)
    ends = _segment_end_indices(n, n_segments)

    snapshots: list[dict[str, Any]] = []
    prev_end = 0
    for k, end in enumerate(ends):
        cumulative = ordered[:end]
        segment = ordered[prev_end:end]
        micro_set = {h["_node_key"] for h in cumulative}
        macro_set = {h["parent_anchor_id"] for h in cumulative}
        snapshots.append(
            {
                "label": f"T{k + 1}",
                "segment_index": k,
                "cumulative_hit_count": end,
                "segment_hit_count": end - prev_end,
                "time_start": ordered[0]["source_timestamp"] if cumulative else None,
                "time_end": cumulative[-1]["source_timestamp"] if cumulative else None,
                "micro_set": sorted(micro_set),
                "macro_set": sorted(macro_set),
                "micro_count": len(micro_set),
                "macro_count": len(macro_set),
            }
        )
        prev_end = end

    return {
        "snapshots": snapshots,
        "n_segments": n_segments,
        "timed_hit_count": n,
        "untimed_hit_count": untimed_count,
        "node_index": node_index,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Step 3 — Metric A: snapshot similarity (Jaccard + decomposition)
# ─────────────────────────────────────────────────────────────────────────────

def _jaccard(a: set, b: set) -> float:
    union = a | b
    if not union:
        return 1.0  # two empty sets are trivially identical
    return len(a & b) / len(union)


def _compare_sets(
    a: set,
    b: set,
    *,
    describe,
) -> dict[str, Any]:
    added = b - a
    retained = a & b
    dropped = a - b  # must be empty under the cumulative model
    return {
        "jaccard": _jaccard(a, b),
        "added": [describe(x) for x in sorted(added)],
        "retained": [describe(x) for x in sorted(retained)],
        "dropped": [describe(x) for x in sorted(dropped)],
        "added_count": len(added),
        "retained_count": len(retained),
        "dropped_count": len(dropped),
    }


def snapshot_similarity(
    snapshot_result: dict[str, Any],
) -> dict[str, Any]:
    """Metric A. Compare adjacent snapshot pairs (T1-T2, T2-T3, ...).

    Accepts the dict returned by :func:`build_snapshots` (or, leniently, a bare
    list of snapshots). For each pair returns Jaccard for the micro and macro
    sets plus the added/retained/dropped decomposition. ``dropped`` should be
    empty in the cumulative model; any non-empty ``dropped`` is surfaced under
    the returned ``anomalies``.
    """
    if isinstance(snapshot_result, dict):
        snapshots = snapshot_result.get("snapshots", [])
        node_index = snapshot_result.get("node_index", {})
    else:  # tolerate a bare list of snapshots
        snapshots = snapshot_result
        node_index = {}

    pairs: list[dict[str, Any]] = []
    anomalies: list[dict[str, Any]] = []

    for earlier, later in zip(snapshots, snapshots[1:]):
        pair_label = f"{earlier['label']}-{later['label']}"
        macro = _compare_sets(
            set(earlier["macro_set"]),
            set(later["macro_set"]),
            describe=_macro_descriptor,
        )
        micro = _compare_sets(
            set(earlier["micro_set"]),
            set(later["micro_set"]),
            describe=lambda key: _micro_descriptor(key, node_index),
        )
        pairs.append({"pair": pair_label, "macro": macro, "micro": micro})

        for scope, block in (("macro", macro), ("micro", micro)):
            if block["dropped"]:
                anomalies.append(
                    {
                        "type": "dropped_in_cumulative_model",
                        "pair": pair_label,
                        "scope": scope,
                        "dropped": block["dropped"],
                    }
                )

    return {"pairs": pairs, "anomalies": anomalies}


# ─────────────────────────────────────────────────────────────────────────────
# Step 4 — Metric B: novelty timeline
# ─────────────────────────────────────────────────────────────────────────────

def novelty_timeline(
    hits: list[dict[str, Any]],
    n_segments: int = DEFAULT_N_SEGMENTS,
) -> dict[str, Any]:
    """Metric B. When / how often new nodes light up.

    Returns:
      - ``first_appearance``: per micro-node, earliest ``source_timestamp`` and
        its 1-indexed ordinal in the global timed hit stream. Sorted by
        appearance order — this is "how the field of view unfolds".
      - ``per_segment_new_count``: for the same ``n_segments`` split, count nodes
        whose FIRST appearance falls in each segment — ``new_macros`` and
        ``new_micros`` separately (a directional, monotone metric).
      - ``hit_frequency``: per micro-node total message count (INCLUDING untimed
        hits) and per-macro totals.
      - ``nodes_without_timed_appearance``: nodes lit only by untimed hits.
    """
    node_index = _build_node_index(hits)
    timed = [h for h in hits if h["source_timestamp"] is not None]
    ordered = sorted(timed, key=_order_key)

    # first appearance (micro + macro) over the ordered timed stream
    first_micro_ordinal: dict[str, int] = {}
    first_micro_ts: dict[str, datetime] = {}
    first_macro_ordinal: dict[str, int] = {}
    first_macro_ts: dict[str, datetime] = {}
    for ordinal, hit in enumerate(ordered, start=1):
        mk = hit["_node_key"]
        if mk not in first_micro_ordinal:
            first_micro_ordinal[mk] = ordinal
            first_micro_ts[mk] = hit["source_timestamp"]
        ak = hit["parent_anchor_id"]
        if ak not in first_macro_ordinal:
            first_macro_ordinal[ak] = ordinal
            first_macro_ts[ak] = hit["source_timestamp"]

    first_appearance = [
        {
            **_micro_descriptor(mk, node_index),
            "first_timestamp": first_micro_ts[mk],
            "first_hit_ordinal": first_micro_ordinal[mk],
        }
        for mk in sorted(first_micro_ordinal, key=lambda k: first_micro_ordinal[k])
    ]

    # per-segment new counts, keyed off first-appearance ordinal
    ends = _segment_end_indices(len(ordered), n_segments)

    def _segment_of(ordinal: int) -> int:
        # ordinal is 1-indexed; find segment k where ends[k-1] < ordinal <= ends[k]
        for k, end in enumerate(ends):
            if ordinal <= end:
                return k
        return n_segments - 1

    new_micros = [0] * n_segments
    new_macros = [0] * n_segments
    for mk, ordinal in first_micro_ordinal.items():
        new_micros[_segment_of(ordinal)] += 1
    for ak, ordinal in first_macro_ordinal.items():
        new_macros[_segment_of(ordinal)] += 1

    per_segment_new_count = {
        "new_micros": new_micros,
        "new_macros": new_macros,
    }

    # hit frequency (ALL hits, timed + untimed)
    micro_freq: dict[str, int] = {}
    macro_freq: dict[str, int] = {}
    for hit in hits:
        micro_freq[hit["_node_key"]] = micro_freq.get(hit["_node_key"], 0) + 1
        macro_freq[hit["parent_anchor_id"]] = macro_freq.get(hit["parent_anchor_id"], 0) + 1

    hit_frequency = {
        "micro": {
            mk: {**_micro_descriptor(mk, node_index), "count": count}
            for mk, count in sorted(micro_freq.items(), key=lambda kv: (-kv[1], kv[0]))
        },
        "macro": {
            ak: {**_macro_descriptor(ak), "count": count}
            for ak, count in sorted(macro_freq.items(), key=lambda kv: (-kv[1], kv[0]))
        },
    }

    nodes_without_timed_appearance = [
        _micro_descriptor(mk, node_index)
        for mk in node_index
        if mk not in first_micro_ordinal
    ]

    return {
        "first_appearance": first_appearance,
        "per_segment_new_count": per_segment_new_count,
        "hit_frequency": hit_frequency,
        "nodes_without_timed_appearance": nodes_without_timed_appearance,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Step 5 — public entry point
# ─────────────────────────────────────────────────────────────────────────────

def _resolve_source(match_or_session: Any) -> tuple[str, dict[str, Any]]:
    """Return (source_type, semantic-tree state dict) for a model instance.

    Imports the models lazily so the analysis functions above stay importable in
    a bare-Python context. Also tolerates a raw state dict for testing.
    """
    from api.models import DialogueMatch, DialogueSessionRecord

    if isinstance(match_or_session, DialogueMatch):
        stats = match_or_session.stats if isinstance(match_or_session.stats, dict) else {}
        return "H-H", (stats.get("semantic_tree") or {})
    if isinstance(match_or_session, DialogueSessionRecord):
        return "H-AI", (match_or_session.semantic_tree_state or {})
    # test / advanced: a raw state dict already in the semantic-tree shape
    if isinstance(match_or_session, dict) and "participants" in match_or_session:
        mode = match_or_session.get("mode")
        source_type = "H-AI" if mode == "ai_user_tree" else "H-H"
        return source_type, match_or_session
    raise TypeError(
        "analyze_conversation_ccnd expects a DialogueMatch, a "
        "DialogueSessionRecord, or a raw semantic-tree state dict; got "
        f"{type(match_or_session).__name__}"
    )


def participant_owner_keys(match_or_session: Any) -> tuple[str, list[str]]:
    """Return (source_type, [owner_key, ...]) for a conversation.

    H-AI -> ["user"]; H-H -> ["user_a", "user_b"] (whichever are present). Lets a
    caller iterate one analysis per test subject.
    """
    source_type, state = _resolve_source(match_or_session)
    participants = state.get("participants") if isinstance(state, dict) else None
    keys = list(participants.keys()) if isinstance(participants, dict) else []
    return source_type, keys


def _resolve_subject_owner_key(
    source_type: str,
    state: dict[str, Any],
    subject_owner_key: str | None,
) -> str:
    participants = state.get("participants") if isinstance(state, dict) else None
    participants = participants if isinstance(participants, dict) else {}
    if subject_owner_key is not None:
        if participants and subject_owner_key not in participants:
            raise ValueError(
                f"subject_owner_key {subject_owner_key!r} not among participants "
                f"{sorted(participants)}"
            )
        return subject_owner_key
    if source_type == "H-AI":
        return OWNER_AI_USER if OWNER_AI_USER in participants else (
            next(iter(participants), OWNER_AI_USER)
        )
    # H-H: default to user_a, else user_b, else whatever exists
    for preferred in (OWNER_USER_A, OWNER_USER_B):
        if preferred in participants:
            return preferred
    return next(iter(participants), OWNER_USER_A)


def _breadth_summary(hits: list[dict[str, Any]]) -> dict[str, Any]:
    """Macro/micro breadth for an arbitrary hit subset (used for partner side)."""
    node_index = _build_node_index(hits)
    micro = {h["_node_key"] for h in hits}
    macro = {h["parent_anchor_id"] for h in hits}
    return {
        "owner_keys": sorted({h["owner_key"] for h in hits}),
        "macro_count": len(macro),
        "micro_count": len(micro),
        "macro_coverage": len(macro) / MACRO_DENOMINATOR,
        "macro_set": sorted(macro),
        "micro_nodes": [node_index[k] for k in sorted(micro)],
    }


def _detect_anomalies(hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    anomalies: list[dict[str, Any]] = []

    # 7th-category / unknown anchor must never appear on a fixed-topology tree
    unknown = sorted(
        {h["parent_anchor_id"] for h in hits if h["parent_anchor_id"] not in FIXED_ANCHOR_IDS}
    )
    if unknown:
        anomalies.append({"type": "unexpected_anchor", "anchor_ids": unknown})

    # timestamp hygiene
    missing = sum(1 for h in hits if h["source_timestamp_raw"] in (None, ""))
    unparseable = sum(
        1
        for h in hits
        if h["source_timestamp_raw"] not in (None, "") and h["source_timestamp"] is None
    )
    if missing:
        anomalies.append({"type": "missing_timestamp", "count": missing})
    if unparseable:
        anomalies.append({"type": "unparseable_timestamp", "count": unparseable})

    return anomalies


def analyze_conversation_ccnd(
    match_or_session: Any,
    n_segments: int = DEFAULT_N_SEGMENTS,
    subject_owner_key: str | None = None,
) -> dict[str, Any]:
    """Run the full CCND snapshot analysis for ONE test subject in a finished
    conversation.

    Auto-detects a ``DialogueMatch`` (H-H) vs ``DialogueSessionRecord`` (H-AI)
    (a raw state dict is also accepted for testing). Breadth metrics count only
    the subject's own side; the partner/AI side is reported separately under
    ``partner_side``. ``subject_owner_key`` selects which side is the subject
    (default: the human — "user" for H-AI, "user_a" for H-H). For H-H, call once
    per side (see :func:`iter_subject_analyses`).
    """
    source_type, state = _resolve_source(match_or_session)
    all_hits = flatten_state(state)
    subject_key = _resolve_subject_owner_key(source_type, state, subject_owner_key)

    subject_hits = [h for h in all_hits if h["owner_key"] == subject_key]
    partner_hits = [h for h in all_hits if h["owner_key"] != subject_key]

    # Full pipeline runs on the subject's own hits only.
    snapshot_result = build_snapshots(subject_hits, n_segments=n_segments)
    snapshots = snapshot_result["snapshots"]
    similarity = snapshot_similarity(snapshot_result)
    novelty = novelty_timeline(subject_hits, n_segments=n_segments)

    # Anomalies are scanned across the WHOLE tree (an unexpected anchor or a bad
    # timestamp matters wherever it sits), plus any dropped-set anomaly.
    anomalies = _detect_anomalies(all_hits) + similarity["anomalies"]

    final = snapshots[-1] if snapshots else {"macro_count": 0, "micro_count": 0}
    pairs = similarity["pairs"]

    def _pair_jaccard(index: int, scope: str) -> float | None:
        if index < len(pairs):
            return pairs[index][scope]["jaccard"]
        return None

    summary = {
        "final_macro_coverage": final["macro_count"] / MACRO_DENOMINATOR,
        "final_macro_count": final["macro_count"],
        "final_micro_count": final["micro_count"],
        "macro_denominator": MACRO_DENOMINATOR,
        "new_macros_per_segment": novelty["per_segment_new_count"]["new_macros"],
        "new_micros_per_segment": novelty["per_segment_new_count"]["new_micros"],
        "jaccard_macro_T1T2": _pair_jaccard(0, "macro"),
        "jaccard_macro_T2T3": _pair_jaccard(1, "macro"),
        "jaccard_micro_T1T2": _pair_jaccard(0, "micro"),
        "jaccard_micro_T2T3": _pair_jaccard(1, "micro"),
        "timed_hit_count": snapshot_result["timed_hit_count"],
        "untimed_hit_count": snapshot_result["untimed_hit_count"],
    }

    return {
        "source_type": source_type,
        "subject_owner_key": subject_key,
        "n_segments": n_segments,
        "snapshots": snapshots,
        "similarity": similarity,   # Metric A (subject side)
        "novelty": novelty,         # Metric B (subject side)
        "summary": summary,
        "anomalies": anomalies,
        "node_index": snapshot_result["node_index"],
        # Non-subject side, kept for contrast only — NOT in the breadth metrics.
        "partner_side": _breadth_summary(partner_hits),
    }


def iter_subject_analyses(
    match_or_session: Any,
    n_segments: int = DEFAULT_N_SEGMENTS,
):
    """Yield one analysis per test subject.

    H-AI -> one result (the human "user"); H-H -> two (user_a, then user_b), each
    with the other side as partner. This is the unit that makes H-H and H-AI
    comparable: one row per subject.
    """
    source_type, keys = participant_owner_keys(match_or_session)
    for owner_key in keys or [None]:
        yield analyze_conversation_ccnd(
            match_or_session,
            n_segments=n_segments,
            subject_owner_key=owner_key,
        )
