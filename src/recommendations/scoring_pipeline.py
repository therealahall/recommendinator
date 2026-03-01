"""Scoring pipeline that aggregates multiple scorers into a single ranking."""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field

from src.models.content import ContentItem
from src.recommendations.scorers import SCORER_NAME_MAP, Scorer, ScoringContext
from src.utils.series import is_first_item_in_series

logger = logging.getLogger(__name__)

# Build reverse map: scorer class -> config key.
# Derived from SCORER_NAME_MAP at import time — if SCORER_NAME_MAP is extended,
# this must be rebuilt (or the new scorer won't appear in breakdowns).
_CLASS_TO_NAME: dict[type[Scorer], str] = {
    scorer_class: name for name, scorer_class in SCORER_NAME_MAP.items()
}


def _tiebreaker_key(item: ContentItem) -> tuple[int, str]:
    """Generate a tiebreaker key for sorting items with equal scores.

    Priority:
    1. First items in a series (to encourage starting new series)
    2. Stable hash of title (for pseudo-random but consistent ordering)

    Args:
        item: Content item to generate key for.

    Returns:
        Tuple of (is_not_first_in_series, title_hash) for sorting.
        Lower values sort first, so first-in-series items come first.
    """
    is_first = is_first_item_in_series(item=item)
    # Hash the title for stable pseudo-random ordering (avoids pure alphabetical).
    # MD5 chosen for speed; this is not a security context (usedforsecurity=False).
    title_hash = hashlib.md5(item.title.encode(), usedforsecurity=False).hexdigest()
    return (0 if is_first else 1, title_hash)


@dataclass
class ScoredCandidate:
    """A candidate item with its aggregate score and per-scorer breakdown.

    Attributes:
        item: The content item.
        aggregate_score: Weight-normalised aggregate score in [0, 1].
        score_breakdown: Mapping of scorer config key to raw (clamped) score.
    """

    item: ContentItem
    aggregate_score: float
    score_breakdown: dict[str, float] = field(default_factory=dict)


class ScoringPipeline:
    """Run a list of :class:`Scorer` instances over candidates and produce
    weight-normalised aggregate scores.

    Usage::

        pipeline = ScoringPipeline(scorers)
        scored = pipeline.score_candidates_with_breakdown(candidates, context)
        # scored is [ScoredCandidate, ...] sorted descending by score
    """

    def __init__(self, scorers: list[Scorer]) -> None:
        """Initialise the pipeline.

        Args:
            scorers: Ordered list of scorers to evaluate.
        """
        self.scorers = scorers

    def score_candidates_with_breakdown(
        self,
        candidates: list[ContentItem],
        context: ScoringContext,
    ) -> list[ScoredCandidate]:
        """Score and sort *candidates*, returning per-scorer breakdowns.

        Each scorer produces a ``[0, 1]`` score that is multiplied by its
        weight. The weighted scores are summed and divided by the total weight
        to produce a normalised aggregate in ``[0, 1]``. Additionally captures
        the raw (clamped, pre-weight) score from each scorer.

        Args:
            candidates: Unconsumed items to evaluate.
            context: Shared scoring context.

        Returns:
            List of :class:`ScoredCandidate` sorted by aggregate score
            descending.
        """
        if not candidates:
            return []

        total_weight = sum(scorer.weight for scorer in self.scorers)
        if total_weight == 0:
            return [
                ScoredCandidate(item=candidate, aggregate_score=0.0)
                for candidate in candidates
            ]

        results: list[ScoredCandidate] = []
        for candidate in candidates:
            weighted_sum = 0.0
            breakdown: dict[str, float] = {}
            for scorer in self.scorers:
                raw_score = scorer.score(candidate, context)
                clamped = max(0.0, min(1.0, raw_score))
                weighted_sum += clamped * scorer.weight
                config_key = _CLASS_TO_NAME.get(type(scorer))
                if config_key:
                    breakdown[config_key] = clamped
            aggregate = max(0.0, min(1.0, weighted_sum / total_weight))
            results.append(
                ScoredCandidate(
                    item=candidate,
                    aggregate_score=aggregate,
                    score_breakdown=breakdown,
                )
            )

        # Sort descending by score, with tiebreaker for equal scores
        results.sort(
            key=lambda scored: (-scored.aggregate_score, _tiebreaker_key(scored.item)),
        )
        return results
