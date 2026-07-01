"""Unit tests for semantic_tree.py prompt/existing-node-listing changes
(pure functions, no DB) — covers the concrete-naming and same-axis
stance-update prompt rules from the CCND 對比測試 design (2026-06-29,
section 10) in /Users/light/project/CCND測試/.
"""
from apps.matching.services.semantic_tree import (
    build_openai_request,
    create_initial_tree,
    list_existing_node_names,
)


def _tree_with_stance_node(claim_text: str, stance: str) -> dict:
    tree = create_initial_tree("核電")
    anchor = next(a for a in tree["children"] if a["id"] == "anchor_waste")
    anchor["children"].append({
        "id": "agent_1",
        "name": "核廢問題待解",
        "type": "point",
        "children": [],
        "claimText": claim_text,
        "messages": [{"text": claim_text, "stance": stance, "confidence": 0.9}],
    })
    return tree


def test_list_existing_node_names_includes_claim_and_latest_stance():
    tree = _tree_with_stance_node("目前核廢處理方式尚未成熟", "反對")
    listing = list_existing_node_names(tree)
    assert "核廢問題待解" in listing
    assert "反對" in listing
    assert "目前核廢處理方式尚未成熟" in listing


def test_list_existing_node_names_falls_back_to_node_stance_without_messages():
    tree = create_initial_tree("核電")
    anchor = next(a for a in tree["children"] if a["id"] == "anchor_waste")
    anchor["children"].append({
        "id": "agent_1",
        "name": "核廢問題待解",
        "type": "point",
        "children": [],
        "claimText": "核廢處理尚未成熟",
        "stance": "反對",
    })
    listing = list_existing_node_names(tree)
    assert "反對" in listing


def test_list_existing_node_names_without_claim_shows_bare_name():
    tree = create_initial_tree("核電")
    anchor = next(a for a in tree["children"] if a["id"] == "anchor_waste")
    anchor["children"].append({"id": "cat_1", "name": "處置方案", "type": "category", "children": []})
    listing = list_existing_node_names(tree)
    assert "處置方案" in listing


def test_list_existing_node_names_joins_multiple_siblings_under_one_anchor():
    tree = create_initial_tree("核電")
    anchor = next(a for a in tree["children"] if a["id"] == "anchor_waste")
    anchor["children"].extend([
        {
            "id": "agent_1",
            "name": "深地質處置",
            "type": "point",
            "children": [],
            "claimText": "核廢料可密封儲存在穩定地質層",
            "messages": [{"text": "核廢料可密封儲存在穩定地質層", "stance": "支持", "confidence": 0.9}],
        },
        {
            "id": "agent_2",
            "name": "核廢體積不大",
            "type": "point",
            "children": [],
            "claimText": "核廢料體積其實不大",
            "messages": [{"text": "核廢料體積其實不大", "stance": "支持", "confidence": 0.9}],
        },
    ])
    listing = list_existing_node_names(tree)
    line = next(line for line in listing.splitlines() if line.startswith("核廢處理："))
    assert "深地質處置" in line
    assert "核廢體積不大" in line
    assert "、" in line


def test_list_existing_node_names_treats_whitespace_only_claim_as_bare_name():
    tree = create_initial_tree("核電")
    anchor = next(a for a in tree["children"] if a["id"] == "anchor_waste")
    anchor["children"].append({
        "id": "agent_1",
        "name": "核廢問題待解",
        "type": "point",
        "children": [],
        "claimText": "   ",
        "messages": [{"text": "   ", "stance": "反對", "confidence": 0.9}],
    })
    listing = list_existing_node_names(tree)
    assert "核廢問題待解" in listing
    assert "主張：" not in listing


def test_build_openai_request_prompt_includes_concrete_naming_rule():
    tree = create_initial_tree("核電")
    request = build_openai_request(text="核電比較安全", tree=tree)
    prompt = request["input"][0]["content"]
    assert "具體名詞" in prompt


def test_build_openai_request_prompt_includes_stance_axis_merge_rule():
    tree = create_initial_tree("核電")
    request = build_openai_request(text="核電比較安全", tree=tree)
    prompt = request["input"][0]["content"]
    assert "立場更新" in prompt
    assert ("同一個討論維度" in prompt) or ("同一議題維度" in prompt)
