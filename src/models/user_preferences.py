"""User preference configuration for the recommendation system."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
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
        custom_rules: Free-form rule descriptions interpreted by the
            pattern-based or LLM-powered preference interpreter.
        content_length_preferences: Per-content-type length preference.
            Maps content type string to length preference string
            (e.g. ``{"book": "short", "movie": "any"}``).
            Valid values: ``"any"``, ``"short"``, ``"medium"``, ``"long"``.
        diversity_weight: Weight for genre-diversity bonus (0.0-1.0).
            When > 0, candidates whose genres differ from recently completed
            items receive a score boost, encouraging genre-hopping.
            Default 0.0 (disabled).
        theme: User's preferred UI theme ID. Persisted to the backend so
            it syncs across browsers/devices. Empty string means "use
            system default (nord)".
    """

    scorer_weights: dict[str, float] = field(default_factory=dict)
    series_in_order: bool = True
    variety_after_completion: bool = False
    custom_rules: list[str] = field(default_factory=list)
    content_length_preferences: dict[str, str] = field(default_factory=dict)
    diversity_weight: float = 0.0
    theme: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a dictionary for JSON storage.

        Returns:
            Dictionary representation suitable for JSON serialization.
        """
        return asdict(self)

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
            custom_rules=data.get("custom_rules", []),
            content_length_preferences=data.get("content_length_preferences", {}),
            diversity_weight=data.get("diversity_weight", 0.0),
            theme=data.get("theme", ""),
        )
