"""Genre-fatigue variety penalty applied after completing content.

When ``variety_after_completion`` is enabled, recently finished genres are
penalised on a stepped ladder so the recommender hops between genres instead
of marching through the next entry in the genre/series the user just finished.

The ladder is built from the user's COMPLETED items ordered by completion date
(newest first). Each distinct thematic genre cluster encountered claims the
next rung; the most recently finished cluster receives the strongest penalty
and the penalty decays linearly to zero over :data:`VARIETY_LADDER_STEPS`
rungs. A candidate is penalised by the strongest penalty among the clusters it
shares with the ladder (i.e. its freshest matching genre).

The penalty is multiplicative on a candidate's final score: a penalty of
``0.8`` multiplies the score by ``0.2``. Because the top penalty is below
``1.0``, a fully-penalised candidate still keeps a fraction of its score, so a
genre-homogeneous library never produces an empty recommendation list.
"""

from __future__ import annotations

from datetime import date

from src.models.content import ConsumptionStatus, ContentItem
from src.recommendations.genre_clusters import get_clusters_for_terms
from src.recommendations.genre_normalizer import extract_and_normalize_genres

# Strongest penalty, applied to the most recently finished genre cluster.
# 0.8 => candidates in that cluster keep 20% of their score: a hard but not
# total suppression, so genre-homogeneous libraries never return an empty list.
VARIETY_TOP_PENALTY = 0.8

# Number of distinct recently finished clusters the penalty ladder spans.
# Penalty decays linearly: rung 0 = TOP, rung STEPS-1 = TOP/STEPS, rung STEPS+ = 0.
VARIETY_LADDER_STEPS = 5


def _completion_sort_key(item: ContentItem) -> tuple[bool, date, int]:
    """Sort key ordering completed items newest-first.

    Items with a known ``date_completed`` sort before those without; among
    dated items the most recent comes first; ``db_id`` breaks ties so the
    ordering is deterministic. Used with ``reverse=True``.

    Args:
        item: A completed content item.

    Returns:
        Tuple ``(has_date, date, db_id)`` for descending sort.
    """
    return (
        item.date_completed is not None,
        item.date_completed or date.min,
        item.db_id or 0,
    )


def build_variety_ladder(
    completed_items: list[ContentItem],
    *,
    steps: int = VARIETY_LADDER_STEPS,
    top_penalty: float = VARIETY_TOP_PENALTY,
) -> dict[str, float]:
    """Build a cluster -> penalty ladder from recently completed items.

    Only items with status :attr:`ConsumptionStatus.COMPLETED` contribute —
    items the user is actively consuming do not represent a *finished* genre.
    Items are scanned newest-first; each distinct thematic cluster claims the
    next rung until ``steps`` distinct clusters have been recorded. Rung ``i``
    receives penalty ``top_penalty * (steps - i) / steps``.

    Args:
        completed_items: Consumed items (across content types). Non-completed
            items are ignored.
        steps: Number of distinct clusters the ladder spans.
        top_penalty: Penalty for the most recently finished cluster.

    Returns:
        Mapping of cluster name to penalty in ``(0, top_penalty]``. Empty when
        no completed items carry a recognised genre cluster.
    """
    if steps <= 0:
        return {}

    completed = [
        item for item in completed_items if item.status == ConsumptionStatus.COMPLETED
    ]
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


def variety_penalty_for(item: ContentItem, ladder: dict[str, float]) -> float:
    """Return the variety penalty for *item* given a penalty *ladder*.

    The penalty is the strongest among the clusters the candidate shares with
    the ladder — i.e. the candidate is judged by its freshest matching genre.

    Args:
        item: Candidate item being scored.
        ladder: Cluster -> penalty mapping from :func:`build_variety_ladder`.

    Returns:
        Penalty in ``[0, top_penalty]``; ``0.0`` when no cluster matches.
    """
    if not ladder:
        return 0.0
    clusters = get_clusters_for_terms(extract_and_normalize_genres(item.metadata))
    return max(
        (ladder[cluster] for cluster in clusters if cluster in ladder), default=0.0
    )
