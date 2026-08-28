from __future__ import annotations

from datetime import date
from typing import NewType

from src.models.content import ConsumptionStatus, ContentItem, ContentType
from src.models.user_preferences import UserPreferenceConfig
from src.recommendations.genre_clusters import get_clusters_for_terms
from src.recommendations.genre_normalizer import extract_and_normalize_genres
from src.utils.series import latest_season_watched_date

#: A penalty as a *fraction* of a candidate's score, in ``[0.0, 1.0]``. Distinct
#: from the user's ``variety_penalty`` preference, which is a 0.0-5.0 strength:
#: a strength used unscaled as a fraction would push scores below zero.
PenaltyFraction = NewType("PenaltyFraction", float)

VARIETY_TOP_PENALTY = PenaltyFraction(1.0)

# Number of distinct recently finished clusters the penalty ladder spans.
VARIETY_LADDER_STEPS = 5

VARIETY_SERIES_CONTINUATION_FACTOR = 0.6


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
) -> dict[str, float]:
    if steps <= 0:
        return {}

    completed = [item for item in completed_items if _is_completion_event(item)]
    completed.sort(key=_completion_sort_key, reverse=True)

    ladder: dict[str, float] = {}
    for item in completed:
        if len(ladder) >= steps:
            break
        clusters = get_clusters_for_terms(extract_and_normalize_genres(item.metadata))
        # Sort for deterministic rung assignment when a single item belongs
        # to several clusters (e.g. a fantasy-adventure novel).
        for cluster in sorted(clusters):
            if cluster in ladder:
                continue
            rung = len(ladder)
            ladder[cluster] = top_penalty * (steps - rung) / steps
            if len(ladder) >= steps:
                break

    return ladder


def variety_penalty_for(
    item: ContentItem,
    ladder: dict[str, float],
    *,
    is_series_continuation: bool = False,
) -> float:
    if not ladder:
        return 0.0
    clusters = get_clusters_for_terms(extract_and_normalize_genres(item.metadata))
    penalty = max(
        (ladder[cluster] for cluster in clusters if cluster in ladder), default=0.0
    )
    if is_series_continuation:
        penalty *= VARIETY_SERIES_CONTINUATION_FACTOR
    return penalty
