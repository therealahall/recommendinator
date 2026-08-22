"""Regression tests for bugs found during code quality audit."""

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
    """Regression tests for article stripping bugs in engine.py."""

    def test_article_stripped_from_middle_of_title_regression(self) -> None:
        """Regression test: Article stripping should only remove leading articles.

        Bug reported: .replace("the ", "") removes "the" from anywhere in a
        title, e.g. "Into the Wild" becomes "Into Wild", "Cathedral" becomes
        "Cdral", "A Gathering Storm" mishandles "a" mid-word.

        Root cause: Using str.replace() instead of regex anchored to start.

        Fix: Use re.sub(r"^(the|a|an)\\s+", "", s, flags=re.I) for
        leading-article removal.
        """

        # Test titles_similar which also strips articles
        # "Into the Wild" should NOT have "the" stripped from the middle
        assert titles_similar("Into the Wild", "Into the Wild") is True
        # "Cathedral" should not have "a" stripped out of the middle
        assert titles_similar("Cathedral", "Cathedral") is True

    def test_title_with_leading_article_still_matches_regression(self) -> None:
        """Regression test: Titles with leading articles should still match.

        Ensures the fix doesn't break the intended functionality of removing
        leading articles for matching purposes.
        """
        # Leading articles should still be stripped for matching
        assert titles_similar("The Matrix", "Matrix") is True
        assert titles_similar("A Beautiful Mind", "Beautiful Mind") is True
        assert titles_similar("An Inspector Calls", "Inspector Calls") is True


class TestPreferenceInterpreterRegression:
    """Regression tests for preference interpreter bugs."""

    def test_into_genre_parsed_as_prefer_not_avoid_regression(self) -> None:
        """Regression test: "into X" should be parsed as PREFER, not AVOID.

        Bug reported: "into sci-fi" was matched by the AVOID_PATTERNS regex
        r"(?:not?\\s+)?(?:into|interested in)\\s+(.+)" because "not?" makes
        the "not" optional, causing "into sci-fi" to match as an avoid pattern.

        Root cause: The "not?" in the regex made "not" optional, so both
        "not into sci-fi" and "into sci-fi" matched the AVOID pattern.

        Fix: Split into two patterns — "not into X" stays in AVOID_PATTERNS,
        "into X" moves to PREFER_PATTERNS.
        """
        interpreter = PatternBasedInterpreter()

        # "into sci-fi" should boost sci-fi, not penalize it
        result = interpreter.interpret("into sci-fi")
        assert result.genre_boosts, "Expected genre boosts for 'into sci-fi'"
        assert (
            not result.genre_penalties
        ), "'into sci-fi' should NOT produce genre penalties"

    def test_not_into_genre_still_avoids_regression(self) -> None:
        """Regression test: "not into X" should still be parsed as AVOID.

        Ensures the fix doesn't break the intended "not into" avoidance.
        """
        interpreter = PatternBasedInterpreter()

        result = interpreter.interpret("not into horror")
        assert result.genre_penalties, "Expected genre penalties for 'not into horror'"
        assert (
            not result.genre_boosts
        ), "'not into horror' should NOT produce genre boosts"


class TestMinRatingZeroRegression:
    """Regression tests for min_rating=0 being skipped."""

    def test_min_rating_zero_not_skipped_regression(self, tmp_path: Path) -> None:
        """Regression test: min_rating=0 should filter items with no rating.

        Bug reported: Using `if min_rating:` treats 0 as falsy, so
        min_rating=0 is effectively ignored (no filter applied).

        Root cause: `if min_rating:` evaluates to False when min_rating is 0.

        Fix: Use `if min_rating is not None:` instead.
        """
        database = SQLiteDB(tmp_path / "test.db")

        # Save items with various ratings
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

        # min_rating=0 should include rated items and exclude unrated (NULL)
        items = database.get_content_items(min_rating=0)
        titles = [item.title for item in items]
        assert "Rated Book" in titles
        # Unrated items have NULL rating, which fails the >= 0 check in SQL
        assert "Unrated Book" not in titles

    def test_min_rating_none_returns_all_regression(self, tmp_path: Path) -> None:
        """Ensure min_rating=None returns all items (including unrated)."""
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
    """Regression tests for enrichment stats query bugs."""

    def test_mark_enrichment_preserves_existing_status_regression(self) -> None:
        """Regression test: re-syncing items should not re-enrich already enriched items.

        Bug reported: Importing finished TV shows via JSON re-enriched
        everything, even items already enriched in a previous sync.

        Root cause: mark_item_needs_enrichment used INSERT OR REPLACE,
        which overwrote existing enrichment_status rows (with provider,
        quality, etc.) with a fresh needs_enrichment=1 row.

        Fix: Changed to INSERT OR IGNORE so existing rows are preserved.
        """
        conn = sqlite3.connect(":memory:")
        create_schema(conn)

        # Insert a content item
        conn.execute(
            "INSERT INTO content_items (id, title, content_type, status, source, user_id)"
            " VALUES (1, 'Test Show', 'tv_show', 'completed', 'test', 1)"
        )
        conn.commit()

        # Mark it for enrichment and then complete enrichment
        mark_item_needs_enrichment(conn, 1)
        mark_enrichment_complete(
            conn,
            content_item_id=1,
            provider="tmdb",
            quality="high",
        )

        # Verify it's enriched
        cursor = conn.execute(
            "SELECT needs_enrichment, enrichment_provider FROM enrichment_status"
            " WHERE content_item_id = 1"
        )
        row = cursor.fetchone()
        assert row[0] == 0  # needs_enrichment = False
        assert row[1] == "tmdb"

        # Re-sync marks the item again — should NOT overwrite
        mark_item_needs_enrichment(conn, 1)

        cursor = conn.execute(
            "SELECT needs_enrichment, enrichment_provider FROM enrichment_status"
            " WHERE content_item_id = 1"
        )
        row = cursor.fetchone()
        assert row[0] == 0  # Still enriched, not reset
        assert row[1] == "tmdb"  # Provider preserved

        conn.close()


class TestIgnoredFieldRegression:
    """Regression tests for ignored field persistence."""

    def test_upsert_preserves_ignored_field_regression(self, tmp_path: Path) -> None:
        """Regression test: re-importing item with ignored=True should update the field.

        Bug reported: Importing Bridgerton with ignored: true did not
        set ignored in the database because the UPDATE branch of
        save_content_item did not include the ignored column.

        Root cause: The UPDATE SQL in save_content_item only set title,
        status, rating, review, date_completed, and source — not ignored.

        Fix: Added ignored to the UPDATE statement.
        """
        database = SQLiteDB(tmp_path / "test.db")

        # First import: item not ignored
        item = ContentItem(
            id="bridgerton",
            title="Bridgerton",
            content_type=ContentType.TV_SHOW,
            status=ConsumptionStatus.UNREAD,
            ignored=False,
        )
        database.save_content_item(item)

        # Re-import with ignored=True
        item_ignored = ContentItem(
            id="bridgerton",
            title="Bridgerton",
            content_type=ContentType.TV_SHOW,
            status=ConsumptionStatus.UNREAD,
            ignored=True,
        )
        database.save_content_item(item_ignored)

        # Verify ignored is now True
        items = database.get_content_items()
        bridgerton = [i for i in items if i.title == "Bridgerton"]
        assert len(bridgerton) == 1
        assert bridgerton[0].ignored is True

    def test_expand_tv_seasons_propagates_ignored_regression(self) -> None:
        """Regression test: season expansion should propagate ignored flag.

        Bug reported: Even if an ignored TV show slipped through filtering,
        its expanded seasons would not carry the ignored flag because
        expand_tv_shows_to_seasons did not copy it.

        Fix: Added ignored=item.ignored to the season ContentItem.
        """
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
        """Regression test: re-syncing from an API plugin should not reset ignored.

        Bug reported: After manually ignoring Bridgerton via the UI, re-syncing
        from Sonarr (which has no concept of ignored) reset ignored to False
        because the UPDATE always wrote the field.

        Root cause: API plugins don't set ignored, but the UPDATE branch
        unconditionally wrote ignored=0 (the default).

        Fix: Changed ContentItem.ignored to bool | None (tri-state). None
        means "source didn't specify" and the UPDATE branch only includes
        ignored in the SQL when it is not None.
        """
        database = SQLiteDB(tmp_path / "test.db")

        # Import via JSON with ignored=True (file-based source)
        item = ContentItem(
            id="bridgerton",
            title="Bridgerton",
            content_type=ContentType.TV_SHOW,
            status=ConsumptionStatus.COMPLETED,
            source="finished_tv_shows",
            ignored=True,
        )
        database.save_content_item(item)

        # Verify ignored is True
        items = database.get_content_items()
        bridgerton = [i for i in items if i.title == "Bridgerton"]
        assert bridgerton[0].ignored is True

        # Re-sync from Sonarr (API plugin, ignored=None — not specified)
        sonarr_item = ContentItem(
            id="bridgerton",
            title="Bridgerton",
            content_type=ContentType.TV_SHOW,
            status=ConsumptionStatus.COMPLETED,
            source="sonarr",
            ignored=None,  # API plugin doesn't set this
        )
        database.save_content_item(sonarr_item)

        # Verify ignored is STILL True — not overwritten
        items = database.get_content_items()
        bridgerton = [i for i in items if i.title == "Bridgerton"]
        assert len(bridgerton) == 1
        assert bridgerton[0].ignored is True


class TestCurrentlyConsumingInclusionRegression:
    """Regression tests for CURRENTLY_CONSUMING items missing from recommendation pools.

    Bug reported: TV shows with currently_consuming status (e.g. DuckTales
    seasons 1-3 watched, Wednesday, Welcome to Wrexham) were invisible to the
    recommendation engine.

    Root cause: get_unconsumed_items() only returned UNREAD items and
    get_completed_items() only returned COMPLETED items, so
    CURRENTLY_CONSUMING fell through the gap — excluded from both candidate
    generation and preference analysis.

    Fix: get_unconsumed_items() now returns UNREAD + CURRENTLY_CONSUMING;
    get_completed_items() now returns COMPLETED + CURRENTLY_CONSUMING.
    get_content_items() accepts a list of statuses for IN-clause filtering.
    """

    def test_unconsumed_includes_currently_consuming(self, tmp_path: Path) -> None:
        """get_unconsumed_items must return both UNREAD and CURRENTLY_CONSUMING."""
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
        """get_completed_items must return both COMPLETED and CURRENTLY_CONSUMING."""
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
        """min_rating filter must apply to CURRENTLY_CONSUMING items too."""
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
        """get_content_items with a list of statuses must use IN-clause filtering."""
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
        """Single ConsumptionStatus value must still work after multi-status support."""
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
        """get_content_items with an empty status list must return [], not crash."""
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
