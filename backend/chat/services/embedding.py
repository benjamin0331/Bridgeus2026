"""
Embedding service for H-H dialogue NLP pipeline.

Model: paraphrase-multilingual-MiniLM-L12-v2 (384-dim, supports zh/en)
Singleton pattern: model weights loaded once per process, reused across requests.
Async wrappers use thread_sensitive=False so inference runs in the thread pool
and does not block the event loop.
"""

import threading

import numpy as np
from asgiref.sync import sync_to_async
from sentence_transformers import SentenceTransformer

_MODEL_NAME = "paraphrase-multilingual-MiniLM-L12-v2"
_model: SentenceTransformer | None = None
_lock = threading.Lock()


def _get_model() -> SentenceTransformer:
    global _model
    if _model is None:
        with _lock:
            if _model is None:
                _model = SentenceTransformer(_MODEL_NAME)
    return _model


# ---------- sync API ----------

def get_embedding(text: str) -> list[float]:
    """Encode one text string into a 384-dim float vector."""
    return _get_model().encode(text, convert_to_numpy=True).tolist()


def get_embeddings(texts: list[str], batch_size: int = 32) -> list[list[float]]:
    """Batch-encode a list of texts into 384-dim float vectors."""
    return (
        _get_model()
        .encode(texts, batch_size=batch_size, convert_to_numpy=True)
        .tolist()
    )


def cosine_similarity(
    vec_a: list[float] | np.ndarray,
    vec_b: list[float] | np.ndarray,
) -> float:
    """Cosine similarity between two vectors.  Returns a value in [-1, 1]."""
    a = np.array(vec_a, dtype=np.float32)
    b = np.array(vec_b, dtype=np.float32)
    norm_a = float(np.linalg.norm(a))
    norm_b = float(np.linalg.norm(b))
    if norm_a < 1e-10 or norm_b < 1e-10:
        return 0.0
    return float(np.dot(a, b) / (norm_a * norm_b))


def cosine_distance(
    vec_a: list[float] | np.ndarray,
    vec_b: list[float] | np.ndarray,
) -> float:
    """Cosine distance = 1 - cosine_similarity.  Range: [0, 2]."""
    return 1.0 - cosine_similarity(vec_a, vec_b)


# ---------- async API (for use inside Django Channels consumers) ----------

aget_embedding = sync_to_async(get_embedding, thread_sensitive=False)
aget_embeddings = sync_to_async(get_embeddings, thread_sensitive=False)
