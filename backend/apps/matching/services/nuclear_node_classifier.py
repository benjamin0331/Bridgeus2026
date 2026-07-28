"""Local two-stage BERT classifier for topic-102 (nuclear energy) semantic-tree
nodes. Replaces the OpenAI call in `semantic_tree.analyze_with_openai` for this
one topic; produces the same candidate-item shape so it can be run through
`semantic_tree.validate_analysis_items` unchanged.

See `apps/matching/ml_models/nuclear_node_model/README.md` for the model
architecture and provenance.
"""

import json
import threading
from pathlib import Path
from typing import Any

MODEL_DIR = Path(__file__).resolve().parent.parent / "ml_models" / "nuclear_node_model"
MAX_LENGTH = 128
OTHER_CLASS_ID = 6  # "其他" — no anchor, no micro model, never emitted as a node

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
                f"找不到核能節點分類模型權重: {macro_dir / 'model.safetensors'}。"
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


def build_candidate_items(text: str, anchors: list[dict[str, str]]) -> list[dict[str, Any]]:
    """Classify `text` and shape the result as a candidate item list for
    `semantic_tree.validate_analysis_items`. Empty list means "no node" —
    same convention as the OpenAI path returning `items: []`.
    """
    cleaned = (text or "").strip()
    if not cleaned:
        return []

    result = classify(cleaned)
    if result["class_id"] == OTHER_CLASS_ID or result["cluster_id"] == -1:
        return []

    anchor_id = next(
        (anchor["id"] for anchor in anchors if anchor["name"] == result["class_name"]),
        None,
    )
    if anchor_id is None:
        return []

    return [
        {
            "claimText": cleaned,
            "anchorId": anchor_id,
            "path": [],
            "pointName": result["cluster_name"],
            "stance": "中立",
            "confidence": result["cluster_conf"],
            "rationale": (
                f"分類模型判斷：{result['class_name']}（{result['class_conf']:.2f}）"
                f" > {result['cluster_name']}（{result['cluster_conf']:.2f}）"
            ),
        }
    ]
