"""Scoring pipeline that aggregates multiple scorers into a single ranking."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from src.models.content import ContentItem
from src.recommendations.scorers import SCORER_NAME_MAP, Scorer, ScoringContext

logger = logging.getLogger(__name__)

# Build reverse map: scorer class -> config key
_CLASS_TO_NAME: dict[type[Scorer], str] = {
    scorer_class: name for name, scorer_class in SCORER_NAME_MAP.items()
}


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

    def score_candidates_with_breakdown(
        self,
        candidates: list[ContentItem],
        context: ScoringContext,
    ) -> list[ScoredCandidate]:
        """Score and sort *candidates*, returning per-scorer breakdowns.

        Behaves like :meth:`score_candidates` but additionally captures the
        raw (clamped, pre-weight) score from each scorer.

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

        results.sort(key=lambda scored: scored.aggregate_score, reverse=True)
        return results
