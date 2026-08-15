"""Tests for UserPreferenceConfig dataclass."""

from src.models.user_preferences import UserPreferenceConfig


class TestUserPreferenceConfig:
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

    def test_from_dict_reads_past_the_retired_diversity_weight(self) -> None:
        """Preference JSON stored before diversity_weight was deleted still loads.

        The key is written into ``users.settings`` for anyone who ran an
        earlier release, so from_dict must ignore it rather than raise.
        """
        config = UserPreferenceConfig.from_dict(
            {"diversity_weight": 0.3, "series_in_order": False}
        )

        assert config == UserPreferenceConfig(series_in_order=False)

    def test_from_dict_clamps_variety_penalty_above_max(self) -> None:
        """An out-of-range high value is clamped to the maximum penalty."""
        config = UserPreferenceConfig.from_dict({"variety_penalty": 7.5})
        # Anchor to the literal cap so the test fails loudly if the constant moves.
        assert config.variety_penalty == 5.0
        assert config.variety_penalty == UserPreferenceConfig.MAX_VARIETY_PENALTY

    def test_from_dict_clamps_negative_variety_penalty(self) -> None:
        """A negative value is clamped up to zero (disabled)."""
        config = UserPreferenceConfig.from_dict({"variety_penalty": -1.0})
        assert config.variety_penalty == 0.0


class TestVarietyPenaltyMigrationRegression:
    """Regression tests for migrating the legacy boolean variety field.

    Bug context: ``variety_after_completion`` was a stored boolean. Converting
    it to the ``variety_penalty`` float slider risked silently dropping every
    user's existing on/off choice that lives in ``users.settings`` JSON written
    before this change.
    Root cause: ``from_dict`` keyed only on the new field, so legacy JSON would
    have resolved to the default (disabled) regardless of the old toggle.
    Fix: ``from_dict`` migrates a legacy ``variety_after_completion`` key when
    no ``variety_penalty`` is present — ``True`` -> ``LEGACY_VARIETY_ON`` (4.0 on
    the 0.0-5.0 scale, reproducing the old full-strength 0.8 penalty fraction),
    ``False`` -> ``0.0`` — while a present ``variety_penalty`` always wins.
    """

    def test_legacy_true_maps_to_max_penalty_regression(self) -> None:
        """Old "on" preference migrates to the full-strength scale value (4.0)."""
        config = UserPreferenceConfig.from_dict({"variety_after_completion": True})
        # 4.0/5.0 == the old fixed 0.8 top penalty fraction, so behaviour is kept.
        assert config.variety_penalty == 4.0
        assert config.variety_penalty == UserPreferenceConfig.LEGACY_VARIETY_ON

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


class TestAPoisonedRowIsStillReadableRegression:
    """Reported: a row predating ``raise_if_unstorable`` can hold ``Infinity``,
    which no JSON response renders, so the preferences page 500s forever and no
    door is left to correct it by. The penalty was clamped on read; the weights
    were taken raw.
    """

    def test_a_non_finite_stored_weight_is_read_past(self) -> None:
        """Dropped, not clamped: there is no bound to clamp a weight into."""
        config = UserPreferenceConfig.from_dict(
            {"scorer_weights": {"recency": float("inf"), "genre_match": 2.0}}
        )

        assert config.scorer_weights == {"genre_match": 2.0}

    def test_a_stored_weight_that_is_not_a_number_is_dropped(self) -> None:
        """``float("high")`` raises, so the type half of the guard carries it."""
        config = UserPreferenceConfig.from_dict(
            {"scorer_weights": {"recency": "high", "genre_match": 2.0}}
        )

        assert config.scorer_weights == {"genre_match": 2.0}

    def test_a_non_finite_penalty_is_still_clamped(self) -> None:
        """The half that already worked, so the weights fix cannot cost it."""
        assert (
            UserPreferenceConfig.from_dict({"variety_penalty": float("inf")})
        ).variety_penalty == UserPreferenceConfig.MAX_VARIETY_PENALTY
