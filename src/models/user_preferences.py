"""User preference configuration for the recommendation system."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class UserPreferenceConfig:
    """Per-user preference configuration that overrides system defaults.

    Attributes:
        scorer_weights: Sparse dict of scorer name -> weight. Only keys the
            user has explicitly set are present; missing keys mean "use system
            default."
        series_in_order: Whether to prefer recommending series in order.
        variety_after_completion: Whether to recommend variety after completing
            a series.
        minimum_book_pages: Deprecated -- use content_length_preferences instead.
        maximum_movie_runtime: Deprecated -- use content_length_preferences instead.
        custom_rules: Free-form rule descriptions interpreted by the
            pattern-based or LLM-powered preference interpreter.
        content_length_preferences: Per-content-type length preference.
            Maps content type string to length preference string
            (e.g. ``{"book": "short", "movie": "any"}``).
            Valid values: ``"any"``, ``"short"``, ``"medium"``, ``"long"``.
    """

    scorer_weights: dict[str, float] = field(default_factory=dict)
    series_in_order: bool = True
    variety_after_completion: bool = False
    minimum_book_pages: int | None = None
    maximum_movie_runtime: int | None = None
    custom_rules: list[str] = field(default_factory=list)
    content_length_preferences: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a dictionary for JSON storage.

        Returns:
            Dictionary representation suitable for JSON serialization.
        """
        return {
            "scorer_weights": self.scorer_weights,
            "series_in_order": self.series_in_order,
            "variety_after_completion": self.variety_after_completion,
            "minimum_book_pages": self.minimum_book_pages,
            "maximum_movie_runtime": self.maximum_movie_runtime,
            "custom_rules": self.custom_rules,
            "content_length_preferences": self.content_length_preferences,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> UserPreferenceConfig:
        """Deserialize from a dictionary.

        Args:
            data: Dictionary representation (e.g. from JSON).

        Returns:
            New UserPreferenceConfig instance.
        """
        return cls(
            scorer_weights=data.get("scorer_weights", {}),
            series_in_order=data.get("series_in_order", True),
            variety_after_completion=data.get("variety_after_completion", False),
            minimum_book_pages=data.get("minimum_book_pages"),
            maximum_movie_runtime=data.get("maximum_movie_runtime"),
            custom_rules=data.get("custom_rules", []),
            content_length_preferences=data.get("content_length_preferences", {}),
        )
