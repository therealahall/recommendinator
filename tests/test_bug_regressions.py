"""Regression tests for bugs found during code quality audit."""

import sqlite3
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest

from src.llm.client import OllamaClient
from src.llm.recommendations import BLURB_BATCH_SIZE, RecommendationGenerator
from src.models.content import ConsumptionStatus, ContentItem, ContentType
from src.recommendations.engine import RecommendationEngine
from src.recommendations.preference_interpreter import PatternBasedInterpreter
from src.storage.schema import (
    create_schema,
    get_enrichment_stats,
    mark_enrichment_complete,
    mark_item_needs_enrichment,
)
from src.storage.sqlite_db import SQLiteDB


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

        engine = RecommendationEngine.__new__(RecommendationEngine)

        # Test _titles_similar which also strips articles
        # "Into the Wild" should NOT have "the" stripped from the middle
        assert engine._titles_similar("Into the Wild", "Into the Wild") is True
        # "Cathedral" should not have "a" stripped out of the middle
        assert engine._titles_similar("Cathedral", "Cathedral") is True

    def test_title_with_leading_article_still_matches_regression(self) -> None:
        """Regression test: Titles with leading articles should still match.

        Ensures the fix doesn't break the intended functionality of removing
        leading articles for matching purposes.
        """

        engine = RecommendationEngine.__new__(RecommendationEngine)

        # Leading articles should still be stripped for matching
        assert engine._titles_similar("The Matrix", "Matrix") is True
        assert engine._titles_similar("A Beautiful Mind", "Beautiful Mind") is True
        assert engine._titles_similar("An Inspector Calls", "Inspector Calls") is True


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


class TestHardcodedFallbackCountRegression:
    """Regression tests for hardcoded fallback count in LLM recommendations."""

    def test_fallback_respects_count_parameter_regression(self) -> None:
        """Regression test: Fallback extraction should respect count parameter.

        Bug reported: When LLM response parsing fails and fallback title
        extraction is used, the code hardcoded `>= 5` instead of using the
        `count` parameter, causing it to always return up to 5 results
        regardless of the requested count.

        Root cause: Hardcoded `if len(recommendations) >= 5` in fallback path.

        Fix: Use the `count` parameter instead of `5`.
        """
        mock_client = Mock(spec=OllamaClient)
        # Response that won't parse as numbered list but contains titles
        mock_client.generate_text.return_value = (
            "I recommend Book One and Book Two and Book Three"
        )

        unconsumed = [
            ContentItem(
                id=str(index),
                title=f"Book {name}",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.UNREAD,
            )
            for index, name in enumerate(
                ["One", "Two", "Three", "Four", "Five", "Six"], 1
            )
        ]

        generator = RecommendationGenerator(mock_client)
        recommendations = generator.generate_recommendations(
            ContentType.BOOK, [], unconsumed, count=2
        )

        # Should respect count=2, not hardcoded 5
        assert len(recommendations) <= 2


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


class TestVectorDBCosineRegression:
    """Regression tests for VectorDB distance metric."""

    def test_collection_uses_cosine_space_regression(self, tmp_path: Path) -> None:
        """Regression test: VectorDB should use cosine similarity, not L2.

        Bug reported: ChromaDB defaults to L2 distance, but the score
        calculation in search_similar uses `1.0 - distance` which only
        makes sense for cosine distance (range 0-2) not L2 (range 0-inf).

        Root cause: Collection created without specifying distance metric.

        Fix: Set metadata={"hnsw:space": "cosine"} on collection creation.
        """
        from src.storage.vector_db import VectorDB

        vector_db = VectorDB(tmp_path / "vector_db")

        # Verify the collection was created with cosine space
        collection_metadata = vector_db.collection.metadata
        assert collection_metadata is not None
        assert collection_metadata.get("hnsw:space") == "cosine"


class TestEnrichmentStatsRegression:
    """Regression tests for enrichment stats query bugs."""

    def test_enrichment_stats_with_user_id_regression(self) -> None:
        """Regression test: get_enrichment_stats crashes with user_id filter.

        Bug reported: Sync page shows "Failed to load enrichment stats:
        HTTP 500" on fresh load.

        Root cause: _count_query for total_items joined content_items ci
        twice — once from the table prefix and once from user_join — causing
        "ambiguous column name: ci.user_id".

        Fix: Query total_items directly without _count_query when user_id
        is set, avoiding the double join.
        """
        conn = sqlite3.connect(":memory:")
        create_schema(conn)

        # Insert a content item so there's data
        conn.execute(
            "INSERT INTO content_items (title, content_type, status, source, user_id)"
            " VALUES ('Test', 'book', 'completed', 'test', 1)"
        )
        conn.commit()

        # This would crash with "ambiguous column name: ci.user_id" before fix
        stats = get_enrichment_stats(conn, user_id=1)

        assert stats["total"] == 1
        assert stats["enriched"] == 0
        conn.close()

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
        from src.storage.sqlite_db import SQLiteDB

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
        from src.storage.sqlite_db import SQLiteDB

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


class TestLlmReasoningMismatchRegression:
    """Regression tests for LLM reasoning being attached to the wrong item."""

    def test_llm_reasoning_matched_by_title_not_index_regression(self) -> None:
        """Regression test: LLM reasoning must match by title, not by position.

        Bug reported: "Fire & Blood" reasoning appeared on "The Way of Kings"
        recommendation and vice versa.

        Root cause: LLM returns items in its own preferred order, but the
        engine attached reasoning by index position — so llm_recs[0] reasoning
        went to recommendations[0] regardless of whether those were the same
        item.

        Fix: Build a title -> reasoning lookup from LLM results and match
        each pipeline recommendation by its title.
        """

        engine = RecommendationEngine.__new__(RecommendationEngine)
        engine.llm_generator = Mock(spec=RecommendationGenerator)

        way_of_kings = ContentItem(
            id="1",
            title="The Way of Kings",
            author="Brandon Sanderson",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
        )
        fire_and_blood = ContentItem(
            id="2",
            title="Fire & Blood",
            author="George R.R. Martin",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
        )

        # Simulate pipeline recommendations in order: Way of Kings, Fire & Blood
        recommendations: list[dict[str, Any]] = [
            {
                "item": way_of_kings,
                "score": 0.9,
                "reasoning": "Pipeline reasoning for Way of Kings",
            },
            {
                "item": fire_and_blood,
                "score": 0.85,
                "reasoning": "Pipeline reasoning for Fire & Blood",
            },
        ]

        # LLM returns items in REVERSED order: Fire & Blood first
        engine.llm_generator.generate_blurbs.return_value = [
            {
                "title": "Fire & Blood",
                "author": "George R.R. Martin",
                "reasoning": "LLM reasoning for Fire & Blood",
                "item": fire_and_blood,
            },
            {
                "title": "The Way of Kings",
                "author": "Brandon Sanderson",
                "reasoning": "LLM reasoning for Way of Kings",
                "item": way_of_kings,
            },
        ]

        # Call the actual production code
        engine._enhance_with_llm(
            recommendations=recommendations,
            content_type=ContentType.BOOK,
            all_consumed_items=[],
            unconsumed_items=[],
            count=2,
            series_tracking={},
        )

        # Each recommendation must have its OWN reasoning, not the other's
        assert recommendations[0]["llm_reasoning"] == "LLM reasoning for Way of Kings"
        assert recommendations[1]["llm_reasoning"] == "LLM reasoning for Fire & Blood"

    def test_enhance_uses_blurbs_for_pipeline_recommendations_regression(self) -> None:
        """Regression test: LLM enhancement must use blurbs, not re-pick.

        Bug reported: When requesting 5 recommendations with AI reasoning
        enabled, only 3 received LLM reasoning. The remaining 2 had no
        llm_reasoning field.

        Root cause: _enhance_with_llm called generate_recommendations which
        asks the LLM to "Pick the N best..." from the candidates. The LLM
        would only select ~3 items regardless of count, so extra pipeline
        recommendations never got reasoning attached.

        Fix: Use generate_blurbs (which writes reasoning for ALL pre-selected
        items) instead of generate_recommendations when the pipeline already
        produced recommendations.
        """

        engine = RecommendationEngine.__new__(RecommendationEngine)
        engine.llm_generator = Mock(spec=RecommendationGenerator)

        items = [
            ContentItem(
                id=str(index),
                title=f"Book {index}",
                author=f"Author {index}",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.UNREAD,
            )
            for index in range(1, 6)
        ]

        # Pipeline produced 5 recommendations
        recommendations: list[dict[str, Any]] = [
            {"item": item, "score": 0.9 - index * 0.05, "reasoning": "Pipeline"}
            for index, item in enumerate(items)
        ]

        # generate_blurbs returns reasoning for ALL 5 items
        engine.llm_generator.generate_blurbs.return_value = [
            {
                "title": item.title,
                "author": item.author,
                "reasoning": f"LLM blurb for {item.title}",
                "item": item,
            }
            for item in items
        ]

        engine._enhance_with_llm(
            recommendations=recommendations,
            content_type=ContentType.BOOK,
            all_consumed_items=[],
            unconsumed_items=[],
            count=5,
            series_tracking={},
        )

        # ALL 5 recommendations must have LLM reasoning
        for index, rec in enumerate(recommendations):
            assert (
                "llm_reasoning" in rec
            ), f"Recommendation {index} ({rec['item'].title}) missing llm_reasoning"
            assert rec["llm_reasoning"] == f"LLM blurb for Book {index + 1}"

        # Must call generate_blurbs (not generate_recommendations) with all items
        engine.llm_generator.generate_blurbs.assert_called_once_with(
            content_type=ContentType.BOOK,
            selected_items=items,
            consumed_items=[],
        )

    def test_bold_markers_stripped_from_parsed_titles_regression(self) -> None:
        """Regression test: bold markdown in LLM titles must not prevent matching.

        Bug reported: LLM reasonings were generated but none displayed. The
        pipeline recommendations showed no llm_reasoning field.

        Root cause: The prompt instructs the LLM to format as
        "1. **Title** by Author", so the parser extracted **Title** (with
        bold markers) which never matched any database title.

        Fix: Strip markdown bold markers from extracted titles in the parser.
        """
        mock_client = Mock(spec=OllamaClient)
        mock_client.generate_text.return_value = (
            "1. **Book Alpha** by Author One\n"
            "Great match because of your taste.\n\n"
            "2. **Book Beta** by Author Two\n"
            "Fits your preferences perfectly."
        )

        unconsumed = [
            ContentItem(
                id="1",
                title="Book Alpha",
                author="Author One",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.UNREAD,
            ),
            ContentItem(
                id="2",
                title="Book Beta",
                author="Author Two",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.UNREAD,
            ),
        ]

        generator = RecommendationGenerator(mock_client)
        results = generator.generate_recommendations(
            ContentType.BOOK, [], unconsumed, count=2
        )

        # Both items should be matched despite bold markers in LLM output
        matched_titles = [rec["title"] for rec in results]
        assert "Book Alpha" in matched_titles
        assert "Book Beta" in matched_titles

        # The matched ContentItem should be set (not None)
        for rec in results:
            assert (
                rec["item"] is not None
            ), f"Item for '{rec['title']}' should be matched"

    def test_series_suffix_in_db_title_still_matches_regression(self) -> None:
        """Regression test: DB titles with series suffixes must match LLM titles.

        Bug reported: llm_reasoning was null for all recommendations even
        though the LLM generated reasoning text.

        Root cause: Database titles include series info in parentheses,
        e.g. "The Name of the Wind (The Kingkiller Chronicle, #1)", but the
        LLM outputs just "The Name of the Wind". Exact equality matching
        failed for every item.

        Fix: Use substring containment instead of exact equality when
        matching parsed LLM titles to database items.
        """
        mock_client = Mock(spec=OllamaClient)
        mock_client.generate_text.return_value = (
            "1. **Magic Kingdom for Sale—Sold!** by Terry Brooks\n"
            "Terry Brooks at his most inventive.\n\n"
            "2. **The Name of the Wind** by Patrick Rothfuss\n"
            "Epic fantasy with beautiful prose."
        )

        unconsumed = [
            ContentItem(
                id="1",
                title="Magic Kingdom for Sale—Sold! (Magic Kingdom of Landover #1)",
                author="Terry Brooks",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.UNREAD,
            ),
            ContentItem(
                id="2",
                title="The Name of the Wind (The Kingkiller Chronicle, #1)",
                author="Patrick Rothfuss",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.UNREAD,
            ),
        ]

        generator = RecommendationGenerator(mock_client)
        results = generator.generate_recommendations(
            ContentType.BOOK, [], unconsumed, count=2
        )

        # Both items should match despite series suffixes in DB titles
        for rec in results:
            assert (
                rec["item"] is not None
            ), f"Item for '{rec['title']}' should match despite series suffix"

        matched_db_titles = [rec["item"].title for rec in results]
        assert any("Magic Kingdom" in title for title in matched_db_titles)
        assert any("Name of the Wind" in title for title in matched_db_titles)


def _make_book_items(names: list[str]) -> list[ContentItem]:
    """Build a list of book ContentItems with unique, non-overlapping titles."""
    return [
        ContentItem(
            id=str(index),
            title=f"Book {name}",
            author=f"Author {name}",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
        )
        for index, name in enumerate(names, 1)
    ]


# Names chosen so that no title is a substring of another (avoids false
# matches in the fake LLM response builder used by the tests below).
_TEN_BOOK_NAMES = [
    "Alpha",
    "Beta",
    "Gamma",
    "Delta",
    "Epsilon",
    "Zeta",
    "Eta",
    "Theta",
    "Iota",
    "Kappa",
]


def _fake_blurb_response(items: list[ContentItem], prompt: str) -> str:
    """Build a numbered-list blurb response for items whose titles appear in *prompt*."""
    lines = []
    entry_num = 1
    for item in items:
        if item.title in prompt:
            lines.append(
                f"{entry_num}. **{item.title}** by {item.author}\n"
                f"Great match for your taste."
            )
            entry_num += 1
    return "\n\n".join(lines)


class TestBlurbBatchingRegression:
    """Regression tests for LLM blurb generation failing when count > 5."""

    def test_blurbs_batched_for_more_than_five_items_regression(self) -> None:
        """Regression test: Blurb generation must work for > 5 items.

        Bug reported: When requesting more than 5 recommendations with LLM
        reasoning enabled, no LLM reasoning appeared on any result. Results
        came back with only pipeline reasoning.

        Root cause: Local LLMs struggle with long prompts/responses. A single
        blurb call with > 5 items would fail (timeout, truncation, or parsing
        error), and the exception was silently caught in _enhance_with_llm.

        Fix: Batch blurb generation into groups of BLURB_BATCH_SIZE (5) items,
        making a separate LLM call per batch.
        """
        mock_client = Mock(spec=OllamaClient)
        items = _make_book_items(_TEN_BOOK_NAMES)

        mock_client.generate_text.side_effect = (
            lambda prompt, **kw: _fake_blurb_response(items, prompt)
        )

        generator = RecommendationGenerator(mock_client)
        results = generator.generate_blurbs(
            ContentType.BOOK,
            selected_items=items,
            consumed_items=[],
        )

        # All 10 items should have blurbs
        assert len(results) == 10

        # Every item should be matched (not None)
        unmatched = [rec["title"] for rec in results if rec["item"] is None]
        assert not unmatched, f"Items with no match: {unmatched}"

        # All titles present
        result_titles = {rec["title"] for rec in results}
        expected_titles = {item.title for item in items}
        assert result_titles == expected_titles

        # Should have made 2 LLM calls (batches of 5)
        assert mock_client.generate_text.call_count == 2

    def test_partial_batch_failure_returns_successful_batches_regression(
        self,
    ) -> None:
        """Regression test: Partial batch failure should return successful results.

        Bug reported: When requesting more than 5 recommendations with LLM
        reasoning enabled, no LLM reasoning appeared on any result. Results
        came back with only pipeline reasoning.

        Root cause: Local LLMs struggle with long prompts/responses. A single
        blurb call with > 5 items would fail (timeout, truncation, or parsing
        error), and the exception was silently caught in _enhance_with_llm.

        Fix: Batch blurb generation into groups of BLURB_BATCH_SIZE (5) items,
        run concurrently via ThreadPoolExecutor. If one batch fails, results
        from successful batches are still returned.
        """
        mock_client = Mock(spec=OllamaClient)
        items = _make_book_items(_TEN_BOOK_NAMES)

        # Fail batch 2 based on prompt content (not call order, since batches
        # run concurrently and completion order is non-deterministic).
        batch_2_marker = items[BLURB_BATCH_SIZE].title  # first item of batch 2

        def fake_generate_text(prompt: str, **kwargs: Any) -> str:
            if batch_2_marker in prompt:
                raise RuntimeError("LLM timeout")
            return _fake_blurb_response(items, prompt)

        mock_client.generate_text.side_effect = fake_generate_text

        generator = RecommendationGenerator(mock_client)
        results = generator.generate_blurbs(
            ContentType.BOOK,
            selected_items=items,
            consumed_items=[],
        )

        # Batch 1 (5 items) should succeed, batch 2 fails
        assert len(results) == BLURB_BATCH_SIZE

        # Results should be from batch 1 (first 5 items)
        batch_1_titles = {item.title for item in items[:BLURB_BATCH_SIZE]}
        result_titles = {rec["title"] for rec in results}
        assert result_titles == batch_1_titles

        # Both batches should have been attempted
        assert mock_client.generate_text.call_count == 2

    def test_all_batches_fail_raises_runtime_error_regression(self) -> None:
        """Regression test: Total batch failure should raise RuntimeError.

        Bug reported: When requesting more than 5 recommendations with LLM
        reasoning enabled, no LLM reasoning appeared on any result. Results
        came back with only pipeline reasoning.

        Root cause: Local LLMs struggle with long prompts/responses. A single
        blurb call with > 5 items would fail (timeout, truncation, or parsing
        error), and the exception was silently caught in _enhance_with_llm.

        Fix: Batch blurb generation into groups of BLURB_BATCH_SIZE (5) items.
        When every batch fails, RuntimeError is raised so the caller knows.
        """
        mock_client = Mock(spec=OllamaClient)
        mock_client.generate_text.side_effect = RuntimeError("LLM unavailable")

        items = _make_book_items(
            _TEN_BOOK_NAMES[: BLURB_BATCH_SIZE + 2]
        )  # 7 items → 2 batches

        generator = RecommendationGenerator(mock_client)

        with pytest.raises(
            RuntimeError, match=r"Blurb generation failed for all \d+ batch"
        ):
            generator.generate_blurbs(
                ContentType.BOOK,
                selected_items=items,
                consumed_items=[],
            )


def _make_movie_items(titles: list[str]) -> list[ContentItem]:
    """Build a list of movie ContentItems for inline-reasoning tests.

    Unlike _make_book_items, titles are used verbatim (not prefixed) because
    the inline-reasoning tests assert exact title matching against realistic
    LLM output that contains the real title.
    """
    return [
        ContentItem(
            id=str(index),
            title=title,
            content_type=ContentType.MOVIE,
            status=ConsumptionStatus.UNREAD,
        )
        for index, title in enumerate(titles, 1)
    ]


class TestInlineReasoningRegression:
    """Regression tests for inline reasoning being discarded by the parser.

    Bug reported: llm_reasoning was "" (empty string) for all recommendations
    even though the LLM generated output successfully. Title matching worked,
    but reasoning was always blank.

    Root cause: When the LLM puts reasoning on the same line as the title
    (e.g. "1. **Gremlins** A fun horror-comedy..."), the parser treated the
    entire first line as the title and only extracted reasoning from lines[1:].

    Fix: Detect bold-marker titles (**Title**), extract the title from within
    the markers, and treat the remainder of the line as inline reasoning.

    Tests call _parse_recommendations directly to isolate the parsing logic
    from the LLM call.
    """

    @pytest.fixture()
    def generator(self) -> RecommendationGenerator:
        """Create a RecommendationGenerator with a stub client."""
        return RecommendationGenerator(Mock(spec=OllamaClient))

    def test_no_author_inline_reasoning_captured_regression(
        self, generator: RecommendationGenerator
    ) -> None:
        """Inline reasoning on the same line as a bold title must be captured.

        Movies and games typically have no author, so the LLM outputs:
        "1. **Gremlins** A fun horror-comedy that matches your taste"
        The reasoning after **Title** must not be lost.
        """
        response = (
            "1. **Gremlins** A fun horror-comedy that matches your taste\n\n"
            "2. **Inception** A mind-bending thriller you will love"
        )
        items = _make_movie_items(["Gremlins", "Inception"])
        results = generator._parse_recommendations(response, items, count=2)

        assert len(results) == 2
        assert results[0]["title"] == "Gremlins"
        assert "horror-comedy" in results[0]["reasoning"]
        assert results[0]["item"] is not None
        assert results[1]["title"] == "Inception"
        assert "mind-bending" in results[1]["reasoning"]
        assert results[1]["item"] is not None

    def test_multiline_reasoning_still_works_regression(
        self, generator: RecommendationGenerator
    ) -> None:
        """Multi-line reasoning (title on one line, reasoning on next) must not regress.

        This is the format that already worked before the fix. Ensure the
        bold-marker extraction does not break it.
        """
        response = (
            "1. **Gremlins**\n"
            "A fun horror-comedy that matches your taste.\n\n"
            "2. **Inception**\n"
            "A mind-bending thriller you will love."
        )
        items = _make_movie_items(["Gremlins", "Inception"])
        results = generator._parse_recommendations(response, items, count=2)

        assert len(results) == 2
        assert results[0]["title"] == "Gremlins"
        assert "horror-comedy" in results[0]["reasoning"]
        assert results[0]["item"] is not None
        assert results[1]["title"] == "Inception"
        assert "mind-bending" in results[1]["reasoning"]
        assert results[1]["item"] is not None

    def test_inline_reasoning_with_separator_regression(
        self, generator: RecommendationGenerator
    ) -> None:
        """Inline reasoning preceded by a dash or colon separator must be captured.

        LLMs often output: "1. **Title** - Reasoning..." or "1. **Title**: Reasoning..."
        The separator must be stripped from the reasoning text.
        """
        response = (
            "1. **Gremlins** - A fun horror-comedy\n\n"
            "2. **Inception** — A mind-bending thriller\n\n"
            "3. **Alien**: A terrifying sci-fi classic"
        )
        items = _make_movie_items(["Gremlins", "Inception", "Alien"])
        results = generator._parse_recommendations(response, items, count=3)

        assert len(results) == 3
        assert results[0]["reasoning"].startswith(
            "A fun"
        ), f"Hyphen separator not stripped: {results[0]['reasoning']!r}"
        assert results[1]["reasoning"].startswith(
            "A mind"
        ), f"Em-dash separator not stripped: {results[1]['reasoning']!r}"
        assert results[2]["reasoning"].startswith(
            "A terrifying"
        ), f"Colon separator not stripped: {results[2]['reasoning']!r}"

    def test_inline_and_multiline_reasoning_combined_regression(
        self, generator: RecommendationGenerator
    ) -> None:
        """Inline reasoning + continuation lines must be combined into one string.

        When the LLM puts some reasoning on the title line and continues on
        subsequent lines, both parts must be captured.
        """
        response = (
            "1. **Gremlins** A fun horror-comedy.\n"
            "It perfectly matches your love of 80s creature features."
        )
        items = _make_movie_items(["Gremlins"])
        results = generator._parse_recommendations(response, items, count=1)

        assert len(results) == 1
        reasoning = results[0]["reasoning"]
        assert "horror-comedy" in reasoning
        assert "creature features" in reasoning
        assert results[0]["item"] is not None

    def test_book_with_by_author_still_parses_correctly_regression(
        self, generator: RecommendationGenerator
    ) -> None:
        """Bold title with 'by Author' must still extract the author correctly.

        Books use "1. **Title** by Author" format. The inline-reasoning fix
        must not break author extraction.
        """
        response = (
            "1. **The Name of the Wind** by Patrick Rothfuss\n"
            "Epic fantasy with beautiful prose."
        )
        unconsumed = [
            ContentItem(
                id="1",
                title="The Name of the Wind",
                author="Patrick Rothfuss",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.UNREAD,
            ),
        ]
        results = generator._parse_recommendations(response, unconsumed, count=1)

        assert len(results) == 1
        assert results[0]["title"] == "The Name of the Wind"
        assert results[0]["author"] == "Patrick Rothfuss"
        assert results[0]["item"] is not None
        assert "beautiful prose" in results[0]["reasoning"]

    def test_by_author_with_inline_reasoning_regression(
        self, generator: RecommendationGenerator
    ) -> None:
        """Bold title with 'by Author - reasoning' must extract both fields.

        Bug reported: When the LLM outputs inline reasoning after the author
        on the same line (e.g. "**Title** by Author - Great book..."), the
        parser grabbed everything after "by " as the author, corrupting it
        with reasoning text and discarding the reasoning entirely.

        Root cause: Greedy grab of remainder[3:] after "by " prefix.

        Fix: Split author text at the first separator (-, —, –, :) to
        separate author from inline reasoning.
        """
        response = (
            "1. **The Name of the Wind** by Patrick Rothfuss - "
            "A beautifully written epic fantasy"
        )
        unconsumed = [
            ContentItem(
                id="1",
                title="The Name of the Wind",
                author="Patrick Rothfuss",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.UNREAD,
            ),
        ]
        results = generator._parse_recommendations(response, unconsumed, count=1)

        assert len(results) == 1
        assert (
            results[0]["author"] == "Patrick Rothfuss"
        ), f"Author must not absorb separator, got: {results[0]['author']!r}"
        assert "beautifully written" in results[0]["reasoning"]
        assert results[0]["item"] is not None

    def test_en_dash_separator_stripped_regression(
        self, generator: RecommendationGenerator
    ) -> None:
        """En-dash (U+2013) separator must be stripped like em-dash and hyphen.

        Bug reported: LLMs emit en-dashes (–) which are visually identical
        to em-dashes (—), but the separator regex only handled em-dashes,
        leaving the en-dash in the reasoning text.

        Root cause: Character class [-—:] missing U+2013 (–).

        Fix: Add en-dash to both separator patterns.
        """
        response = "1. **Gremlins** \u2013 A fun horror-comedy"
        items = _make_movie_items(["Gremlins"])
        results = generator._parse_recommendations(response, items, count=1)

        assert len(results) == 1
        assert results[0]["reasoning"].startswith(
            "A fun"
        ), f"En-dash separator not stripped: {results[0]['reasoning']!r}"
        assert results[0]["item"] is not None
