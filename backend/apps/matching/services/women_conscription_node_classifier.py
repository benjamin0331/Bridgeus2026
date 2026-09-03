"""Local two-stage BERT classifier for topic-103 (女性義務兵役) semantic-tree
nodes. Mirrors `nuclear_node_classifier.py` (topic 102) — same macro -> micro
pipeline, same candidate-item shape for `semantic_tree.validate_analysis_items`.

See `apps/matching/ml_models/women_conscription_node_model/` for the model
weights (`class_maps.json` documents the trainer's original macro/micro
Chinese labels; those are display-only, see CLASS_ID_TO_ANCHOR_ID below for
why anchor resolution does NOT go through name matching here).

Coverage gap (confirmed intentional, not a bug): the trained model only has
5 real macro classes (0-4) plus a catch-all "其他" (OTHER_CLASS_ID = 5,
mirrors class 6 in the nuclear model). Topic 103's fixed anchors in
`api/dialogue_topics.py` were trimmed from 6 to 5 to match — there is no
macro class for "體能訓練" or "個人意願"; that content either falls under one
of the 5 real classes or into the "其他" bucket, which — same as nuclear's
class 6 — never produces a node.
"""

import json
import threading
from pathlib import Path
from typing import Any

MODEL_DIR = Path(__file__).resolve().parent.parent / "ml_models" / "women_conscription_node_model"
MAX_LENGTH = 128
OTHER_CLASS_ID = 5  # "其他" — no anchor, no micro model, never emitted as a node

# Fixed class_id -> anchor_id mapping (topic 103's 5 real anchors, see
# api/dialogue_topics.py TOPIC_CONFIGS[103]["anchors"]).
#
# Deliberately NOT resolved by matching `class_name_map` strings against the
# anchors' display names (unlike nuclear_node_classifier) — the trainer's
# raw macro labels ("國防", "軍中狀況", "社會大眾"...) are short internal
# shorthand, not meant to be the user-facing anchor names, so keeping them as
# the join key would either force ugly anchor names or silently fail to
# match. A fixed id->id table is also more robust: it can't break if
# class_maps.json is edited for display purposes later.
CLASS_ID_TO_ANCHOR_ID: dict[int, str] = {
    0: "anchor_equality",
    1: "anchor_defense",
    2: "anchor_policy",
    3: "anchor_military_conditions",
    4: "anchor_social",
}

_lock = threading.Lock()
_state: dict[str, Any] = {}


def _device():
    import torch

    return "cuda" if torch.cuda.is_available() else "cpu"


def _load_label_mapping(model_dir: Path) -> dict[int, int]:
    with open(model_dir / "label_mapping.json", "r", encoding="utf-8") as f:
        info = json.load(f)
    # raw_label_to_id: {"<raw_id>": <softmax_index>} -> invert to {index: raw_id}
    return {int(v): int(k) for k, v in info["raw_label_to_id"].items()}


def _load_class_maps() -> dict[str, dict[int, str]]:
    with open(MODEL_DIR / "class_maps.json", "r", encoding="utf-8") as f:
        data = json.load(f)
    return {
        "class_name_map": {int(k): v for k, v in data["class_name_map"].items()},
        "cluster_name_map": {int(k): v for k, v in data["cluster_name_map"].items()},
    }


def _ensure_loaded() -> dict[str, Any]:
    if _state:
        return _state
    with _lock:
        if _state:
            return _state

        from transformers import AutoModelForSequenceClassification, AutoTokenizer

        macro_dir = MODEL_DIR / "model_macro"
        if not (macro_dir / "model.safetensors").exists():
            raise FileNotFoundError(
                f"找不到女性義務兵役節點分類模型權重: {macro_dir / 'model.safetensors'}。"
                " 這個檔案未進版控，需從共用空間另外複製進來。"
            )

        device = _device()
        macro_tokenizer = AutoTokenizer.from_pretrained(str(macro_dir))
        macro_model = AutoModelForSequenceClassification.from_pretrained(str(macro_dir))
        macro_model.to(device)
        macro_model.eval()

        _state.update(
            {
                "device": device,
                "macro_tokenizer": macro_tokenizer,
                "macro_model": macro_model,
                "macro_mapping": _load_label_mapping(macro_dir),
                "micro_cache": {},
                **_load_class_maps(),
            }
        )
    return _state


def _get_micro_model(class_id: int):
    state = _ensure_loaded()
    with _lock:
        cache = state["micro_cache"]
        if class_id in cache:
            return cache[class_id]

        micro_dir = MODEL_DIR / f"model_micro_{class_id}"
        if not (micro_dir / "config.json").exists() or not (micro_dir / "model.safetensors").exists():
            cache[class_id] = None
            return None

        from transformers import AutoModelForSequenceClassification, AutoTokenizer

        tokenizer = AutoTokenizer.from_pretrained(str(micro_dir))
        model = AutoModelForSequenceClassification.from_pretrained(str(micro_dir))
        model.to(state["device"])
        model.eval()
        mapping = _load_label_mapping(micro_dir)

        cache[class_id] = (tokenizer, model, mapping)
        return cache[class_id]


def classify(text: str) -> dict[str, Any]:
    """Run the macro -> micro pipeline on one message.

    Returns class_id/class_name/class_conf (anchor-level) and
    cluster_id/cluster_name/cluster_conf (sub-topic level; cluster_id is -1
    when class_id has no trained micro model, i.e. class_id == OTHER_CLASS_ID).
    """
    import torch

    state = _ensure_loaded()
    device = state["device"]

    macro_inputs = state["macro_tokenizer"](
        text, return_tensors="pt", truncation=True, padding="max_length", max_length=MAX_LENGTH
    )
    macro_inputs = {k: v.to(device) for k, v in macro_inputs.items()}
    with torch.no_grad():
        macro_logits = state["macro_model"](**macro_inputs).logits
        macro_probs = torch.softmax(macro_logits, dim=-1).cpu().numpy()[0]

    macro_idx = int(macro_probs.argmax())
    class_id = state["macro_mapping"].get(macro_idx, macro_idx)
    class_conf = float(macro_probs[macro_idx])

    micro_components = _get_micro_model(class_id)
    if micro_components is None:
        return {
            "class_id": class_id,
            "class_name": state["class_name_map"].get(class_id, "未知"),
            "class_conf": class_conf,
            "cluster_id": -1,
            "cluster_name": "未分流/未訓練子模型",
            "cluster_conf": 0.0,
        }

    micro_tokenizer, micro_model, micro_mapping = micro_components
    micro_inputs = micro_tokenizer(
        text, return_tensors="pt", truncation=True, padding="max_length", max_length=MAX_LENGTH
    )
    micro_inputs = {k: v.to(device) for k, v in micro_inputs.items()}
    with torch.no_grad():
        micro_logits = micro_model(**micro_inputs).logits
        micro_probs = torch.softmax(micro_logits, dim=-1).cpu().numpy()[0]

    micro_idx = int(micro_probs.argmax())
    cluster_id = micro_mapping.get(micro_idx, micro_idx)
    cluster_conf = float(micro_probs[micro_idx])

    return {
        "class_id": class_id,
        "class_name": state["class_name_map"].get(class_id, "未知"),
        "class_conf": class_conf,
        "cluster_id": cluster_id,
        "cluster_name": state["cluster_name_map"].get(cluster_id, "未知"),
        "cluster_conf": cluster_conf,
    }


# Same convention as nuclear_node_classifier.COMPOUND_CLUSTER_IDS: these
# cluster ids were merged from multiple sparse sub-topics during training, so
# their cluster_name is a "/"-joined compound label (see class_maps.json)
# rather than one concept. Emitting the raw compound string as a node name is
# misleading, so for these ids only, pick the single "/" segment whose
# embedding is closest (cosine) to the message.
COMPOUND_CLUSTER_IDS = {6, 9, 11, 17}


def _resolve_point_name(cleaned_text: str, cluster_id: int, cluster_name: str) -> str:
    if cluster_id not in COMPOUND_CLUSTER_IDS:
        return cluster_name

    segments = [segment.strip() for segment in cluster_name.split("/") if segment.strip()]
    if len(segments) <= 1:
        return cluster_name

    from chat.services.embedding import cosine_similarity, get_embedding

    text_vec = get_embedding(cleaned_text)
    return max(segments, key=lambda segment: cosine_similarity(text_vec, get_embedding(segment)))


def build_candidate_items(text: str, anchors: list[dict[str, str]]) -> list[dict[str, Any]]:
    """Classify `text` and shape the result as a candidate item list for
    `semantic_tree.validate_analysis_items`. Empty list means "no node" —
    same convention as the OpenAI path returning `items: []`.

    `anchors` is accepted for signature parity with `nuclear_node_classifier`
    but is not used to resolve the anchor id — see CLASS_ID_TO_ANCHOR_ID.
    """
    cleaned = (text or "").strip()
    if not cleaned:
        return []

    result = classify(cleaned)
    if result["class_id"] == OTHER_CLASS_ID or result["cluster_id"] == -1:
        return []

    anchor_id = CLASS_ID_TO_ANCHOR_ID.get(result["class_id"])
    if anchor_id is None:
        return []

    point_name = _resolve_point_name(cleaned, result["cluster_id"], result["cluster_name"])

    from apps.matching.services.semantic_tree import classify_stance_with_openai

    stance = classify_stance_with_openai(text=cleaned, context_label=point_name)

    return [
        {
            "claimText": cleaned,
            "anchorId": anchor_id,
            "path": [],
            "pointName": point_name,
            "stance": stance,
            "confidence": result["cluster_conf"],
            "rationale": (
                f"分類模型判斷：{result['class_name']}（{result['class_conf']:.2f}）"
                f" > {result['cluster_name']}（{result['cluster_conf']:.2f}）"
            ),
        }
    ]
