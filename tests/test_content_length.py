"""Tests for normalized content length preferences."""

from src.models.content import ConsumptionStatus, ContentItem, ContentType
from src.recommendations.content_length import (
    LengthPreference,
    classify_length,
    filter_by_length,
    get_length_value,
    passes_length_filter,
    score_length_match,
)


def _make_item(
    content_type: ContentType = ContentType.BOOK,
    metadata: dict | None = None,
) -> ContentItem:
    return ContentItem(
        title="Test Item",
        content_type=content_type,
        status=ConsumptionStatus.UNREAD,
        metadata=metadata or {},
    )


# ---------------------------------------------------------------------------
# get_length_value tests
# ---------------------------------------------------------------------------


class TestGetLengthValue:
    def test_book_pages(self) -> None:
        item = _make_item(ContentType.BOOK, {"pages": 300})
        assert get_length_value(item) == 300

    def test_book_num_pages(self) -> None:
        item = _make_item(ContentType.BOOK, {"num_pages": 150})
        assert get_length_value(item) == 150

    def test_movie_runtime(self) -> None:
        item = _make_item(ContentType.MOVIE, {"runtime": 120})
        assert get_length_value(item) == 120

    def test_tv_show_seasons(self) -> None:
        item = _make_item(ContentType.TV_SHOW, {"seasons": 5})
        assert get_length_value(item) == 5

    def test_video_game_playtime(self) -> None:
        item = _make_item(ContentType.VIDEO_GAME, {"playtime_hours": 25})
        assert get_length_value(item) == 25

    def test_no_metadata_returns_none(self) -> None:
        item = _make_item(ContentType.BOOK, {})
        assert get_length_value(item) is None

    def test_non_numeric_value_returns_none(self) -> None:
        item = _make_item(ContentType.BOOK, {"pages": "unknown"})
        assert get_length_value(item) is None

    def test_string_numeric_value_converts(self) -> None:
        item = _make_item(ContentType.BOOK, {"pages": "300"})
        assert get_length_value(item) == 300


# ---------------------------------------------------------------------------
# classify_length tests
# ---------------------------------------------------------------------------


class TestClassifyLength:
    def test_short_book(self) -> None:
        item = _make_item(ContentType.BOOK, {"pages": 200})
        assert classify_length(item) == LengthPreference.SHORT

    def test_medium_book(self) -> None:
        item = _make_item(ContentType.BOOK, {"pages": 350})
        assert classify_length(item) == LengthPreference.MEDIUM

    def test_long_book(self) -> None:
        item = _make_item(ContentType.BOOK, {"pages": 800})
        assert classify_length(item) == LengthPreference.LONG

    def test_short_movie(self) -> None:
        item = _make_item(ContentType.MOVIE, {"runtime": 80})
        assert classify_length(item) == LengthPreference.SHORT

    def test_medium_movie(self) -> None:
        item = _make_item(ContentType.MOVIE, {"runtime": 120})
        assert classify_length(item) == LengthPreference.MEDIUM

    def test_long_movie(self) -> None:
        item = _make_item(ContentType.MOVIE, {"runtime": 180})
        assert classify_length(item) == LengthPreference.LONG

    def test_short_tv_show(self) -> None:
        item = _make_item(ContentType.TV_SHOW, {"seasons": 2})
        assert classify_length(item) == LengthPreference.SHORT

    def test_medium_tv_show(self) -> None:
        item = _make_item(ContentType.TV_SHOW, {"seasons": 4})
        assert classify_length(item) == LengthPreference.MEDIUM

    def test_long_tv_show(self) -> None:
        item = _make_item(ContentType.TV_SHOW, {"seasons": 10})
        assert classify_length(item) == LengthPreference.LONG

    def test_short_video_game(self) -> None:
        item = _make_item(ContentType.VIDEO_GAME, {"playtime_hours": 5})
        assert classify_length(item) == LengthPreference.SHORT

    def test_medium_video_game(self) -> None:
        item = _make_item(ContentType.VIDEO_GAME, {"playtime_hours": 20})
        assert classify_length(item) == LengthPreference.MEDIUM

    def test_long_video_game(self) -> None:
        item = _make_item(ContentType.VIDEO_GAME, {"playtime_hours": 60})
        assert classify_length(item) == LengthPreference.LONG

    def test_no_metadata_returns_none(self) -> None:
        item = _make_item(ContentType.BOOK, {})
        assert classify_length(item) is None

    def test_boundary_short_max(self) -> None:
        """Value exactly at short_max boundary is classified as short."""
        item = _make_item(ContentType.BOOK, {"pages": 250})
        assert classify_length(item) == LengthPreference.SHORT

    def test_boundary_medium_max(self) -> None:
        """Value exactly at medium_max boundary is classified as medium."""
        item = _make_item(ContentType.BOOK, {"pages": 500})
        assert classify_length(item) == LengthPreference.MEDIUM


# ---------------------------------------------------------------------------
# passes_length_filter tests
# ---------------------------------------------------------------------------


class TestScoreLengthMatch:
    """Tests for the soft scoring function."""

    def test_any_preference_returns_1(self) -> None:
        item = _make_item(ContentType.BOOK, {"pages": 1000})
        assert score_length_match(item, {"book": "any"}) == 1.0

    def test_no_preference_defaults_to_1(self) -> None:
        item = _make_item(ContentType.BOOK, {"pages": 1000})
        assert score_length_match(item, {}) == 1.0

    def test_exact_match_returns_1(self) -> None:
        item = _make_item(ContentType.BOOK, {"pages": 200})
        assert score_length_match(item, {"book": "short"}) == 1.0

    def test_adjacent_category_returns_07(self) -> None:
        """Medium item with short preference is adjacent (distance 1)."""
        item = _make_item(ContentType.BOOK, {"pages": 350})
        assert score_length_match(item, {"book": "short"}) == 0.7

    def test_opposite_ends_returns_04(self) -> None:
        """Long item with short preference is opposite (distance 2)."""
        item = _make_item(ContentType.BOOK, {"pages": 800})
        assert score_length_match(item, {"book": "short"}) == 0.4

    def test_no_metadata_returns_08(self) -> None:
        """Items without length metadata get benefit of the doubt."""
        item = _make_item(ContentType.BOOK, {})
        assert score_length_match(item, {"book": "short"}) == 0.8

    def test_different_content_type_not_affected(self) -> None:
        """A movie preference does not penalize a book."""
        item = _make_item(ContentType.BOOK, {"pages": 800})
        assert score_length_match(item, {"movie": "short"}) == 1.0


class TestPassesLengthFilter:
    def test_any_preference_always_passes(self) -> None:
        item = _make_item(ContentType.BOOK, {"pages": 1000})
        assert passes_length_filter(item, {"book": "any"}) is True

    def test_no_preference_defaults_to_any(self) -> None:
        item = _make_item(ContentType.BOOK, {"pages": 1000})
        assert passes_length_filter(item, {}) is True

    def test_short_preference_passes_short_item(self) -> None:
        item = _make_item(ContentType.BOOK, {"pages": 200})
        assert passes_length_filter(item, {"book": "short"}) is True

    def test_short_preference_fails_long_item(self) -> None:
        item = _make_item(ContentType.BOOK, {"pages": 800})
        assert passes_length_filter(item, {"book": "short"}) is False

    def test_no_metadata_passes_through(self) -> None:
        """Items without length metadata pass through unfiltered."""
        item = _make_item(ContentType.BOOK, {})
        assert passes_length_filter(item, {"book": "short"}) is True

    def test_different_content_type_not_affected(self) -> None:
        """A movie filter does not affect a book."""
        item = _make_item(ContentType.BOOK, {"pages": 800})
        assert passes_length_filter(item, {"movie": "short"}) is True


# ---------------------------------------------------------------------------
# filter_by_length tests
# ---------------------------------------------------------------------------


class TestFilterByLength:
    def test_filters_multiple_items(self) -> None:
        short_book = _make_item(ContentType.BOOK, {"pages": 200})
        long_book = _make_item(ContentType.BOOK, {"pages": 800})
        medium_book = _make_item(ContentType.BOOK, {"pages": 350})

        result = filter_by_length(
            [short_book, long_book, medium_book],
            {"book": "short"},
        )
        assert result == [short_book]

    def test_empty_preferences_returns_all(self) -> None:
        items = [
            _make_item(ContentType.BOOK, {"pages": 200}),
            _make_item(ContentType.BOOK, {"pages": 800}),
        ]
        result = filter_by_length(items, {})
        assert result == items

    def test_mixed_content_types(self) -> None:
        short_book = _make_item(ContentType.BOOK, {"pages": 200})
        long_movie = _make_item(ContentType.MOVIE, {"runtime": 180})
        short_movie = _make_item(ContentType.MOVIE, {"runtime": 80})

        result = filter_by_length(
            [short_book, long_movie, short_movie],
            {"book": "short", "movie": "short"},
        )
        assert result == [short_book, short_movie]

    def test_items_without_metadata_pass_through(self) -> None:
        no_meta = _make_item(ContentType.BOOK, {})
        long_book = _make_item(ContentType.BOOK, {"pages": 800})

        result = filter_by_length(
            [no_meta, long_book],
            {"book": "short"},
        )
        assert result == [no_meta]
