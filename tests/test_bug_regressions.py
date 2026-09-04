import sqlite3
from pathlib import Path

from src.models.content import ConsumptionStatus, ContentItem, ContentType
from src.recommendations.preference_interpreter import PatternBasedInterpreter
from src.storage.schema import (
    create_schema,
    mark_enrichment_complete,
    mark_item_needs_enrichment,
)
from src.storage.sqlite_db import SQLiteDB
from src.utils.sorting import titles_similar


class TestArticleStrippingRegression:
    def test_article_stripped_from_middle_of_title_regression(self) -> None:
        """Bug reported: .replace("the ", "") removes "the" from anywhere in a title,
        e.g. "Into the Wild" becomes "Into Wild", "Cathedral" becomes "Cdral",
        "A Gathering Storm" mishandles "a" mid-word."""

        assert titles_similar("Into the Wild", "Into the Wild") is True
        assert titles_similar("Cathedral", "Cathedral") is True

    def test_title_with_leading_article_still_matches_regression(self) -> None:
        assert titles_similar("The Matrix", "Matrix") is True
        assert titles_similar("A Beautiful Mind", "Beautiful Mind") is True
        assert titles_similar("An Inspector Calls", "Inspector Calls") is True


class TestPreferenceInterpreterRegression:
    def test_into_genre_parsed_as_prefer_not_avoid_regression(self) -> None:
        """Bug reported: "into sci-fi" was matched by the AVOID_PATTERNS regex
        r"(?:not?\\s+)?(?:into|interested in)\\s+(.+)" because "not?" makes the "not"
        optional, causing "into sci-fi" to match as an avoid pattern."""
        interpreter = PatternBasedInterpreter()

        result = interpreter.interpret("into sci-fi")
        assert result.genre_boosts, "Expected genre boosts for 'into sci-fi'"
        assert (
            not result.genre_penalties
        ), "'into sci-fi' should NOT produce genre penalties"

    def test_not_into_genre_still_avoids_regression(self) -> None:
        interpreter = PatternBasedInterpreter()

        result = interpreter.interpret("not into horror")
        assert result.genre_penalties, "Expected genre penalties for 'not into horror'"
        assert (
            not result.genre_boosts
        ), "'not into horror' should NOT produce genre boosts"


class TestMinRatingZeroRegression:
    def test_min_rating_zero_not_skipped_regression(self, tmp_path: Path) -> None:
        """Bug reported: Using `if min_rating:` treats 0 as falsy, so min_rating=0 is
        effectively ignored (no filter applied)."""
        database = SQLiteDB(tmp_path / "test.db")

        database.save_content_item(
            ContentItem(
                id="rated",
                title="Rated Book",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.COMPLETED,
                rating=3,
            )
        )
        database.save_content_item(
            ContentItem(
                id="unrated",
                title="Unrated Book",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.COMPLETED,
                rating=None,
            )
        )

        items = database.get_content_items(min_rating=0)
        titles = [item.title for item in items]
        assert "Rated Book" in titles
        assert "Unrated Book" not in titles

    def test_min_rating_none_returns_all_regression(self, tmp_path: Path) -> None:
        database = SQLiteDB(tmp_path / "test.db")

        database.save_content_item(
            ContentItem(
                id="rated",
                title="Rated Book",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.COMPLETED,
                rating=3,
            )
        )
        database.save_content_item(
            ContentItem(
                id="unrated",
                title="Unrated Book",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.COMPLETED,
                rating=None,
            )
        )

        items = database.get_content_items(min_rating=None)
        assert len(items) == 2


class TestEnrichmentStatsRegression:
    def test_mark_enrichment_preserves_existing_status_regression(self) -> None:
        """Bug reported: Importing finished TV shows via JSON re-enriched everything,
        even items already enriched in a previous sync."""
        conn = sqlite3.connect(":memory:")
        create_schema(conn)

        conn.execute(
            "INSERT INTO content_items (id, title, content_type, status, source, user_id)"
            " VALUES (1, 'Test Show', 'tv_show', 'completed', 'test', 1)"
        )
        conn.commit()

        mark_item_needs_enrichment(conn, 1)
        mark_enrichment_complete(
            conn,
            content_item_id=1,
            provider="tmdb",
            quality="high",
        )

        cursor = conn.execute(
            "SELECT needs_enrichment, enrichment_provider FROM enrichment_status"
            " WHERE content_item_id = 1"
        )
        row = cursor.fetchone()
        assert row[0] == 0
        assert row[1] == "tmdb"

        mark_item_needs_enrichment(conn, 1)

        cursor = conn.execute(
            "SELECT needs_enrichment, enrichment_provider FROM enrichment_status"
            " WHERE content_item_id = 1"
        )
        row = cursor.fetchone()
        assert row[0] == 0
        assert row[1] == "tmdb"

        conn.close()


class TestIgnoredFieldRegression:
    def test_upsert_preserves_ignored_field_regression(self, tmp_path: Path) -> None:
        """Bug reported: Importing Bridgerton with ignored: true did not set ignored
        in the database because the UPDATE branch of save_content_item did not
        include the ignored column."""
        database = SQLiteDB(tmp_path / "test.db")

        item = ContentItem(
            id="bridgerton",
            title="Bridgerton",
            content_type=ContentType.TV_SHOW,
            status=ConsumptionStatus.UNREAD,
            ignored=False,
        )
        database.save_content_item(item)

        item_ignored = ContentItem(
            id="bridgerton",
            title="Bridgerton",
            content_type=ContentType.TV_SHOW,
            status=ConsumptionStatus.UNREAD,
            ignored=True,
        )
        database.save_content_item(item_ignored)

        items = database.get_content_items()
        bridgerton = [i for i in items if i.title == "Bridgerton"]
        assert len(bridgerton) == 1
        assert bridgerton[0].ignored is True

    def test_expand_tv_seasons_propagates_ignored_regression(self) -> None:
        """Bug reported: Even if an ignored TV show slipped through filtering, its
        expanded seasons would not carry the ignored flag because
        expand_tv_shows_to_seasons did not copy it."""
        from src.utils.series import expand_tv_shows_to_seasons

        show = ContentItem(
            id="bridgerton",
            title="Bridgerton",
            content_type=ContentType.TV_SHOW,
            status=ConsumptionStatus.UNREAD,
            ignored=True,
            metadata={"total_seasons": 3},
        )

        expanded = expand_tv_shows_to_seasons([show])

        assert len(expanded) == 3
        for season in expanded:
            assert season.ignored is True

    def test_resync_with_ignored_none_preserves_value_regression(
        self, tmp_path: Path
    ) -> None:
        """Bug reported: After manually ignoring Bridgerton via the UI, re-syncing
        from Sonarr (which has no concept of ignored) reset ignored to False because
        the UPDATE always wrote the field."""
        database = SQLiteDB(tmp_path / "test.db")

        item = ContentItem(
            id="bridgerton",
            title="Bridgerton",
            content_type=ContentType.TV_SHOW,
            status=ConsumptionStatus.COMPLETED,
            source="finished_tv_shows",
            ignored=True,
        )
        database.save_content_item(item)

        items = database.get_content_items()
        bridgerton = [i for i in items if i.title == "Bridgerton"]
        assert bridgerton[0].ignored is True

        sonarr_item = ContentItem(
            id="bridgerton",
            title="Bridgerton",
            content_type=ContentType.TV_SHOW,
            status=ConsumptionStatus.COMPLETED,
            source="sonarr",
            ignored=None,
        )
        database.save_content_item(sonarr_item)

        items = database.get_content_items()
        bridgerton = [i for i in items if i.title == "Bridgerton"]
        assert len(bridgerton) == 1
        assert bridgerton[0].ignored is True


class TestCurrentlyConsumingInclusionRegression:
    """Bug reported: TV shows with currently_consuming status (e.g. DuckTales seasons
    1-3 watched, Wednesday, Welcome to Wrexham) were invisible to the recommendation
    engine."""

    def test_unconsumed_includes_currently_consuming(self, tmp_path: Path) -> None:
        database = SQLiteDB(tmp_path / "test.db")

        database.save_content_item(
            ContentItem(
                id="unread",
                title="Unwatched Show",
                content_type=ContentType.TV_SHOW,
                status=ConsumptionStatus.UNREAD,
            )
        )
        database.save_content_item(
            ContentItem(
                id="in-progress",
                title="DuckTales (Season 2)",
                content_type=ContentType.TV_SHOW,
                status=ConsumptionStatus.CURRENTLY_CONSUMING,
            )
        )
        database.save_content_item(
            ContentItem(
                id="done",
                title="Finished Show",
                content_type=ContentType.TV_SHOW,
                status=ConsumptionStatus.COMPLETED,
                rating=4,
            )
        )

        items = database.get_unconsumed_items()
        titles = {item.title for item in items}
        assert (
            len(items) == 2
        ), f"Expected 2 unconsumed items, got {len(items)}: {titles}"
        assert (
            "Unwatched Show" in titles
        ), f"UNREAD item missing from unconsumed: {titles}"
        assert (
            "DuckTales (Season 2)" in titles
        ), f"CURRENTLY_CONSUMING item missing from unconsumed: {titles}"
        assert (
            "Finished Show" not in titles
        ), f"COMPLETED item should not be in unconsumed: {titles}"

    def test_completed_includes_currently_consuming(self, tmp_path: Path) -> None:
        database = SQLiteDB(tmp_path / "test.db")

        database.save_content_item(
            ContentItem(
                id="unread",
                title="Unwatched Show",
                content_type=ContentType.TV_SHOW,
                status=ConsumptionStatus.UNREAD,
            )
        )
        database.save_content_item(
            ContentItem(
                id="in-progress",
                title="Wednesday",
                content_type=ContentType.TV_SHOW,
                status=ConsumptionStatus.CURRENTLY_CONSUMING,
                rating=4,
            )
        )
        database.save_content_item(
            ContentItem(
                id="done",
                title="Finished Show",
                content_type=ContentType.TV_SHOW,
                status=ConsumptionStatus.COMPLETED,
                rating=5,
            )
        )

        items = database.get_completed_items()
        titles = {item.title for item in items}
        assert (
            len(items) == 2
        ), f"Expected 2 completed items, got {len(items)}: {titles}"
        assert (
            "Wednesday" in titles
        ), f"CURRENTLY_CONSUMING item missing from completed: {titles}"
        assert (
            "Finished Show" in titles
        ), f"COMPLETED item missing from completed: {titles}"
        assert (
            "Unwatched Show" not in titles
        ), f"UNREAD item should not be in completed: {titles}"

    def test_completed_min_rating_applies_to_currently_consuming(
        self, tmp_path: Path
    ) -> None:
        database = SQLiteDB(tmp_path / "test.db")

        database.save_content_item(
            ContentItem(
                id="high-rated",
                title="Welcome to Wrexham",
                content_type=ContentType.TV_SHOW,
                status=ConsumptionStatus.CURRENTLY_CONSUMING,
                rating=5,
            )
        )
        database.save_content_item(
            ContentItem(
                id="low-rated",
                title="Mediocre Show",
                content_type=ContentType.TV_SHOW,
                status=ConsumptionStatus.CURRENTLY_CONSUMING,
                rating=2,
            )
        )
        database.save_content_item(
            ContentItem(
                id="completed-high",
                title="Completed High Rated",
                content_type=ContentType.TV_SHOW,
                status=ConsumptionStatus.COMPLETED,
                rating=5,
            )
        )

        items = database.get_completed_items(min_rating=4)
        titles = {item.title for item in items}
        assert (
            len(items) == 2
        ), f"Expected 2 items with min_rating=4, got {len(items)}: {titles}"
        assert (
            "Welcome to Wrexham" in titles
        ), f"High-rated CURRENTLY_CONSUMING item missing: {titles}"
        assert (
            "Completed High Rated" in titles
        ), f"High-rated COMPLETED item missing: {titles}"
        assert (
            "Mediocre Show" not in titles
        ), f"Low-rated item should be excluded by min_rating: {titles}"

    def test_get_content_items_multi_status_filter(self, tmp_path: Path) -> None:
        database = SQLiteDB(tmp_path / "test.db")

        database.save_content_item(
            ContentItem(
                id="unread",
                title="Unread Book",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.UNREAD,
            )
        )
        database.save_content_item(
            ContentItem(
                id="in-progress",
                title="Reading Now",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.CURRENTLY_CONSUMING,
            )
        )
        database.save_content_item(
            ContentItem(
                id="done",
                title="Finished Book",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.COMPLETED,
                rating=4,
            )
        )

        items = database.get_content_items(
            status=[ConsumptionStatus.UNREAD, ConsumptionStatus.CURRENTLY_CONSUMING]
        )
        titles = {item.title for item in items}
        assert (
            len(items) == 2
        ), f"Expected 2 items for multi-status filter, got {len(items)}: {titles}"
        assert "Unread Book" in titles, f"UNREAD item missing: {titles}"
        assert "Reading Now" in titles, f"CURRENTLY_CONSUMING item missing: {titles}"
        assert (
            "Finished Book" not in titles
        ), f"COMPLETED item should be excluded: {titles}"

    def test_get_content_items_single_status_still_works(self, tmp_path: Path) -> None:
        database = SQLiteDB(tmp_path / "test.db")

        database.save_content_item(
            ContentItem(
                id="unread",
                title="Unread Book",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.UNREAD,
            )
        )
        database.save_content_item(
            ContentItem(
                id="done",
                title="Finished Book",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.COMPLETED,
                rating=4,
            )
        )

        items = database.get_content_items(status=ConsumptionStatus.COMPLETED)
        titles = {item.title for item in items}
        assert titles == {
            "Finished Book"
        }, f"Expected only COMPLETED item, got: {titles}"

    def test_get_content_items_empty_status_list_returns_nothing(
        self, tmp_path: Path
    ) -> None:
        database = SQLiteDB(tmp_path / "test.db")

        database.save_content_item(
            ContentItem(
                id="unread",
                title="Some Book",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.UNREAD,
            )
        )

        items = database.get_content_items(status=[])
        assert items == [], f"Empty status list must return no items, got: {items}"
