"""Scoring pipeline that aggregates multiple scorers into a single ranking."""

from __future__ import annotations

import logging

from src.models.content import ContentItem
from src.recommendations.scorers import Scorer, ScoringContext

logger = logging.getLogger(__name__)


class ScoringPipeline:
    """Run a list of :class:`Scorer` instances over candidates and produce
    weight-normalised aggregate scores.

    Usage::

        pipeline = ScoringPipeline(scorers)
        scored = pipeline.score_candidates(candidates, context)
        # scored is [(ContentItem, float), ...] sorted descending by score
    """

    def __init__(self, scorers: list[Scorer]) -> None:
        """Initialise the pipeline.

        Args:
            scorers: Ordered list of scorers to evaluate.
        """
        self.scorers = scorers

    def score_candidates(
        self,
        candidates: list[ContentItem],
        context: ScoringContext,
    ) -> list[tuple[ContentItem, float]]:
        """Score and sort *candidates*.

        Each scorer produces a ``[0, 1]`` score that is multiplied by
        its weight. The weighted scores are summed and divided by the
        total weight to produce a normalised aggregate in ``[0, 1]``.

        Args:
            candidates: Unconsumed items to evaluate.
            context: Shared scoring context.

        Returns:
            List of ``(ContentItem, aggregate_score)`` tuples sorted
            by score descending.
        """
        if not candidates:
            return []

        total_weight = sum(scorer.weight for scorer in self.scorers)
        if total_weight == 0:
            return [(candidate, 0.0) for candidate in candidates]

        scored: list[tuple[ContentItem, float]] = []
        for candidate in candidates:
            weighted_sum = 0.0
            for scorer in self.scorers:
                raw_score = scorer.score(candidate, context)
                # Clamp individual score to [0, 1]
                clamped = max(0.0, min(1.0, raw_score))
                weighted_sum += clamped * scorer.weight
            aggregate = weighted_sum / total_weight
            # Clamp final score to [0, 1]
            aggregate = max(0.0, min(1.0, aggregate))
            scored.append((candidate, aggregate))

        # Sort descending by score
        scored.sort(key=lambda pair: pair[1], reverse=True)
        return scored
