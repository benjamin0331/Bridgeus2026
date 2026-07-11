# Nuclear-energy node classifier (topic 102)

Two-stage hierarchical Chinese text classifier used to generate CCND/semantic-tree
nodes for the nuclear-energy topic (`topic_id=102`) without calling an LLM.
Other topics (e.g. `103`) keep using the generative path in
`apps/matching/services/semantic_tree.py` (`analyze_with_openai`).

Source training project: `test_for_2_label_pytorch_train_30` (trained by 黃筱筑).
Training/eval scripts are not copied into this repo; see the original project for
`train_pytorch.py` / `test_pytorch.py` / `evaluate_pytorch.py` if the model needs
to be retrained.

## Architecture

- Base model: `hfl/chinese-roberta-wwm-ext` (`BertForSequenceClassification`,
  12 layers, hidden size 768), fine-tuned separately for each stage via
  HuggingFace `Trainer`.
- **Stage 1 — `model_macro/`**: classifies a raw message into one of 7 macro
  classes (`class_id` 0-6). Classes 0-5 map 1:1 onto the topic-102 anchors in
  `api/dialogue_topics.py`; class 6 ("其他") means "doesn't belong to any
  anchor" and has no downstream model.
- **Stage 2 — `model_micro_<class_id>/`**: one classifier per macro class,
  predicting a fine-grained `cluster_id` within that class (36 clusters total
  across classes 0-5). `model_micro_6` does not exist — class 6 had under 5
  training samples so it was skipped during training (see `train_pytorch.py`).
- `class_maps.json`: human-readable Chinese names for `class_id` (`class_name_map`)
  and `cluster_id` (`cluster_name_map`). Copied from the training project's
  `map.json`.
- Each model directory's `label_mapping.json` maps the model's internal
  0..N-1 softmax index back to the raw `class_id`/`cluster_id` used in
  `class_maps.json` — required because HuggingFace re-indexes labels
  contiguously per model.

## Inference contract

Given one message of text, the pipeline (stage 1 then stage 2, routed by the
stage-1 prediction) outputs:

```
class_id, class_name, class_conf   (macro: anchor-level)
cluster_id, cluster_name, cluster_conf   (micro: sub-topic level, -1/"未分流" if class_id == 6)
```

`apps/matching/services/nuclear_node_classifier.py` wraps this into the same
candidate-item shape (`claimText` / `anchorId` / `path` / `pointName` / `stance`
/ `confidence` / `rationale`) that `semantic_tree.validate_analysis_items`
expects from the OpenAI path, so both paths can feed the same tree-building
code. The classifier does not predict `stance`; it defaults to `"中立"`
(matches the existing fallback in `ConversationTreePanel.jsx`).

## Files excluded from git

`model.safetensors` in each `model_macro/` / `model_micro_*/` directory
(~391MB each, ~2.7GB total) is gitignored — see root `.gitignore`. Anyone
setting up this repo needs those files copied in separately (shared drive /
cloud storage) before the local classifier path will work; without them,
topic 102 analysis will raise `FileNotFoundError` and should fall back to
being treated as a setup error, not silently switch to the OpenAI path.

## Dependencies

`torch` and `transformers` are already declared in `backend/pyproject.toml`.
