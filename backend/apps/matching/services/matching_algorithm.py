"""Single place to tune the matching algorithm.

If you want to change how users are paired, edit this file first.
The rest of the matching service only handles persistence and lifecycle.
"""

import math
from dataclasses import dataclass
from decimal import Decimal

from scipy.spatial.distance import cosine

from api.models import MatchQueueEntry, UserStanceProfile
from core.env import _env_bool

LIKERT_WEIGHT = 0.6
SEMANTIC_WEIGHT = 0.4
MAX_LIKERT_DISTANCE = 6.0
MAX_COSINE_DISTANCE = 2.0
MATCHING_ALGORITHM_VERSION = "likert-semantic-v1"

STRICT_CATEGORY_PRIORITY = {
    UserStanceProfile.StanceCategory.SUPPORT: (
        UserStanceProfile.StanceCategory.OPPOSE,
    ),
    UserStanceProfile.StanceCategory.OPPOSE: (
        UserStanceProfile.StanceCategory.SUPPORT,
    ),
    UserStanceProfile.StanceCategory.NEUTRAL: (),
}

FALLBACK_CATEGORY_PRIORITY = {
    UserStanceProfile.StanceCategory.SUPPORT: (
        UserStanceProfile.StanceCategory.OPPOSE,
        UserStanceProfile.StanceCategory.SUPPORT,
        UserStanceProfile.StanceCategory.NEUTRAL,
    ),
    UserStanceProfile.StanceCategory.OPPOSE: (
        UserStanceProfile.StanceCategory.SUPPORT,
        UserStanceProfile.StanceCategory.OPPOSE,
        UserStanceProfile.StanceCategory.NEUTRAL,
    ),
    UserStanceProfile.StanceCategory.NEUTRAL: (
        UserStanceProfile.StanceCategory.SUPPORT,
        UserStanceProfile.StanceCategory.OPPOSE,
        UserStanceProfile.StanceCategory.NEUTRAL,
    ),
}


@dataclass(frozen=True)
class MatchScore:
    likert_distance: float
    semantic_distance: float
    match_score: float
    algorithm_version: str = MATCHING_ALGORITHM_VERSION


@dataclass(frozen=True)
class ScoredCandidate:
    candidate: MatchQueueEntry
    score: MatchScore


def allow_same_stance_fallback() -> bool:
    return _env_bool("MATCHING_ALLOW_SAME_STANCE_FALLBACK", False)


def candidate_categories_for(category: str) -> tuple[str, ...]:
    rules = (
        FALLBACK_CATEGORY_PRIORITY
        if allow_same_stance_fallback()
        else STRICT_CATEGORY_PRIORITY
    )
    return tuple(rules.get(category, ()))


def target_score_for(score: Decimal) -> float:
    return 8 - float(score)


def _clean_vector(vector) -> list[float] | None:
    if vector is None:
        return None

    try:
        cleaned = [float(value) for value in vector]
    except (TypeError, ValueError):
        return None

    if not cleaned or any(not math.isfinite(value) for value in cleaned):
        return None
    return cleaned


def semantic_distance_between(vector_a, vector_b) -> float:
    clean_a = _clean_vector(vector_a)
    clean_b = _clean_vector(vector_b)
    if not clean_a or not clean_b:
        return 0.0

    try:
        distance = float(cosine(clean_a, clean_b))
    except (TypeError, ValueError):
        return 0.0

    if not math.isfinite(distance):
        return 0.0
    return max(0.0, min(MAX_COSINE_DISTANCE, distance))


def calculate_match_score(
    *,
    requester_score: Decimal,
    candidate_score: Decimal,
    requester_embedding,
    candidate_embedding,
) -> MatchScore:
    likert_distance = abs(float(requester_score) - float(candidate_score))
    normalized_likert = min(likert_distance / MAX_LIKERT_DISTANCE, 1.0)
    semantic_distance = semantic_distance_between(
        requester_embedding,
        candidate_embedding,
    )
    normalized_semantic = min(semantic_distance / MAX_COSINE_DISTANCE, 1.0)
    weighted_score = (LIKERT_WEIGHT * normalized_likert) + (
        SEMANTIC_WEIGHT * normalized_semantic
    )

    return MatchScore(
        likert_distance=round(likert_distance, 4),
        semantic_distance=round(semantic_distance, 4),
        match_score=round(weighted_score, 4),
    )


def candidate_sort_key(
    *,
    candidate: MatchQueueEntry,
    requester_score: Decimal,
    requester_embedding,
) -> tuple:
    score = calculate_match_score(
        requester_score=requester_score,
        candidate_score=candidate.stance_score,
        requester_embedding=requester_embedding,
        candidate_embedding=candidate.profile.q9_embedding,
    )
    return (
        score.match_score,
        -candidate.waiting_started_at.timestamp(),
        -candidate.id,
    )


def score_candidate(
    *,
    candidate: MatchQueueEntry,
    requester_score: Decimal,
    requester_embedding,
) -> ScoredCandidate:
    score = calculate_match_score(
        requester_score=requester_score,
        candidate_score=candidate.stance_score,
        requester_embedding=requester_embedding,
        candidate_embedding=candidate.profile.q9_embedding,
    )
    return ScoredCandidate(candidate=candidate, score=score)


def choose_best_candidate(
    *,
    candidates: list[MatchQueueEntry],
    requester_score: Decimal,
    requester_category: str,
    requester_embedding,
) -> ScoredCandidate | None:
    allowed_categories = set(candidate_categories_for(requester_category))
    filtered_candidates = [
        candidate
        for candidate in candidates
        if candidate.profile.stance_category in allowed_categories
    ]
    if not filtered_candidates:
        return None

    best_candidate = max(
        filtered_candidates,
        key=lambda candidate: candidate_sort_key(
            candidate=candidate,
            requester_score=requester_score,
            requester_embedding=requester_embedding,
        ),
    )
    return score_candidate(
        candidate=best_candidate,
        requester_score=requester_score,
        requester_embedding=requester_embedding,
    )
