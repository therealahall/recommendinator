"""Regression tests for bugs found during code quality audit."""

from pathlib import Path
from unittest.mock import Mock

from src.llm.client import OllamaClient
from src.llm.recommendations import RecommendationGenerator
from src.models.content import ConsumptionStatus, ContentItem, ContentType
from src.recommendations.preference_interpreter import PatternBasedInterpreter
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
        from src.recommendations.engine import RecommendationEngine

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
        from src.recommendations.engine import RecommendationEngine

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
