from __future__ import annotations

from datetime import date
from typing import NewType

from src.models.content import ConsumptionStatus, ContentItem, ContentType
from src.models.user_preferences import UserPreferenceConfig
from src.recommendations.genre_clusters import (
    cluster_coverage,
    get_clusters_for_terms,
)
from src.recommendations.genre_normalizer import extract_and_normalize_genres
from src.utils.series import latest_season_watched_date

#: A penalty as a *fraction* of a candidate's score, in ``[0.0, 1.0]``. Distinct
#: from the user's ``variety_penalty`` preference, which is a 0.0-5.0 strength:
#: a strength used unscaled as a fraction would push scores below zero.
PenaltyFraction = NewType("PenaltyFraction", float)

#: The clusters one completion reached, and the penalty for matching all of them.
VarietyRung = tuple[frozenset[str], float]

VARIETY_TOP_PENALTY = PenaltyFraction(1.0)

# Number of recent completions the penalty ladder spans, one to a rung.
VARIETY_LADDER_STEPS = 5


def top_penalty_for_preference(variety_penalty: float) -> PenaltyFraction:
    fraction = variety_penalty / UserPreferenceConfig.MAX_VARIETY_PENALTY
    return PenaltyFraction(min(max(fraction, 0.0), 1.0))


def _is_completion_event(item: ContentItem) -> bool:
    if item.status == ConsumptionStatus.COMPLETED:
        return True
    if (
        item.content_type == ContentType.TV_SHOW
        and item.status == ConsumptionStatus.CURRENTLY_CONSUMING
    ):
        seasons_watched = item.metadata.get("seasons_watched")
        return isinstance(seasons_watched, list) and bool(seasons_watched)
    return False


def _completion_recency(item: ContentItem) -> date | None:
    if item.status == ConsumptionStatus.COMPLETED:
        if item.date_completed is None and item.content_type == ContentType.TV_SHOW:
            return latest_season_watched_date(item)
        return item.date_completed
    return latest_season_watched_date(item)


def _completion_sort_key(item: ContentItem) -> tuple[bool, date, int]:
    recency = _completion_recency(item)
    return (recency is not None, recency or date.min, item.db_id or 0)


def build_variety_ladder(
    completed_items: list[ContentItem],
    *,
    steps: int = VARIETY_LADDER_STEPS,
    top_penalty: PenaltyFraction = VARIETY_TOP_PENALTY,
) -> list[VarietyRung]:
    if steps <= 0:
        return []

    completed = [item for item in completed_items if _is_completion_event(item)]
    completed.sort(key=_completion_sort_key, reverse=True)

    # One rung per completion, shared by every cluster it reaches: claiming one
    # of them would leave the rest floating a near-identical title. A completion
    # adding no new cluster spends no rung.
    ladder: list[VarietyRung] = []
    placed: set[str] = set()
    for item in completed:
        if len(ladder) >= steps:
            break
        clusters = get_clusters_for_terms(extract_and_normalize_genres(item.metadata))
        if clusters <= placed:
            continue
        ladder.append(
            (frozenset(clusters), top_penalty * (steps - len(ladder)) / steps)
        )
        placed |= clusters

    return ladder


def variety_penalty_for(item: ContentItem, ladder: list[VarietyRung]) -> float:
    clusters = get_clusters_for_terms(extract_and_normalize_genres(item.metadata))
    # Coverage of the rung, not similarity to it: a symmetric measure puts the
    # candidate's own clusters in the denominator, so richer enrichment alone
    # would soften the penalty for repeating what was just finished.
    return max(
        (penalty * cluster_coverage(clusters, rung) for rung, penalty in ladder),
        default=0.0,
    )
