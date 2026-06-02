import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from copy import deepcopy
from typing import Any

from django.db import transaction
from django.utils import timezone

from api.models import AIConversation, DialogueMatch


DEFAULT_MODEL = "gemini-2.5-flash"
MAX_ANALYSIS_ITEMS = 3
MAX_PATH_DEPTH = 2
MIN_CONFIDENCE = 0.55
SEMANTIC_TREE_STATS_KEY = "semantic_tree"
SEMANTIC_TREE_STATE_VERSION = 2
MATCH_TREE_MODE = "participant_trees"
AI_TREE_MODE = "ai_user_tree"
OWNER_USER_A = "user_a"
OWNER_USER_B = "user_b"
OWNER_AI_USER = "user"

FIXED_ANCHORS = [
    {"id": "anchor_safety", "name": "核能安全"},
    {"id": "anchor_economy", "name": "經濟成本"},
    {"id": "anchor_energy", "name": "能源問題"},
    {"id": "anchor_environment", "name": "環境保護"},
    {"id": "anchor_governance", "name": "民主治理"},
    {"id": "anchor_waste", "name": "核廢處理"},
]

ANALYSIS_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "maxItems": MAX_ANALYSIS_ITEMS,
            "items": {
                "type": "object",
                "properties": {
                    "claimText": {
                        "type": "string",
                        "description": "從使用者輸入拆出的單一語意重點。",
                    },
                    "anchorId": {
                        "type": "string",
                        "enum": [anchor["id"] for anchor in FIXED_ANCHORS],
                        "description": "最適合的固定第二層分類 id。",
                    },
                    "path": {
                        "type": "array",
                        "maxItems": MAX_PATH_DEPTH,
                        "items": {"type": "string"},
                        "description": "anchor 底下的子分類路徑，不包含 anchor 名稱。可為空陣列。",
                    },
                    "pointName": {
                        "type": "string",
                        "description": "要顯示在圖上的短節點名稱。",
                    },
                    "stance": {
                        "type": "string",
                        "description": "支持、反對、中立或混合。",
                    },
                    "confidence": {
                        "type": "number",
                        "minimum": MIN_CONFIDENCE,
                        "maximum": 1,
                        "description": "分類信心，0.55 到 1。低於 0.55 不要輸出。",
                    },
                    "rationale": {
                        "type": "string",
                        "description": "簡短說明為什麼這樣分類。",
                    },
                },
                "required": [
                    "claimText",
                    "anchorId",
                    "path",
                    "pointName",
                    "stance",
                    "confidence",
                    "rationale",
                ],
                "additionalProperties": False,
            },
        }
    },
    "required": ["items"],
    "additionalProperties": False,
}


class SemanticTreeError(Exception):
    status_code = 500
    code = "semantic_tree_failed"


class MissingGeminiApiKey(SemanticTreeError):
    status_code = 503
    code = "missing_gemini_api_key"


class GeminiApiError(SemanticTreeError):
    status_code = 502
    code = "gemini_api_failed"


def clean_text(value: Any) -> str:
    return str(value or "").strip()


def create_anchor_node(anchor: dict[str, str]) -> dict[str, Any]:
    return {
        "id": anchor["id"],
        "name": anchor["name"],
        "type": "anchor",
        "hiddenUntilUsed": True,
        "children": [],
    }


def create_initial_tree(root_name: str = "核電") -> dict[str, Any]:
    return {
        "id": "root",
        "name": root_name or "核電",
        "type": "root",
        "children": [create_anchor_node(anchor) for anchor in FIXED_ANCHORS],
    }


def create_owner_tree_state(owner_key: str, root_name: str) -> dict[str, Any]:
    return {
        "ownerKey": owner_key,
        "treeData": create_initial_tree(root_name),
        "analyzedSourceIds": [],
        "analysisHistory": [],
    }


def _empty_match_state(root_name: str) -> dict[str, Any]:
    return {
        "version": SEMANTIC_TREE_STATE_VERSION,
        "mode": MATCH_TREE_MODE,
        "anchors": deepcopy(FIXED_ANCHORS),
        "participants": {
            OWNER_USER_A: create_owner_tree_state(OWNER_USER_A, root_name),
            OWNER_USER_B: create_owner_tree_state(OWNER_USER_B, root_name),
        },
    }


def _empty_ai_state(root_name: str) -> dict[str, Any]:
    return {
        "version": SEMANTIC_TREE_STATE_VERSION,
        "mode": AI_TREE_MODE,
        "anchors": deepcopy(FIXED_ANCHORS),
        "participants": {
            OWNER_AI_USER: create_owner_tree_state(OWNER_AI_USER, root_name),
        },
    }


def _stats_dict(match: DialogueMatch) -> dict[str, Any]:
    return match.stats if isinstance(match.stats, dict) else {}


def _normalize_source_ids(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [clean_text(item) for item in value if clean_text(item)]


def _ensure_owner_tree_state(
    owner_state: Any,
    *,
    owner_key: str,
    root_name: str,
) -> dict[str, Any]:
    if not isinstance(owner_state, dict):
        owner_state = create_owner_tree_state(owner_key, root_name)

    owner_state["ownerKey"] = owner_key
    tree_data = owner_state.get("treeData")
    if not isinstance(tree_data, dict) or not isinstance(tree_data.get("children"), list):
        tree_data = create_initial_tree(root_name)
        owner_state["treeData"] = tree_data

    if root_name and tree_data.get("name") in {None, "", "核電"}:
        tree_data["name"] = root_name

    owner_state["analyzedSourceIds"] = _normalize_source_ids(
        owner_state.get("analyzedSourceIds")
        or owner_state.get("analyzedMessageIds")
    )
    if not isinstance(owner_state.get("analysisHistory"), list):
        owner_state["analysisHistory"] = []

    _normalize_tree_node_types(tree_data)
    _ensure_fixed_anchors(tree_data)
    return owner_state


def get_semantic_tree_state(match: DialogueMatch, *, root_name: str) -> dict[str, Any]:
    stats = _stats_dict(match)
    state = stats.get(SEMANTIC_TREE_STATS_KEY)

    # v1 stored one room-wide tree. v2 intentionally starts fresh so new
    # analysis represents each participant's own thought context.
    if (
        not isinstance(state, dict)
        or state.get("version") != SEMANTIC_TREE_STATE_VERSION
        or state.get("mode") != MATCH_TREE_MODE
        or not isinstance(state.get("participants"), dict)
    ):
        return _empty_match_state(root_name)

    state["anchors"] = deepcopy(FIXED_ANCHORS)
    participants = state.setdefault("participants", {})
    participants[OWNER_USER_A] = _ensure_owner_tree_state(
        participants.get(OWNER_USER_A),
        owner_key=OWNER_USER_A,
        root_name=root_name,
    )
    participants[OWNER_USER_B] = _ensure_owner_tree_state(
        participants.get(OWNER_USER_B),
        owner_key=OWNER_USER_B,
        root_name=root_name,
    )
    return state


def get_ai_semantic_tree_state(
    session_record: dict[str, Any],
    *,
    root_name: str,
) -> dict[str, Any]:
    state = session_record.get(SEMANTIC_TREE_STATS_KEY)
    if (
        not isinstance(state, dict)
        or state.get("version") != SEMANTIC_TREE_STATE_VERSION
        or state.get("mode") != AI_TREE_MODE
        or not isinstance(state.get("participants"), dict)
    ):
        return _empty_ai_state(root_name)

    state["anchors"] = deepcopy(FIXED_ANCHORS)
    participants = state.setdefault("participants", {})
    participants[OWNER_AI_USER] = _ensure_owner_tree_state(
        participants.get(OWNER_AI_USER),
        owner_key=OWNER_AI_USER,
        root_name=root_name,
    )
    return state


def save_semantic_tree_state(match: DialogueMatch, state: dict[str, Any]) -> None:
    stats = _stats_dict(match).copy()
    stats[SEMANTIC_TREE_STATS_KEY] = state
    match.stats = stats
    match.save(update_fields=["stats"])


def save_ai_semantic_tree_state(
    session_record: dict[str, Any],
    state: dict[str, Any],
) -> None:
    session_record[SEMANTIC_TREE_STATS_KEY] = state


def _normalize_tree_node_types(node: dict[str, Any], depth: int = 0) -> None:
    if depth == 0:
        node["type"] = "root"
    elif depth == 1:
        node["type"] = "anchor"
    else:
        node["type"] = "point"

    for child in node.get("children") or []:
        if isinstance(child, dict):
            _normalize_tree_node_types(child, depth + 1)


def _ensure_fixed_anchors(tree_data: dict[str, Any]) -> None:
    children = [child for child in tree_data.get("children", []) if isinstance(child, dict)]
    child_by_id = {child.get("id"): child for child in children}
    ordered_children = []

    for anchor in FIXED_ANCHORS:
        node = child_by_id.get(anchor["id"]) or create_anchor_node(anchor)
        node["id"] = anchor["id"]
        node["name"] = anchor["name"]
        node["type"] = "anchor"
        node.setdefault("hiddenUntilUsed", True)
        node.setdefault("children", [])
        ordered_children.append(node)

    tree_data["children"] = ordered_children


def find_node_by_id(node: dict[str, Any] | None, node_id: str) -> dict[str, Any] | None:
    if not node or not node_id:
        return None
    if node.get("id") == node_id:
        return node
    for child in node.get("children") or []:
        match = find_node_by_id(child, node_id)
        if match:
            return match
    return None


def find_child_by_name(node: dict[str, Any] | None, name: str) -> dict[str, Any] | None:
    return next(
        (
            child
            for child in (node or {}).get("children", [])
            if isinstance(child, dict) and child.get("name") == name
        ),
        None,
    )


def compact_tree_for_prompt(
    node: dict[str, Any] | None,
    *,
    depth: int = 0,
    max_depth: int = 5,
) -> dict[str, Any] | None:
    if not node or depth > max_depth:
        return None

    return {
        "id": node.get("id"),
        "name": node.get("name"),
        "type": node.get("type"),
        "children": [
            child
            for child in (
                compact_tree_for_prompt(child, depth=depth + 1, max_depth=max_depth)
                for child in node.get("children") or []
            )
            if child
        ],
    }


def build_gemini_request(
    *,
    text: str,
    tree: dict[str, Any],
    anchors: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    resolved_anchors = anchors or FIXED_ANCHORS
    anchor_list = "\n".join(f"{anchor['id']}: {anchor['name']}" for anchor in resolved_anchors)
    tree_summary = json.dumps(compact_tree_for_prompt(tree), ensure_ascii=False, indent=2)

    prompt_text = "\n".join(
        [
            "你是「核能議題個人想法脈絡樹」的語意整理 agent，不是逐字拆句工具。",
            "",
            "你的任務：",
            "只整理單一說話者自己的想法脈絡，把這次輸入歸納成少量、高品質、適合放進樹狀圖的語意節點。",
            "你看見的目前樹狀資料只屬於同一位說話者；不要推論或整理對話中另一個人的立場。",
            "重點是「歸納」而不是「盡量拆分」。",
            "",
            "第二層固定分類不可新增，只能使用下列 anchorId：",
            anchor_list,
            "",
            "核心原則：",
            f"1. 每次輸入最多輸出 {MAX_ANALYSIS_ITEMS} 個 items。",
            "2. 優先輸出 1 到 2 個 items；只有當輸入明確包含不同議題時才輸出第 3 個。",
            "3. 不要因為一句話裡有「例如、等等、包含、以及」就拆成很多節點。",
            "4. 相近意思要合併成同一個 claim。",
            "5. 不要把原因、例子、補充說明拆成獨立節點，除非它本身是另一個明確議題。",
            "6. 若使用者只是在表達單一立場，請產生一個總結型節點。",
            "",
            "分類規則：",
            "- anchorId 必須是最主要的議題分類。",
            "- 如果同一段話同時涉及兩個主題，才分成兩個 items。",
            "- 例如「經濟效益低，因為維護成本和核廢料處理」：",
            "  - 可輸出「核能經濟效益偏低」到 anchor_economy。",
            "  - 可輸出「核廢處理增加長期負擔」到 anchor_waste。",
            "  - 不要再拆出「維護成本」、「處理成本」、「長期成本」等重複節點。",
            "",
            "path 規則：",
            "- path 不包含 anchor 本身，只描述 anchor 底下的子分類路徑。",
            f"- path 最多 {MAX_PATH_DEPTH} 層。",
            "- 優先使用目前這位說話者樹中已有的相近節點名稱。",
            "- 只有當現有節點完全不適合時，才建立新的子分類。",
            "- 新子分類名稱必須抽象、可容納未來類似討論，不要太細。",
            "- 不要為單一小例子建立新子分類。",
            "",
            "pointName 規則：",
            "- pointName 是圖上顯示的節點名稱，必須短、清楚、可讀。",
            "- 長度建議 6 到 12 個中文字。",
            "- 不要使用完整句子。",
            "- 不要使用「等等」、「很多問題」、「有疑慮」這種模糊名稱。",
            "- 不要和 path 最後一層完全同名。",
            "",
            "confidence 規則：",
            "- 只有在你有明確判斷時才輸出 item。",
            f"- confidence 低於 {MIN_CONFIDENCE} 的內容不要輸出。",
            "- 如果輸入太模糊，輸出 items: []。",
            "",
            "stance 規則：",
            "- stance 只能用：支持、反對、中立、混合。",
            "- 根據使用者語氣判斷，不要過度推論。",
            "",
            "輸出品質要求：",
            "- 請避免製造太多節點。",
            "- 請避免同義節點。",
            "- 請避免過深 path。",
            "- 請讓結果適合視覺化，而不是適合文章摘要。",
            "",
            "目前樹狀資料摘要：",
            tree_summary,
            "",
            "使用者輸入：",
            clean_text(text),
        ]
    )

    return {
        "contents": [
            {
                "role": "user",
                "parts": [{"text": prompt_text}],
            }
        ],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseJsonSchema": ANALYSIS_RESPONSE_SCHEMA,
        },
    }


def _item_error(raw_item: Any, message: str) -> dict[str, Any]:
    item = raw_item.copy() if isinstance(raw_item, dict) else {"value": raw_item}
    item["error"] = message
    return item


def validate_analysis_items(
    payload: dict[str, Any],
    tree: dict[str, Any],
    anchors: list[dict[str, str]] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    anchor_map = {anchor["id"]: anchor for anchor in (anchors or FIXED_ANCHORS)}
    items = []
    invalid_items = []

    raw_items = payload.get("items") if isinstance(payload, dict) else []
    for raw_item in raw_items if isinstance(raw_items, list) else []:
        claim_text = clean_text(raw_item.get("claimText") if isinstance(raw_item, dict) else "")
        anchor_id = clean_text(raw_item.get("anchorId") if isinstance(raw_item, dict) else "")
        point_name = clean_text(raw_item.get("pointName") if isinstance(raw_item, dict) else "")
        stance = clean_text(raw_item.get("stance") if isinstance(raw_item, dict) else "") or "中立"
        rationale = clean_text(raw_item.get("rationale") if isinstance(raw_item, dict) else "")
        try:
            confidence = float(raw_item.get("confidence"))
        except (TypeError, ValueError, AttributeError):
            confidence = float("nan")
        path = (
            [clean_text(segment) for segment in raw_item.get("path", [])]
            if isinstance(raw_item, dict) and isinstance(raw_item.get("path"), list)
            else []
        )
        path = [segment for segment in path if segment]

        if not claim_text:
            invalid_items.append(_item_error(raw_item, "empty claimText"))
            continue
        if anchor_id not in anchor_map:
            invalid_items.append(_item_error(raw_item, f"unknown anchorId: {anchor_id or '(empty)'}"))
            continue
        if not point_name:
            invalid_items.append(_item_error(raw_item, "empty pointName"))
            continue
        if confidence != confidence:
            invalid_items.append(_item_error(raw_item, "missing confidence"))
            continue
        if confidence < MIN_CONFIDENCE:
            invalid_items.append(_item_error(raw_item, f"confidence below {MIN_CONFIDENCE}"))
            continue
        if confidence > 1:
            invalid_items.append(_item_error(raw_item, "confidence must be 1 or lower"))
            continue
        if len(path) > MAX_PATH_DEPTH:
            invalid_items.append(_item_error(raw_item, f"path too deep: maximum {MAX_PATH_DEPTH}"))
            continue
        if len(items) >= MAX_ANALYSIS_ITEMS:
            invalid_items.append(_item_error(raw_item, f"maximum of {MAX_ANALYSIS_ITEMS} items exceeded"))
            continue

        anchor_node = find_node_by_id(tree, anchor_id) or {"id": anchor_id, "children": []}
        parent_node = anchor_node
        for segment in path:
            parent_node = find_child_by_name(parent_node, segment) or {
                "id": f"virtual_{segment}",
                "name": segment,
                "children": [],
            }

        items.append(
            {
                "claimText": claim_text,
                "anchorId": anchor_id,
                "path": path,
                "pointName": point_name,
                "stance": stance,
                "confidence": confidence,
                "rationale": rationale,
                "mergeWithParent": bool(path and path[-1] == point_name),
                "mergeTargetName": (find_child_by_name(parent_node, point_name) or {}).get("name", ""),
            }
        )

    return {"items": items, "invalidItems": invalid_items}


def _max_generated_counter(node: dict[str, Any] | None) -> int:
    if not node:
        return 0
    match = re.match(r"^agent_(\d+)$", clean_text(node.get("id")))
    current = int(match.group(1)) if match else 0
    child_max = max((_max_generated_counter(child) for child in node.get("children") or []), default=0)
    return max(current, child_max)


def _create_generated_point_node(name: str, counter: int, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "id": f"agent_{counter}",
        "name": name,
        "type": "point",
        "children": [],
        **(metadata or {}),
    }


def _message_source_metadata(source_message: dict[str, Any] | None) -> dict[str, Any]:
    if not source_message:
        return {}
    return {
        "source": clean_text(source_message.get("source")) or "match_message",
        "sourceMessageId": clean_text(source_message.get("id")),
        "sourceTimestamp": clean_text(source_message.get("created_at")),
        "sourceParticipant": clean_text(source_message.get("sender_id")),
        "sourceMatchId": clean_text(source_message.get("match_id")),
        "sourceSessionId": clean_text(source_message.get("session_id")),
    }


def _append_node_message(
    node: dict[str, Any],
    item: dict[str, Any],
    *,
    mode: str,
    source_message: dict[str, Any] | None,
) -> bool:
    node.setdefault("messages", [])
    text = clean_text(item.get("claimText"))
    source_message_id = clean_text((source_message or {}).get("id"))
    already_exists = any(
        message.get("text") == text and clean_text(message.get("sourceMessageId")) == source_message_id
        for message in node["messages"]
    )
    if already_exists:
        return False

    node["messages"].append(
        {
            "text": text,
            "stance": item.get("stance") or "中立",
            "confidence": item.get("confidence"),
            "rationale": item.get("rationale") or "",
            "mode": mode,
            "recordedAt": timezone.now().isoformat(),
            **_message_source_metadata(source_message),
        }
    )
    if not node.get("claimText") and mode != "category":
        node["claimText"] = text
    return True


def apply_analysis_items_to_tree(
    tree: dict[str, Any],
    items: list[dict[str, Any]],
    *,
    source_message: dict[str, Any] | None = None,
) -> dict[str, Any]:
    applied_items = []
    counter = _max_generated_counter(tree)

    for item in items:
        anchor_node = find_node_by_id(tree, item.get("anchorId"))
        if not anchor_node:
            continue

        anchor_node["hiddenUntilUsed"] = False
        parent_node = anchor_node

        for path_segment in item.get("path") or []:
            existing_node = find_child_by_name(parent_node, path_segment)
            if existing_node:
                parent_node = existing_node
                continue

            counter += 1
            new_node = _create_generated_point_node(
                path_segment,
                counter,
                {
                    "semanticRole": "category",
                    "generatedBy": "gemini",
                    "sourceClaim": item.get("claimText"),
                },
            )
            parent_node.setdefault("children", []).append(new_node)
            parent_node = new_node

        existing_point_node = find_child_by_name(parent_node, item.get("pointName"))
        if item.get("mergeWithParent") or parent_node.get("name") == item.get("pointName"):
            target_node = parent_node
            mode = "merge-parent"
        elif existing_point_node:
            target_node = existing_point_node
            mode = "merge-existing"
        else:
            counter += 1
            target_node = _create_generated_point_node(
                item.get("pointName"),
                counter,
                {
                    "generatedBy": "gemini",
                    "stance": item.get("stance"),
                    "confidence": item.get("confidence"),
                    "rationale": item.get("rationale"),
                },
            )
            parent_node.setdefault("children", []).append(target_node)
            mode = "new"

        _append_node_message(target_node, item, mode=mode, source_message=source_message)
        applied_items.append(
            {
                **item,
                "nodeId": target_node.get("id"),
                "appliedAt": timezone.now().isoformat(),
                "applyMode": mode,
            }
        )

    _normalize_tree_node_types(tree)
    return {"treeData": tree, "appliedItems": applied_items}


def strip_json_fence(value: str) -> str:
    text = clean_text(value)
    match = re.match(r"^```(?:json)?\s*([\s\S]*?)\s*```$", text, re.IGNORECASE)
    return match.group(1).strip() if match else text


def parse_gemini_response(response_body: dict[str, Any]) -> dict[str, Any]:
    parts = response_body.get("candidates", [{}])[0].get("content", {}).get("parts", [])
    text = "".join(part.get("text", "") for part in parts).strip()
    if not text:
        raise GeminiApiError("Gemini response did not include JSON text.")
    try:
        return json.loads(strip_json_fence(text))
    except json.JSONDecodeError as exc:
        raise GeminiApiError(f"Gemini response JSON parse failed: {exc}") from exc


def get_gemini_api_key() -> str:
    return clean_text(os.getenv("GEMINI_API_KEY"))


def get_gemini_model() -> str:
    return clean_text(os.getenv("GEMINI_MODEL")) or DEFAULT_MODEL


def analyze_with_gemini(
    *,
    text: str,
    tree: dict[str, Any],
    anchors: list[dict[str, str]] | None = None,
    api_key: str | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    cleaned_text = clean_text(text)
    if not cleaned_text:
        return {"items": [], "invalidItems": [], "model": model or get_gemini_model()}

    resolved_api_key = api_key if api_key is not None else get_gemini_api_key()
    if not resolved_api_key:
        raise MissingGeminiApiKey("server 缺少 GEMINI_API_KEY，無法呼叫 Gemini。")

    resolved_model = model or get_gemini_model()
    request_body = build_gemini_request(text=cleaned_text, tree=tree, anchors=anchors or FIXED_ANCHORS)
    endpoint = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{urllib.parse.quote(resolved_model, safe='')}:generateContent"
    )
    request = urllib.request.Request(
        endpoint,
        data=json.dumps(request_body, ensure_ascii=False).encode("utf-8"),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "x-goog-api-key": resolved_api_key,
        },
    )

    try:
        timeout = float(os.getenv("GEMINI_API_TIMEOUT_SECONDS", "20"))
        with urllib.request.urlopen(request, timeout=timeout) as response:
            response_body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        response_text = exc.read().decode("utf-8", errors="replace")
        raise GeminiApiError(f"Gemini API error {exc.code}: {response_text}") from exc
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise GeminiApiError(f"Gemini API request failed: {exc}") from exc

    parsed = parse_gemini_response(response_body)
    return {
        **validate_analysis_items(parsed, tree, anchors or FIXED_ANCHORS),
        "model": resolved_model,
    }


def _message_to_source(message) -> dict[str, Any]:
    return {
        "source": "match_message",
        "id": message.id,
        "content": message.content,
        "created_at": message.created_at.isoformat() if message.created_at else "",
        "sender_id": message.sender_id,
        "match_id": message.match_id,
    }


def _ai_turn_to_source(turn: AIConversation) -> dict[str, Any]:
    return {
        "source": "ai_user_prompt",
        "id": turn.id,
        "content": turn.user_prompt,
        "created_at": turn.created_at.isoformat() if turn.created_at else "",
        "sender_id": turn.user_id,
        "session_id": turn.session_id,
    }


def _owner_key_for_user(match: DialogueMatch, user_id: int | None) -> str:
    if user_id == match.user_a_id:
        return OWNER_USER_A
    if user_id == match.user_b_id:
        return OWNER_USER_B
    return OWNER_USER_A


def _owner_key_for_message(match: DialogueMatch, sender_id: int | None) -> str | None:
    if sender_id == match.user_a_id:
        return OWNER_USER_A
    if sender_id == match.user_b_id:
        return OWNER_USER_B
    return None


def _owner_payload(
    owner_state: dict[str, Any],
    *,
    label: str,
    is_current_user: bool,
) -> dict[str, Any]:
    return {
        "ownerKey": owner_state["ownerKey"],
        "label": label,
        "isCurrentUser": is_current_user,
        "treeData": owner_state["treeData"],
        "analysisHistory": owner_state["analysisHistory"],
        "analyzedSourceIds": owner_state["analyzedSourceIds"],
    }


def _match_tree_payloads(
    *,
    state: dict[str, Any],
    match: DialogueMatch,
    current_user_id: int | None,
) -> list[dict[str, Any]]:
    current_owner_key = _owner_key_for_user(match, current_user_id)
    owner_state = state["participants"][current_owner_key]
    return [
        _owner_payload(
            owner_state,
            label="我的脈絡",
            is_current_user=True,
        )
    ]


def _active_tree_payload(trees: list[dict[str, Any]]) -> dict[str, Any]:
    return next((tree for tree in trees if tree.get("isCurrentUser")), trees[0])


def semantic_tree_payload(
    *,
    match: DialogueMatch,
    root_name: str,
    current_user_id: int | None = None,
    analysis_status: str = "ready",
    message: str = "",
    analyzed_count: int = 0,
) -> dict[str, Any]:
    state = get_semantic_tree_state(match, root_name=root_name)
    trees = _match_tree_payloads(
        state=state,
        match=match,
        current_user_id=current_user_id,
    )
    active_tree = _active_tree_payload(trees)
    return {
        "room_id": match.room_id,
        "match_id": match.id,
        "topic_id": match.topic_id,
        "semanticMode": MATCH_TREE_MODE,
        "treeData": active_tree["treeData"],
        "trees": trees,
        "anchors": state["anchors"],
        "analysisHistory": active_tree["analysisHistory"],
        "analyzedMessageIds": active_tree["analyzedSourceIds"],
        "analyzedSourceIds": active_tree["analyzedSourceIds"],
        "analysisStatus": analysis_status,
        "message": message,
        "analyzedCount": analyzed_count,
    }


def semantic_tree_session_payload(
    *,
    session_record: dict[str, Any],
    session_id: str,
    root_name: str,
    analysis_status: str = "ready",
    message: str = "",
    analyzed_count: int = 0,
) -> dict[str, Any]:
    state = get_ai_semantic_tree_state(session_record, root_name=root_name)
    owner_state = state["participants"][OWNER_AI_USER]
    trees = [
        _owner_payload(
            owner_state,
            label="我的脈絡",
            is_current_user=True,
        )
    ]
    return {
        "session_id": session_id,
        "topic_id": session_record.get("topic_id"),
        "semanticMode": AI_TREE_MODE,
        "treeData": owner_state["treeData"],
        "trees": trees,
        "anchors": state["anchors"],
        "analysisHistory": owner_state["analysisHistory"],
        "analyzedMessageIds": owner_state["analyzedSourceIds"],
        "analyzedSourceIds": owner_state["analyzedSourceIds"],
        "analysisStatus": analysis_status,
        "message": message,
        "analyzedCount": analyzed_count,
    }


def semantic_tree_batch_size() -> int:
    try:
        return max(1, int(os.getenv("SEMANTIC_TREE_ANALYZE_BATCH_SIZE", "5")))
    except ValueError:
        return 5


def analyze_pending_room_messages(
    *,
    match: DialogueMatch,
    root_name: str,
    current_user_id: int | None = None,
) -> dict[str, Any]:
    with transaction.atomic():
        locked_match = DialogueMatch.objects.select_for_update().get(pk=match.pk)
        state = get_semantic_tree_state(locked_match, root_name=root_name)
        current_owner_key = _owner_key_for_user(locked_match, current_user_id)
        pending_messages = [
            (message, owner_key)
            for message in locked_match.messages.order_by("created_at", "id")
            for owner_key in [_owner_key_for_message(locked_match, message.sender_id)]
            if owner_key
            and owner_key == current_owner_key
            and clean_text(message.id)
            not in set(state["participants"][owner_key]["analyzedSourceIds"])
        ][: semantic_tree_batch_size()]

        if pending_messages and not get_gemini_api_key():
            raise MissingGeminiApiKey("server 缺少 GEMINI_API_KEY，無法呼叫 Gemini。")

        analyzed_count = 0
        for message, owner_key in pending_messages:
            owner_state = state["participants"][owner_key]
            source_message = _message_to_source(message)
            result = analyze_with_gemini(
                text=message.content,
                tree=owner_state["treeData"],
                anchors=state["anchors"],
            )
            apply_result = apply_analysis_items_to_tree(
                owner_state["treeData"],
                result.get("items", []),
                source_message=source_message,
            )
            owner_state["analysisHistory"].append(
                {
                    "sourceId": clean_text(message.id),
                    "sourceType": "match_message",
                    "analyzedAt": timezone.now().isoformat(),
                    "model": result.get("model") or get_gemini_model(),
                    "sourceText": message.content,
                    "appliedItems": apply_result["appliedItems"],
                    "invalidItems": result.get("invalidItems", []),
                }
            )
            owner_state["analyzedSourceIds"].append(clean_text(message.id))
            analyzed_count += 1

        save_semantic_tree_state(locked_match, state)
        return semantic_tree_payload(
            match=locked_match,
            root_name=root_name,
            current_user_id=current_user_id,
            analysis_status="ready",
            analyzed_count=analyzed_count,
        )


def analyze_pending_ai_conversations(
    *,
    session_record: dict[str, Any],
    session_id: str,
    user_id: int,
    root_name: str,
) -> dict[str, Any]:
    state = get_ai_semantic_tree_state(session_record, root_name=root_name)
    owner_state = state["participants"][OWNER_AI_USER]
    analyzed_ids = set(owner_state["analyzedSourceIds"])
    pending_turns = [
        turn
        for turn in AIConversation.objects.filter(
            user_id=user_id,
            session_id=session_id,
        ).order_by("created_at", "id")
        if clean_text(turn.user_prompt) and clean_text(turn.id) not in analyzed_ids
    ][: semantic_tree_batch_size()]

    if pending_turns and not get_gemini_api_key():
        raise MissingGeminiApiKey("server 缺少 GEMINI_API_KEY，無法呼叫 Gemini。")

    analyzed_count = 0
    for turn in pending_turns:
        source_message = _ai_turn_to_source(turn)
        result = analyze_with_gemini(
            text=turn.user_prompt,
            tree=owner_state["treeData"],
            anchors=state["anchors"],
        )
        apply_result = apply_analysis_items_to_tree(
            owner_state["treeData"],
            result.get("items", []),
            source_message=source_message,
        )
        owner_state["analysisHistory"].append(
            {
                "sourceId": clean_text(turn.id),
                "sourceType": "ai_user_prompt",
                "analyzedAt": timezone.now().isoformat(),
                "model": result.get("model") or get_gemini_model(),
                "sourceText": turn.user_prompt,
                "appliedItems": apply_result["appliedItems"],
                "invalidItems": result.get("invalidItems", []),
            }
        )
        owner_state["analyzedSourceIds"].append(clean_text(turn.id))
        analyzed_count += 1

    save_ai_semantic_tree_state(session_record, state)
    return semantic_tree_session_payload(
        session_record=session_record,
        session_id=session_id,
        root_name=root_name,
        analysis_status="ready",
        analyzed_count=analyzed_count,
    )
