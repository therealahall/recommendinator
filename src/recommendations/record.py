"""The recommendation record the engine emits and every consumer reads.

One declared shape for all three producer paths in
:mod:`src.recommendations.engine` — the scored path, the LLM-only path and the
library fallback — so a path with nothing to say about references, blurbs or
per-scorer rows says so with an empty default instead of a missing key.
"""

from dataclasses import dataclass, field
from typing import TypedDict

from src.models.content import ContentItem


class RecommendationPayload(TypedDict):
    """The JSON shape both interfaces emit for one recommendation.

    The field order is the order :class:`src.web.api.RecommendationResponse`
    declares, so the CLI's ``--format json`` and the web response serialise the
    same keys in the same order.
    """

    db_id: int | None
    title: str
    author: str | None
    score: float
    reasoning: str
    llm_reasoning: str | None
    score_breakdown: dict[str, float]
    variety_penalty: float


@dataclass(frozen=True)
class Recommendation:
    """One recommended item with everything the engine worked out about it.

    Attributes:
        item: The recommended content item.
        score: Pipeline aggregate in ``[0, 1]``, after any variety penalty.
        reasoning: Pipeline-generated explanation shown to the user.
        score_breakdown: Scorer config key -> raw score, empty on the paths
            that never ran the pipeline.
        variety_penalty: Genre-fatigue fraction applied to ``score`` (0.0 when
            the preference is off or the item's genre was not recently
            finished).
        contributing_items: Consumed items cited as the reason for this pick.
        adaptations: Consumed items this candidate adapts across media.
        llm_reasoning: LLM blurb, ``None`` until one is generated.
    """

    item: ContentItem
    score: float
    reasoning: str
    score_breakdown: dict[str, float] = field(default_factory=dict)
    variety_penalty: float = 0.0
    contributing_items: list[ContentItem] = field(default_factory=list)
    adaptations: list[ContentItem] = field(default_factory=list)
    llm_reasoning: str | None = None

    def to_payload(self) -> RecommendationPayload:
        """Serialise to the shared CLI/web JSON shape.

        The CLI emits this mapping directly; the web API validates it into a
        :class:`src.web.api.RecommendationResponse`. Keeping the construction
        here is what stops the two interfaces from drifting apart.
        """
        return {
            "db_id": self.item.db_id,
            "title": self.item.title,
            "author": self.item.author,
            "score": self.score,
            "reasoning": self.reasoning,
            "llm_reasoning": self.llm_reasoning,
            "score_breakdown": self.score_breakdown,
            "variety_penalty": self.variety_penalty,
        }
