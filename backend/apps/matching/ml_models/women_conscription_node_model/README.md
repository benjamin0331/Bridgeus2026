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
| 2 | 徵兵制度 | `anchor_policy` 徵兵制度 |
| 3 | 軍中狀況 | `anchor_military_conditions` 軍中現況 |
| 4 | 社會大眾 | `anchor_social` 社會影響 |
| 5 | 其他 | (none — never emitted as a node) |

`class_maps.json`'s `class_name_map` / `cluster_name_map` keep the trainer's
original Chinese labels verbatim — they're used for the human-readable
`rationale` string on each candidate item, not for anchor resolution.

## Setting this up — what's missing from git, and why

Two different things are absent, and conflating them wastes an afternoon:

**1. `model.safetensors` — gitignored on purpose (~391MB each).** Same
convention as `nuclear_node_model`; see the root `.gitignore`. These will
never be in the repo.

**2. Everything else in `model_macro/` and `model_micro_*/` — simply not
committed yet.** `config.json`, `tokenizer.json`, `tokenizer_config.json`,
`special_tokens_map.json`, `vocab.txt`, `label_mapping.json`. These are
*not* gitignored (the ignore rules only name `model.safetensors`) and they
are small — the equivalent 44 files for `nuclear_node_model` total 3.8MB, and
that model does track them. They're missing here only because the model
directories were never added. **Whoever has the trained model should commit
these**, so that copying in the weights is the only manual step, exactly as
it is for topic 102.

Until then, copy in the whole `model_macro/` and `model_micro_*/`
directories, not just the weights — with the weights alone,
`AutoTokenizer.from_pretrained()` fails on the missing tokenizer files, which
looks like an unrelated bug.

Without any of it, analysis for topic 103 raises `FileNotFoundError`, which
`semantic_tree.analyze_text_for_tree` converts into
`MissingLocalClassifierModel` → HTTP 503 `missing_local_classifier_model`,
carrying the message that names the missing path.
