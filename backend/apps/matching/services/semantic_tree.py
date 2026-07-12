import json
import os
import re
import urllib.error
import urllib.request
from copy import deepcopy
from typing import Any

from django.db import transaction
from django.utils import timezone

from api.models import AIConversation, DialogueMatch


DEFAULT_MODEL = "gpt-5.4-mini"
DEFAULT_OPENAI_TIMEOUT_SECONDS = 20.0
# Research-validated on human-human dialogue (api_matchmessage_li): each message
# carries at most 2 distinct anchors, and a single anchor only ever needs 1
# mid-level category to stay readable (max 4 visual layers). See
# prompt_experiments/li-depth-recommendation.md in the CCND prototype.
MAX_ANALYSIS_ITEMS = 2
MAX_PATH_DEPTH = 1
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

ANCHOR_DESCRIPTIONS = {
    "anchor_safety": "事故風險、老舊延役、地震帶、反應爐技術、輻射外洩、安全審查。",
    "anchor_economy": "發電成本、維護成本、除役成本、補貼、電價、投資效益。",
    "anchor_energy": "供電穩定、缺電風險、基載、能源配置、再生能源互補。",
    "anchor_environment": "減碳、空污、生態衝擊、土地使用、氣候風險。",
    "anchor_governance": "資訊公開、民意溝通、政府信任、程序正義、決策透明、主權與責任。",
    "anchor_waste": "核廢料處置、最終儲存、地方承擔、長期管理、處置場風險。",
}


def get_topic_anchors(topic_id: int | None) -> list[dict[str, str]]:
    from api.dialogue_topics import TOPIC_CONFIGS
    return TOPIC_CONFIGS.get(topic_id or 0, {}).get("anchors") or FIXED_ANCHORS


def get_topic_anchor_descriptions(topic_id: int | None) -> dict[str, str]:
    from api.dialogue_topics import TOPIC_CONFIGS
    return TOPIC_CONFIGS.get(topic_id or 0, {}).get("anchor_descriptions") or ANCHOR_DESCRIPTIONS

# Internal node ids the model must never emit as a human-readable path segment.
_INTERNAL_ID_RE = re.compile(r"^(anchor|agent|category|point|virtual)_[a-z0-9_-]+$", re.IGNORECASE)

def _analysis_response_schema(anchors: list[dict[str, str]]) -> dict:
    return {
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
                            "enum": [anchor["id"] for anchor in anchors],
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


class MissingOpenAIApiKey(SemanticTreeError):
    status_code = 503
    code = "missing_openai_api_key"


class OpenAIApiError(SemanticTreeError):
    status_code = 502
    code = "openai_api_failed"


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


def create_initial_tree(root_name: str = "核電", anchors: list | None = None) -> dict[str, Any]:
    resolved = anchors or FIXED_ANCHORS
    return {
        "id": "root",
        "name": root_name or "核電",
        "type": "root",
        "children": [create_anchor_node(anchor) for anchor in resolved],
    }


def create_owner_tree_state(owner_key: str, root_name: str, anchors: list | None = None) -> dict[str, Any]:
    return {
        "ownerKey": owner_key,
        "treeData": create_initial_tree(root_name, anchors),
        "analyzedSourceIds": [],
        "analysisHistory": [],
    }


def _empty_match_state(root_name: str, anchors: list | None = None) -> dict[str, Any]:
    resolved = anchors or FIXED_ANCHORS
    return {
        "version": SEMANTIC_TREE_STATE_VERSION,
        "mode": MATCH_TREE_MODE,
        "anchors": deepcopy(resolved),
        "participants": {
            OWNER_USER_A: create_owner_tree_state(OWNER_USER_A, root_name, resolved),
            OWNER_USER_B: create_owner_tree_state(OWNER_USER_B, root_name, resolved),
        },
    }


def _empty_ai_state(root_name: str, anchors: list | None = None) -> dict[str, Any]:
    resolved = anchors or FIXED_ANCHORS
    return {
        "version": SEMANTIC_TREE_STATE_VERSION,
        "mode": AI_TREE_MODE,
        "anchors": deepcopy(resolved),
        "participants": {
            OWNER_AI_USER: create_owner_tree_state(OWNER_AI_USER, root_name, resolved),
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
    anchors: list | None = None,
) -> dict[str, Any]:
    if not isinstance(owner_state, dict):
        owner_state = create_owner_tree_state(owner_key, root_name, anchors)

    owner_state["ownerKey"] = owner_key
    tree_data = owner_state.get("treeData")
    if not isinstance(tree_data, dict) or not isinstance(tree_data.get("children"), list):
        tree_data = create_initial_tree(root_name, anchors)
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
    _ensure_fixed_anchors(tree_data, anchors)
    return owner_state


def get_semantic_tree_state(match: DialogueMatch, *, root_name: str) -> dict[str, Any]:
    anchors = get_topic_anchors(match.topic_id)
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
        return _empty_match_state(root_name, anchors)

    state["anchors"] = deepcopy(anchors)
    participants = state.setdefault("participants", {})
    participants[OWNER_USER_A] = _ensure_owner_tree_state(
        participants.get(OWNER_USER_A),
        owner_key=OWNER_USER_A,
        root_name=root_name,
        anchors=anchors,
    )
    participants[OWNER_USER_B] = _ensure_owner_tree_state(
        participants.get(OWNER_USER_B),
        owner_key=OWNER_USER_B,
        root_name=root_name,
        anchors=anchors,
    )
    return state


def get_ai_semantic_tree_state(
    session_record: dict[str, Any],
    *,
    root_name: str,
) -> dict[str, Any]:
    anchors = get_topic_anchors(session_record.get("topic_id"))
    state = session_record.get(SEMANTIC_TREE_STATS_KEY)
    if (
        not isinstance(state, dict)
        or state.get("version") != SEMANTIC_TREE_STATE_VERSION
        or state.get("mode") != AI_TREE_MODE
        or not isinstance(state.get("participants"), dict)
    ):
        return _empty_ai_state(root_name, anchors)

    state["anchors"] = deepcopy(anchors)
    participants = state.setdefault("participants", {})
    participants[OWNER_AI_USER] = _ensure_owner_tree_state(
        participants.get(OWNER_AI_USER),
        owner_key=OWNER_AI_USER,
        root_name=root_name,
        anchors=anchors,
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


def _ensure_fixed_anchors(tree_data: dict[str, Any], anchors: list | None = None) -> None:
    resolved = anchors or FIXED_ANCHORS
    children = [child for child in tree_data.get("children", []) if isinstance(child, dict)]
    child_by_id = {child.get("id"): child for child in children}
    ordered_children = []

    for anchor in resolved:
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


def find_nodes_born_from_message(
    node: dict[str, Any] | None,
    source_message_id: str,
) -> list[dict[str, Any]]:
    """Return every node whose first message (mode == "new") was recorded
    for `source_message_id` — i.e. the nodes that were created when that
    message was analyzed, as opposed to nodes it only merged into.
    """
    born: list[dict[str, Any]] = []
    for child in (node or {}).get("children") or []:
        if not isinstance(child, dict):
            continue
        messages = child.get("messages") or []
        if messages and isinstance(messages[0], dict):
            first_message = messages[0]
            if (
                first_message.get("mode") == "new"
                and clean_text(first_message.get("sourceMessageId")) == clean_text(source_message_id)
            ):
                born.append(child)
        born.extend(find_nodes_born_from_message(child, source_message_id))
    return born


def born_nodes_payload(
    tree: dict[str, Any] | None,
    source_message_id: str,
) -> list[dict[str, Any]]:
    """Light, UI-facing shape of the nodes a given message created.

    Note the time axis: this is keyed on `sourceMessageId` (a property of the
    message itself), NOT on recordedAt/analyzedAt. The timeline reconstructs the
    tree using the analysis clock (`recordedAt`), but "which nodes did this
    message give birth to" must never be derived from that clock — keeping the
    two axes apart is what stops batch-analysis timing from distorting the
    figure.
    """
    return [
        {
            "id": clean_text(node.get("id")),
            "name": clean_text(node.get("name")),
            "stance": clean_text(node.get("stance")),
        }
        for node in find_nodes_born_from_message(tree, source_message_id)
    ]


def resolve_cutoff_for_message(
    analysis_history: list[dict[str, Any]] | None,
    source_message_id: str,
) -> str | None:
    """Look up the `analyzedAt` timestamp recorded for `source_message_id`
    in an owner's `analysisHistory` — this is the cutoff to pass to
    `reconstruct_tree_as_of()` to see the tree "as of" that message, even
    if the message produced no items of its own.
    """
    target_id = clean_text(source_message_id)
    for entry in analysis_history or []:
        if isinstance(entry, dict) and clean_text(entry.get("sourceId")) == target_id:
            return clean_text(entry.get("analyzedAt")) or None
    return None


def reconstruct_tree_as_of(tree: dict[str, Any], cutoff_recorded_at: str) -> dict[str, Any]:
    """Return a copy of `tree` showing only messages recorded at or before
    `cutoff_recorded_at`. Nodes aren't repositioned once created, so this
    is a prune of the current tree rather than a replay from scratch: a
    node whose messages are all after the cutoff hadn't been created yet
    and is dropped along with its descendants; a node that had already
    been created keeps only its messages up to the cutoff, with
    `claimText` rolled back to match the last of those messages.

    Anchor nodes are never dropped (the UI always shows all fixed anchors)
    but their `hiddenUntilUsed` flag is recomputed for this cutoff, since
    the live tree only ever flips it from True to False and never back —
    an anchor first touched *after* the cutoff must still show as hidden
    in the snapshot even though it's long since unhidden in the live tree.
    """
    cutoff = clean_text(cutoff_recorded_at)
    snapshot = deepcopy(tree)

    def prune(node: dict[str, Any]) -> None:
        kept_children = []
        for child in node.get("children") or []:
            if not isinstance(child, dict):
                continue
            is_anchor = child.get("type") == "anchor"
            messages = child.get("messages")
            if messages:
                visible = [
                    message
                    for message in messages
                    if isinstance(message, dict) and clean_text(message.get("recordedAt")) <= cutoff
                ]
                if not visible:
                    continue
                child["messages"] = visible
                if child.get("claimText"):
                    child["claimText"] = visible[-1].get("text") or child["claimText"]
            prune(child)
            if is_anchor:
                child["hiddenUntilUsed"] = not bool(child.get("children"))
            elif not messages and not child.get("children"):
                continue
            kept_children.append(child)
        node["children"] = kept_children

    prune(snapshot)
    return snapshot


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


def _looks_like_internal_id(value: Any) -> bool:
    return bool(_INTERNAL_ID_RE.match(clean_text(value)))


def _latest_stance(node: dict[str, Any]) -> str:
    messages = node.get("messages") or []
    if messages and isinstance(messages[-1], dict):
        return clean_text(messages[-1].get("stance")) or "中立"
    return clean_text(node.get("stance")) or "中立"


def list_existing_node_names(tree: dict[str, Any] | None) -> str:
    """Flatten each anchor's existing child names (plus, for leaf claim
    nodes, their current stance and underlying claim) so the model can
    reuse the exact string and merge synonymous claims — or recognize that
    a new, opposite-stance claim is actually a stance update on the same
    discussion axis — instead of spawning near-duplicate/mirror nodes.
    """
    lines = []
    for anchor in (tree or {}).get("children") or []:
        if not isinstance(anchor, dict):
            continue
        entries: list[str] = []
        seen_labels: set[str] = set()

        def collect(node: dict[str, Any], path_prefix: list[str]) -> None:
            for child in node.get("children") or []:
                if not isinstance(child, dict):
                    continue
                name = clean_text(child.get("name"))
                child_path = path_prefix + [name] if name else path_prefix
                if name:
                    # Include the path prefix so the model can see a node's
                    # place in the hierarchy (needed to pick the right
                    # `path` when reusing a node) and so two different nodes
                    # that happen to share a bare name aren't collapsed into
                    # one displayed entry.
                    label = "＞".join(child_path)
                    if label not in seen_labels:
                        seen_labels.add(label)
                        claim = clean_text(child.get("claimText"))
                        if claim:
                            entries.append(f"{label}（目前立場：{_latest_stance(child)}；主張：{claim}）")
                        else:
                            entries.append(label)
                collect(child, child_path)

        collect(anchor, [])
        if entries:
            lines.append(f"{anchor.get('name')}：{'、'.join(entries)}")
    return "\n".join(lines) if lines else "（目前各分類底下還沒有任何節點）"


def build_openai_request(
    *,
    text: str,
    tree: dict[str, Any],
    anchors: list[dict[str, str]] | None = None,
    anchor_descriptions: dict[str, str] | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    resolved_anchors = anchors or FIXED_ANCHORS
    resolved_descriptions = anchor_descriptions or ANCHOR_DESCRIPTIONS
    anchor_list = "\n".join(
        f"{anchor['id']}: {anchor['name']} - {resolved_descriptions.get(anchor['id'], '議題分類。')}"
        for anchor in resolved_anchors
    )
    tree_summary = json.dumps(compact_tree_for_prompt(tree), ensure_ascii=False, indent=2)

    prompt_text = "\n".join(
        [
            "你是「個人想法脈絡樹」的語意整理 agent，不是逐字拆句工具。",
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
            "2. 優先輸出 1 個 item；只有當輸入明確橫跨兩個不同固定分類時才輸出第 2 個。",
            "   ★ 兩個潛在 item 的 anchorId 若相同，一律合併成一個，不得輸出兩個 item。",
            "   BAD：「核四建設超支（anchor_economy）＋ 超支是全球現象（anchor_economy）」→ 後者只是佐證，應合併。",
            "   BAD：「核電低碳（anchor_environment）＋ 脫碳不可或缺（anchor_environment）」→ 同一主張，應合併。",
            "   BAD：「深地質處置（anchor_waste）＋ 芬蘭昂卡洛（anchor_waste）」→ 昂卡洛是例子，不是獨立論點，應合併。",
            "3. 不要因為一句話裡有「例如、等等、包含、以及」就拆成很多節點。",
            "4. 相近意思要合併成同一個 claim。",
            "5. 不要把原因、例子、補充說明拆成獨立節點，除非它本身是另一個明確議題。",
            "6. 若使用者只是在表達單一立場，請產生一個總結型節點。",
            "7. 若訊息只是問候、確認、追問、附和，或沒有新的核能議題主張，輸出 items: []。",
            "",
            "合併規則（最重要，務必避免近義節點）：",
            "- 產生 pointName 與 path 前，先看「現有節點清單」裡，同一個 anchor（以及同一個中層分類）底下已經存在的節點名稱。",
            "- 只要既有節點的『核心主張』和你要表達的相同或高度重疊，就直接輸出『與該既有節點完全相同的 pointName 與 path』，讓系統把它合併到同一個節點，不要另開一個近義節點。",
            "- 判斷是否同義，看的是『核心主張是否一致』，不是字面用詞是否一樣。",
            "- 範例：「核能較低污染」「核電減少空污排放」「核電比火力乾淨」核心主張都是『核電污染較低』，必須合併成同一個節點，不可變成三個。",
            "- 範例：「核電風險不可控」「核能事故後果嚴重」核心主張都是『核安風險高』，應合併。",
            "- 寧可掛到既有節點，也不要為了細微差異新增節點；同一則訊息內也不要同時輸出兩個意思相近的 items。",
            "",
            # NOTE: this rule and 合併規則 above both resolve to the same action
            # (reuse the existing node's exact pointName/path) — see
            # find_child_by_name / apply_analysis_items_to_tree below, which
            # merge purely by exact-name match regardless of *why* the model
            # decided to reuse the name. The model doesn't need to classify
            # which rule "applies"; either one converges on the same output.
            "立場更新規則（新增，務必遵守）：",
            "- 有些新主張和某個既有節點在討論『同一個討論維度』（例如都在回答『核廢問題能不能解決』），但這次的立場和既有節點不同（例如既有節點是『反對』，這次語氣是『支持』）。",
            "- 這種情況屬於『立場更新』，不是新論點：請直接重用該既有節點『完全相同的 pointName 與 path』，讓系統把這次的立場記錄併入同一個節點的歷史，不要另外新增一個看起來相反的節點。",
            "- 判斷依據是『討論的是不是同一個潛在問題』，不是『立場是否相同』；立場不同不代表要拆成新節點。",
            "- 範例：現有節點『核廢問題待解（目前立場：反對；主張：核廢處理方式尚未成熟）』，本次輸入『瑞典的地下處置方式已經證實可行』→ 屬於同一討論維度（核廢問題能否解決）、立場轉為支持，應輸出 pointName＝『核廢問題待解』（沿用既有節點），不要新建『核廢處理有解方』這類新節點。",
            "- 若不確定屬於合併規則還是立場更新規則，效果相同：兩者都指向重用既有 pointName，不需要為了分辨規則類型而猶豫。",
            "",
            "分類規則：",
            "- anchorId 必須是最主要的議題分類。",
            "- 如果同一段話同時涉及兩個主題，才分成兩個 items。",
            "- 例如「經濟效益低，因為維護成本和核廢料處理」：",
            "  - 可輸出「核能經濟效益偏低」到 anchor_economy。",
            "  - 可輸出「核廢處理增加長期負擔」到 anchor_waste。",
            "  - 不要再拆出「維護成本」、「處理成本」、「長期成本」等重複節點。",
            "- 技術規格、老舊核電廠、延役安全、地震風險、事故後果，優先歸到 anchor_safety。",
            "- 資訊公開、政府說明、信任、民意、公投、程序正義、主權與責任，優先歸到 anchor_governance。",
            "- 核廢料最終處置、儲存地點、長期管理，優先歸到 anchor_waste；只有在重點明確是價格或財務負擔時才放 anchor_economy。",
            "",
            "path 規則：",
            "- path 不包含 anchor 本身，只描述 anchor 底下的子分類路徑。",
            f"- path 最多 {MAX_PATH_DEPTH} 層。",
            "- path 每一段都必須是可讀的中文分類名稱，不可使用任何內部 id，例如 anchor_*、agent_*、category_*、point_*。",
            "- path 不可和 anchor 名稱相同；例如 anchor_governance 底下不要再建立「民主治理」。",
            "- 優先使用目前這位說話者樹中已有的相近節點名稱。",
            "- 如果沒有穩定、可重複使用的中層分類，path 請用空陣列。",
            "- 只有當現有節點完全不適合時，才建立新的子分類。",
            "- 新子分類名稱必須抽象、可容納未來類似討論，不要太細。",
            "- 不要為單一小例子建立新子分類。",
            "",
            "pointName 規則：",
            "- pointName 是圖上顯示的節點名稱，必須短、清楚、可讀。",
            "- 長度建議 6 到 12 個中文字。",
            "- 不要使用完整句子。",
            "- 不要使用「等等」、「很多問題」、「有疑慮」這種模糊名稱。",
            "- pointName 必須包含至少一個具體名詞或實體（例如地名、技術名稱、政策名稱、數據），不能只是抽象的立場摘要。",
            "  BAD：「核廢問題待解」「有解方」「風險很高」→ 太抽象，看不出實質內容。",
            "  GOOD：「瑞典地下處置方案」「核四延役爭議」「反應爐被動安全設計」→ 具體、可辨識。",
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
            "現有節點清單（最重要的合併依據）：",
            "若你的主張與下列某個名稱相同或高度重疊，請直接輸出『完全相同的名稱』讓它合併，不要改寫成新名稱。",
            list_existing_node_names(tree),
            "",
            "目前樹狀資料摘要：",
            tree_summary,
            "",
            "使用者輸入：",
            clean_text(text),
        ]
    )

    return {
        "model": model or get_openai_model(),
        "input": [
            {
                "role": "user",
                "content": prompt_text,
            }
        ],
        "text": {
            "format": {
                "type": "json_schema",
                "name": "semantic_tree_analysis",
                "strict": True,
                "schema": _analysis_response_schema(resolved_anchors),
            }
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
    *,
    min_confidence: float = MIN_CONFIDENCE,
) -> dict[str, list[dict[str, Any]]]:
    resolved_anchors = anchors or FIXED_ANCHORS
    anchor_map = {anchor["id"]: anchor for anchor in resolved_anchors}
    anchor_names = {anchor["name"] for anchor in resolved_anchors}
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
        if confidence < min_confidence:
            invalid_items.append(_item_error(raw_item, f"confidence below {min_confidence}"))
            continue
        if confidence > 1:
            invalid_items.append(_item_error(raw_item, "confidence must be 1 or lower"))
            continue
        if len(path) > MAX_PATH_DEPTH:
            invalid_items.append(_item_error(raw_item, f"path too deep: maximum {MAX_PATH_DEPTH}"))
            continue
        internal_id_segment = next((segment for segment in path if _looks_like_internal_id(segment)), None)
        if internal_id_segment:
            invalid_items.append(_item_error(raw_item, f"path contains internal id: {internal_id_segment}"))
            continue
        anchor_name_segment = next((segment for segment in path if segment in anchor_names), None)
        if anchor_name_segment:
            invalid_items.append(_item_error(raw_item, f"path contains anchor name: {anchor_name_segment}"))
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

    # 同一則訊息輸出兩個相同 anchorId 時，只保留信心最高的那一個。
    # Prompt 規則是機率性的，這裡做程式層兜底，確保每個 anchor 最多貢獻一個節點。
    seen_anchors: dict[str, dict] = {}
    deduped_items: list[dict] = []
    for item in items:
        existing = seen_anchors.get(item["anchorId"])
        if existing is None:
            seen_anchors[item["anchorId"]] = item
            deduped_items.append(item)
        elif item["confidence"] > existing["confidence"]:
            invalid_items.append(_item_error(existing, f"duplicate anchorId {item['anchorId']}: kept higher-confidence item"))
            seen_anchors[item["anchorId"]] = item
            deduped_items[deduped_items.index(existing)] = item
        else:
            invalid_items.append(_item_error(item, f"duplicate anchorId {item['anchorId']}: dropped in favor of higher-confidence item"))

    return {"items": deduped_items, "invalidItems": invalid_items}


def _max_generated_counter(node: dict[str, Any] | None) -> int:
    if not node:
        return 0
    match = re.match(r"^agent_(\d+)$", clean_text(node.get("id")))
    current = int(match.group(1)) if match else 0
    child_max = max((_max_generated_counter(child) for child in node.get("children") or []), default=0)
    return max(current, child_max)


def _create_generated_point_node(
    name: str,
    counter: int,
    metadata: dict[str, Any] | None = None,
    *,
    message_id: str = "",
) -> dict[str, Any]:
    return {
        "id": f"agent_{counter}",
        "name": name,
        "type": "point",
        "children": [],
        "created_at": timezone.now().isoformat(),
        "message_id": message_id,
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
    if text and mode != "category":
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
                    "generatedBy": "openai",
                    "sourceClaim": item.get("claimText"),
                },
                message_id=clean_text((source_message or {}).get("id")),
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
                    "generatedBy": "openai",
                    "stance": item.get("stance"),
                    "confidence": item.get("confidence"),
                    "rationale": item.get("rationale"),
                },
                message_id=clean_text((source_message or {}).get("id")),
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


def parse_openai_response(response_body: dict[str, Any]) -> dict[str, Any]:
    text = clean_text(response_body.get("output_text"))
    if not text:
        parts = []
        for item in response_body.get("output") or []:
            for content in item.get("content") or []:
                if isinstance(content, dict):
                    parts.append(content.get("text") or "")
        text = "".join(parts).strip()
    if not text:
        raise OpenAIApiError("OpenAI response did not include JSON text.")
    try:
        return json.loads(strip_json_fence(text))
    except json.JSONDecodeError as exc:
        raise OpenAIApiError(f"OpenAI response JSON parse failed: {exc}") from exc


def get_openai_api_key() -> str:
    return clean_text(os.getenv("OPENAI_API_KEY"))


def get_openai_model() -> str:
    return clean_text(os.getenv("OPENAI_MODEL")) or DEFAULT_MODEL


def _openai_timeout_seconds() -> float:
    try:
        return float(os.getenv("OPENAI_API_TIMEOUT_SECONDS", "20"))
    except (TypeError, ValueError):
        return DEFAULT_OPENAI_TIMEOUT_SECONDS


def analyze_with_openai(
    *,
    text: str,
    tree: dict[str, Any],
    anchors: list[dict[str, str]] | None = None,
    anchor_descriptions: dict[str, str] | None = None,
    api_key: str | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    cleaned_text = clean_text(text)
    resolved_model = model or get_openai_model()
    if not cleaned_text:
        return {"items": [], "invalidItems": [], "model": resolved_model}

    resolved_api_key = api_key if api_key is not None else get_openai_api_key()
    if not resolved_api_key:
        raise MissingOpenAIApiKey("server 缺少 OPENAI_API_KEY，無法呼叫 OpenAI。")

    request_body = build_openai_request(
        text=cleaned_text,
        tree=tree,
        anchors=anchors or FIXED_ANCHORS,
        anchor_descriptions=anchor_descriptions,
        model=resolved_model,
    )
    endpoint = "https://api.openai.com/v1/responses"
    request = urllib.request.Request(
        endpoint,
        data=json.dumps(request_body, ensure_ascii=False).encode("utf-8"),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {resolved_api_key}",
        },
    )

    timeout = _openai_timeout_seconds()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            response_body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        response_text = exc.read().decode("utf-8", errors="replace")
        raise OpenAIApiError(f"OpenAI API error {exc.code}: {response_text}") from exc
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise OpenAIApiError(f"OpenAI API request failed: {exc}") from exc

    parsed = parse_openai_response(response_body)
    return {
        **validate_analysis_items(parsed, tree, anchors or FIXED_ANCHORS),
        "model": resolved_model,
    }


LOCAL_CLASSIFIER_TOPIC_IDS = {102}

# MIN_CONFIDENCE (0.55) was tuned for an LLM's self-reported meta-confidence,
# which tends to run high. The local classifier's confidence is a raw softmax
# argmax probability over a 36-way cluster space, where even a correct call
# often lands around 0.5 — reusing MIN_CONFIDENCE would silently drop most of
# its output. Tune this independently as real traffic comes in.
LOCAL_CLASSIFIER_MIN_CONFIDENCE = 0.35


def uses_local_classifier(topic_id: int | None) -> bool:
    return topic_id in LOCAL_CLASSIFIER_TOPIC_IDS


def analyze_text_for_tree(
    *,
    topic_id: int | None,
    text: str,
    tree: dict[str, Any],
    anchors: list[dict[str, str]] | None = None,
    anchor_descriptions: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Dispatch node analysis by topic: topic 102 (nuclear energy) uses the
    locally fine-tuned classifier pipeline; every other topic keeps using the
    generative OpenAI path.
    """
    resolved_anchors = anchors or FIXED_ANCHORS
    if uses_local_classifier(topic_id):
        from apps.matching.services import nuclear_node_classifier

        candidate_items = nuclear_node_classifier.build_candidate_items(text, resolved_anchors)
        return {
            **validate_analysis_items(
                {"items": candidate_items},
                tree,
                resolved_anchors,
                min_confidence=LOCAL_CLASSIFIER_MIN_CONFIDENCE,
            ),
            "model": "local-bert-pipeline",
        }

    return analyze_with_openai(
        text=text,
        tree=tree,
        anchors=resolved_anchors,
        anchor_descriptions=anchor_descriptions,
    )


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


def semantic_tree_timeline_payload(
    *,
    match: DialogueMatch,
    root_name: str,
    current_user_id: int | None,
    source_message_id: str,
) -> dict[str, Any] | None:
    """Reconstruct the current user's tree as it looked right after
    `source_message_id` was analyzed. Returns None if that message hasn't
    been analyzed yet (or doesn't belong to this participant), so the
    caller can turn that into a 404.
    """
    state = get_semantic_tree_state(match, root_name=root_name)
    owner_key = _owner_key_for_user(match, current_user_id)
    owner_state = state["participants"][owner_key]

    cutoff = resolve_cutoff_for_message(owner_state["analysisHistory"], source_message_id)
    if cutoff is None:
        return None

    snapshot = reconstruct_tree_as_of(owner_state["treeData"], cutoff)
    return {
        "room_id": match.room_id,
        "match_id": match.id,
        "topic_id": match.topic_id,
        "asOfMessageId": clean_text(source_message_id),
        "asOfTimestamp": cutoff,
        "treeData": snapshot,
        "anchors": state["anchors"],
        "bornNodes": born_nodes_payload(snapshot, source_message_id),
    }


def semantic_tree_session_timeline_payload(
    *,
    session_record: dict[str, Any],
    session_id: str,
    root_name: str,
    source_message_id: str,
) -> dict[str, Any] | None:
    """AI-session equivalent of semantic_tree_timeline_payload(): reconstruct
    the tree as it looked right after `source_message_id` (an AIConversation
    turn id) was analyzed. Returns None if that turn hasn't been analyzed
    yet, so the caller can turn that into a 404.

    `room_id` mirrors `session_id` here — AI sessions don't have a real
    room, but exposing the same key lets the frontend/serializer treat both
    conversation kinds uniformly instead of branching on kind everywhere.
    """
    state = get_ai_semantic_tree_state(session_record, root_name=root_name)
    owner_state = state["participants"][OWNER_AI_USER]

    cutoff = resolve_cutoff_for_message(owner_state["analysisHistory"], source_message_id)
    if cutoff is None:
        return None

    snapshot = reconstruct_tree_as_of(owner_state["treeData"], cutoff)
    return {
        "session_id": session_id,
        "room_id": session_id,
        "topic_id": session_record.get("topic_id"),
        "asOfMessageId": clean_text(source_message_id),
        "asOfTimestamp": cutoff,
        "treeData": snapshot,
        "anchors": state["anchors"],
        "bornNodes": born_nodes_payload(snapshot, source_message_id),
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
        anchor_descriptions = get_topic_anchor_descriptions(locked_match.topic_id)
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

        if (
            pending_messages
            and not uses_local_classifier(locked_match.topic_id)
            and not get_openai_api_key()
        ):
            raise MissingOpenAIApiKey("server 缺少 OPENAI_API_KEY，無法呼叫 OpenAI。")

        analyzed_count = 0
        for message, owner_key in pending_messages:
            owner_state = state["participants"][owner_key]
            source_message = _message_to_source(message)
            result = analyze_text_for_tree(
                topic_id=locked_match.topic_id,
                text=message.content,
                tree=owner_state["treeData"],
                anchors=state["anchors"],
                anchor_descriptions=anchor_descriptions,
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
                    "model": result.get("model") or get_openai_model(),
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
    anchor_descriptions = get_topic_anchor_descriptions(session_record.get("topic_id"))
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

    if (
        pending_turns
        and not uses_local_classifier(session_record.get("topic_id"))
        and not get_openai_api_key()
    ):
        raise MissingOpenAIApiKey("server 缺少 OPENAI_API_KEY，無法呼叫 OpenAI。")

    analyzed_count = 0
    for turn in pending_turns:
        source_message = _ai_turn_to_source(turn)
        result = analyze_text_for_tree(
            topic_id=session_record.get("topic_id"),
            text=turn.user_prompt,
            tree=owner_state["treeData"],
            anchors=state["anchors"],
            anchor_descriptions=anchor_descriptions,
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
                "model": result.get("model") or get_openai_model(),
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
