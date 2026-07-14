"""
Tests for apps.summary.pipeline (M6 觀點知識庫 ORM pipeline).

Ported from the standalone 觀點知識庫/test_pipeline.py smoke test. Writer
tests load the real SentenceTransformer model via chat.services.embedding
(same singleton chat's own tests use — see chat/tests_embedding.py), so the
first run is slow.
"""

import pytest

from apps.summary.models import DialogueSummary, ViewpointNode
from apps.summary.pipeline.quality_filter import passes_quality_filter
from apps.summary.pipeline.writer import write_dialogue_summary, write_viewpoint

GOOD_DIALOGUE = [
    {"role": "user", "content": "我認為核能發電在當前能源轉型過程中仍有其存在必要性，因為它的碳排放量相對低"},
    {"role": "user", "content": "但是核廢料的處理問題確實是一大隱憂，目前全球都還沒有完善的解決方案"},
    {"role": "user", "content": "不過相比煤炭發電，核能在發電過程中產生的空污明顯少很多"},
    {"role": "user", "content": "台灣地震頻繁也是一個需要考量的安全因素，這點我承認是核能的弱點"},
    {"role": "user", "content": "但從電力穩定性來看，再生能源目前還無法完全取代基載電力"},
    {"role": "user", "content": "所以我認為在儲能技術成熟之前，核能是必要的過渡選項"},
]

SHORT_DIALOGUE = [
    {"role": "user", "content": "核能不好"},
    {"role": "user", "content": "會爆炸"},
    {"role": "user", "content": "不要蓋"},
]

ATTACK_DIALOGUE = GOOD_DIALOGUE + [
    {"role": "user", "content": "你這個白痴根本不懂能源問題"},
]


def test_quality_filter_accepts_normal_dialogue():
    assert passes_quality_filter(GOOD_DIALOGUE) is True


def test_quality_filter_rejects_short_dialogue():
    assert passes_quality_filter(SHORT_DIALOGUE) is False


def test_quality_filter_rejects_attack_heavy_dialogue():
    assert passes_quality_filter(ATTACK_DIALOGUE) is False


FAKE_DIALOGUE_SUMMARY = {
    "dialogue_id": "9001",
    "topic_id": 1,
    "summary_text": "使用者討論了核能發電的優缺點，包含碳排放、核廢料處理與安全性等面向，最終認為核能是儲能技術成熟前的必要過渡選項。",
    "side_a_stance": "pro",
    "side_b_stance": "neutral",
    "quality_score": 0.82,
    "stance_shift_magnitude": 0.35,
}

FAKE_VIEWPOINT = {
    "topic_id": 1,
    "dimension": "safety",
    "stance_direction": "pro",
    "user_input_text": "核能在低碳排放這個面向確實有其優勢，但安全疑慮不能忽視",
    "ai_response_text": "您提到了核能的兩個核心矛盾：低碳優勢與安全風險，這正是當前辯論的焦點",
    "viewpoint_summary": "使用者承認核能低碳優勢，但對安全性持保留態度",
    "source_message_ids": [101, 102],
    "composite_score": 0.74,
    "score_detail": {
        "semantic_dist": 0.73,
        "stance_shift": 0.81,
        "lexical_richness": 0.65,
    },
}


@pytest.mark.django_db
def test_write_dialogue_summary_persists_fields():
    summary = write_dialogue_summary(FAKE_DIALOGUE_SUMMARY)

    fetched = DialogueSummary.objects.get(id=summary.id)
    assert fetched.dialogue_id == FAKE_DIALOGUE_SUMMARY["dialogue_id"]
    assert fetched.topic_id == FAKE_DIALOGUE_SUMMARY["topic_id"]
    assert fetched.side_a_stance == FAKE_DIALOGUE_SUMMARY["side_a_stance"]


@pytest.mark.django_db
def test_write_viewpoint_then_duplicate_increments_citation():
    summary = write_dialogue_summary(FAKE_DIALOGUE_SUMMARY)
    viewpoint_data = {**FAKE_VIEWPOINT, "summary_id": summary.id}

    node = write_viewpoint(viewpoint_data)
    assert node is not None
    assert ViewpointNode.objects.filter(id=node.id).exists()

    # Second write of the same text is a near-duplicate: no new row, existing
    # node's citation_count bumps instead.
    duplicate = write_viewpoint(viewpoint_data)
    assert duplicate is None

    node.refresh_from_db()
    assert node.citation_count == 1
    assert ViewpointNode.objects.filter(topic_id=1, dimension="safety").count() == 1


@pytest.mark.django_db
def test_write_viewpoint_distinct_text_not_treated_as_duplicate():
    summary = write_dialogue_summary(FAKE_DIALOGUE_SUMMARY)
    first = write_viewpoint({**FAKE_VIEWPOINT, "summary_id": summary.id})

    distinct_data = {
        **FAKE_VIEWPOINT,
        "summary_id": summary.id,
        "user_input_text": "太陽能板的光電轉換效率近年有顯著提升，是能源轉型的另一個重要方向",
    }
    second = write_viewpoint(distinct_data)

    assert first is not None
    assert second is not None
    assert first.id != second.id
