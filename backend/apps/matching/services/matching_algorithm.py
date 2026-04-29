"""Single place to tune the matching algorithm.

If you want to change how users are paired, edit this file first.
The rest of the matching service only handles persistence and lifecycle.

Testing defaults:
- Prefer opposite stances first
- Allow same-stance pairing as a fallback
- Allow neutral users to enter the fallback pool
"""

from decimal import Decimal

from api.models import MatchQueueEntry, UserStanceProfile

MATCHING_RULES = {
    "category_priority": {
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
            UserStanceProfile.StanceCategory.NEUTRAL,
            UserStanceProfile.StanceCategory.SUPPORT,
            UserStanceProfile.StanceCategory.OPPOSE,
        ),
    },
}


def candidate_categories_for(category: str) -> tuple[str, ...]:
    return tuple(MATCHING_RULES["category_priority"].get(category, ()))


def target_score_for(score: Decimal) -> float:
    return 8 - float(score)


def _category_rank(*, requester_category: str, candidate_category: str) -> int:
    priorities = candidate_categories_for(requester_category)
    try:
        return priorities.index(candidate_category)
    except ValueError:
        return len(priorities)


def candidate_sort_key(
    *,
    candidate: MatchQueueEntry,
    requester_score: Decimal,
    requester_category: str,
) -> tuple:
    return (
        _category_rank(
            requester_category=requester_category,
            candidate_category=candidate.profile.stance_category,
        ),
        abs(float(candidate.stance_score) - target_score_for(requester_score)),
        candidate.waiting_started_at,
        candidate.id,
    )


def choose_best_candidate(
    *,
    candidates: list[MatchQueueEntry],
    requester_score: Decimal,
    requester_category: str,
) -> MatchQueueEntry | None:
    allowed_categories = set(candidate_categories_for(requester_category))
    filtered_candidates = [
        candidate
        for candidate in candidates
        if candidate.profile.stance_category in allowed_categories
    ]
    if not filtered_candidates:
        return None

    return min(
        filtered_candidates,
        key=lambda candidate: candidate_sort_key(
            candidate=candidate,
            requester_score=requester_score,
            requester_category=requester_category,
        ),
    )
