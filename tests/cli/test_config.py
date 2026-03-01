"""Tests for CLI configuration, especially scorer registration.

Regression: ContinuationScorer, SeriesAffinityScorer, and ContentLengthScorer
were missing from _SCORER_CONFIG_MAP, so they never ran in production even
though they were listed in SCORER_NAME_MAP and DEFAULT_SCORERS.
"""

from pathlib import Path
from typing import Any

import pytest

from src.cli.config import (
    _SCORER_CONFIG_MAP,
    build_scorers_from_config,
    load_config,
)
from src.recommendations.scorers import SCORER_NAME_MAP, Scorer

# These scorers are instantiated directly by RecommendationEngine, not via
# _SCORER_CONFIG_MAP. They require special construction (embeddings client,
# rule objects) that the config map cannot provide generically.
_ENGINE_MANAGED_SCORERS = {"semantic_similarity", "custom_preference"}


@pytest.fixture()
def example_config() -> dict[str, Any]:
    """Load the example config for tests."""
    return load_config(Path("config/example.yaml"))


class TestScorerConfigMap:
    """Verify _SCORER_CONFIG_MAP stays in sync with SCORER_NAME_MAP."""

    def test_config_map_contains_all_standard_scorers(self) -> None:
        """Every scorer in SCORER_NAME_MAP (except engine-managed ones) must
        appear in _SCORER_CONFIG_MAP so it actually runs in production.

        Bug: ContinuationScorer, SeriesAffinityScorer, ContentLengthScorer
        were absent from _SCORER_CONFIG_MAP, causing them to silently not run.
        """
        expected = set(SCORER_NAME_MAP.keys()) - _ENGINE_MANAGED_SCORERS
        actual = set(_SCORER_CONFIG_MAP.keys())
        assert actual == expected, (
            f"_SCORER_CONFIG_MAP is out of sync with SCORER_NAME_MAP.\n"
            f"  Missing: {expected - actual}\n"
            f"  Extra:   {actual - expected}"
        )

    def test_config_map_classes_match_scorer_name_map(self) -> None:
        """Classes in _SCORER_CONFIG_MAP must match SCORER_NAME_MAP."""
        for key, cls in _SCORER_CONFIG_MAP.items():
            assert key in SCORER_NAME_MAP, f"{key!r} not in SCORER_NAME_MAP"
            assert cls is SCORER_NAME_MAP[key], (
                f"Class mismatch for {key!r}: "
                f"config has {cls.__name__}, name map has {SCORER_NAME_MAP[key].__name__}"
            )


class TestBuildScorersFromConfig:
    """Verify build_scorers_from_config produces the right scorers."""

    def test_produces_all_config_map_scorers(
        self, example_config: dict[str, Any]
    ) -> None:
        """build_scorers_from_config returns one scorer per _SCORER_CONFIG_MAP entry."""
        scorers = build_scorers_from_config(example_config)

        scorer_types = {type(s) for s in scorers}
        expected_types = set(_SCORER_CONFIG_MAP.values())
        assert scorer_types == expected_types, (
            f"build_scorers_from_config is missing scorer types.\n"
            f"  Missing: {expected_types - scorer_types}\n"
            f"  Extra:   {scorer_types - expected_types}"
        )

    def test_respects_weight_overrides(self) -> None:
        """Config weight overrides are applied to the returned scorers."""
        config: dict[str, Any] = {
            "recommendations": {
                "scorer_weights": {
                    "genre_match": 5.0,
                    "continuation": 0.5,
                },
            },
        }
        scorers = build_scorers_from_config(config)
        cls_to_name = {cls: name for name, cls in _SCORER_CONFIG_MAP.items()}
        by_name: dict[str, Scorer] = {
            cls_to_name[type(s)]: s for s in scorers if type(s) in cls_to_name
        }

        assert by_name["genre_match"].weight == 5.0
        assert by_name["continuation"].weight == 0.5

    def test_uses_class_defaults_without_overrides(self) -> None:
        """Without config overrides, each scorer uses its class default weight."""
        config: dict[str, Any] = {"recommendations": {}}
        scorers = build_scorers_from_config(config)

        for scorer in scorers:
            # Each scorer should have its class default (created with no args)
            default_instance = type(scorer)()
            assert scorer.weight == default_instance.weight, (
                f"{type(scorer).__name__} weight {scorer.weight} != "
                f"default {default_instance.weight}"
            )
