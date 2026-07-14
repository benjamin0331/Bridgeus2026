"""
Tests for chat.services.embedding.

These tests load the actual SentenceTransformer model (~470 MB, cached locally).
First run may be slow; subsequent runs reuse the singleton.
"""

import pytest

from chat.services.embedding import (
    aget_embedding,
    aget_embeddings,
    cosine_distance,
    cosine_similarity,
    get_embedding,
    get_embeddings,
)

DIM = 384  # paraphrase-multilingual-MiniLM-L12-v2


# ---------- shape / type ----------

def test_single_embedding_shape_and_type():
    vec = get_embedding("核能發電可以減少碳排放")
    assert len(vec) == DIM
    assert isinstance(vec, list)
    assert all(isinstance(x, float) for x in vec)


def test_batch_embeddings_shape():
    texts = ["支持核能發電", "反對核能發電", "氣候變遷問題嚴峻"]
    vecs = get_embeddings(texts)
    assert len(vecs) == 3
    assert all(len(v) == DIM for v in vecs)


def test_empty_string_returns_dim_vector():
    vec = get_embedding("")
    assert len(vec) == DIM


def test_single_and_batch_agree():
    text = "台灣的能源政策需要長期規劃"
    single = get_embedding(text)
    batch = get_embeddings([text])[0]
    for a, b in zip(single, batch):
        assert abs(a - b) < 1e-5


# ---------- cosine similarity ----------

def test_self_similarity_is_one():
    vec = get_embedding("核能對環境衝擊需要客觀評估")
    sim = cosine_similarity(vec, vec)
    assert abs(sim - 1.0) < 1e-5


def test_similar_chinese_sentences_high_similarity():
    # Paraphrases: both argue Taiwan should keep/develop nuclear energy
    vec_a = get_embedding("台灣應該繼續發展核能電廠")
    vec_b = get_embedding("台灣有必要保留核能作為能源來源")
    sim = cosine_similarity(vec_a, vec_b)
    assert sim > 0.7, f"Expected similar sentences > 0.7, got {sim:.4f}"


def test_unrelated_sentences_low_similarity():
    # Completely different topics: weather vs nuclear waste
    vec_a = get_embedding("今天天氣非常晴朗，適合出去散步")
    vec_b = get_embedding("核電廠的放射性廢料需要安全封存數百年")
    sim = cosine_similarity(vec_a, vec_b)
    assert sim < 0.4, f"Expected unrelated sentences < 0.4, got {sim:.4f}"


def test_cosine_distance_is_complement_of_similarity():
    vec_a = get_embedding("再生能源是台灣能源轉型的重要方向")
    vec_b = get_embedding("太陽能和風力發電可以取代核電")
    sim = cosine_similarity(vec_a, vec_b)
    dist = cosine_distance(vec_a, vec_b)
    assert abs(sim + dist - 1.0) < 1e-6


def test_cosine_distance_nonnegative():
    # For normalised sentence embeddings distance is always in [0, 1]
    texts = [
        "支持核能",
        "反對核能",
        "核能安全性問題",
        "再生能源發展",
    ]
    vecs = get_embeddings(texts)
    for i in range(len(vecs)):
        for j in range(i + 1, len(vecs)):
            d = cosine_distance(vecs[i], vecs[j])
            assert 0.0 <= d <= 1.0 + 1e-6, f"distance out of range: {d}"


# ---------- async wrappers ----------

@pytest.mark.asyncio
async def test_aget_embedding_returns_correct_dim():
    vec = await aget_embedding("異步向量化測試")
    assert len(vec) == DIM


@pytest.mark.asyncio
async def test_aget_embeddings_batch():
    texts = ["第一句", "第二句", "第三句"]
    vecs = await aget_embeddings(texts)
    assert len(vecs) == 3
    assert all(len(v) == DIM for v in vecs)
