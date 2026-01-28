"""Tests for UserPreferenceConfig dataclass."""

from src.models.user_preferences import UserPreferenceConfig


class TestUserPreferenceConfig:
    def test_default_values(self) -> None:
        """New config has sensible defaults."""
        config = UserPreferenceConfig()
        assert config.scorer_weights == {}
        assert config.series_in_order is True
        assert config.variety_after_completion is False
        assert config.minimum_book_pages is None
        assert config.maximum_movie_runtime is None
        assert config.custom_rules == []

    def test_round_trip(self) -> None:
        """to_dict -> from_dict produces an equal object."""
        original = UserPreferenceConfig(
            scorer_weights={"genre_match": 3.0, "creator_match": 0.5},
            series_in_order=False,
            variety_after_completion=True,
            minimum_book_pages=200,
            maximum_movie_runtime=120,
            custom_rules=["no horror"],
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
            "variety_after_completion",
            "minimum_book_pages",
            "maximum_movie_runtime",
            "custom_rules",
        }
        assert set(data.keys()) == expected_keys
