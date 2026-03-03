"""Tests for normalized content length preferences."""

from src.models.content import ContentType
from src.recommendations.content_length import (
    LengthPreference,
    classify_length,
    get_length_value,
    score_length_match,
)
from tests.factories import make_item

# ---------------------------------------------------------------------------
# get_length_value tests
# ---------------------------------------------------------------------------


class TestGetLengthValue:
    def test_book_pages(self) -> None:
        item = make_item(content_type=ContentType.BOOK, metadata={"pages": 300})
        assert get_length_value(item) == 300

    def test_book_num_pages(self) -> None:
        item = make_item(content_type=ContentType.BOOK, metadata={"num_pages": 150})
        assert get_length_value(item) == 150

    def test_movie_runtime(self) -> None:
        item = make_item(content_type=ContentType.MOVIE, metadata={"runtime": 120})
        assert get_length_value(item) == 120

    def test_tv_show_seasons(self) -> None:
        item = make_item(content_type=ContentType.TV_SHOW, metadata={"seasons": 5})
        assert get_length_value(item) == 5

    def test_video_game_playtime(self) -> None:
        item = make_item(
            content_type=ContentType.VIDEO_GAME, metadata={"playtime_hours": 25}
        )
        assert get_length_value(item) == 25

    def test_no_metadata_returns_none(self) -> None:
        item = make_item(content_type=ContentType.BOOK, metadata={})
        assert get_length_value(item) is None

    def test_non_numeric_value_returns_none(self) -> None:
        item = make_item(content_type=ContentType.BOOK, metadata={"pages": "unknown"})
        assert get_length_value(item) is None

    def test_string_numeric_value_converts(self) -> None:
        item = make_item(content_type=ContentType.BOOK, metadata={"pages": "300"})
        assert get_length_value(item) == 300


# ---------------------------------------------------------------------------
# classify_length tests
# ---------------------------------------------------------------------------


class TestClassifyLength:
    def test_short_book(self) -> None:
        item = make_item(content_type=ContentType.BOOK, metadata={"pages": 200})
        assert classify_length(item) == LengthPreference.SHORT

    def test_medium_book(self) -> None:
        item = make_item(content_type=ContentType.BOOK, metadata={"pages": 350})
        assert classify_length(item) == LengthPreference.MEDIUM

    def test_long_book(self) -> None:
        item = make_item(content_type=ContentType.BOOK, metadata={"pages": 800})
        assert classify_length(item) == LengthPreference.LONG

    def test_short_movie(self) -> None:
        item = make_item(content_type=ContentType.MOVIE, metadata={"runtime": 80})
        assert classify_length(item) == LengthPreference.SHORT

    def test_medium_movie(self) -> None:
        item = make_item(content_type=ContentType.MOVIE, metadata={"runtime": 120})
        assert classify_length(item) == LengthPreference.MEDIUM

    def test_long_movie(self) -> None:
        item = make_item(content_type=ContentType.MOVIE, metadata={"runtime": 180})
        assert classify_length(item) == LengthPreference.LONG

    def test_short_tv_show(self) -> None:
        item = make_item(content_type=ContentType.TV_SHOW, metadata={"seasons": 2})
        assert classify_length(item) == LengthPreference.SHORT

    def test_medium_tv_show(self) -> None:
        item = make_item(content_type=ContentType.TV_SHOW, metadata={"seasons": 4})
        assert classify_length(item) == LengthPreference.MEDIUM

    def test_long_tv_show(self) -> None:
        item = make_item(content_type=ContentType.TV_SHOW, metadata={"seasons": 10})
        assert classify_length(item) == LengthPreference.LONG

    def test_short_video_game(self) -> None:
        item = make_item(
            content_type=ContentType.VIDEO_GAME, metadata={"playtime_hours": 5}
        )
        assert classify_length(item) == LengthPreference.SHORT

    def test_medium_video_game(self) -> None:
        item = make_item(
            content_type=ContentType.VIDEO_GAME, metadata={"playtime_hours": 20}
        )
        assert classify_length(item) == LengthPreference.MEDIUM

    def test_long_video_game(self) -> None:
        item = make_item(
            content_type=ContentType.VIDEO_GAME, metadata={"playtime_hours": 60}
        )
        assert classify_length(item) == LengthPreference.LONG

    def test_no_metadata_returns_none(self) -> None:
        item = make_item(content_type=ContentType.BOOK, metadata={})
        assert classify_length(item) is None

    def test_boundary_short_max(self) -> None:
        """Value exactly at short_max boundary is classified as short."""
        item = make_item(content_type=ContentType.BOOK, metadata={"pages": 250})
        assert classify_length(item) == LengthPreference.SHORT

    def test_boundary_medium_max(self) -> None:
        """Value exactly at medium_max boundary is classified as medium."""
        item = make_item(content_type=ContentType.BOOK, metadata={"pages": 500})
        assert classify_length(item) == LengthPreference.MEDIUM


# ---------------------------------------------------------------------------
# score_length_match tests
# ---------------------------------------------------------------------------


class TestScoreLengthMatch:
    """Tests for the soft scoring function."""

    def test_any_preference_returns_1(self) -> None:
        item = make_item(content_type=ContentType.BOOK, metadata={"pages": 1000})
        assert score_length_match(item, {"book": "any"}) == 1.0

    def test_no_preference_defaults_to_1(self) -> None:
        item = make_item(content_type=ContentType.BOOK, metadata={"pages": 1000})
        assert score_length_match(item, {}) == 1.0

    def test_exact_match_returns_1(self) -> None:
        item = make_item(content_type=ContentType.BOOK, metadata={"pages": 200})
        assert score_length_match(item, {"book": "short"}) == 1.0

    def test_adjacent_category_returns_07(self) -> None:
        """Medium item with short preference is adjacent (distance 1)."""
        item = make_item(content_type=ContentType.BOOK, metadata={"pages": 350})
        assert score_length_match(item, {"book": "short"}) == 0.7

    def test_opposite_ends_returns_04(self) -> None:
        """Long item with short preference is opposite (distance 2)."""
        item = make_item(content_type=ContentType.BOOK, metadata={"pages": 800})
        assert score_length_match(item, {"book": "short"}) == 0.4

    def test_no_metadata_returns_08(self) -> None:
        """Items without length metadata get benefit of the doubt."""
        item = make_item(content_type=ContentType.BOOK, metadata={})
        assert score_length_match(item, {"book": "short"}) == 0.8

    def test_different_content_type_not_affected(self) -> None:
        """A movie preference does not penalize a book."""
        item = make_item(content_type=ContentType.BOOK, metadata={"pages": 800})
        assert score_length_match(item, {"movie": "short"}) == 1.0
