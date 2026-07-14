# CCND Backend Modularization — Design Spec

Date: 2026-06-30
Author: Claude (with 伍晨安)
Status: Approved by user, ready for implementation planning

## Background

`feat/Light` currently generates CCND (概念認知網路圖) semantic-tree nodes
through a single hardcoded path: `semantic_tree.analyze_with_openai()`. An
earlier, divergent branch (`feat/hsiao`, no common merge-base with
`feat/Light`) built a local two-stage BERT classifier
(`nuclear_node_classifier.py` + `apps/matching/ml_models/nuclear_node_model/`,
trained by 黃筱筑 on `test_for_2_label_pytorch_train_30`) that replaced the
OpenAI call for topic 102 (核能) only. That branch's dispatch was a hardcoded
set: `LOCAL_CLASSIFIER_TOPIC_IDS = {102}`.

`feat/Light` has since diverged (commit `9d9565f`, "move analysis to openai
gpt-5.4-mini") and no longer has any local-classifier code path at all. This
spec ports the ML classifier's *capability* back in, as a swappable backend
alongside the OpenAI path, so the choice of which backend serves CCND
analysis is explicit per-topic configuration rather than a hardcoded branch.

Weight files (`model.safetensors`, ~2.7GB total across macro + 6 micro
models) are gitignored in the source project and are not being copied as
part of this work — the module is scaffolded now, weights are supplied
later. Until weights exist, this spec keeps both existing topics (102, 103)
configured to use the AI backend, so shipping this doesn't change runtime
behavior.

## Goals

- Introduce a clean seam between "how CCND analyzes a message" and "which
  backend does the analysis" so a future backend (this ML classifier, or
  something else) can be added without touching `semantic_tree.py`'s
  orchestration logic (tree state, batching, history).
- Make the AI/ML choice a per-topic config value in `TOPIC_CONFIGS`
  (`api/dialogue_topics.py`), not a hardcoded id set.
- Restore the ML classifier code path from `feat/hsiao`, unmodified in
  behavior, wrapped to fit the new backend interface.
- Fail fast and clearly when a topic is configured for `"ml"` but the
  model weights aren't present locally — no silent fallback to AI.
- Ship with both topic 102 and 103 set to `"ai"` — this PR changes no
  observable behavior; it only adds the capability to switch 102 to `"ml"`
  later by flipping one config value once weights are copied in.

## Non-goals

- No fallback-to-AI-on-missing-weights behavior.
- No global env-var toggle — granularity is strictly per-topic.
- No generalizing the ML classifier itself to arbitrary topics/anchor sets
  beyond what it already does for topic 102 (it's a topic-102-specific
  trained model; the *dispatch mechanism* is generic, the *model* is not).
- No copying `model.safetensors` weight files into the repo (they stay
  gitignored; user supplies them out-of-band later).

## Architecture

New subpackage: `backend/apps/matching/services/ccnd_backends/`

```
ccnd_backends/
├── __init__.py        # public dispatcher: analyze_text_for_tree(...)
├── openai_backend.py  # thin wrapper around semantic_tree.analyze_with_openai
└── ml_backend.py       # ported nuclear_node_classifier.py (macro/micro BERT)
```

### `__init__.py` — dispatcher

`get_ccnd_backend(topic_id)` lives in this file and reads
`api.dialogue_topics.TOPIC_CONFIGS[topic_id].get("ccnd_backend", "ai")`
(imported lazily inside the function to avoid a module-load-order
dependency between `apps.matching` and `api`).

```python
def analyze_text_for_tree(
    *,
    topic_id: int | None,
    text: str,
    tree: dict[str, Any],
    anchors: list[dict[str, str]] | None = None,
    anchor_descriptions: dict[str, str] | None = None,
) -> dict[str, Any]:
    backend = get_ccnd_backend(topic_id)  # reads TOPIC_CONFIGS[topic_id]["ccnd_backend"], default "ai"
    if backend == "ml":
        from . import ml_backend
        return ml_backend.analyze(text=text, tree=tree, anchors=anchors)
    from . import openai_backend
    return openai_backend.analyze(
        text=text, tree=tree, anchors=anchors, anchor_descriptions=anchor_descriptions,
    )
```

This function name and call shape intentionally match what `semantic_tree.py`
used to call before `9d9565f` removed the dispatch (`analyze_text_for_tree`),
so the two call sites in `semantic_tree.py`
(`analyze_pending_room_messages`, `analyze_pending_ai_conversations`) change
minimally: swap `analyze_with_openai(...)` for
`ccnd_backends.analyze_text_for_tree(topic_id=..., ...)`.

### `openai_backend.py`

Pure delegation — no logic duplication:

```python
from apps.matching.services.semantic_tree import analyze_with_openai

def analyze(*, text, tree, anchors=None, anchor_descriptions=None):
    return analyze_with_openai(
        text=text, tree=tree, anchors=anchors, anchor_descriptions=anchor_descriptions,
    )
```

### `ml_backend.py`

Ported from `feat/hsiao`'s `nuclear_node_classifier.py` with no behavior
changes: `classify()` (macro model → conditional micro model) and
`build_candidate_items()` (shapes classifier output into the same
candidate-item dict shape `semantic_tree.validate_analysis_items` expects).
Lazy-imports `torch`/`transformers` inside functions (already the case in
the source) so nothing new is required at Django startup for topics that
don't use ml.

`analyze()` entry point wraps `build_candidate_items()` and runs the result
through `semantic_tree.validate_analysis_items()`, matching what the old
`analyze_text_for_tree` dispatch did:

```python
def analyze(*, text, tree, anchors=None):
    from apps.matching.services.semantic_tree import validate_analysis_items, FIXED_ANCHORS
    resolved_anchors = anchors or FIXED_ANCHORS
    items = build_candidate_items(text, resolved_anchors)
    return {**validate_analysis_items({"items": items}, tree, resolved_anchors), "model": "local-bert-pipeline"}
```

`_ensure_loaded()` keeps raising `FileNotFoundError` when
`model.safetensors` is missing (ported unchanged). This is caught at the
`analyze()` boundary and re-raised as `MissingMLModelWeights`, a new
`SemanticTreeError` subclass added to `semantic_tree.py`:

```python
class MissingMLModelWeights(SemanticTreeError):
    status_code = 503
    code = "missing_ml_model_weights"
```

This lets `views.py`'s existing `except SemanticTreeError` handling in
`DialogueSessionSemanticTreeAnalyzeView` (and the equivalent room-message
view) return this as a normal structured API error, no unhandled 500.

### Model files

`backend/apps/matching/ml_models/nuclear_node_model/` — copied from
`feat/hsiao` as-is: `README.md`, `class_maps.json`,
`model_macro/{config.json,label_mapping.json,special_tokens_map.json,tokenizer.json,tokenizer_config.json,vocab.txt}`,
same for `model_micro_0` through `model_micro_5`. `model.safetensors` in
each directory is not copied; `.gitignore` gets the same exclusion rule
`feat/hsiao` had for it.

### `TOPIC_CONFIGS` changes (`api/dialogue_topics.py`)

Each topic gains a `"ccnd_backend"` key. Both topics ship as `"ai"`:

```python
102: {
    ...,
    "ccnd_backend": "ai",  # flip to "ml" once model.safetensors weights are copied in
    ...
},
103: {
    ...,
    "ccnd_backend": "ai",
    ...
},
```

`get_ccnd_backend(topic_id)` defaults to `"ai"` if the topic or the key is
missing, so this is backward compatible with any topic config that doesn't
set it.

### `semantic_tree.py` changes

1. Two call sites swap `analyze_with_openai(...)` for
   `ccnd_backends.analyze_text_for_tree(topic_id=locked_match.topic_id, ...)`
   (room messages) and `topic_id=session_record.get("topic_id")` (AI
   conversations) respectively.
2. The `OPENAI_API_KEY` pre-flight check
   (`if pending_messages and not get_openai_api_key(): raise MissingOpenAIApiKey`)
   is gated on the resolved backend being `"ai"` for that topic — an
   ml-backend topic must not be blocked by a missing OpenAI key it doesn't
   need.
3. Add `MissingMLModelWeights(SemanticTreeError)` exception class.

## Error handling

| Condition | Behavior |
|---|---|
| Topic backend = `"ai"`, no `OPENAI_API_KEY` | `MissingOpenAIApiKey` (existing, unchanged) |
| Topic backend = `"ml"`, `model.safetensors` missing | `MissingMLModelWeights`, fails fast, no fallback |
| Topic backend = `"ml"`, weights present | Runs local BERT pipeline, same candidate-item shape as AI path |

## Testing

- Unit test for `ccnd_backends.analyze_text_for_tree` dispatch: given a
  topic_id with `"ccnd_backend": "ml"` and no weights present, asserts
  `MissingMLModelWeights` is raised (no network/model calls needed — this
  exercises the fail-fast path without requiring actual weight files).
  Given `"ccnd_backend": "ai"` (or missing key), asserts it delegates to
  `openai_backend.analyze` (mock/patch that function, assert called with
  right args).
- Existing `apps/matching/tests_reasoning_mode.py` and
  `chat/tests_*` suites should continue passing unmodified since no
  existing behavior changes (both topics stay on `"ai"`).
- No test requires actual `.safetensors` weights — the ml path here is only
  exercised down to the "weights missing → fail fast" boundary.

## Rollout

This change is behavior-neutral on merge: both topics remain on `"ai"`.
Enabling ML for topic 102 later is a one-line config flip
(`"ccnd_backend": "ml"`) plus copying the actual weight files into
`backend/apps/matching/ml_models/nuclear_node_model/*/model.safetensors`
out-of-band.
