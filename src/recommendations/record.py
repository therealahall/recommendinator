"""The recommendation record the engine emits and every consumer reads."""

from dataclasses import dataclass, field
from typing import TypedDict

from src.covers import cover_payload_url
from src.models.content import ContentItem, get_enum_value
from src.utils.item_serialization import RelatedItemPayload, related_item_to_dict
from src.utils.series import (
    get_series_name_from_metadata,
    get_series_position_from_metadata,
)


class RecommendationPayload(TypedDict):
    """The JSON shape both interfaces emit for one recommendation."""

    db_id: int | None
    title: str
    author: str | None
    content_type: str
    cover_url: str | None
    series: str | None
    series_index: float | None
    score: float
    reasoning: str
    score_breakdown: dict[str, float]
    scorer_weights: dict[str, float]
    variety_penalty: float
    contributing_items: list[RelatedItemPayload]
    adaptations: list[RelatedItemPayload]


@dataclass(frozen=True)
class Recommendation:
    item: ContentItem
    score: float
    reasoning: str
    score_breakdown: dict[str, float] = field(default_factory=dict)
    scorer_weights: dict[str, float] = field(default_factory=dict)
    variety_penalty: float = 0.0
    contributing_items: list[ContentItem] = field(default_factory=list)
    adaptations: list[ContentItem] = field(default_factory=list)

    def to_payload(self) -> RecommendationPayload:
        return {
            "db_id": self.item.db_id,
            "title": self.item.title,
            "author": self.item.author,
            "content_type": get_enum_value(self.item.content_type),
            "cover_url": cover_payload_url(self.item),
            "series": get_series_name_from_metadata(self.item.metadata),
            "series_index": get_series_position_from_metadata(self.item.metadata),
            "score": self.score,
            "reasoning": self.reasoning,
            "score_breakdown": self.score_breakdown,
            "scorer_weights": self.scorer_weights,
            "variety_penalty": self.variety_penalty,
            "contributing_items": [
                related_item_to_dict(item) for item in self.contributing_items
            ],
            "adaptations": [related_item_to_dict(item) for item in self.adaptations],
        }
