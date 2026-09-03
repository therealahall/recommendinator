from datetime import date

import pytest

from src.models.content import (
    ConsumptionStatus,
    ContentItem,
    ContentType,
    ExternalId,
)
from src.utils.series import (
    MAX_SEASONS,
    _extract_series_from_title,
    _roman_to_int,
    build_series_tracking,
    expand_tv_shows_to_seasons,
    extract_series_info,
    find_earliest_recommendable,
    get_series_item_number,
    get_series_name,
    inject_seasons_watched_tracking,
    is_active_series_continuation,
    is_first_item_in_series,
    is_next_after_consumed,
    latest_season_watched_date,
    should_recommend_item,
    split_series_from_title,
)


def test_extract_series_info():
    assert extract_series_info("Book Title (The Witcher, #4)") == ("The Witcher", 4)

    assert extract_series_info("Book (Series #2)") == ("Series", 2)

    assert extract_series_info("Book (Series, Book 3)") == ("Series", 3)

    assert extract_series_info("Show (The Expanse, Season 1)") == ("The Expanse", 1)

    assert extract_series_info("Show (The Expanse, S1)") == ("The Expanse", 1)

    assert extract_series_info("Movie (Lord of the Rings, Part 1)") == (
        "Lord of the Rings",
        1,
    )

    assert extract_series_info("Movie (Star Wars, Episode 4)") == ("Star Wars", 4)

    assert extract_series_info("Standalone Book") is None
    assert extract_series_info("Book (Not a Series)") is None


def test_extract_series_info_from_metadata():
    metadata_tv = {"series": "The Expanse", "season": 2}
    assert extract_series_info("The Expanse", metadata_tv, ContentType.TV_SHOW) == (
        "The Expanse",
        2,
    )

    metadata_game = {"series_title": "Mass Effect", "part_number": 2}
    assert extract_series_info("ME2", metadata_game, ContentType.VIDEO_GAME) == (
        "Mass Effect",
        2,
    )


def test_expand_tv_shows_to_seasons():
    show_with_seasons = ContentItem(
        id="tvdb:280619",
        db_id=42,
        title="The Expanse",
        content_type=ContentType.TV_SHOW,
        status=ConsumptionStatus.UNREAD,
        metadata={"total_seasons": 6, "genres": ["Sci-Fi"]},
    )
    show_without_seasons = ContentItem(
        id="tvdb:999",
        db_id=99,
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
    assert expanded[6].parent_id is None

    # Every season item carries the parent show's db_id so recommendation
    # actions (mark complete / ignore) resolve to the show-level library row.
    for season_item in expanded[:6]:
        assert season_item.db_id == 42
    assert expanded[6].db_id == 99


def test_get_series_name():
    assert get_series_name(title="Book (The Witcher, #4)") == "The Witcher"
    assert get_series_name(title="Standalone Book") is None

    item_with_metadata = ContentItem(
        id="2",
        title="Show",
        content_type=ContentType.TV_SHOW,
        status=ConsumptionStatus.UNREAD,
        metadata={"series": "Breaking Bad", "season": 1},
    )
    assert get_series_name(item=item_with_metadata) == "Breaking Bad"


def test_get_series_item_number():
    assert get_series_item_number(title="Book (The Witcher, #4)") == 4
    assert get_series_item_number(title="Standalone Book") is None
    # Half-numbered novellas parse as floats, not truncated to an int.
    novella_number = get_series_item_number(title="Gods of Risk (The Expanse, #2.5)")
    assert novella_number == 2.5


def test_build_series_tracking():
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
    assert "Standalone Book" not in tracking


def test_build_series_tracking_preserves_decimal_positions():
    items = [
        ContentItem(
            id="1",
            title="Leviathan Wakes (The Expanse, #1)",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
        ),
        ContentItem(
            id="2",
            title="Gods of Risk (The Expanse, #2.5)",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
        ),
    ]
    tracking = build_series_tracking(items)
    assert tracking["The Expanse"] == {1.0, 2.5}


def test_is_first_item_in_series():
    assert is_first_item_in_series(title="Book (Series, #1)") is True
    assert is_first_item_in_series(title="Show (Series, Season 1)") is True
    assert is_first_item_in_series(title="Book (Series, #2)") is False

    item_first = ContentItem(
        id="1",
        title="The Expanse (The Expanse, Season 1)",
        content_type=ContentType.TV_SHOW,
        status=ConsumptionStatus.UNREAD,
    )
    assert is_first_item_in_series(item=item_first) is True

    item_with_metadata = ContentItem(
        id="3",
        title="Movie",
        content_type=ContentType.MOVIE,
        status=ConsumptionStatus.UNREAD,
        metadata={"series": "Star Wars", "episode": 1},
    )
    assert is_first_item_in_series(item=item_with_metadata) is True


class TestIsNextAfterConsumed:
    """``SeriesOrderScorer`` orders entries with it, so the boundaries it draws
    decide which candidate gets the rating-boosted score."""

    def test_next_whole_number_when_nothing_sits_between(self) -> None:
        assert is_next_after_consumed(3.0, {1.0, 2.0}, set()) is True

    def test_fraction_takes_the_slot_ahead_of_the_next_book(self) -> None:
        known = {2.5, 3.0}
        assert is_next_after_consumed(2.5, {1.0, 2.0}, known) is True
        assert is_next_after_consumed(3.0, {1.0, 2.0}, known) is False

    def test_position_at_or_below_the_consumed_run_is_never_next(self) -> None:
        assert is_next_after_consumed(2.0, {1.0, 2.0}, {3.0}) is False
        assert is_next_after_consumed(1.0, {1.0, 2.0}, {3.0}) is False
        assert is_next_after_consumed(2.5, {1.0, 2.0, 2.5}, {3.0}) is False
        # A gap the reader gets to only by going backwards stays behind them.
        assert is_next_after_consumed(2.0, {1.0, 3.0}, {2.0}) is False

    def test_prequel_unlocks_book_one(self) -> None:
        assert is_next_after_consumed(1.0, {0.0}, {1.0, 2.0}) is True
        assert is_next_after_consumed(2.0, {0.0}, {1.0, 2.0}) is False


def test_should_recommend_book_not_in_series():
    item = ContentItem(
        id="1",
        title="Standalone Book",
        author="Author",
        content_type=ContentType.BOOK,
        status=ConsumptionStatus.UNREAD,
    )
    assert should_recommend_item(item, {}) is True


def test_should_recommend_first_book_unstarted_series():
    item = ContentItem(
        id="1",
        title="Book (New Series, #1)",
        author="Author",
        content_type=ContentType.BOOK,
        status=ConsumptionStatus.UNREAD,
    )
    assert should_recommend_item(item, {}) is True


def test_should_not_recommend_later_book_unstarted_series():
    item = ContentItem(
        id="1",
        title="Book (New Series, #4)",
        author="Author",
        content_type=ContentType.BOOK,
        status=ConsumptionStatus.UNREAD,
    )
    assert should_recommend_item(item, {}) is False


def test_should_recommend_next_book_started_series():
    item = ContentItem(
        id="1",
        title="Book (Series A, #3)",
        author="Author",
        content_type=ContentType.BOOK,
        status=ConsumptionStatus.UNREAD,
    )
    series_tracking = {"Series A": {1, 2}}
    assert should_recommend_item(item, series_tracking) is True


def test_should_not_recommend_skipped_book_started_series():
    item = ContentItem(
        id="1",
        title="Book (Series A, #5)",
        author="Author",
        content_type=ContentType.BOOK,
        status=ConsumptionStatus.UNREAD,
    )
    series_tracking = {"Series A": {1, 2}}
    assert should_recommend_item(item, series_tracking) is False


def test_should_recommend_book_zero_prequel():
    item = ContentItem(
        id="1",
        title="Book (Series A, #1)",
        author="Author",
        content_type=ContentType.BOOK,
        status=ConsumptionStatus.UNREAD,
    )
    series_tracking = {"Series A": {0}}
    assert should_recommend_item(item, series_tracking) is True


def test_should_not_recommend_item_if_previous_exists_unconsumed():
    series_tracking = {"Mass Effect": set()}

    unconsumed_items = [
        ContentItem(
            id="me1",
            title="Mass Effect 1 (Mass Effect, #1)",
            content_type=ContentType.VIDEO_GAME,
            status=ConsumptionStatus.UNREAD,
        ),
    ]

    item_me3 = ContentItem(
        id="me3",
        title="Mass Effect 3 (Mass Effect, #3)",
        content_type=ContentType.VIDEO_GAME,
        status=ConsumptionStatus.UNREAD,
    )
    assert should_recommend_item(item_me3, series_tracking, unconsumed_items) is False

    item_me1 = ContentItem(
        id="me1",
        title="Mass Effect 1 (Mass Effect, #1)",
        content_type=ContentType.VIDEO_GAME,
        status=ConsumptionStatus.UNREAD,
    )
    assert should_recommend_item(item_me1, series_tracking, unconsumed_items) is True


def test_should_recommend_item_if_previous_not_in_data():
    series_tracking = {"Mass Effect": set()}

    unconsumed_items = [
        ContentItem(
            id="me3",
            title="Mass Effect 3 (Mass Effect, #3)",
            content_type=ContentType.VIDEO_GAME,
            status=ConsumptionStatus.UNREAD,
        ),
    ]

    item_me3 = ContentItem(
        id="me3",
        title="Mass Effect 3 (Mass Effect, #3)",
        content_type=ContentType.VIDEO_GAME,
        status=ConsumptionStatus.UNREAD,
    )
    assert should_recommend_item(item_me3, series_tracking, unconsumed_items) is True


def test_is_active_series_continuation():
    """Drives the softened variety penalty: the next entry in a series the user is
    mid-way through should not be demoted as if its genre were finished."""
    started_tracking = {"The Expanse": {1.0}}
    unconsumed_items = [
        ContentItem(
            id="exp2",
            title="Caliban's War (The Expanse, #2)",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
        ),
        ContentItem(
            id="exp25",
            title="Gods of Risk (The Expanse, #2.5)",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
        ),
    ]
    book_two = unconsumed_items[0]
    novella_25 = unconsumed_items[1]

    assert (
        is_active_series_continuation(book_two, started_tracking, unconsumed_items)
        is True
    )
    assert (
        is_active_series_continuation(novella_25, started_tracking, unconsumed_items)
        is False
    )

    first_book = ContentItem(
        id="new1",
        title="The Way of Kings (Stormlight, #1)",
        content_type=ContentType.BOOK,
        status=ConsumptionStatus.UNREAD,
    )
    assert is_active_series_continuation(first_book, {}, [first_book]) is False

    standalone = ContentItem(
        id="solo",
        title="A Standalone Novel",
        content_type=ContentType.BOOK,
        status=ConsumptionStatus.UNREAD,
    )
    assert is_active_series_continuation(standalone, started_tracking, []) is False


class TestShouldRecommendNonSequentialSeasons:
    def test_non_sequential_seasons_5_6_recommends_season_1(self) -> None:
        series_tracking = {"The Show": {5, 6}}
        item_s1 = ContentItem(
            id="s1",
            title="The Show (The Show, Season 1)",
            content_type=ContentType.TV_SHOW,
            status=ConsumptionStatus.UNREAD,
        )
        assert should_recommend_item(item_s1, series_tracking) is True

    def test_non_sequential_seasons_5_6_does_not_recommend_season_7(self) -> None:
        series_tracking = {"The Show": {5, 6}}
        item_s7 = ContentItem(
            id="s7",
            title="The Show (The Show, Season 7)",
            content_type=ContentType.TV_SHOW,
            status=ConsumptionStatus.UNREAD,
        )
        assert should_recommend_item(item_s7, series_tracking) is False


class TestExpandTvShowsSkipsWatchedSeasons:
    def test_skips_watched_seasons(self) -> None:
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


class TestSeasonItemsCarryTheShow:
    def _show(self) -> ContentItem:
        return ContentItem(
            user_id=7,
            id="show1",
            db_id=42,
            title="The Show",
            content_type=ContentType.TV_SHOW,
            status=ConsumptionStatus.UNREAD,
            author="A Network",
            rating=4,
            review="Worth it",
            date_completed=date(2026, 1, 2),
            cover_url="https://example.test/show.jpg",
            source="trakt",
            external_ids=[ExternalId(source="trakt", external_id="show1")],
            enriched=True,
            manually_enriched=True,
            ignored=False,
            metadata={"total_seasons": 2},
        )

    def test_season_item_keeps_the_shows_cover_art(self) -> None:
        seasons = expand_tv_shows_to_seasons([self._show()])
        assert [item.cover_url for item in seasons] == [
            "https://example.test/show.jpg"
        ] * 2

    def test_season_item_differs_from_the_show_only_in_season_identity(self) -> None:
        show = self._show()
        per_season = {"id", "title", "parent_id", "metadata"}

        season = expand_tv_shows_to_seasons([show])[0]

        assert season.model_dump(exclude=per_season) == show.model_dump(
            exclude=per_season
        )
        assert (season.id, season.parent_id, season.title) == (
            "show1:s1",
            "show1",
            "The Show (Season 1)",
        )
        assert season.metadata["season"] == 1


class TestInjectSeasonsWatchedTracking:
    def test_does_not_mutate_original(self) -> None:
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
        assert original["The Show"] == {1, 2}
        assert result["The Show"] == {1, 2, 5, 6}

    def test_ignores_non_tv_items(self) -> None:
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


class TestRomanToInt:
    def test_compound_values(self) -> None:
        assert _roman_to_int("IV") == 4
        assert _roman_to_int("IX") == 9
        assert _roman_to_int("XII") == 12
        assert _roman_to_int("XIV") == 14

    def test_invalid_input(self) -> None:
        assert _roman_to_int("") is None
        assert _roman_to_int("ABC") is None
        assert _roman_to_int("123") is None


class TestTitleEmbeddedSeriesDetection:
    """Bug reported: "Dungeon Siege 3" and "Final Fantasy XII" were not detected as
    series entries because game sources don't populate series metadata and the
    titles don't use parenthetical format."""

    def test_arabic_numeral_dungeon_siege_3_regression(self) -> None:
        result = extract_series_info(
            "Dungeon Siege 3", content_type=ContentType.VIDEO_GAME
        )
        assert result is not None
        assert result[0] == "Dungeon Siege"
        assert result[1] == 3

    def test_roman_numeral_final_fantasy_xii_regression(self) -> None:
        result = extract_series_info(
            "Final Fantasy XII", content_type=ContentType.VIDEO_GAME
        )
        assert result is not None
        assert result[0] == "Final Fantasy"
        assert result[1] == 12

    def test_not_applied_to_books(self) -> None:
        result = extract_series_info("Catch 22", content_type=ContentType.BOOK)
        assert result is None

    def test_parenthetical_takes_precedence(self) -> None:
        result = extract_series_info(
            "Mass Effect 3 (Mass Effect, #3)",
            content_type=ContentType.VIDEO_GAME,
        )
        assert result is not None
        assert result[0] == "Mass Effect"
        assert result[1] == 3

    def test_number_only_title_not_matched(self) -> None:
        result = _extract_series_from_title("1942")
        assert result is None


class TestSeriesPositionMetadataRegression:
    """Bug reported: TMDB movies store series position as "series_position" in
    extra_metadata, but _extract_from_metadata() didn't check that key."""

    def test_movie_with_series_position_from_tmdb_regression(self) -> None:
        metadata = {"series_name": "The Godfather Collection", "series_position": 2}
        result = extract_series_info(
            "The Godfather Part II", metadata, ContentType.MOVIE
        )
        assert result == ("The Godfather Collection", 2)

    def test_game_with_series_position_and_franchise_regression(self) -> None:
        metadata = {"franchise": "Dragon Age", "series_position": 3}
        result = extract_series_info(
            "Dragon Age Inquisition", metadata, ContentType.VIDEO_GAME
        )
        assert result == ("Dragon Age", 3)

    def test_series_position_takes_priority_over_other_keys(self) -> None:
        metadata = {
            "series_name": "Mass Effect",
            "series_position": 2,
            "part_number": 99,
        }
        result = extract_series_info("ME2", metadata, ContentType.VIDEO_GAME)
        assert result == ("Mass Effect", 2)


class TestFindEarliestRecommendable:
    def test_finds_earliest_item_by_series_number(self) -> None:
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
        series_tracking: dict[str, set[float]] = {}

        result = find_earliest_recommendable(
            "Final Fantasy", series_tracking, unconsumed
        )
        assert result is not None
        assert result.id == "ff7"

    def test_returns_none_for_unknown_series(self) -> None:
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


class TestTitleRegexPatternsRegression:
    def test_ff_xii_zodiac_age_regression(self) -> None:
        result = extract_series_info(
            "FINAL FANTASY XII THE ZODIAC AGE",
            content_type=ContentType.VIDEO_GAME,
        )
        assert result is not None
        assert result[0] == "FINAL FANTASY"
        assert result[1] == 12

    def test_kingdom_hearts_iii_dlc_regression(self) -> None:
        result = extract_series_info(
            "KINGDOM HEARTS III + Re Mind (DLC)",
            content_type=ContentType.VIDEO_GAME,
        )
        assert result is not None
        assert result[0] == "KINGDOM HEARTS"
        assert result[1] == 3

    def test_ff_x_standalone_roman_numeral_regression(self) -> None:
        result = extract_series_info(
            "FINAL FANTASY X", content_type=ContentType.VIDEO_GAME
        )
        assert result is not None
        assert result[0] == "FINAL FANTASY"
        assert result[1] == 10

    def test_lightning_returns_title_fallback_regression(self) -> None:
        """In practice RAWG franchise metadata is preferred for this title."""
        result = extract_series_info(
            "LIGHTNING RETURNS: FINAL FANTASY XIII",
            content_type=ContentType.VIDEO_GAME,
        )
        assert result is not None
        assert result[0] == "LIGHTNING RETURNS: FINAL FANTASY"
        assert result[1] == 13


class TestSplitSeriesFromTitle:
    @pytest.mark.parametrize(
        ("marker", "index"),
        [
            ("(Murderbot, #1)", 1.0),
            ("(Murderbot, Book 1)", 1.0),
            ("(Murderbot #1)", 1.0),
            ("(Murderbot, #1.5)", 1.5),
            ("(Murderbot, #1-3)", 1.0),
        ],
    )
    def test_a_series_marker_leaves_the_title_and_states_itself(
        self, marker: str, index: float
    ) -> None:
        assert split_series_from_title(f"All Systems Red {marker}") == (
            "All Systems Red",
            {"series": "Murderbot", "series_index": index},
        )

    def test_a_marker_ahead_of_the_titles_own_parenthetical_still_leaves_it(
        self,
    ) -> None:
        assert split_series_from_title("Dune (Dune, #1) (1965)") == (
            "Dune (1965)",
            {"series": "Dune", "series_index": 1.0},
        )

    @pytest.mark.parametrize(
        "title",
        ["Deadhouse Gates (Malazan Book 2)", "Portal 2 (Game)", "(Murderbot, #1)"],
    )
    def test_a_parenthetical_naming_the_work_is_left_on_the_title(
        self, title: str
    ) -> None:
        assert split_series_from_title(title) == (title, {})


class TestDecimalSeriesOrderingRegression:
    def _expanse(self, item_id: str, title: str) -> ContentItem:
        return ContentItem(
            id=item_id,
            title=title,
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
        )

    def test_decimal_position_parses_to_float_regression(self) -> None:
        """Bug reported: a 200-book run recommended Expanse novellas
        "Drive (The Expanse, #2.7)", "Gods of Risk (The Expanse, #2.5)", and
        "The Vital Abyss (The Expanse, #5.5)" out of series order."""
        assert extract_series_info("Drive (The Expanse, #2.7)") == ("The Expanse", 2.7)
        # Whole-number positions still parse, now as floats (the point of the
        # migration) — assert the type explicitly, not just equality.
        whole = extract_series_info("Caliban's War (The Expanse, #2)")
        assert whole == ("The Expanse", 2)
        assert whole is not None and isinstance(whole[1], float)
        # Decimal position carried in metadata as a string.
        metadata_str = {"series_name": "The Expanse", "series_position": "2.7"}
        assert extract_series_info("Drive", metadata_str, ContentType.BOOK) == (
            "The Expanse",
            2.7,
        )

    def test_non_finite_metadata_position_rejected_regression(self) -> None:
        """Unlike ``int()``, ``float()`` accepts "inf"/"nan", which would
        enter series tracking and corrupt ordering comparisons (``nan`` compares
        False against everything; ``inf`` breaks the virtual slot ``range()``)."""
        for bad_value in ("inf", "-inf", "nan", float("inf"), float("nan")):
            metadata = {"series_name": "The Expanse", "series_position": bad_value}
            assert extract_series_info("Drive", metadata, ContentType.BOOK) is None

    def test_novella_blocked_before_next_book_regression(self) -> None:
        """Bug reported: with only Expanse book #1 read, novellas #2.5 and #2.7 were
        recommended ahead of the actual next book #2 (Caliban's War)."""
        series_tracking = {"The Expanse": {1.0}}
        book_two = self._expanse("exp2", "Caliban's War (The Expanse, #2)")
        novella_25 = self._expanse("exp25", "Gods of Risk (The Expanse, #2.5)")
        novella_27 = self._expanse("exp27", "Drive (The Expanse, #2.7)")
        unconsumed = [book_two, novella_25, novella_27]

        # The legit next book is recommendable; the novellas must wait for it.
        assert should_recommend_item(book_two, series_tracking, unconsumed) is True
        assert should_recommend_item(novella_25, series_tracking, unconsumed) is False
        assert should_recommend_item(novella_27, series_tracking, unconsumed) is False

        # Substitution offers Caliban's War in place of an out-of-order novella.
        substitute = find_earliest_recommendable(
            "The Expanse", series_tracking, unconsumed
        )
        assert substitute is not None
        assert substitute.id == "exp2"

    def test_novella_blocked_in_unstarted_series_regression(self) -> None:
        """Bug reported: novellas surfaced out of order even for series the user had
        never started."""
        series_tracking: dict[str, set[float]] = {}
        book_two = self._expanse("exp2", "Caliban's War (The Expanse, #2)")
        novella_25 = self._expanse("exp25", "Gods of Risk (The Expanse, #2.5)")
        unconsumed = [book_two, novella_25]

        assert should_recommend_item(novella_25, series_tracking, unconsumed) is False

    def test_novella_eligible_after_book_read_regression(self) -> None:
        """Bug reported: the fix must not over-correct and permanently bury novellas
        — once eligible, a #2.5 novella should be recommended in order."""
        series_tracking = {"The Expanse": {1.0, 2.0}}
        novella_25 = self._expanse("exp25", "Gods of Risk (The Expanse, #2.5)")
        book_three = self._expanse("exp3", "Abaddon's Gate (The Expanse, #3)")
        unconsumed = [novella_25, book_three]

        assert should_recommend_item(novella_25, series_tracking, unconsumed) is True
        # Book 3 waits behind the novella that precedes it.
        assert should_recommend_item(book_three, series_tracking, unconsumed) is False


class TestSeasonBoundsRegression:
    """Season counts and numbers arrive from imports and the web edit endpoint and
    feed ``range()`` calls, so a malformed value must not allocate an unbounded
    amount of work."""

    def test_expand_caps_total_seasons_regression(self) -> None:
        """Bug reported: ``total_seasons`` from metadata fed
        ``range(1, total_seasons + 1)`` in ``expand_tv_shows_to_seasons`` with no
        upper bound, so a value like 2_000_000_000 would allocate billions of season
        items (local CPU/memory DoS)."""
        show = ContentItem(
            id="show1",
            title="Endless Show",
            content_type=ContentType.TV_SHOW,
            status=ConsumptionStatus.UNREAD,
            metadata={"total_seasons": 2_000_000_000},
        )
        expanded = expand_tv_shows_to_seasons([show])
        assert len(expanded) == MAX_SEASONS
        # The cap produces seasons 1..MAX_SEASONS, not some other run of 200.
        assert expanded[0].metadata["season"] == 1
        assert expanded[-1].metadata["season"] == MAX_SEASONS
        assert expanded[-1].title == f"Endless Show (Season {MAX_SEASONS})"

    def test_inject_drops_out_of_range_seasons_regression(self) -> None:
        """Bug reported: ``inject_seasons_watched_tracking`` added raw season ints
        into series tracking with no bound, so a value like 2_000_000 became
        ``max_consumed`` and exploded the gap-ladder ``range()`` in
        ``should_recommend_item``."""
        show = ContentItem(
            id="show1",
            title="The Show",
            content_type=ContentType.TV_SHOW,
            status=ConsumptionStatus.UNREAD,
            metadata={
                "seasons_watched": [1, 5, MAX_SEASONS, MAX_SEASONS + 1, 2_000_000]
            },
        )
        tracking = inject_seasons_watched_tracking([show], {})
        # In-range seasons (including the cap boundary) kept; out-of-range dropped.
        assert tracking["The Show"] == {1, 5, MAX_SEASONS}


def _show(dates: dict[str, str]) -> ContentItem:
    return ContentItem(
        id="x",
        title="Show",
        content_type=ContentType.TV_SHOW,
        status=ConsumptionStatus.CURRENTLY_CONSUMING,
        metadata={"seasons_watched": [1, 2], "seasons_watched_dates": dates},
    )


def test_latest_season_watched_date_returns_max():
    item = _show({"1": "2026-01-05T00:00:00+00:00", "2": "2026-03-10T00:00:00+00:00"})
    assert latest_season_watched_date(item) == date(2026, 3, 10)


def test_latest_season_watched_date_none_when_absent():
    item = ContentItem(
        id="x",
        title="Show",
        content_type=ContentType.TV_SHOW,
        status=ConsumptionStatus.CURRENTLY_CONSUMING,
        metadata={"seasons_watched": [1]},
    )
    assert latest_season_watched_date(item) is None


class TestSeasonWatchedDateTimezone:
    def test_season_date_uses_local_calendar_day_regression(self, host_timezone):
        """``latest_season_watched_date`` called ``.date()`` straight on
        the parsed instant, which yields the UTC calendar day."""
        host_timezone("America/Los_Angeles")
        item = _show({"1": "2026-03-15T04:00:00+00:00"})
        assert latest_season_watched_date(item) == date(2026, 3, 14)

    def test_latest_season_is_chosen_by_local_day(self, host_timezone):
        """Both instants fall on 2026-03-14 in UTC, so before the fix the two seasons
        tied."""
        host_timezone("Asia/Tokyo")
        item = _show(
            {
                "1": "2026-03-14T15:30:00+00:00",
                "2": "2026-03-14T14:00:00+00:00",
            }
        )
        assert latest_season_watched_date(item) == date(2026, 3, 15)
