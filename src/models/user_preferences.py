"""User preference configuration for the recommendation system."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, ClassVar


@dataclass
class UserPreferenceConfig:
    """Per-user preference configuration that overrides system defaults.

    Attributes:
        scorer_weights: Sparse dict of scorer name -> weight. Only keys the
            user has explicitly set are present; missing keys mean "use system
            default."
        series_in_order: Whether to prefer recommending series in order.
        variety_penalty: Strength of the genre-fatigue penalty applied after
            completing content, on the same 0.0-5.0 scale as the scorer
            weights. ``0.0`` disables it; higher values demote candidates whose
            genre the user recently finished more strongly. The engine divides
            it by ``MAX_VARIETY_PENALTY`` to derive the ladder's top penalty
            fraction (see ``src/recommendations/variety.py``), so ``5.0`` fully
            zeroes a just-finished genre. Default 0.0 (disabled).
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
    variety_penalty: float = 0.0
    custom_rules: list[str] = field(default_factory=list)
    content_length_preferences: dict[str, str] = field(default_factory=dict)
    diversity_weight: float = 0.0
    theme: str = ""

    #: Highest variety strength a user may set, on the same 0.0-5.0 scale as the
    #: scorer weights. The engine divides this preference by ``MAX_VARIETY_PENALTY``
    #: to get the ladder's top penalty fraction, so ``5.0`` yields a 1.0 fraction
    #: that fully zeroes a just-finished genre (no score floor).
    MAX_VARIETY_PENALTY: ClassVar[float] = 5.0

    #: Strength a legacy ``variety_after_completion = true`` migrates to. The old
    #: boolean applied a fixed 0.8 top-penalty fraction; on the 0.0-5.0 scale that
    #: same full-strength fraction is ``0.8 * MAX_VARIETY_PENALTY == 4.0``, so
    #: migrated users keep the exact behaviour they had before the slider existed.
    LEGACY_VARIETY_ON: ClassVar[float] = 0.8 * MAX_VARIETY_PENALTY

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a dictionary for JSON storage.

        Returns:
            Dictionary representation suitable for JSON serialization.
        """
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> UserPreferenceConfig:
        """Deserialize from a dictionary.

        Migrates the legacy boolean ``variety_after_completion`` field: stored
        JSON written before the slider existed maps ``True`` -> ``LEGACY_VARIETY_ON``
        (the old full-strength behaviour) and ``False`` -> ``0.0``. A present
        ``variety_penalty`` always wins and is clamped into
        ``[0.0, MAX_VARIETY_PENALTY]``.

        Args:
            data: Dictionary representation (e.g. from JSON).

        Returns:
            New UserPreferenceConfig instance.
        """
        return cls(
            scorer_weights=data.get("scorer_weights", {}),
            series_in_order=data.get("series_in_order", True),
            variety_penalty=cls._resolve_variety_penalty(data),
            custom_rules=data.get("custom_rules", []),
            content_length_preferences=data.get("content_length_preferences", {}),
            diversity_weight=data.get("diversity_weight", 0.0),
            theme=data.get("theme", ""),
        )

    @classmethod
    def _resolve_variety_penalty(cls, data: dict[str, Any]) -> float:
        """Resolve the variety penalty from stored data, migrating the old key.

        Args:
            data: Dictionary representation (e.g. from JSON).

        Returns:
            A penalty clamped into ``[0.0, MAX_VARIETY_PENALTY]``.
        """
        if "variety_penalty" in data:
            penalty = float(data["variety_penalty"])
            return max(0.0, min(cls.MAX_VARIETY_PENALTY, penalty))
        if data.get("variety_after_completion"):
            return cls.LEGACY_VARIETY_ON
        return 0.0
