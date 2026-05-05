"""Semantic helpers for matching survey answers."""

import logging
import math

from chat.services.embedding import get_embedding

logger = logging.getLogger(__name__)
Q9_EMBEDDING_DIMENSIONS = 384
Q9_OPEN_QUESTION_CODE = "Q9"


def _clean_embedding(vector) -> list[float] | None:
    if vector is None:
        return None

    try:
        cleaned = [float(value) for value in vector]
    except (TypeError, ValueError):
        return None

    if len(cleaned) != Q9_EMBEDDING_DIMENSIONS:
        return None
    if any(not math.isfinite(value) for value in cleaned):
        return None
    return cleaned


def build_q9_embedding(survey_open_answers: dict) -> list[float] | None:
    """Return a 384-dim embedding for Q9, or None when unavailable."""
    q9_answer = (survey_open_answers.get(Q9_OPEN_QUESTION_CODE) or "").strip()
    if not q9_answer:
        return None

    try:
        return _clean_embedding(get_embedding(q9_answer))
    except Exception:
        logger.exception("Failed to build Q9 semantic embedding for matching.")
        return None
