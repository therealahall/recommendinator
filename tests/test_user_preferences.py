"""Tests for UserPreferenceConfig dataclass."""

from src.models.user_preferences import UserPreferenceConfig


class TestUserPreferenceConfig:
    def test_default_values(self) -> None:
        """New config has sensible defaults."""
        config = UserPreferenceConfig()
        assert config.scorer_weights == {}
        assert config.series_in_order is True
        assert config.variety_penalty == 0.0
        assert config.custom_rules == []
        assert config.content_length_preferences == {}
        assert config.diversity_weight == 0.0

    def test_round_trip(self) -> None:
        """to_dict -> from_dict produces an equal object."""
        original = UserPreferenceConfig(
            scorer_weights={"genre_match": 3.0, "creator_match": 0.5},
            series_in_order=False,
            variety_penalty=0.4,
            custom_rules=["no horror"],
            content_length_preferences={"book": "short", "movie": "long"},
        )
        restored = UserPreferenceConfig.from_dict(original.to_dict())
        assert restored == original

    def test_from_dict_empty_input(self) -> None:
        """from_dict with empty dict produces defaults."""
        config = UserPreferenceConfig.from_dict({})
        assert config == UserPreferenceConfig()

    def test_from_dict_partial_weights(self) -> None:
        """from_dict with partial data fills defaults for missing keys."""
        config = UserPreferenceConfig.from_dict(
            {"scorer_weights": {"genre_match": 2.5}}
        )
        assert config.scorer_weights == {"genre_match": 2.5}
        assert config.series_in_order is True
        assert config.custom_rules == []

    def test_to_dict_contains_all_fields(self) -> None:
        """to_dict output contains every expected key."""
        config = UserPreferenceConfig()
        data = config.to_dict()
        expected_keys = {
            "scorer_weights",
            "series_in_order",
            "variety_penalty",
            "custom_rules",
            "content_length_preferences",
            "diversity_weight",
            "theme",
        }
        assert set(data.keys()) == expected_keys

    def test_diversity_weight_round_trip(self) -> None:
        """diversity_weight survives to_dict -> from_dict."""
        config = UserPreferenceConfig(diversity_weight=0.3)
        restored = UserPreferenceConfig.from_dict(config.to_dict())
        assert restored.diversity_weight == 0.3

    def test_variety_penalty_round_trip(self) -> None:
        """variety_penalty survives to_dict -> from_dict."""
        config = UserPreferenceConfig(variety_penalty=0.5)
        restored = UserPreferenceConfig.from_dict(config.to_dict())
        assert restored.variety_penalty == 0.5

    def test_from_dict_missing_variety_penalty_defaults_to_zero(self) -> None:
        """Stored JSON without either variety key yields the disabled default."""
        config = UserPreferenceConfig.from_dict({"scorer_weights": {}})
        assert config.variety_penalty == 0.0

    def test_from_dict_keeps_variety_penalty_at_max_boundary(self) -> None:
        """The maximum value passes through unchanged (boundary, not clamped)."""
        config = UserPreferenceConfig.from_dict({"variety_penalty": 0.8})
        assert config.variety_penalty == 0.8

    def test_from_dict_clamps_variety_penalty_above_max(self) -> None:
        """An out-of-range high value is clamped to the maximum penalty."""
        config = UserPreferenceConfig.from_dict({"variety_penalty": 5.0})
        # Anchor to the literal cap so the test fails loudly if the constant moves.
        assert config.variety_penalty == 0.8
        assert config.variety_penalty == UserPreferenceConfig.MAX_VARIETY_PENALTY

    def test_from_dict_clamps_negative_variety_penalty(self) -> None:
        """A negative value is clamped up to zero (disabled)."""
        config = UserPreferenceConfig.from_dict({"variety_penalty": -1.0})
        assert config.variety_penalty == 0.0

    def test_from_dict_ignores_deprecated_keys(self) -> None:
        """from_dict safely ignores old deprecated keys in stored JSON."""
        data = {
            "scorer_weights": {},
            "minimum_book_pages": 200,
            "maximum_movie_runtime": 120,
        }
        config = UserPreferenceConfig.from_dict(data)
        assert config.scorer_weights == {}
        assert not hasattr(config, "minimum_book_pages")
        assert not hasattr(config, "maximum_movie_runtime")


class TestVarietyPenaltyMigrationRegression:
    """Regression tests for migrating the legacy boolean variety field.

    Bug context: ``variety_after_completion`` was a stored boolean. Converting
    it to the ``variety_penalty`` float slider risked silently dropping every
    user's existing on/off choice that lives in ``users.settings`` JSON written
    before this change.
    Root cause: ``from_dict`` keyed only on the new field, so legacy JSON would
    have resolved to the default (disabled) regardless of the old toggle.
    Fix: ``from_dict`` migrates a legacy ``variety_after_completion`` key when
    no ``variety_penalty`` is present — ``True`` -> the maximum penalty (the
    old "on" behaviour), ``False`` -> ``0.0`` — while a present
    ``variety_penalty`` always wins.
    """

    def test_legacy_true_maps_to_max_penalty_regression(self) -> None:
        """Old "on" preference migrates to the maximum penalty (old behaviour)."""
        config = UserPreferenceConfig.from_dict({"variety_after_completion": True})
        # Anchor to the literal cap so the test fails loudly if the constant moves.
        assert config.variety_penalty == 0.8
        assert config.variety_penalty == UserPreferenceConfig.MAX_VARIETY_PENALTY

    def test_legacy_false_maps_to_zero_regression(self) -> None:
        """Old "off" preference migrates to a disabled penalty."""
        config = UserPreferenceConfig.from_dict({"variety_after_completion": False})
        assert config.variety_penalty == 0.0

    def test_new_field_wins_over_legacy_key_regression(self) -> None:
        """A present variety_penalty takes precedence over the legacy boolean."""
        config = UserPreferenceConfig.from_dict(
            {"variety_after_completion": True, "variety_penalty": 0.3}
        )
        assert config.variety_penalty == 0.3
