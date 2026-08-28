"""The recommendation record the engine emits and every consumer reads."""

from dataclasses import dataclass, field
from typing import TypedDict

from src.models.content import ContentItem
from src.utils.series import (
    get_series_name_from_metadata,
    get_series_position_from_metadata,
)


class RecommendationPayload(TypedDict):
    """The JSON shape both interfaces emit for one recommendation."""

    db_id: int | None
    title: str
    author: str | None
    series: str | None
    series_index: float | None
    score: float
    reasoning: str
    score_breakdown: dict[str, float]
    variety_penalty: float


@dataclass(frozen=True)
class Recommendation:
    item: ContentItem
    score: float
    reasoning: str
    score_breakdown: dict[str, float] = field(default_factory=dict)
    variety_penalty: float = 0.0
    contributing_items: list[ContentItem] = field(default_factory=list)
    adaptations: list[ContentItem] = field(default_factory=list)

    def to_payload(self) -> RecommendationPayload:
        return {
            "db_id": self.item.db_id,
            "title": self.item.title,
            "author": self.item.author,
            "series": get_series_name_from_metadata(self.item.metadata),
            "series_index": get_series_position_from_metadata(self.item.metadata),
            "score": self.score,
            "reasoning": self.reasoning,
            "score_breakdown": self.score_breakdown,
            "variety_penalty": self.variety_penalty,
        }
