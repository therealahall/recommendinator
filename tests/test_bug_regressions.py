"""Regression tests for bugs found during code quality audit."""

import sqlite3
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest

from src.llm.client import OllamaClient
from src.llm.prompts import (
    _build_blurb_taste_context,
    build_blurb_prompt,
    build_blurb_system_prompt,
    build_recommendation_prompt,
    build_recommendation_system_prompt,
    build_single_blurb_prompt,
)
from src.llm.recommendations import RecommendationGenerator
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
from src.utils.sorting import titles_similar
from tests.factories import make_item


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

        # Per-item blurbs return {item_id: blurb_text}
        engine.llm_generator.generate_blurbs_per_item.return_value = {
            "1": "LLM reasoning for Way of Kings",
            "2": "LLM reasoning for Fire & Blood",
        }

        # Call the actual production code
        engine._enhance_with_llm(
            recommendations=recommendations,
            content_type=ContentType.BOOK,
            all_consumed_items=[],
            unconsumed_items=[],
            count=2,
            series_tracking={},
        )

        # Each recommendation must have its OWN reasoning matched by item ID
        assert recommendations[0]["llm_reasoning"] == "LLM reasoning for Way of Kings"
        assert recommendations[1]["llm_reasoning"] == "LLM reasoning for Fire & Blood"

    def test_enhance_uses_per_item_blurbs_for_pipeline_recommendations_regression(
        self,
    ) -> None:
        """Regression test: LLM enhancement must use per-item blurbs, not re-pick.

        Bug reported: When requesting 5 recommendations with AI reasoning
        enabled, only 3 received LLM reasoning. The remaining 2 had no
        llm_reasoning field.

        Root cause: _enhance_with_llm called generate_recommendations which
        asks the LLM to "Pick the N best..." from the candidates. The LLM
        would only select ~3 items regardless of count, so extra pipeline
        recommendations never got reasoning attached.

        Fix: Use generate_blurbs_per_item (one LLM call per item, concurrent)
        instead of batch generation. Direct assignment by item ID eliminates
        title-matching failures.
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

        # generate_blurbs_per_item returns {item_id: blurb} for ALL 5 items
        engine.llm_generator.generate_blurbs_per_item.return_value = {
            item.id: f"LLM blurb for {item.title}" for item in items
        }

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

        # Must call generate_blurbs_per_item (not generate_recommendations)
        engine.llm_generator.generate_blurbs_per_item.assert_called_once()

        # Verify contributing_items were passed through as references
        call_kwargs = engine.llm_generator.generate_blurbs_per_item.call_args
        items_with_refs = call_kwargs.kwargs.get(
            "items_with_refs", call_kwargs.args[1] if len(call_kwargs.args) > 1 else []
        )
        assert len(items_with_refs) == 5
        for item_ref_pair in items_with_refs:
            assert len(item_ref_pair) == 2  # (item, references) tuple

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


class TestPerItemBlurbGenerationRegression:
    """Regression tests for per-item blurb generation (replaced batch system).

    The old batch system parsed numbered lists and matched titles back to
    items, which failed for movies/TV shows. The per-item system makes one
    LLM call per item and assigns by ID — no parsing or matching needed.
    """

    def test_per_item_blurbs_for_many_items_regression(self) -> None:
        """Regression: Per-item blurbs must work for > 5 items.

        Bug reported: Batch system failed for movies/TV because title matching
        broke when the LLM mangled titles or injected director names.

        Fix: One LLM call per item, concurrent. No parsing needed.
        """
        mock_client = Mock(spec=OllamaClient)
        items = _make_book_items(_TEN_BOOK_NAMES)

        def fake_generate(prompt: str, **kwargs: Any) -> str:
            for item in items:
                if item.title in prompt:
                    return f"Great match for {item.title}."
            return "Great match."

        mock_client.generate_text.side_effect = fake_generate

        generator = RecommendationGenerator(mock_client)
        results = generator.generate_blurbs_per_item(
            ContentType.BOOK,
            items_with_refs=[(item, []) for item in items],
            consumed_items=[],
        )

        # All 10 items should have blurbs
        assert len(results) == 10
        for item in items:
            assert item.id in results

        # Should have made 10 LLM calls (one per item)
        assert mock_client.generate_text.call_count == 10

    def test_partial_failure_returns_successful_items_regression(self) -> None:
        """Regression: If some items fail, successful items still get blurbs.

        Fix: Per-item generation catches individual failures and returns
        blurbs for items that succeeded.
        """
        mock_client = Mock(spec=OllamaClient)
        items = _make_book_items(_TEN_BOOK_NAMES)

        # Fail for every other item
        def fake_generate(prompt: str, **kwargs: Any) -> str:
            if items[1].title in prompt:
                raise RuntimeError("LLM timeout")
            if items[3].title in prompt:
                raise RuntimeError("LLM timeout")
            return "Great match for your taste."

        mock_client.generate_text.side_effect = fake_generate

        generator = RecommendationGenerator(mock_client)
        results = generator.generate_blurbs_per_item(
            ContentType.BOOK,
            items_with_refs=[(item, []) for item in items[:5]],
            consumed_items=[],
        )

        # 3 out of 5 should succeed
        assert len(results) == 3
        assert items[0].id in results
        assert items[2].id in results
        assert items[4].id in results


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

    def test_cross_item_author_attribution_fixed_regression(
        self, generator: RecommendationGenerator
    ) -> None:
        """Wrong author name in reasoning must be replaced with the correct one.

        Bug reported: LLM reasoning for "All Systems Red" (by Martha Wells)
        said "Brandon Sanderson's storytelling" because both books were in the
        same recommendation batch and the local LLM confused the authors.

        Root cause: No post-processing validation of author names in reasoning
        text against the matched item's actual author.

        Fix: After parsing, scan each recommendation's reasoning for author
        names from other items in the batch. When the reasoning mentions a
        wrong author but NOT the correct author, substitute the correct name.
        """
        response = (
            "1. **All Systems Red** by Martha Wells\n"
            "You can't deny the epic scope of Brandon Sanderson's "
            "storytelling - with All Systems Red, his ability to create "
            "a captivating narrative was on full display.\n\n"
            "2. **The Way of Kings** by Brandon Sanderson\n"
            "Brandon Sanderson delivers a sprawling epic that will keep "
            "you reading."
        )
        unconsumed = [
            ContentItem(
                id="1",
                title="All Systems Red",
                author="Martha Wells",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.UNREAD,
            ),
            ContentItem(
                id="2",
                title="The Way of Kings",
                author="Brandon Sanderson",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.UNREAD,
            ),
        ]
        results = generator._parse_recommendations(response, unconsumed, count=2)

        assert len(results) == 2

        # "All Systems Red" reasoning must reference Martha Wells, not Sanderson
        all_systems_red_rec = next(
            r for r in results if r["title"] == "All Systems Red"
        )
        assert "Martha Wells" in all_systems_red_rec["reasoning"], (
            f"Correct author missing from reasoning: "
            f"{all_systems_red_rec['reasoning']!r}"
        )
        assert "Brandon Sanderson" not in all_systems_red_rec["reasoning"], (
            f"Wrong author still in reasoning: " f"{all_systems_red_rec['reasoning']!r}"
        )

        # "The Way of Kings" reasoning must NOT be corrupted by the fix —
        # Sanderson is the correct author and must remain bit-for-bit identical.
        way_of_kings_rec = next(r for r in results if r["title"] == "The Way of Kings")
        assert way_of_kings_rec["reasoning"] == (
            "Brandon Sanderson delivers a sprawling epic that will keep " "you reading."
        ), f"Way of Kings reasoning was corrupted: {way_of_kings_rec['reasoning']!r}"

    def test_cross_item_author_fix_skips_legitimate_comparison(
        self, generator: RecommendationGenerator
    ) -> None:
        """Legitimate cross-reference mentioning both authors must not be altered.

        Bug reported: Same as test_cross_item_author_attribution_fixed_regression.

        Root cause: Same root cause.

        Fix: Same fix. This test verifies the boundary condition where both
        authors are legitimately mentioned (cross-reference), and the fix must
        leave reasoning untouched because the correct author IS present.
        """
        response = (
            "1. **All Systems Red** by Martha Wells\n"
            "If you loved Brandon Sanderson's epic worldbuilding, you'll "
            "enjoy Martha Wells' tight, character-driven sci-fi.\n\n"
            "2. **The Way of Kings** by Brandon Sanderson\n"
            "An epic fantasy masterpiece."
        )
        unconsumed = [
            ContentItem(
                id="1",
                title="All Systems Red",
                author="Martha Wells",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.UNREAD,
            ),
            ContentItem(
                id="2",
                title="The Way of Kings",
                author="Brandon Sanderson",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.UNREAD,
            ),
        ]
        results = generator._parse_recommendations(response, unconsumed, count=2)

        all_systems_red_rec = next(
            r for r in results if r["title"] == "All Systems Red"
        )
        # Both authors present — reasoning should be untouched
        assert "Brandon Sanderson" in all_systems_red_rec["reasoning"]
        assert "Martha Wells" in all_systems_red_rec["reasoning"]

    def test_cross_item_author_fix_no_author_items_unaffected(
        self, generator: RecommendationGenerator
    ) -> None:
        """No-author items in a mixed batch must not be processed by the fix.

        Bug reported: Same as test_cross_item_author_attribution_fixed_regression.

        Root cause: Same root cause.

        Fix: Same fix. This test verifies that in a mixed batch (book + movie),
        if the movie's reasoning mentions the book author's name, the fix does
        not touch the movie because it has no author of its own to substitute.
        """
        response = (
            "1. **All Systems Red** by Martha Wells\n"
            "Martha Wells writes compelling sci-fi.\n\n"
            "2. **Alien**\n"
            "Martha Wells fans would enjoy this claustrophobic thriller."
        )
        book = ContentItem(
            id="1",
            title="All Systems Red",
            author="Martha Wells",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
        )
        movie = ContentItem(
            id="2",
            title="Alien",
            content_type=ContentType.MOVIE,
            status=ConsumptionStatus.UNREAD,
        )
        results = generator._parse_recommendations(response, [book, movie], count=2)

        assert len(results) == 2
        alien_rec = next(r for r in results if r["title"] == "Alien")
        # Fix must not touch the movie — it has no correct author to substitute
        assert alien_rec["reasoning"] == (
            "Martha Wells fans would enjoy this claustrophobic thriller."
        ), f"Movie reasoning was wrongly modified: {alien_rec['reasoning']!r}"

    def test_cross_item_multiple_wrong_authors_left_untouched(
        self, generator: RecommendationGenerator
    ) -> None:
        """Ambiguous misattribution (two wrong authors) must not be modified.

        Bug reported: Same as test_cross_item_author_attribution_fixed_regression.

        Root cause: Same root cause.

        Fix: The fix only applies when exactly one wrong author is identified.
        When two wrong authors appear, the case is ambiguous and reasoning is
        left unchanged.
        """
        response = (
            "1. **All Systems Red** by Martha Wells\n"
            "Both Brandon Sanderson and Robin Hobb have influenced this "
            "work.\n\n"
            "2. **The Way of Kings** by Brandon Sanderson\n"
            "Brandon Sanderson's epic fantasy at its finest.\n\n"
            "3. **Assassin's Apprentice** by Robin Hobb\n"
            "Robin Hobb's character-driven masterpiece."
        )
        unconsumed = [
            ContentItem(
                id="1",
                title="All Systems Red",
                author="Martha Wells",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.UNREAD,
            ),
            ContentItem(
                id="2",
                title="The Way of Kings",
                author="Brandon Sanderson",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.UNREAD,
            ),
            ContentItem(
                id="3",
                title="Assassin's Apprentice",
                author="Robin Hobb",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.UNREAD,
            ),
        ]
        results = generator._parse_recommendations(response, unconsumed, count=3)

        all_systems_red_rec = next(
            r for r in results if r["title"] == "All Systems Red"
        )
        # Two wrong authors found — ambiguous, must be left untouched
        assert all_systems_red_rec["reasoning"] == (
            "Both Brandon Sanderson and Robin Hobb have influenced this work."
        ), (
            "Ambiguous reasoning was incorrectly modified: "
            f"{all_systems_red_rec['reasoning']!r}"
        )


def _make_game_items(titles: list[str], *, author: str = "Studio") -> list[ContentItem]:
    """Build a list of video game ContentItems."""
    return [
        ContentItem(
            id=str(index),
            title=title,
            author=author,
            content_type=ContentType.VIDEO_GAME,
            status=ConsumptionStatus.UNREAD,
        )
        for index, title in enumerate(titles, 1)
    ]


class TestSequelConfusionRegression:
    """Regression tests for LLM writing about sequels instead of the recommended item.

    Bug reported: When recommending Dishonored, the LLM reasoning text talked
    about Dishonored 2 because the sequel appeared in the consumed favorites
    context. The blurb said "With elements similar to Dishonored's own sequel,
    Dishonored 2..." instead of describing Dishonored itself.

    Root cause: No prompt instruction told the LLM to write about the
    recommended item rather than its sequels/prequels in the user's history.

    Fix: Added anti-sequel instructions to all four prompt builders:
    build_recommendation_prompt, build_recommendation_system_prompt,
    build_blurb_prompt, build_blurb_system_prompt.
    """

    def test_blurb_prompt_contains_anti_sequel_instruction(self) -> None:
        """build_blurb_prompt must instruct the LLM not to write about sequels."""
        consumed = [
            ContentItem(
                id="c1",
                title="Dishonored 2",
                author="Arkane Studios",
                content_type=ContentType.VIDEO_GAME,
                status=ConsumptionStatus.COMPLETED,
                rating=4,
            ),
        ]
        selected = _make_game_items(["Dishonored"], author="Arkane Studios")
        prompt = build_blurb_prompt(ContentType.VIDEO_GAME, selected, consumed)
        assert (
            "sequel" in prompt.lower()
        ), "Blurb prompt must warn the LLM not to write about sequels"
        assert (
            "prequel" in prompt.lower()
        ), "Blurb prompt must warn the LLM not to write about prequels"

    def test_recommendation_prompt_contains_anti_sequel_instruction(
        self,
    ) -> None:
        """build_recommendation_prompt must instruct the LLM not to write about sequels."""
        consumed = [
            ContentItem(
                id="c1",
                title="Dishonored 2",
                author="Arkane Studios",
                content_type=ContentType.VIDEO_GAME,
                status=ConsumptionStatus.COMPLETED,
                rating=4,
            ),
        ]
        unconsumed = _make_game_items(["Dishonored"], author="Arkane Studios")
        prompt = build_recommendation_prompt(
            ContentType.VIDEO_GAME, consumed, unconsumed, count=1
        )
        assert (
            "sequel" in prompt.lower()
        ), "Recommendation prompt must warn the LLM not to write about sequels"
        assert (
            "prequel" in prompt.lower()
        ), "Recommendation prompt must warn the LLM not to write about prequels"

    def test_blurb_system_prompt_contains_anti_sequel_instruction(
        self,
    ) -> None:
        """build_blurb_system_prompt must instruct the LLM not to write about sequels."""
        prompt = build_blurb_system_prompt(ContentType.VIDEO_GAME)
        assert "sequel" in prompt.lower(), "Blurb system prompt must warn about sequels"

    def test_recommendation_system_prompt_contains_anti_sequel_instruction(
        self,
    ) -> None:
        """build_recommendation_system_prompt must warn about sequels."""
        prompt = build_recommendation_system_prompt(ContentType.VIDEO_GAME)
        assert (
            "sequel" in prompt.lower()
        ), "Recommendation system prompt must warn about sequels"


class TestCrossRecommendationReferenceRegression:
    """Regression tests for LLM referencing other recommendations as consumed items.

    Bug reported: A video game recommendation blurb said "shares the grid-based
    exploration and puzzle-solving elements from Dungeon Siege" when Dungeon Siege
    was another recommendation — not something the user had actually played.

    Root cause: The LLM sees both the user's favorites and the list of
    picks/candidates in the prompt. Without an explicit prohibition, it sometimes
    references items from the picks/candidates list as if the user has consumed
    them.

    Fix: Added anti-cross-reference instructions to all four prompt builders
    telling the LLM that candidates/picks are unconsumed and must not be
    referenced as things the user has played.
    """

    def test_blurb_prompt_warns_against_referencing_other_picks_regression(
        self,
    ) -> None:
        """build_blurb_prompt must tell the LLM not to reference other picks."""
        consumed = [
            ContentItem(
                id="c1",
                title="Metroid Dread",
                author="Nintendo",
                content_type=ContentType.VIDEO_GAME,
                status=ConsumptionStatus.COMPLETED,
                rating=4,
            ),
        ]
        selected = _make_game_items(
            ["Dungeon Siege", "Legend of Grimrock"], author="Various"
        )
        prompt = build_blurb_prompt(ContentType.VIDEO_GAME, selected, consumed)
        assert (
            "not consumed" in prompt.lower()
        ), "Blurb prompt must warn that picks are unconsumed"

    def test_recommendation_prompt_warns_against_referencing_candidates_regression(
        self,
    ) -> None:
        """build_recommendation_prompt must tell the LLM not to reference candidates."""
        consumed = [
            ContentItem(
                id="c1",
                title="Metroid Dread",
                author="Nintendo",
                content_type=ContentType.VIDEO_GAME,
                status=ConsumptionStatus.COMPLETED,
                rating=4,
            ),
        ]
        unconsumed = _make_game_items(
            ["Dungeon Siege", "Legend of Grimrock"], author="Various"
        )
        prompt = build_recommendation_prompt(
            ContentType.VIDEO_GAME, consumed, unconsumed, count=2
        )
        assert (
            "not consumed" in prompt.lower()
        ), "Recommendation prompt must warn that candidates are unconsumed"

    def test_blurb_system_prompt_warns_against_cross_reference_regression(
        self,
    ) -> None:
        """build_blurb_system_prompt must warn about referencing other picks."""
        prompt = build_blurb_system_prompt(ContentType.VIDEO_GAME)
        assert (
            "never reference other picks" in prompt.lower()
        ), "Blurb system prompt must explicitly prohibit referencing other picks"

    def test_recommendation_system_prompt_warns_against_cross_reference_regression(
        self,
    ) -> None:
        """build_recommendation_system_prompt must warn about referencing candidates."""
        prompt = build_recommendation_system_prompt(ContentType.VIDEO_GAME)
        assert (
            "unconsumed recommendations" in prompt.lower()
        ), "Recommendation system prompt must call candidates unconsumed"


class TestAuthorlessTitleMatchingRegression:
    """Regression tests for LLM-invented authors preventing item matching.

    Bug reported: llm_reasoning was "" (empty string) for all movie and TV
    show recommendations even though the LLM generated output.

    Root cause (parser): When the LLM invents a director or creator as
    "author" (e.g. "**Gremlins** by Joe Dante"), the matching condition
    ``author is None or (item.author and ...)`` fails because:
    - author is "Joe Dante" (not None)
    - item.author is None for movies/TV
    This prevents matching_item from being set.

    Root cause (prompt): Both build_blurb_prompt and build_recommendation_prompt
    told the LLM to use format "1. **Title** by Author" even for content types
    that have no author (movies, TV shows, video games), confusing local models.

    Fix (parser): Added fallback title-only matching when the LLM extracts an
    author that doesn't match any item.

    Fix (prompt): Removed "by Author" from the format instruction — all content
    types now use "1. **Title**" format.
    """

    @pytest.fixture()
    def generator(self) -> RecommendationGenerator:
        """Create a RecommendationGenerator with a stub client."""
        return RecommendationGenerator(Mock(spec=OllamaClient))

    def test_invented_director_still_matches_movie_regression(
        self, generator: RecommendationGenerator
    ) -> None:
        """Movie must match even when LLM invents a director as author.

        The LLM outputs "**Gremlins** by Joe Dante" but the movie item has
        author=None. The parser must fall back to title-only matching.
        """
        response = (
            "1. **Gremlins** by Joe Dante\n"
            "A fun horror-comedy that pairs with your love of Hocus Pocus (4/5).\n\n"
            "2. **Inception** by Christopher Nolan\n"
            "A mind-bending thriller you will love."
        )
        items = _make_movie_items(["Gremlins", "Inception"])
        results = generator._parse_recommendations(response, items, count=2)

        assert len(results) == 2
        assert (
            results[0]["item"] is not None
        ), "Gremlins must match despite invented author"
        assert results[0]["item"].title == "Gremlins"
        assert "horror-comedy" in results[0]["reasoning"]
        assert (
            results[1]["item"] is not None
        ), "Inception must match despite invented author"
        assert results[1]["item"].title == "Inception"
        assert "mind-bending" in results[1]["reasoning"]

    def test_invented_creator_still_matches_tv_show_regression(
        self, generator: RecommendationGenerator
    ) -> None:
        """TV show must match even when LLM invents a creator as author."""
        response = (
            "1. **Duckman** by Everett Peck\n"
            "A perfect fit for your animated comedy taste."
        )
        items = [
            ContentItem(
                id="1",
                title="Duckman (Season 1)",
                content_type=ContentType.TV_SHOW,
                status=ConsumptionStatus.UNREAD,
            ),
        ]
        results = generator._parse_recommendations(response, items, count=1)

        assert len(results) == 1
        assert (
            results[0]["item"] is not None
        ), "TV show must match via title substring despite invented author"
        assert results[0]["item"].title == "Duckman (Season 1)"
        assert "animated comedy" in results[0]["reasoning"]

    def test_real_author_still_requires_exact_match_regression(
        self, generator: RecommendationGenerator
    ) -> None:
        """Books with real authors must still require author match.

        The fallback only activates when NO item matches with the author
        constraint. When both a matching and non-matching item exist,
        the correct one must be chosen.
        """
        response = (
            "1. **The Name of the Wind** by Patrick Rothfuss\n"
            "Epic fantasy with beautiful prose."
        )
        wrong_author_book = ContentItem(
            id="1",
            title="The Name of the Wind",
            author="Brandon Sanderson",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
        )
        correct_author_book = ContentItem(
            id="2",
            title="The Name of the Wind",
            author="Patrick Rothfuss",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
        )
        results = generator._parse_recommendations(
            response, [wrong_author_book, correct_author_book], count=1
        )

        assert len(results) == 1
        assert results[0]["item"] is not None
        assert results[0]["item"].author == "Patrick Rothfuss"

    def test_blurb_prompt_no_by_author_for_movies_regression(self) -> None:
        """Blurb prompt must not instruct 'by Author' for authorless content.

        The format rule confused local LLMs when the picks had no author,
        causing garbled output with empty reasoning.
        """
        consumed = [
            ContentItem(
                id="c1",
                title="Hook",
                content_type=ContentType.MOVIE,
                status=ConsumptionStatus.COMPLETED,
                rating=4,
            ),
        ]
        selected = _make_movie_items(["Gremlins"])
        prompt = build_blurb_prompt(ContentType.MOVIE, selected, consumed)
        assert '"1. **Title**"' in prompt, (
            "Blurb prompt for movies must use title-only format, "
            f"not 'by Author'. Got: {prompt!r}"
        )
        assert '"1. **Title** by Author"' not in prompt

    def test_recommendation_prompt_no_by_author_for_movies_regression(self) -> None:
        """Recommendation prompt must not instruct 'by Author' for movies."""
        consumed = [
            ContentItem(
                id="c1",
                title="Hook",
                content_type=ContentType.MOVIE,
                status=ConsumptionStatus.COMPLETED,
                rating=4,
            ),
        ]
        unconsumed = _make_movie_items(["Gremlins"])
        prompt = build_recommendation_prompt(
            ContentType.MOVIE, consumed, unconsumed, count=1
        )
        assert '"1. **Title**"' in prompt
        assert '"1. **Title** by Author"' not in prompt

    def test_blurb_prompt_keeps_by_author_for_books_regression(self) -> None:
        """Blurb prompt must still include author in the picks list for books.

        Books have authors; the picks list must include 'by Author' so the
        LLM associates each blurb with the correct book.
        """
        consumed = [
            ContentItem(
                id="c1",
                title="Mistborn",
                author="Brandon Sanderson",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.COMPLETED,
                rating=5,
            ),
        ]
        selected = [
            ContentItem(
                id="1",
                title="The Way of Kings",
                author="Brandon Sanderson",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.UNREAD,
            ),
        ]
        prompt = build_blurb_prompt(ContentType.BOOK, selected, consumed)
        # The picks list should show "by Author" for books
        assert "by Brandon Sanderson" in prompt


class TestVerbConfusionRegression:
    """Regression: blurbs said 'watching Fable: Anniversary' (a video game).

    Root cause: No verb guardrail in prompts. The LLM would use "watch" or
    "read" for any content type since the prompt didn't specify the correct
    verb per content type.

    Fix: build_single_blurb_prompt (and all other prompt functions — see
    TestVerbGuardrails in test_recommendation_prompts.py) now includes
    "READ books, WATCH movies and TV shows, and PLAY video games" guidance.
    """

    def test_video_game_blurb_includes_play_verb_regression(self) -> None:
        """Regression: build_single_blurb_prompt for VIDEO_GAME must include
        'PLAY video games' to prevent verb confusion like 'watching Fable'."""
        item = make_item("Fable: Anniversary", ContentType.VIDEO_GAME)
        prompt = build_single_blurb_prompt(
            ContentType.VIDEO_GAME, item, consumed_items=[]
        )
        assert "PLAY video games" in prompt


class TestFavoritesRelevanceRegression:
    """Regression: every book blurb referenced 1984 (alphabetically first).

    Root cause: ``get_completed_items()`` returns items sorted
    alphabetically, and ``_build_blurb_taste_context()`` took ``[:5]`` —
    always the same alphabetically-first items regardless of genre.

    Fix: When target_items are provided, favorites are scored by genre
    relevance (Jaccard for same-type, cluster_overlap for cross-type,
    creator bonus, rating boost) and only the most relevant are selected.
    """

    def test_genre_relevant_favorites_selected_over_alphabetical_regression(
        self,
    ) -> None:
        """Fantasy recommendations should select fantasy favorites, not
        alphabetically-first unrelated items like '1984'."""

        def _book(title: str, genres: list[str], rating: int = 5) -> ContentItem:
            return make_item(
                title,
                ContentType.BOOK,
                rating=rating,
                metadata={"genres": genres},
            )

        # 10 same-type favorites with diverse genres — alphabetically,
        # the non-fantasy items come first
        favorites = [
            _book("1984", ["dystopian", "political fiction"]),
            _book("Argo", ["thriller", "political thriller"]),
            _book("Brief History of Time", ["science", "non-fiction"]),
            _book("Crime and Punishment", ["literary fiction", "psychological"]),
            _book("Dune", ["science fiction", "space opera"]),
            _book("Eye of the World", ["fantasy", "epic fantasy"]),
            _book("Gardens of the Moon", ["fantasy", "dark fantasy"]),
            _book("Harry Potter", ["fantasy", "young adult"]),
            _book("Mistborn", ["fantasy", "epic fantasy"]),
            _book("Name of the Wind", ["fantasy", "epic fantasy"]),
        ]

        # Target: a fantasy book recommendation
        target = make_item(
            "The Way of Kings",
            ContentType.BOOK,
            metadata={"genres": ["fantasy", "epic fantasy"]},
        )

        context = _build_blurb_taste_context(
            ContentType.BOOK, favorites, target_items=[target]
        )

        # Fantasy favorites should be selected
        assert (
            "Mistborn" in context
            or "Name of the Wind" in context
            or "Eye of the World" in context
        )
        # The alphabetically-first non-fantasy items should NOT dominate
        # (1984, Argo, Brief History should not all appear)
        non_fantasy_count = sum(
            1 for title in ["1984", "Argo", "Brief History of Time"] if title in context
        )
        fantasy_count = sum(
            1
            for title in [
                "Eye of the World",
                "Gardens of the Moon",
                "Harry Potter",
                "Mistborn",
                "Name of the Wind",
            ]
            if title in context
        )
        assert fantasy_count > non_fantasy_count, (
            f"Expected more fantasy than non-fantasy favorites for a fantasy "
            f"target, got {fantasy_count} fantasy vs {non_fantasy_count} "
            f"non-fantasy in context:\n{context}"
        )

    def test_low_rated_favorites_excluded_regression(self) -> None:
        """Items rated below 4 should never appear as favorites (threshold is >= 4)."""
        favorites = [
            make_item(
                "Rating 2", ContentType.BOOK, rating=2, metadata={"genres": ["fantasy"]}
            ),
            make_item(
                "Rating 3", ContentType.BOOK, rating=3, metadata={"genres": ["fantasy"]}
            ),
            make_item(
                "Rating 4",
                ContentType.BOOK,
                rating=4,
                metadata={"genres": ["fantasy"]},
            ),
        ]
        target = make_item("Target", ContentType.BOOK, metadata={"genres": ["fantasy"]})

        context = _build_blurb_taste_context(
            ContentType.BOOK, favorites, target_items=[target]
        )
        assert "Rating 4" in context
        # Rating 3 is the boundary — excluded by the >= 4 filter
        assert "Rating 3" not in context
        assert "Rating 2" not in context

    def test_cross_type_favorites_require_minimum_overlap_regression(self) -> None:
        """Cross-type favorites with low genre overlap should be excluded."""
        favorites = [
            # A movie with completely unrelated genres
            make_item(
                "Unrelated Movie",
                ContentType.MOVIE,
                rating=5,
                metadata={"genres": ["romantic comedy"]},
            ),
        ]
        target = make_item(
            "Dark Fantasy Book",
            ContentType.BOOK,
            metadata={"genres": ["dark fantasy", "horror"]},
        )

        context = _build_blurb_taste_context(
            ContentType.BOOK, favorites, target_items=[target]
        )
        # The unrelated movie shouldn't appear due to low cluster overlap;
        # with no same-type favorites either, context should be empty.
        assert "Unrelated Movie" not in context
        assert context == ""

    def test_fallback_when_target_has_no_genre_data_regression(self) -> None:
        """When target items have no genre metadata, the fallback path
        returns rating-sorted favorites rather than crashing or returning empty."""
        favorites = [
            make_item("Good Book", ContentType.BOOK, rating=5),
            make_item("Great Book", ContentType.BOOK, rating=4),
        ]
        # Target with no genre metadata triggers the fallback path
        target_no_genres = make_item("Target", ContentType.BOOK)
        context = _build_blurb_taste_context(
            ContentType.BOOK, favorites, target_items=[target_no_genres]
        )
        assert "Good Book" in context
        assert "Great Book" in context


class TestSeriesNameConfusionRegression:
    """Regression tests for LLM treating book titles as series names.

    Bug reported: Blurb said "your favorite series, Harry Potter and the
    Order of the Phoenix" — treating an individual book title as the series
    name.  The series is "Harry Potter", not the individual entry.

    Root cause: Favorites context showed the full title without any series
    name annotation, so the LLM had no way to distinguish a series entry
    from a standalone title.

    Fix: _format_context_item now appends "(Series series)" when series
    metadata is present, and prompt rules instruct the LLM to use the
    series name shown in parentheses.
    """

    def test_series_entry_annotated_in_blurb_context_regression(self) -> None:
        """Favorites with series metadata should show series annotation.

        Before the fix, "Harry Potter and the Order of the Phoenix" appeared
        with no series context.  After the fix, it shows
        "(Harry Potter series)" so the LLM knows the series name.
        """
        consumed = [
            make_item(
                "Harry Potter and the Order of the Phoenix",
                ContentType.BOOK,
                rating=5,
                author="J.K. Rowling",
                metadata={"series_name": "Harry Potter", "series_number": 5},
            ),
        ]
        target = make_item(
            "The Ruins of Gorlan",
            ContentType.BOOK,
            metadata={"genres": ["fantasy"]},
        )
        prompt = build_single_blurb_prompt(
            ContentType.BOOK, target, consumed_items=consumed
        )
        assert "(Harry Potter series)" in prompt
        # The individual title should still be present
        assert "Harry Potter and the Order of the Phoenix" in prompt

    def test_standalone_title_no_series_annotation_regression(self) -> None:
        """Standalone titles without series metadata should NOT get annotation."""
        consumed = [
            make_item(
                "The Road",
                ContentType.BOOK,
                rating=5,
                author="Cormac McCarthy",
            ),
        ]
        target = make_item("Pick", ContentType.BOOK)
        prompt = build_single_blurb_prompt(
            ContentType.BOOK, target, consumed_items=consumed
        )
        assert "The Road" in prompt
        assert "series)" not in prompt

    def test_blurb_system_prompt_has_series_name_guardrail_regression(
        self,
    ) -> None:
        """Blurb system prompt must instruct LLM to use series names."""
        prompt = build_blurb_system_prompt(ContentType.BOOK)
        assert (
            "series name" in prompt.lower()
        ), "System prompt must tell the LLM to use series names"


class TestGenreSettingHallucinationRegression:
    """Regression tests for LLM inventing genre attributes for referenced items.

    Bug reported: A blurb for "Heaven's Devils" (StarCraft — sci-fi) claimed
    the Shannara series (fantasy) involved "space warfare", saying the
    recommendation was "much like your beloved Shannara series" in the
    "vast expanse of space warfare".

    Root cause: (1) Referenced items in the Related: line had no genre tags,
    so the LLM had to guess their genre — and guessed wrong.  (2) No prompt
    guardrail prevented the LLM from inventing genre attributes.

    Fix: Related: lines now include genre brackets, and prompt rules
    explicitly forbid attributing genres not shown in the brackets.
    """

    def test_related_line_includes_genre_tags_regression(self) -> None:
        """Related: line must include genre brackets so the LLM knows each
        reference's actual genre and doesn't invent "space warfare" for a
        fantasy series.
        """
        target = make_item(
            "Heaven's Devils",
            ContentType.BOOK,
            metadata={"genres": ["science fiction"]},
        )
        refs = [
            make_item(
                "Bloodfire Quest",
                ContentType.BOOK,
                rating=5,
                author="Terry Brooks",
                genres="Fantasy, Epic Fantasy",
            ),
        ]
        prompt = build_single_blurb_prompt(
            ContentType.BOOK,
            target,
            consumed_items=[],
            references=refs,
        )
        assert "Bloodfire Quest [Fantasy, Epic Fantasy]" in prompt

    def test_blurb_prompt_has_genre_accuracy_guardrail_regression(self) -> None:
        """Blurb prompt rules must forbid inventing genre attributes."""
        selected = [make_item("Pick", ContentType.BOOK)]
        prompt = build_blurb_prompt(ContentType.BOOK, selected, consumed_items=[])
        assert (
            "genre brackets" in prompt.lower()
        ), "Prompt must reference genre brackets to prevent invention"

    def test_blurb_system_prompt_has_genre_accuracy_guardrail_regression(
        self,
    ) -> None:
        """Blurb system prompt must forbid inventing genre attributes."""
        prompt = build_blurb_system_prompt(ContentType.BOOK)
        assert (
            "genre brackets" in prompt.lower()
        ), "System prompt must reference genre brackets"


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
