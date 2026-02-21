"""Tests for series detection and filtering utilities."""

from src.models.content import ConsumptionStatus, ContentItem, ContentType
from src.utils.series import (
    _extract_series_from_title,
    _roman_to_int,
    build_series_tracking,
    expand_tv_shows_to_seasons,
    extract_series_info,
    find_earliest_recommendable,
    get_series_item_number,
    get_series_name,
    inject_seasons_watched_tracking,
    is_first_item_in_series,
    should_recommend_item,
)


def test_extract_series_info():
    """Test series information extraction from titles."""
    # Pattern 1: (Series Name, #N) - works for all types
    assert extract_series_info("Book Title (The Witcher, #4)") == ("The Witcher", 4)
    assert extract_series_info("Book (Series, #1)") == ("Series", 1)

    # Pattern 2: (Series Name #N)
    assert extract_series_info("Book (Series #2)") == ("Series", 2)

    # Pattern 3: (Series Name, Book N) - books
    assert extract_series_info("Book (Series, Book 3)") == ("Series", 3)

    # Pattern 4: (Series Name, Season N) - TV shows
    assert extract_series_info("Show (The Expanse, Season 1)") == ("The Expanse", 1)
    assert extract_series_info("Show (Breaking Bad, Season 2)") == ("Breaking Bad", 2)

    # Pattern 5: (Series Name, S1) - TV shows shorthand
    assert extract_series_info("Show (The Expanse, S1)") == ("The Expanse", 1)
    assert extract_series_info("Show (Game of Thrones, S3)") == ("Game of Thrones", 3)

    # Pattern 6: (Series Name, Part N) - movies/games
    assert extract_series_info("Movie (Lord of the Rings, Part 1)") == (
        "Lord of the Rings",
        1,
    )
    assert extract_series_info("Game (Mass Effect, Part 2)") == ("Mass Effect", 2)

    # Pattern 7: (Series Name, Episode N) - movies/TV
    assert extract_series_info("Movie (Star Wars, Episode 4)") == ("Star Wars", 4)

    # No series
    assert extract_series_info("Standalone Book") is None
    assert extract_series_info("Book (Not a Series)") is None


def test_extract_series_info_from_metadata():
    """Test series information extraction from metadata."""
    # TV show with season in metadata
    metadata_tv = {"series": "The Expanse", "season": 2}
    assert extract_series_info("The Expanse", metadata_tv, ContentType.TV_SHOW) == (
        "The Expanse",
        2,
    )

    # Movie with part in metadata
    metadata_movie = {"series_name": "Lord of the Rings", "part": 1}
    assert extract_series_info("Fellowship", metadata_movie, ContentType.MOVIE) == (
        "Lord of the Rings",
        1,
    )

    # Book with series_number in metadata
    metadata_book = {"series": "The Witcher", "series_number": 3}
    assert extract_series_info("Blood", metadata_book, ContentType.BOOK) == (
        "The Witcher",
        3,
    )

    # Game with part in metadata
    metadata_game = {"series_title": "Mass Effect", "part_number": 2}
    assert extract_series_info("ME2", metadata_game, ContentType.VIDEO_GAME) == (
        "Mass Effect",
        2,
    )


def test_expand_tv_shows_to_seasons():
    """Test expanding TV shows into season-level items for recommendations."""
    show_with_seasons = ContentItem(
        id="tvdb:280619",
        title="The Expanse",
        content_type=ContentType.TV_SHOW,
        status=ConsumptionStatus.UNREAD,
        metadata={"total_seasons": 6, "genres": ["Sci-Fi"]},
    )
    show_without_seasons = ContentItem(
        id="tvdb:999",
        title="Unknown Show",
        content_type=ContentType.TV_SHOW,
        status=ConsumptionStatus.UNREAD,
        metadata={},
    )

    expanded = expand_tv_shows_to_seasons([show_with_seasons, show_without_seasons])

    # The Expanse: 6 seasons; Unknown Show: 1 (passthrough, no expansion)
    assert len(expanded) == 7
    assert expanded[0].title == "The Expanse (Season 1)"
    assert expanded[0].id == "tvdb:280619:s1"
    assert expanded[0].parent_id == "tvdb:280619"
    assert expanded[0].metadata.get("season_number") == 1
    assert expanded[5].title == "The Expanse (Season 6)"
    assert expanded[5].id == "tvdb:280619:s6"
    assert expanded[5].parent_id == "tvdb:280619"
    assert expanded[6].title == "Unknown Show"
    assert expanded[6].id == "tvdb:999"
    assert expanded[6].parent_id is None  # passthrough items have no parent


def test_get_series_name():
    """Test getting series name from title or ContentItem."""
    # Test with title string (backward compatibility)
    assert get_series_name(title="Book (The Witcher, #4)") == "The Witcher"
    assert get_series_name(title="Standalone Book") is None

    # Test with ContentItem
    item = ContentItem(
        id="1",
        title="Book (The Expanse, #1)",
        content_type=ContentType.BOOK,
        status=ConsumptionStatus.UNREAD,
    )
    assert get_series_name(item=item) == "The Expanse"

    # Test with ContentItem and metadata
    item_with_metadata = ContentItem(
        id="2",
        title="Show",
        content_type=ContentType.TV_SHOW,
        status=ConsumptionStatus.UNREAD,
        metadata={"series": "Breaking Bad", "season": 1},
    )
    assert get_series_name(item=item_with_metadata) == "Breaking Bad"


def test_get_series_item_number():
    """Test getting item number from title."""
    assert get_series_item_number(title="Book (The Witcher, #4)") == 4
    assert get_series_item_number(title="Standalone Book") is None


def test_build_series_tracking():
    """Test building series tracking from consumed items."""
    items = [
        ContentItem(
            id="1",
            title="Book 1 (Series A, #1)",
            author="Author",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            rating=5,
        ),
        ContentItem(
            id="2",
            title="Book 2 (Series A, #2)",
            author="Author",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            rating=4,
        ),
        ContentItem(
            id="3",
            title="Book 3 (Series B, #1)",
            author="Author",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            rating=5,
        ),
        ContentItem(
            id="4",
            title="Standalone Book",
            author="Author",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            rating=4,
        ),
    ]

    tracking = build_series_tracking(items)
    assert "Series A" in tracking
    assert tracking["Series A"] == {1, 2}
    assert "Series B" in tracking
    assert tracking["Series B"] == {1}
    assert "Standalone Book" not in tracking


def test_is_first_item_in_series_by_title():
    """Test checking if item is first in series using title string."""
    assert is_first_item_in_series(title="Book (Series, #1)") is True
    assert is_first_item_in_series(title="Book (Series, #2)") is False
    assert is_first_item_in_series(title="Standalone Book") is False


def test_is_first_item_in_series():
    """Test checking if item is first in series for all content types."""
    # Test with title string (backward compatibility)
    assert is_first_item_in_series(title="Book (Series, #1)") is True
    assert is_first_item_in_series(title="Show (Series, Season 1)") is True
    assert is_first_item_in_series(title="Movie (Series, Part 1)") is True
    assert is_first_item_in_series(title="Game (Series, #1)") is True
    assert is_first_item_in_series(title="Book (Series, #2)") is False

    # Test with ContentItem
    item_first = ContentItem(
        id="1",
        title="The Expanse (The Expanse, Season 1)",
        content_type=ContentType.TV_SHOW,
        status=ConsumptionStatus.UNREAD,
    )
    assert is_first_item_in_series(item=item_first) is True

    item_second = ContentItem(
        id="2",
        title="The Expanse (The Expanse, Season 2)",
        content_type=ContentType.TV_SHOW,
        status=ConsumptionStatus.UNREAD,
    )
    assert is_first_item_in_series(item=item_second) is False

    # Test with ContentItem and metadata
    item_with_metadata = ContentItem(
        id="3",
        title="Movie",
        content_type=ContentType.MOVIE,
        status=ConsumptionStatus.UNREAD,
        metadata={"series": "Star Wars", "episode": 1},
    )
    assert is_first_item_in_series(item=item_with_metadata) is True


def test_should_recommend_book_not_in_series():
    """Test recommendation for books not in a series."""
    item = ContentItem(
        id="1",
        title="Standalone Book",
        author="Author",
        content_type=ContentType.BOOK,
        status=ConsumptionStatus.UNREAD,
    )
    assert should_recommend_item(item, {}) is True


def test_should_recommend_first_book_unstarted_series():
    """Test recommendation for first book in unstarted series."""
    item = ContentItem(
        id="1",
        title="Book (New Series, #1)",
        author="Author",
        content_type=ContentType.BOOK,
        status=ConsumptionStatus.UNREAD,
    )
    assert should_recommend_item(item, {}) is True


def test_should_not_recommend_later_book_unstarted_series():
    """Test that later books in unstarted series are not recommended."""
    item = ContentItem(
        id="1",
        title="Book (New Series, #4)",
        author="Author",
        content_type=ContentType.BOOK,
        status=ConsumptionStatus.UNREAD,
    )
    assert should_recommend_item(item, {}) is False


def test_should_recommend_next_book_started_series():
    """Test recommendation for next book in started series."""
    item = ContentItem(
        id="1",
        title="Book (Series A, #3)",
        author="Author",
        content_type=ContentType.BOOK,
        status=ConsumptionStatus.UNREAD,
    )
    # User has read books 1 and 2
    series_tracking = {"Series A": {1, 2}}
    assert should_recommend_item(item, series_tracking) is True


def test_should_not_recommend_skipped_book_started_series():
    """Test that skipping ahead in a series is not recommended."""
    item = ContentItem(
        id="1",
        title="Book (Series A, #5)",
        author="Author",
        content_type=ContentType.BOOK,
        status=ConsumptionStatus.UNREAD,
    )
    # User has read books 1 and 2, but not 3 or 4
    series_tracking = {"Series A": {1, 2}}
    assert should_recommend_item(item, series_tracking) is False


def test_should_recommend_book_zero_prequel():
    """Test recommendation when user has read book #0 (prequel)."""
    item = ContentItem(
        id="1",
        title="Book (Series A, #1)",
        author="Author",
        content_type=ContentType.BOOK,
        status=ConsumptionStatus.UNREAD,
    )
    # User has read book #0 (prequel)
    series_tracking = {"Series A": {0}}
    assert should_recommend_item(item, series_tracking) is True


def test_should_recommend_item_video_game_series():
    """Test series filtering works for video games."""
    # User has completed Mass Effect 1 and 2
    series_tracking = {"Mass Effect": {1, 2}}

    # Mass Effect 3 should be recommended (next in series)
    item_me3 = ContentItem(
        id="me3",
        title="Mass Effect 3 (Mass Effect, #3)",
        content_type=ContentType.VIDEO_GAME,
        status=ConsumptionStatus.UNREAD,
    )
    assert should_recommend_item(item_me3, series_tracking) is True

    # Mass Effect 1 should NOT be recommended (already completed)
    item_me1 = ContentItem(
        id="me1",
        title="Mass Effect 1 (Mass Effect, #1)",
        content_type=ContentType.VIDEO_GAME,
        status=ConsumptionStatus.UNREAD,
    )
    assert should_recommend_item(item_me1, series_tracking) is False


def test_should_not_recommend_item_if_previous_exists_unconsumed():
    """Test that items are not recommended if previous items exist unconsumed."""
    # User has NOT completed Mass Effect 1
    series_tracking = {"Mass Effect": set()}

    # Mass Effect 1 exists in unconsumed data
    unconsumed_items = [
        ContentItem(
            id="me1",
            title="Mass Effect 1 (Mass Effect, #1)",
            content_type=ContentType.VIDEO_GAME,
            status=ConsumptionStatus.UNREAD,
        ),
    ]

    # Mass Effect 3 should NOT be recommended (ME1 exists but not completed)
    item_me3 = ContentItem(
        id="me3",
        title="Mass Effect 3 (Mass Effect, #3)",
        content_type=ContentType.VIDEO_GAME,
        status=ConsumptionStatus.UNREAD,
    )
    assert should_recommend_item(item_me3, series_tracking, unconsumed_items) is False

    # Mass Effect 1 SHOULD be recommended (it's the first item)
    item_me1 = ContentItem(
        id="me1",
        title="Mass Effect 1 (Mass Effect, #1)",
        content_type=ContentType.VIDEO_GAME,
        status=ConsumptionStatus.UNREAD,
    )
    assert should_recommend_item(item_me1, series_tracking, unconsumed_items) is True


def test_should_recommend_item_if_previous_completed():
    """Test that items are recommended if previous items are completed."""
    # User has completed Mass Effect 1 and 2
    series_tracking = {"Mass Effect": {1, 2}}

    # Mass Effect 3 should be recommended (previous items completed)
    item_me3 = ContentItem(
        id="me3",
        title="Mass Effect 3 (Mass Effect, #3)",
        content_type=ContentType.VIDEO_GAME,
        status=ConsumptionStatus.UNREAD,
    )
    assert should_recommend_item(item_me3, series_tracking) is True


def test_should_recommend_item_if_previous_not_in_data():
    """Test that items are recommended if previous items don't exist in data."""
    # User has NOT started the series
    series_tracking = {"Mass Effect": set()}

    # Mass Effect 1 and 2 are NOT in unconsumed data (don't exist)
    unconsumed_items = [
        ContentItem(
            id="me3",
            title="Mass Effect 3 (Mass Effect, #3)",
            content_type=ContentType.VIDEO_GAME,
            status=ConsumptionStatus.UNREAD,
        ),
    ]

    # Mass Effect 3 CAN be recommended (previous items don't exist in data)
    item_me3 = ContentItem(
        id="me3",
        title="Mass Effect 3 (Mass Effect, #3)",
        content_type=ContentType.VIDEO_GAME,
        status=ConsumptionStatus.UNREAD,
    )
    assert should_recommend_item(item_me3, series_tracking, unconsumed_items) is True


def test_should_recommend_item_mixed_completion():
    """Test series filtering with mixed completion status."""
    # User has completed Mass Effect 1, but not 2
    series_tracking = {"Mass Effect": {1}}

    unconsumed_items = [
        ContentItem(
            id="me2",
            title="Mass Effect 2 (Mass Effect, #2)",
            content_type=ContentType.VIDEO_GAME,
            status=ConsumptionStatus.UNREAD,
        ),
        ContentItem(
            id="me3",
            title="Mass Effect 3 (Mass Effect, #3)",
            content_type=ContentType.VIDEO_GAME,
            status=ConsumptionStatus.UNREAD,
        ),
    ]

    # Mass Effect 2 should be recommended (next item after 1)
    item_me2 = ContentItem(
        id="me2",
        title="Mass Effect 2 (Mass Effect, #2)",
        content_type=ContentType.VIDEO_GAME,
        status=ConsumptionStatus.UNREAD,
    )
    assert should_recommend_item(item_me2, series_tracking, unconsumed_items) is True

    # Mass Effect 3 should NOT be recommended (ME2 exists but not completed)
    item_me3 = ContentItem(
        id="me3",
        title="Mass Effect 3 (Mass Effect, #3)",
        content_type=ContentType.VIDEO_GAME,
        status=ConsumptionStatus.UNREAD,
    )
    assert should_recommend_item(item_me3, series_tracking, unconsumed_items) is False


def test_should_recommend_item_tv_show_series():
    """Test series filtering works for TV shows."""
    # User has watched Season 1 of The Expanse
    series_tracking = {"The Expanse": {1}}

    # Season 2 should be recommended (next in series)
    item_s2 = ContentItem(
        id="s2",
        title="The Expanse (The Expanse, Season 2)",
        content_type=ContentType.TV_SHOW,
        status=ConsumptionStatus.UNREAD,
    )
    assert should_recommend_item(item_s2, series_tracking) is True

    # Season 3 should NOT be recommended (Season 2 not watched)
    item_s3 = ContentItem(
        id="s3",
        title="The Expanse (The Expanse, Season 3)",
        content_type=ContentType.TV_SHOW,
        status=ConsumptionStatus.UNREAD,
    )
    assert should_recommend_item(item_s3, series_tracking) is False

    # Season 1 should NOT be recommended (already watched)
    item_s1 = ContentItem(
        id="s1",
        title="The Expanse (The Expanse, Season 1)",
        content_type=ContentType.TV_SHOW,
        status=ConsumptionStatus.UNREAD,
    )
    assert should_recommend_item(item_s1, series_tracking) is False


def test_should_recommend_item_movie_series():
    """Test series filtering works for movies."""
    # User has watched Part 1 of Lord of the Rings
    series_tracking = {"Lord of the Rings": {1}}

    # Part 2 should be recommended (next in series)
    item_part2 = ContentItem(
        id="part2",
        title="The Two Towers (Lord of the Rings, Part 2)",
        content_type=ContentType.MOVIE,
        status=ConsumptionStatus.UNREAD,
    )
    assert should_recommend_item(item_part2, series_tracking) is True

    # Part 3 should NOT be recommended (Part 2 not watched)
    item_part3 = ContentItem(
        id="part3",
        title="Return of the King (Lord of the Rings, Part 3)",
        content_type=ContentType.MOVIE,
        status=ConsumptionStatus.UNREAD,
    )
    assert should_recommend_item(item_part3, series_tracking) is False

    # Test with metadata - Episode 4 (A New Hope) already watched
    series_tracking_sw = {"Star Wars": {4}}
    # Episode 5 should NOT be recommended — gap-finding sees Episode 1 as first gap
    item_ep5 = ContentItem(
        id="ep5",
        title="Empire Strikes Back",
        content_type=ContentType.MOVIE,
        status=ConsumptionStatus.UNREAD,
        metadata={"series_name": "Star Wars", "episode": 5},
    )
    assert should_recommend_item(item_ep5, series_tracking_sw) is False

    # Episode 1 SHOULD be recommended (first gap when only Episode 4 consumed)
    item_ep1 = ContentItem(
        id="ep1",
        title="The Phantom Menace",
        content_type=ContentType.MOVIE,
        status=ConsumptionStatus.UNREAD,
        metadata={"series_name": "Star Wars", "episode": 1},
    )
    assert should_recommend_item(item_ep1, series_tracking_sw) is True


class TestShouldRecommendNonSequentialSeasons:
    """Tests for gap-finding logic with non-sequential season watching."""

    def test_non_sequential_seasons_5_6_recommends_season_1(self) -> None:
        """User watched seasons 5 and 6 only -> recommend season 1 (first gap)."""
        series_tracking = {"The Show": {5, 6}}
        item_s1 = ContentItem(
            id="s1",
            title="The Show (The Show, Season 1)",
            content_type=ContentType.TV_SHOW,
            status=ConsumptionStatus.UNREAD,
        )
        assert should_recommend_item(item_s1, series_tracking) is True

    def test_non_sequential_seasons_5_6_does_not_recommend_season_7(self) -> None:
        """User watched seasons 5 and 6 only -> don't recommend season 7."""
        series_tracking = {"The Show": {5, 6}}
        item_s7 = ContentItem(
            id="s7",
            title="The Show (The Show, Season 7)",
            content_type=ContentType.TV_SHOW,
            status=ConsumptionStatus.UNREAD,
        )
        assert should_recommend_item(item_s7, series_tracking) is False

    def test_gap_at_season_2_recommends_season_2(self) -> None:
        """User watched seasons 1, 5, 6 -> recommend season 2 (first gap)."""
        series_tracking = {"The Show": {1, 5, 6}}
        item_s2 = ContentItem(
            id="s2",
            title="The Show (The Show, Season 2)",
            content_type=ContentType.TV_SHOW,
            status=ConsumptionStatus.UNREAD,
        )
        assert should_recommend_item(item_s2, series_tracking) is True

    def test_sequential_1_2_recommends_3(self) -> None:
        """Sequential {1, 2} -> recommend 3 (backward compatible)."""
        series_tracking = {"The Show": {1, 2}}
        item_s3 = ContentItem(
            id="s3",
            title="The Show (The Show, Season 3)",
            content_type=ContentType.TV_SHOW,
            status=ConsumptionStatus.UNREAD,
        )
        assert should_recommend_item(item_s3, series_tracking) is True

    def test_all_watched_nothing_recommended(self) -> None:
        """User watched all seasons 1-5 -> don't recommend any of 1-5 again."""
        series_tracking = {"The Show": {1, 2, 3, 4, 5}}
        for season_num in range(1, 6):
            item = ContentItem(
                id=f"s{season_num}",
                title=f"The Show (The Show, Season {season_num})",
                content_type=ContentType.TV_SHOW,
                status=ConsumptionStatus.UNREAD,
            )
            assert should_recommend_item(item, series_tracking) is False


class TestExpandTvShowsSkipsWatchedSeasons:
    """Tests for expand_tv_shows_to_seasons skipping watched seasons."""

    def test_skips_watched_seasons(self) -> None:
        """Seasons in seasons_watched metadata are not expanded."""
        show = ContentItem(
            id="show1",
            title="The Show",
            content_type=ContentType.TV_SHOW,
            status=ConsumptionStatus.UNREAD,
            metadata={"total_seasons": 6, "seasons_watched": [1, 2, 5]},
        )
        expanded = expand_tv_shows_to_seasons([show])
        season_titles = [item.title for item in expanded]
        assert "The Show (Season 1)" not in season_titles
        assert "The Show (Season 2)" not in season_titles
        assert "The Show (Season 5)" not in season_titles
        assert "The Show (Season 3)" in season_titles
        assert "The Show (Season 4)" in season_titles
        assert "The Show (Season 6)" in season_titles
        assert len(expanded) == 3

    def test_no_seasons_watched_expands_all(self) -> None:
        """Without seasons_watched, all seasons are expanded."""
        show = ContentItem(
            id="show1",
            title="The Show",
            content_type=ContentType.TV_SHOW,
            status=ConsumptionStatus.UNREAD,
            metadata={"total_seasons": 3},
        )
        expanded = expand_tv_shows_to_seasons([show])
        assert len(expanded) == 3

    def test_all_seasons_watched_expands_none(self) -> None:
        """If all seasons are watched, no expansion items created."""
        show = ContentItem(
            id="show1",
            title="The Show",
            content_type=ContentType.TV_SHOW,
            status=ConsumptionStatus.UNREAD,
            metadata={"total_seasons": 3, "seasons_watched": [1, 2, 3]},
        )
        expanded = expand_tv_shows_to_seasons([show])
        assert len(expanded) == 0


class TestInjectSeasonsWatchedTracking:
    """Tests for inject_seasons_watched_tracking."""

    def test_basic_injection(self) -> None:
        """Seasons from metadata are added to tracking."""
        items = [
            ContentItem(
                id="show1",
                title="The Show",
                content_type=ContentType.TV_SHOW,
                status=ConsumptionStatus.UNREAD,
                metadata={"seasons_watched": [5, 6]},
            ),
        ]
        result = inject_seasons_watched_tracking(items, {})
        assert result["The Show"] == {5, 6}

    def test_merges_with_existing(self) -> None:
        """Seasons merge with existing consumed tracking."""
        items = [
            ContentItem(
                id="show1",
                title="The Show",
                content_type=ContentType.TV_SHOW,
                status=ConsumptionStatus.UNREAD,
                metadata={"seasons_watched": [5, 6]},
            ),
        ]
        existing = {"The Show": {1, 2}}
        result = inject_seasons_watched_tracking(items, existing)
        assert result["The Show"] == {1, 2, 5, 6}

    def test_does_not_mutate_original(self) -> None:
        """Original dict is not mutated."""
        items = [
            ContentItem(
                id="show1",
                title="The Show",
                content_type=ContentType.TV_SHOW,
                status=ConsumptionStatus.UNREAD,
                metadata={"seasons_watched": [5, 6]},
            ),
        ]
        original = {"The Show": {1, 2}}
        result = inject_seasons_watched_tracking(items, original)
        assert original["The Show"] == {1, 2}  # unchanged
        assert result["The Show"] == {1, 2, 5, 6}

    def test_ignores_non_tv_items(self) -> None:
        """Non-TV items are ignored."""
        items = [
            ContentItem(
                id="book1",
                title="The Book",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.UNREAD,
                metadata={"seasons_watched": [1, 2]},
            ),
        ]
        result = inject_seasons_watched_tracking(items, {})
        assert "The Book" not in result

    def test_ignores_empty_seasons_watched(self) -> None:
        """Items without seasons_watched are ignored."""
        items = [
            ContentItem(
                id="show1",
                title="The Show",
                content_type=ContentType.TV_SHOW,
                status=ConsumptionStatus.UNREAD,
                metadata={},
            ),
        ]
        result = inject_seasons_watched_tracking(items, {})
        assert "The Show" not in result


class TestRomanToInt:
    """Tests for Roman numeral to integer conversion."""

    def test_basic_values(self) -> None:
        """Single Roman numeral characters convert correctly."""
        assert _roman_to_int("I") == 1
        assert _roman_to_int("V") == 5
        assert _roman_to_int("X") == 10

    def test_compound_values(self) -> None:
        """Compound Roman numerals (IV, IX, XII, XIV) convert correctly."""
        assert _roman_to_int("IV") == 4
        assert _roman_to_int("IX") == 9
        assert _roman_to_int("XII") == 12
        assert _roman_to_int("XIV") == 14

    def test_larger_values(self) -> None:
        """Larger Roman numerals convert correctly."""
        assert _roman_to_int("XX") == 20
        assert _roman_to_int("XL") == 40
        assert _roman_to_int("L") == 50
        assert _roman_to_int("C") == 100

    def test_invalid_input(self) -> None:
        """Invalid input returns None."""
        assert _roman_to_int("") is None
        assert _roman_to_int("ABC") is None
        assert _roman_to_int("123") is None

    def test_case_insensitive(self) -> None:
        """Conversion is case insensitive."""
        assert _roman_to_int("xii") == 12
        assert _roman_to_int("iv") == 4


class TestTitleEmbeddedSeriesDetection:
    """Regression tests for title-embedded series detection in video games.

    Bug reported: "Dungeon Siege 3" and "Final Fantasy XII" were not detected
    as series entries because game sources don't populate series metadata and
    the titles don't use parenthetical format.

    Root cause: extract_series_info only checked parenthetical patterns like
    "(Series Name, #N)" and metadata fields.  Games with numbers in the title
    itself were missed.

    Fix: Added _extract_series_from_title() for trailing Arabic and Roman
    numerals, gated to ContentType.VIDEO_GAME only.
    """

    def test_arabic_numeral_dungeon_siege_3_regression(self) -> None:
        """'Dungeon Siege 3' should be detected as Dungeon Siege #3."""
        result = extract_series_info(
            "Dungeon Siege 3", content_type=ContentType.VIDEO_GAME
        )
        assert result is not None
        assert result[0] == "Dungeon Siege"
        assert result[1] == 3

    def test_roman_numeral_final_fantasy_xii_regression(self) -> None:
        """'Final Fantasy XII' should be detected as Final Fantasy #12."""
        result = extract_series_info(
            "Final Fantasy XII", content_type=ContentType.VIDEO_GAME
        )
        assert result is not None
        assert result[0] == "Final Fantasy"
        assert result[1] == 12

    def test_arabic_with_subtitle(self) -> None:
        """'Fallout 4: Game of the Year Edition' extracts series correctly."""
        result = extract_series_info(
            "Fallout 4: Game of the Year Edition",
            content_type=ContentType.VIDEO_GAME,
        )
        assert result is not None
        assert result[0] == "Fallout"
        assert result[1] == 4

    def test_single_word_base_fallout_4(self) -> None:
        """'Fallout 4' with a single-word series name works."""
        result = extract_series_info("Fallout 4", content_type=ContentType.VIDEO_GAME)
        assert result is not None
        assert result[0] == "Fallout"
        assert result[1] == 4

    def test_not_applied_to_books(self) -> None:
        """Title-embedded detection is NOT applied to books."""
        # "1984" should not be detected, and "Catch 22" should not be detected
        result = extract_series_info("Catch 22", content_type=ContentType.BOOK)
        assert result is None

    def test_not_applied_to_movies(self) -> None:
        """Title-embedded detection is NOT applied to movies."""
        result = extract_series_info(
            "2001: A Space Odyssey", content_type=ContentType.MOVIE
        )
        assert result is None

    def test_not_applied_without_content_type(self) -> None:
        """Title-embedded detection is NOT applied when content_type is None."""
        result = extract_series_info("Dungeon Siege 3", content_type=None)
        assert result is None

    def test_parenthetical_takes_precedence(self) -> None:
        """Parenthetical format takes precedence over title-embedded."""
        result = extract_series_info(
            "Mass Effect 3 (Mass Effect, #3)",
            content_type=ContentType.VIDEO_GAME,
        )
        assert result is not None
        assert result[0] == "Mass Effect"
        assert result[1] == 3

    def test_metadata_takes_precedence(self) -> None:
        """Metadata series info takes precedence over title-embedded."""
        result = extract_series_info(
            "Final Fantasy XII",
            metadata={"series_name": "Final Fantasy", "part_number": 12},
            content_type=ContentType.VIDEO_GAME,
        )
        assert result is not None
        assert result[0] == "Final Fantasy"
        assert result[1] == 12

    def test_number_only_title_not_matched(self) -> None:
        """Titles starting with a number (e.g. '1942') are not matched."""
        result = _extract_series_from_title("1942")
        assert result is None

    def test_roman_with_subtitle(self) -> None:
        """Roman numeral with subtitle: 'Shin Megami Tensei IV: Apocalypse'."""
        result = extract_series_info(
            "Shin Megami Tensei IV: Apocalypse",
            content_type=ContentType.VIDEO_GAME,
        )
        assert result is not None
        assert result[0] == "Shin Megami Tensei"
        assert result[1] == 4


class TestSeriesPositionMetadataRegression:
    """Regression tests for series_position metadata key.

    Bug reported: TMDB movies store series position as "series_position"
    in extra_metadata, but _extract_from_metadata() didn't check that key.
    Similarly, RAWG franchise extraction will store series_position for
    video games whose titles can't be parsed (e.g., "Dragon Age Inquisition").

    Root cause: The "series_position" key was missing from all three
    content-type branches of _extract_from_metadata().

    Fix: Added "series_position" as the first key in each branch so it
    takes priority over other position keys.
    """

    def test_movie_with_series_position_from_tmdb_regression(self) -> None:
        """Movie with series_position from TMDB collection is detected."""
        metadata = {"series_name": "The Godfather Collection", "series_position": 2}
        result = extract_series_info(
            "The Godfather Part II", metadata, ContentType.MOVIE
        )
        assert result == ("The Godfather Collection", 2)

    def test_game_with_series_position_and_franchise_regression(self) -> None:
        """Game with series_position + franchise from RAWG is detected."""
        metadata = {"franchise": "Dragon Age", "series_position": 3}
        result = extract_series_info(
            "Dragon Age Inquisition", metadata, ContentType.VIDEO_GAME
        )
        assert result == ("Dragon Age", 3)

    def test_series_position_takes_priority_over_other_keys(self) -> None:
        """series_position should take priority over part_number etc."""
        metadata = {
            "series_name": "Mass Effect",
            "series_position": 2,
            "part_number": 99,
        }
        result = extract_series_info("ME2", metadata, ContentType.VIDEO_GAME)
        assert result == ("Mass Effect", 2)

    def test_tv_show_series_position(self) -> None:
        """TV show with series_position is detected correctly."""
        metadata = {"series_name": "The Expanse", "series_position": 3}
        result = extract_series_info("The Expanse", metadata, ContentType.TV_SHOW)
        assert result == ("The Expanse", 3)

    def test_book_series_position(self) -> None:
        """Book with series_position is detected correctly."""
        metadata = {"series": "The Witcher", "series_position": 5}
        result = extract_series_info("Blood of Elves", metadata, ContentType.BOOK)
        assert result == ("The Witcher", 5)


class TestFindEarliestRecommendable:
    """Tests for find_earliest_recommendable series substitution."""

    def test_finds_earliest_item_by_series_number(self) -> None:
        """Returns the earliest unconsumed item that passes series rules."""
        unconsumed = [
            ContentItem(
                id="ff12",
                title="Final Fantasy XII",
                content_type=ContentType.VIDEO_GAME,
                status=ConsumptionStatus.UNREAD,
                metadata={"franchise": "Final Fantasy", "series_position": 12},
            ),
            ContentItem(
                id="ff10",
                title="Final Fantasy X",
                content_type=ContentType.VIDEO_GAME,
                status=ConsumptionStatus.UNREAD,
                metadata={"franchise": "Final Fantasy", "series_position": 10},
            ),
            ContentItem(
                id="ff7",
                title="Final Fantasy VII",
                content_type=ContentType.VIDEO_GAME,
                status=ConsumptionStatus.UNREAD,
                metadata={"franchise": "Final Fantasy", "series_position": 7},
            ),
        ]
        # User hasn't played any FF games
        series_tracking: dict[str, set[int]] = {}

        result = find_earliest_recommendable(
            "Final Fantasy", series_tracking, unconsumed
        )
        assert result is not None
        assert result.id == "ff7"

    def test_returns_next_item_after_consumed(self) -> None:
        """Returns the next item in sequence when previous ones are consumed."""
        unconsumed = [
            ContentItem(
                id="me3",
                title="Mass Effect 3 (Mass Effect, #3)",
                content_type=ContentType.VIDEO_GAME,
                status=ConsumptionStatus.UNREAD,
            ),
            ContentItem(
                id="me2",
                title="Mass Effect 2 (Mass Effect, #2)",
                content_type=ContentType.VIDEO_GAME,
                status=ConsumptionStatus.UNREAD,
            ),
        ]
        # User completed ME1
        series_tracking = {"Mass Effect": {1}}

        result = find_earliest_recommendable("Mass Effect", series_tracking, unconsumed)
        # ME2 should be returned (next after completed ME1)
        assert result is not None
        assert result.id == "me2"

    def test_returns_none_for_unknown_series(self) -> None:
        """Returns None when no unconsumed items belong to the series."""
        unconsumed = [
            ContentItem(
                id="other",
                title="Unrelated Game",
                content_type=ContentType.VIDEO_GAME,
                status=ConsumptionStatus.UNREAD,
            ),
        ]
        result = find_earliest_recommendable("Final Fantasy", {}, unconsumed)
        assert result is None

    def test_returns_first_item_when_series_not_started(self) -> None:
        """Returns item #1 when user hasn't started the series."""
        unconsumed = [
            ContentItem(
                id="da3",
                title="Dragon Age: Inquisition",
                content_type=ContentType.VIDEO_GAME,
                status=ConsumptionStatus.UNREAD,
                metadata={"franchise": "Dragon Age", "series_position": 3},
            ),
            ContentItem(
                id="da1",
                title="Dragon Age: Origins",
                content_type=ContentType.VIDEO_GAME,
                status=ConsumptionStatus.UNREAD,
                metadata={"franchise": "Dragon Age", "series_position": 1},
            ),
        ]
        result = find_earliest_recommendable("Dragon Age", {}, unconsumed)
        assert result is not None
        assert result.id == "da1"


class TestTitleRegexPatternsRegression:
    """Regression tests for title-embedded series detection patterns.

    Bug reported: Several games missing franchise/series data:
    - "FINAL FANTASY XII THE ZODIAC AGE" — no franchise
    - "FINAL FANTASY X/X-2 HD Remaster" — no franchise
    - "KINGDOM HEARTS III + Re Mind (DLC)" — no franchise
    - "LIGHTNING RETURNS: FINAL FANTASY XIII" — no franchise

    Root causes:
    1. Suffix pattern required colon/dash/em-dash after numeral; space-separated
       subtitles like "THE ZODIAC AGE" and plus-separated DLC like "+ Re Mind"
       were not matched.
    2. Series name capture ``[^:—\\-]*?`` stopped at colons/dashes, so
       "LIGHTNING RETURNS: FINAL FANTASY XIII" couldn't reach "XIII".
    3. Roman numeral regex rejected standalone V, X, L, C (required at least
       one I), so "FINAL FANTASY X" and "GRAND THEFT AUTO V" failed.

    Fix: Widened suffix delimiters to ``[\\s:—\\-+/]``, changed series-name
    capture to ``.*?`` (lazy), simplified Roman numeral group to
    ``[IVXLCDM]+`` with downstream validation.
    """

    def test_ff_xii_zodiac_age_regression(self) -> None:
        """'FINAL FANTASY XII THE ZODIAC AGE' -> ('FINAL FANTASY', 12)."""
        result = extract_series_info(
            "FINAL FANTASY XII THE ZODIAC AGE",
            content_type=ContentType.VIDEO_GAME,
        )
        assert result is not None
        assert result[0] == "FINAL FANTASY"
        assert result[1] == 12

    def test_kingdom_hearts_iii_dlc_regression(self) -> None:
        """'KINGDOM HEARTS III + Re Mind (DLC)' -> ('KINGDOM HEARTS', 3)."""
        result = extract_series_info(
            "KINGDOM HEARTS III + Re Mind (DLC)",
            content_type=ContentType.VIDEO_GAME,
        )
        assert result is not None
        assert result[0] == "KINGDOM HEARTS"
        assert result[1] == 3

    def test_ff_x_standalone_roman_numeral_regression(self) -> None:
        """'FINAL FANTASY X' -> ('FINAL FANTASY', 10)."""
        result = extract_series_info(
            "FINAL FANTASY X", content_type=ContentType.VIDEO_GAME
        )
        assert result is not None
        assert result[0] == "FINAL FANTASY"
        assert result[1] == 10

    def test_gta_v_standalone_roman_numeral_regression(self) -> None:
        """'GRAND THEFT AUTO V' -> ('GRAND THEFT AUTO', 5)."""
        result = extract_series_info(
            "GRAND THEFT AUTO V", content_type=ContentType.VIDEO_GAME
        )
        assert result is not None
        assert result[0] == "GRAND THEFT AUTO"
        assert result[1] == 5

    def test_witcher_3_wild_hunt_existing_behavior_preserved(self) -> None:
        """'The Witcher 3: Wild Hunt' -> ('The Witcher', 3) — unchanged."""
        result = extract_series_info(
            "The Witcher 3: Wild Hunt", content_type=ContentType.VIDEO_GAME
        )
        assert result is not None
        assert result[0] == "The Witcher"
        assert result[1] == 3

    def test_lightning_returns_title_fallback_regression(self) -> None:
        """'LIGHTNING RETURNS: FINAL FANTASY XIII' -> series name includes colon.

        Title-level parsing captures the series name as
        'LIGHTNING RETURNS: FINAL FANTASY' with position 13.
        In practice RAWG franchise metadata is preferred for this title.
        """
        result = extract_series_info(
            "LIGHTNING RETURNS: FINAL FANTASY XIII",
            content_type=ContentType.VIDEO_GAME,
        )
        assert result is not None
        assert result[0] == "LIGHTNING RETURNS: FINAL FANTASY"
        assert result[1] == 13

    def test_ff_x_x2_hd_remaster_slash_delimiter(self) -> None:
        """'FINAL FANTASY X/X-2 HD Remaster' -> ('FINAL FANTASY', 10).

        The slash after X acts as a subtitle delimiter.
        """
        result = extract_series_info(
            "FINAL FANTASY X/X-2 HD Remaster",
            content_type=ContentType.VIDEO_GAME,
        )
        assert result is not None
        assert result[0] == "FINAL FANTASY"
        assert result[1] == 10
