"""Genre-fatigue variety penalty applied after completing content.

When the user's ``variety_penalty`` preference is non-zero, recently finished
genres are penalised on a stepped ladder so the recommender hops between genres
instead of marching through the next entry in the genre/series just finished.
The engine derives the ladder's ``top_penalty`` fraction from that preference by
dividing it by the preference scale's maximum, so the slider's full value yields
the strongest rung.

The ladder is built from the user's completion events ordered by completion
date (newest first). A completion event is a fully COMPLETED item, or an
ongoing TV show with at least one finished season — that season's completion
is dated by its most recent watched-season timestamp rather than
``date_completed``. Each distinct thematic genre cluster encountered claims
the next rung; the most recently finished cluster receives the strongest
penalty and the penalty decays linearly to zero over
:data:`VARIETY_LADDER_STEPS` rungs. A candidate is penalised by the strongest
penalty among the clusters it shares with the ladder (i.e. its freshest
matching genre).

The penalty is multiplicative on a candidate's final score: a penalty fraction
of ``0.8`` multiplies the score by ``0.2``. At the full strength fraction of
``1.0`` a just-finished genre's same-type candidates are zeroed entirely — there
is no score floor.
"""

from __future__ import annotations

from datetime import date

from src.models.content import ConsumptionStatus, ContentItem, ContentType
from src.recommendations.genre_clusters import get_clusters_for_terms
from src.recommendations.genre_normalizer import extract_and_normalize_genres
from src.utils.series import latest_season_watched_date

# Full-strength penalty fraction, applied to the most recently finished genre
# cluster when the user sets ``variety_penalty`` to its maximum. ``1.0`` zeroes
# that cluster's same-type candidates entirely (no score floor). The engine
# scales it down for lower preference values; it is the default top rung for
# callers that build a ladder directly.
VARIETY_TOP_PENALTY = 1.0

# Number of distinct recently finished clusters the penalty ladder spans.
# Penalty decays linearly: rung 0 = TOP, rung STEPS-1 = TOP/STEPS, rung STEPS+ = 0.
VARIETY_LADDER_STEPS = 5

# Multiplier applied to the variety penalty of an item that continues a series
# the user is actively progressing through. Finishing book #1 of a series does
# not mean the user is done with that genre — an unfinished series is the
# opposite of a completed one — so the next book is softened (halved), not
# exempted: genre fatigue still nudges it down but no longer buries it.
VARIETY_SERIES_CONTINUATION_FACTOR = 0.5


def _is_completion_event(item: ContentItem) -> bool:
    """Whether *item* represents a finished genre for the variety ladder.

    A completed book/movie/game/fully-watched show qualifies, and so does an
    ongoing TV show with at least one finished season — finishing a season is a
    completion just like finishing a book in a series, even while the show as a
    whole stays in progress.
    """
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
    """Completion date used for ladder ordering, or None if undated.

    Fully completed items use ``date_completed``; an ongoing show uses its most
    recent watched-season date. An undated event still lands on the ladder but
    sorts to the weakest rung.
    """
    if item.status == ConsumptionStatus.COMPLETED:
        return item.date_completed
    return latest_season_watched_date(item)


def _completion_sort_key(item: ContentItem) -> tuple[bool, date, int]:
    """Sort key ordering completion events newest-first.

    Dated events sort before undated ones; among dated events the most recent
    comes first; ``db_id`` breaks ties deterministically. Used with
    ``reverse=True``.

    Args:
        item: A completion-event content item.

    Returns:
        Tuple ``(has_date, date, db_id)`` for descending sort.
    """
    recency = _completion_recency(item)
    return (recency is not None, recency or date.min, item.db_id or 0)


def build_variety_ladder(
    completed_items: list[ContentItem],
    *,
    steps: int = VARIETY_LADDER_STEPS,
    top_penalty: float = VARIETY_TOP_PENALTY,
) -> dict[str, float]:
    """Build a cluster -> penalty ladder from recently completed items.

    Items with status :attr:`ConsumptionStatus.COMPLETED` contribute — items
    the user is actively consuming do not otherwise represent a *finished*
    genre. The one exception is an ongoing TV show with at least one finished
    season: that season's completion is dated by its most recent watched-season
    timestamp rather than the show's (absent) ``date_completed``. Items are
    scanned newest-first; each distinct thematic cluster claims the
    next rung until ``steps`` distinct clusters have been recorded. Rung ``i``
    receives penalty ``top_penalty * (steps - i) / steps``.

    Args:
        completed_items: Consumed items (across content types). Only
            completion events contribute: items with status COMPLETED, or an
            ongoing TV show with at least one finished season. Everything
            else is ignored.
        steps: Number of distinct clusters the ladder spans.
        top_penalty: Penalty for the most recently finished cluster.

    Returns:
        Mapping of cluster name to penalty in ``(0, top_penalty]``. Empty when
        no completed items carry a recognised genre cluster.
    """
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
    """Return the variety penalty for *item* given a penalty *ladder*.

    The penalty is the strongest among the clusters the candidate shares with
    the ladder — i.e. the candidate is judged by its freshest matching genre.

    When *is_series_continuation* is True (the item is the next entry in a
    series the user is actively reading) the penalty is softened by
    :data:`VARIETY_SERIES_CONTINUATION_FACTOR` so the next book is nudged but
    not buried. Softening only lowers an existing penalty; a non-matching
    candidate stays at ``0.0``.

    Args:
        item: Candidate item being scored.
        ladder: Cluster -> penalty mapping from :func:`build_variety_ladder`.
        is_series_continuation: Whether the item continues a started series.

    Returns:
        Penalty in ``[0, top_penalty]``; ``0.0`` when no cluster matches.
    """
    if not ladder:
        return 0.0
    clusters = get_clusters_for_terms(extract_and_normalize_genres(item.metadata))
    penalty = max(
        (ladder[cluster] for cluster in clusters if cluster in ladder), default=0.0
    )
    if is_series_continuation:
        penalty *= VARIETY_SERIES_CONTINUATION_FACTOR
    return penalty
