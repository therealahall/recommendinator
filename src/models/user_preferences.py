from __future__ import annotations

from dataclasses import asdict, dataclass, field
from math import isfinite
from typing import Any, ClassVar


class PreferenceValidationError(ValueError):
    pass


@dataclass
class UserPreferenceConfig:
    scorer_weights: dict[str, float] = field(default_factory=dict)
    series_in_order: bool = True
    variety_penalty: float = 0.0
    custom_rules: list[str] = field(default_factory=list)
    content_length_preferences: dict[str, str] = field(default_factory=dict)

    #: Highest variety strength a user may set. The engine divides this
    #: preference by ``MAX_VARIETY_PENALTY`` to get the ladder's top penalty
    #: fraction, so ``5.0`` yields a 1.0 fraction that fully zeroes a
    #: just-finished genre (no score floor).
    MAX_VARIETY_PENALTY: ClassVar[float] = 5.0

    #: Most rules ``custom_rules`` holds. A write merges into a single
    #: ``users.settings`` blob that each recommendation request parses, and
    #: free text is the one collection here no closed key set bounds.
    MAX_CUSTOM_RULES: ClassVar[int] = 50

    #: Longest single rule. Free text with no closed key set to bound it.
    MAX_CUSTOM_RULE_LENGTH: ClassVar[int] = 500

    #: The old boolean applied a fixed 0.8 top-penalty fraction; on the 0.0-5.0
    #: scale that same full-strength fraction is ``0.8 * MAX_VARIETY_PENALTY ==
    #: 4.0``, so migrated users keep the exact behaviour they had before the
    #: slider existed.
    LEGACY_VARIETY_ON: ClassVar[float] = 0.8 * MAX_VARIETY_PENALTY

    def raise_if_unstorable(self) -> None:
        """``JSONResponse`` will not render a non-finite float, so one stored
        answers 500 on every later read. HTTP is not the only door: Click's
        ``float`` takes ``inf``.
        """
        for name, weight in self.scorer_weights.items():
            if not isfinite(weight):
                raise PreferenceValidationError(
                    f"Scorer weight '{name}' must be a finite number."
                )
        if not isfinite(self.variety_penalty):
            raise PreferenceValidationError("variety_penalty must be a finite number.")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> UserPreferenceConfig:
        """Sanitizes on read as well as refusing on write: rows written before
        ``raise_if_unstorable`` existed can hold a weight or a penalty no JSON
        response renders, and those 500 every read until they are read past.
        """
        return cls(
            scorer_weights=cls._resolve_scorer_weights(data),
            series_in_order=data.get("series_in_order", True),
            variety_penalty=cls._resolve_variety_penalty(data),
            custom_rules=data.get("custom_rules", []),
            content_length_preferences=data.get("content_length_preferences", {}),
        )

    @classmethod
    def _resolve_scorer_weights(cls, data: dict[str, Any]) -> dict[str, float]:
        return {
            name: float(weight)
            for name, weight in data.get("scorer_weights", {}).items()
            if isinstance(weight, (int, float)) and isfinite(weight)
        }

    @classmethod
    def _resolve_variety_penalty(cls, data: dict[str, Any]) -> float:
        if "variety_penalty" in data:
            penalty = float(data["variety_penalty"])
            return max(0.0, min(cls.MAX_VARIETY_PENALTY, penalty))
        if data.get("variety_after_completion"):
            return cls.LEGACY_VARIETY_ON
        return 0.0
