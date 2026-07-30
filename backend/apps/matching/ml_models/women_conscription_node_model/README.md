# Women-conscription node classifier (topic 103)

Two-stage hierarchical Chinese text classifier used to generate CCND/semantic-tree
nodes for the women's-conscription topic (`topic_id=103`) without calling an LLM.
Same architecture as `nuclear_node_model` (topic 102): `hfl/chinese-roberta-wwm-ext`
fine-tuned as a macro classifier (`model_macro/`) plus one micro classifier per
macro class (`model_micro_<class_id>/`).

Training source: `woman_army_23_pytorch` (trained by 黃筱筑, provided as
`map_woman.json` + the model directories on 2026-07-30).

## Class structure — 5 real classes, not 6

Unlike the nuclear model (7 macro classes: 6 real + "其他"), this model has
**6 macro classes total: 5 real + "其他" (`class_id=5`)**. Topic 103's fixed
anchors in `api/dialogue_topics.py` were trimmed from the original 6 to 5 to
match — there is no macro class for "體能訓練" (physical) or "個人意願"
(autonomy). Content on those themes either landed inside one of the 5 real
classes during training, or falls into the "其他" catch-all, which — same as
nuclear's class 6 — is never emitted as a node (`OTHER_CLASS_ID` check in
`women_conscription_node_classifier.classify`).

Mapping (see `CLASS_ID_TO_ANCHOR_ID` in
`apps/matching/services/women_conscription_node_classifier.py` — this is a
fixed id->id table, NOT resolved by matching `class_maps.json` name strings
against anchor display names, unlike the nuclear path):

| class_id | trainer's label (`class_maps.json`) | topic-103 anchor |
|---|---|---|
| 0 | 性別議題 | `anchor_equality` 性別平等 |
| 1 | 國防 | `anchor_defense` 國防戰力 |
| 2 | 徵兵制度 | `anchor_policy` 政策制度 |
| 3 | 軍中狀況 | `anchor_military_conditions` 軍中現況 |
| 4 | 社會大眾 | `anchor_social` 社會影響 |
| 5 | 其他 | (none — never emitted as a node) |

`class_maps.json`'s `class_name_map` / `cluster_name_map` keep the trainer's
original Chinese labels verbatim — they're used for the human-readable
`rationale` string on each candidate item, not for anchor resolution.

## Files excluded from git

Same convention as `nuclear_node_model`: `model.safetensors` in each
`model_macro/` / `model_micro_*/` directory is gitignored (see root
`.gitignore`). Everyone setting up this repo needs those files copied in
separately before topic 103's local classifier path will work; without them,
analysis for topic 103 will raise `FileNotFoundError`.
