"""Tests for recommendation engine cross-content-type recommendations."""

from unittest.mock import Mock

import pytest

from src.llm.embeddings import EmbeddingGenerator
from src.models.content import (
    ConsumptionStatus,
    ContentItem,
    ContentType,
    get_enum_value,
)
from src.models.user_preferences import UserPreferenceConfig
from src.recommendations.engine import RecommendationEngine
from src.storage.manager import StorageManager


@pytest.fixture
def mock_storage():
    """Create a mock storage manager."""
    storage = Mock(spec=StorageManager)
    storage.vector_db = Mock()
    storage.vector_db.has_embedding = Mock(return_value=False)
    storage.vector_db.get_embedding = Mock(return_value=None)
    return storage


@pytest.fixture
def mock_embedding_gen():
    """Create a mock embedding generator."""
    embedding_gen = Mock(spec=EmbeddingGenerator)
    embedding_gen.generate_content_embedding = Mock(return_value=[0.1] * 768)
    return embedding_gen


@pytest.fixture
def engine(mock_storage, mock_embedding_gen):
    """Create a recommendation engine with mocked dependencies (AI mode)."""
    return RecommendationEngine(
        storage_manager=mock_storage,
        embedding_generator=mock_embedding_gen,
        recommendation_generator=None,
        min_rating=4,
    )


@pytest.fixture
def non_ai_engine(mock_storage):
    """Create a recommendation engine without embedding generator (non-AI mode)."""
    return RecommendationEngine(
        storage_manager=mock_storage,
        embedding_generator=None,
        recommendation_generator=None,
        min_rating=4,
    )


# ---------------------------------------------------------------------------
# Existing tests (backward-compatible — engine with embedding_generator)
# ---------------------------------------------------------------------------


def test_cross_content_type_preferences(engine, mock_storage, mock_embedding_gen):
    """Test that preferences are extracted from all content types."""
    sci_fi_book = ContentItem(
        id="1",
        title="Dune",
        author="Frank Herbert",
        content_type=ContentType.BOOK,
        status=ConsumptionStatus.COMPLETED,
        rating=5,
        metadata={"genre": "Science Fiction"},
    )

    sci_fi_game = ContentItem(
        id="2",
        title="Mass Effect",
        content_type=ContentType.VIDEO_GAME,
        status=ConsumptionStatus.COMPLETED,
        rating=5,
        metadata={"genres": ["Action", "RPG", "Science Fiction"]},
    )

    sci_fi_tv = ContentItem(
        id="3",
        title="The Expanse",
        content_type=ContentType.TV_SHOW,
        status=ConsumptionStatus.COMPLETED,
        rating=4,
        metadata={"genre": "Science Fiction"},
    )

    unconsumed_game = ContentItem(
        id="4",
        title="Mass Effect 2",
        content_type=ContentType.VIDEO_GAME,
        status=ConsumptionStatus.UNREAD,
        metadata={"genres": ["Action", "RPG", "Science Fiction"]},
    )

    mock_storage.get_completed_items = Mock(
        side_effect=lambda content_type=None, **kwargs: (
            [sci_fi_book, sci_fi_game, sci_fi_tv]
            if content_type is None
            else ([sci_fi_game] if content_type == ContentType.VIDEO_GAME else [])
        )
    )

    mock_storage.get_unconsumed_items = Mock(return_value=[unconsumed_game])

    mock_storage.search_similar = Mock(
        return_value=[
            {
                "content_id": "4",
                "score": 0.85,
                "metadata": {"title": "Mass Effect 2"},
            }
        ]
    )

    mock_storage.get_content_items = Mock(return_value=[unconsumed_game])
    mock_storage.vector_db.has_embedding = Mock(return_value=False)

    engine.generate_recommendations(content_type=ContentType.VIDEO_GAME, count=1)

    assert mock_storage.get_completed_items.call_count >= 1
    call_args = mock_storage.get_completed_items.call_args_list
    assert any(
        call.kwargs.get("content_type") is None for call in call_args
    ), "Should fetch consumed items from all content types"


def test_cross_content_type_similarity(engine, mock_storage, mock_embedding_gen):
    """Test that similarity search uses reference items from all content types."""
    sci_fi_book = ContentItem(
        id="1",
        title="Dune",
        author="Frank Herbert",
        content_type=ContentType.BOOK,
        status=ConsumptionStatus.COMPLETED,
        rating=5,
        metadata={"genre": "Science Fiction"},
    )

    sci_fi_game = ContentItem(
        id="2",
        title="Mass Effect",
        content_type=ContentType.VIDEO_GAME,
        status=ConsumptionStatus.COMPLETED,
        rating=5,
        metadata={"genres": ["Science Fiction"]},
    )

    unconsumed_tv = ContentItem(
        id="3",
        title="The Expanse",
        content_type=ContentType.TV_SHOW,
        status=ConsumptionStatus.UNREAD,
        metadata={"genre": "Science Fiction"},
    )

    mock_storage.get_completed_items = Mock(
        side_effect=lambda content_type=None, **kwargs: (
            [sci_fi_book, sci_fi_game] if content_type is None else []
        )
    )
    mock_storage.get_unconsumed_items = Mock(return_value=[unconsumed_tv])
    mock_storage.search_similar = Mock(
        return_value=[
            {
                "content_id": "3",
                "score": 0.9,
                "metadata": {"title": "The Expanse"},
            }
        ]
    )
    mock_storage.get_content_items = Mock(return_value=[unconsumed_tv])
    mock_storage.vector_db.has_embedding = Mock(return_value=False)

    engine.generate_recommendations(content_type=ContentType.TV_SHOW, count=1)

    assert mock_storage.search_similar.called
    assert mock_embedding_gen.generate_content_embedding.call_count >= 1


def test_cold_start_with_other_content_types(engine, mock_storage):
    """Test cold start when requesting type has no items but other types do."""
    sci_fi_book = ContentItem(
        id="1",
        title="Dune",
        content_type=ContentType.BOOK,
        status=ConsumptionStatus.COMPLETED,
        rating=5,
    )

    mock_storage.get_completed_items = Mock(
        side_effect=lambda content_type=None, **kwargs: (
            [sci_fi_book] if content_type is None else []
        )
    )
    mock_storage.get_unconsumed_items = Mock(return_value=[])

    recommendations = engine.generate_recommendations(
        content_type=ContentType.VIDEO_GAME, count=5
    )

    assert recommendations == []


def test_reasoning_mentions_cross_content_type(
    engine, mock_storage, mock_embedding_gen
):
    """Test that reasoning mentions cross-content-type preferences."""
    sci_fi_book = ContentItem(
        id="1",
        title="Dune",
        content_type=ContentType.BOOK,
        status=ConsumptionStatus.COMPLETED,
        rating=5,
        metadata={"genre": "Science Fiction"},
    )

    unconsumed_game = ContentItem(
        id="2",
        title="Mass Effect",
        content_type=ContentType.VIDEO_GAME,
        status=ConsumptionStatus.UNREAD,
        metadata={"genres": ["Science Fiction"]},
    )

    mock_storage.get_completed_items = Mock(
        side_effect=lambda content_type=None, **kwargs: (
            [sci_fi_book] if content_type is None else []
        )
    )
    mock_storage.get_unconsumed_items = Mock(return_value=[unconsumed_game])
    mock_storage.search_similar = Mock(
        return_value=[
            {
                "content_id": "2",
                "score": 0.8,
                "metadata": {"title": "Mass Effect"},
            }
        ]
    )
    mock_storage.get_content_items = Mock(return_value=[unconsumed_game])
    mock_storage.vector_db.has_embedding = Mock(return_value=False)

    recommendations = engine.generate_recommendations(
        content_type=ContentType.VIDEO_GAME, count=1
    )

    if recommendations:
        reasoning = recommendations[0].get("reasoning", "")
        # Reasoning should mention the specific item that influenced the recommendation
        assert "dune" in reasoning.lower() or "preferences" in reasoning.lower()


# ---------------------------------------------------------------------------
# Non-AI engine tests (Phase 3)
# ---------------------------------------------------------------------------


class TestNonAIEngine:
    """Tests for the recommendation engine operating without embeddings."""

    def test_non_ai_engine_produces_recommendations(self, non_ai_engine, mock_storage):
        """Engine with embedding_generator=None still produces ranked recs."""
        consumed_book = ContentItem(
            id="1",
            title="Dune",
            author="Frank Herbert",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            rating=5,
            metadata={"genre": "Science Fiction"},
        )

        unconsumed_book = ContentItem(
            id="2",
            title="Hyperion",
            author="Dan Simmons",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
            metadata={"genre": "Science Fiction"},
        )

        mock_storage.get_completed_items = Mock(
            side_effect=lambda content_type=None, **kwargs: (
                [consumed_book] if content_type is None else [consumed_book]
            )
        )
        mock_storage.get_unconsumed_items = Mock(return_value=[unconsumed_book])

        recommendations = non_ai_engine.generate_recommendations(
            content_type=ContentType.BOOK, count=5
        )

        assert len(recommendations) >= 1
        assert recommendations[0]["item"].title == "Hyperion"

    def test_genre_preferences_boost_matching_candidates(
        self, non_ai_engine, mock_storage
    ):
        """Items with preferred genres should rank higher."""
        consumed = ContentItem(
            id="1",
            title="Dune",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            rating=5,
            metadata={"genre": "Science Fiction"},
        )

        good_match = ContentItem(
            id="2",
            title="Hyperion",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
            metadata={"genre": "Science Fiction"},
        )

        poor_match = ContentItem(
            id="3",
            title="Romance Novel",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
            metadata={"genre": "Romance"},
        )

        mock_storage.get_completed_items = Mock(
            side_effect=lambda content_type=None, **kwargs: (
                [consumed] if content_type is None else [consumed]
            )
        )
        mock_storage.get_unconsumed_items = Mock(return_value=[poor_match, good_match])

        recommendations = non_ai_engine.generate_recommendations(
            content_type=ContentType.BOOK, count=2
        )

        assert len(recommendations) >= 2
        titles = [rec["item"].title for rec in recommendations]
        # Sci-fi match should be ranked first
        assert titles[0] == "Hyperion"

    def test_creator_matching_across_types(self, non_ai_engine, mock_storage):
        """Creator matching should work across content types."""
        consumed_book = ContentItem(
            id="1",
            title="The Shining",
            author="Stephen King",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            rating=5,
            metadata={"genre": "Horror"},
        )

        unconsumed_by_same_author = ContentItem(
            id="2",
            title="It",
            author="Stephen King",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
            metadata={"genre": "Horror"},
        )

        unconsumed_other = ContentItem(
            id="3",
            title="Random Book",
            author="Unknown Author",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
            metadata={"genre": "Romance"},
        )

        mock_storage.get_completed_items = Mock(
            side_effect=lambda content_type=None, **kwargs: (
                [consumed_book] if content_type is None else [consumed_book]
            )
        )
        mock_storage.get_unconsumed_items = Mock(
            return_value=[unconsumed_other, unconsumed_by_same_author]
        )

        recommendations = non_ai_engine.generate_recommendations(
            content_type=ContentType.BOOK, count=2
        )

        assert len(recommendations) >= 2
        # Stephen King book should be ranked higher
        assert recommendations[0]["item"].title == "It"

    def test_cold_start_returns_empty(self, non_ai_engine, mock_storage):
        """Non-AI engine should handle cold start gracefully."""
        mock_storage.get_completed_items = Mock(return_value=[])

        recommendations = non_ai_engine.generate_recommendations(
            content_type=ContentType.BOOK, count=5
        )

        assert recommendations == []

    def test_no_unconsumed_items_returns_empty(self, non_ai_engine, mock_storage):
        """Non-AI engine returns empty when nothing to recommend."""
        consumed = ContentItem(
            id="1",
            title="Dune",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            rating=5,
        )

        mock_storage.get_completed_items = Mock(
            side_effect=lambda content_type=None, **kwargs: (
                [consumed] if content_type is None else [consumed]
            )
        )
        mock_storage.get_unconsumed_items = Mock(return_value=[])

        recommendations = non_ai_engine.generate_recommendations(
            content_type=ContentType.BOOK, count=5
        )

        assert recommendations == []

    def test_end_to_end_scoring_and_sorting(self, non_ai_engine, mock_storage):
        """End-to-end: consumed items with genres -> unconsumed -> scored + sorted."""
        consumed_items = [
            ContentItem(
                id="c1",
                title="Foundation",
                author="Isaac Asimov",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.COMPLETED,
                rating=5,
                metadata={"genre": "Science Fiction"},
            ),
            ContentItem(
                id="c2",
                title="Neuromancer",
                author="William Gibson",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.COMPLETED,
                rating=5,
                metadata={"genre": "Science Fiction"},
            ),
        ]

        unconsumed_items = [
            ContentItem(
                id="u1",
                title="Left Hand of Darkness",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.UNREAD,
                metadata={"genre": "Science Fiction"},
            ),
            ContentItem(
                id="u2",
                title="Pride and Prejudice",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.UNREAD,
                metadata={"genre": "Romance"},
            ),
            ContentItem(
                id="u3",
                title="Dracula",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.UNREAD,
                metadata={"genre": "Horror"},
            ),
        ]

        mock_storage.get_completed_items = Mock(
            side_effect=lambda content_type=None, **kwargs: (
                consumed_items if content_type is None else consumed_items
            )
        )
        mock_storage.get_unconsumed_items = Mock(return_value=unconsumed_items)

        recommendations = non_ai_engine.generate_recommendations(
            content_type=ContentType.BOOK, count=3
        )

        assert len(recommendations) == 3
        # Sci-fi should be first since all consumed items are sci-fi
        assert recommendations[0]["item"].title == "Left Hand of Darkness"
        # All recs should have scores
        for rec in recommendations:
            assert "score" in rec
            assert "reasoning" in rec


# ---------------------------------------------------------------------------
# User preference config override tests (Phase 5)
# ---------------------------------------------------------------------------


class TestUserPreferenceOverride:
    """Tests for per-user preference config overrides."""

    def test_generate_recommendations_without_user_config(
        self, non_ai_engine, mock_storage
    ):
        """Engine works normally when no user_preference_config is passed."""
        consumed = ContentItem(
            id="1",
            title="Dune",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            rating=5,
            metadata={"genre": "Science Fiction"},
        )
        unconsumed = ContentItem(
            id="2",
            title="Hyperion",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
            metadata={"genre": "Science Fiction"},
        )

        mock_storage.get_completed_items = Mock(
            side_effect=lambda content_type=None, **kwargs: (
                [consumed] if content_type is None else [consumed]
            )
        )
        mock_storage.get_unconsumed_items = Mock(return_value=[unconsumed])

        recommendations = non_ai_engine.generate_recommendations(
            content_type=ContentType.BOOK,
            count=1,
            user_preference_config=None,
        )
        assert len(recommendations) >= 1

    def test_generate_recommendations_with_user_config(
        self, non_ai_engine, mock_storage
    ):
        """Engine uses overridden scorer weights when user config is provided."""
        consumed = ContentItem(
            id="1",
            title="Dune",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            rating=5,
            metadata={"genre": "Science Fiction"},
        )
        unconsumed = ContentItem(
            id="2",
            title="Hyperion",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
            metadata={"genre": "Science Fiction"},
        )

        mock_storage.get_completed_items = Mock(
            side_effect=lambda content_type=None, **kwargs: (
                [consumed] if content_type is None else [consumed]
            )
        )
        mock_storage.get_unconsumed_items = Mock(return_value=[unconsumed])

        user_config = UserPreferenceConfig(
            scorer_weights={"genre_match": 10.0, "creator_match": 0.0}
        )
        recommendations = non_ai_engine.generate_recommendations(
            content_type=ContentType.BOOK,
            count=1,
            user_preference_config=user_config,
        )
        assert len(recommendations) >= 1


# ---------------------------------------------------------------------------
# Custom rules integration tests (Phase 7)
# ---------------------------------------------------------------------------


class TestCustomRulesIntegration:
    """Tests for custom rules integration in the recommendation engine."""

    def test_custom_rules_boost_matching_genre(self, non_ai_engine, mock_storage):
        """Custom rule \"prefer sci-fi\" should boost sci-fi items."""
        consumed = ContentItem(
            id="1",
            title="Old Book",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            rating=4,
            metadata={"genre": "Fantasy"},
        )
        scifi_item = ContentItem(
            id="2",
            title="Space Adventure",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
            metadata={"genre": "science fiction"},
        )
        fantasy_item = ContentItem(
            id="3",
            title="Dragon Tale",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
            metadata={"genre": "fantasy"},
        )

        mock_storage.get_completed_items = Mock(
            side_effect=lambda content_type=None, **kwargs: [consumed]
        )
        mock_storage.get_unconsumed_items = Mock(
            return_value=[scifi_item, fantasy_item]
        )

        user_config = UserPreferenceConfig(custom_rules=["prefer sci-fi"])
        recommendations = non_ai_engine.generate_recommendations(
            content_type=ContentType.BOOK,
            count=2,
            user_preference_config=user_config,
        )
        # The sci-fi item should be ranked higher due to the custom rule
        assert len(recommendations) == 2
        titles = [rec["item"].title for rec in recommendations]
        assert "Space Adventure" in titles

    def test_custom_rules_penalize_genre(self, non_ai_engine, mock_storage):
        """Custom rule \"avoid horror\" should penalize horror items."""
        consumed = ContentItem(
            id="1",
            title="Old Book",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            rating=4,
            metadata={"genre": "Drama"},  # Neutral genre, not horror
        )
        horror_item = ContentItem(
            id="2",
            title="Scary Story",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
            metadata={"genre": "horror"},
        )
        comedy_item = ContentItem(
            id="3",
            title="Funny Book",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
            metadata={"genre": "comedy"},
        )

        mock_storage.get_completed_items = Mock(
            side_effect=lambda content_type=None, **kwargs: [consumed]
        )
        mock_storage.get_unconsumed_items = Mock(
            return_value=[horror_item, comedy_item]
        )

        user_config = UserPreferenceConfig(custom_rules=["avoid horror"])
        recommendations = non_ai_engine.generate_recommendations(
            content_type=ContentType.BOOK,
            count=2,
            user_preference_config=user_config,
        )
        # With horror penalized, comedy should rank first
        assert len(recommendations) == 2
        assert recommendations[0]["item"].title == "Funny Book"

    def test_multiple_custom_rules(self, non_ai_engine, mock_storage):
        """Multiple custom rules are all applied."""
        consumed = ContentItem(
            id="1",
            title="Consumed",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            rating=4,
            metadata={"genre": "Drama"},
        )
        items = [
            ContentItem(
                id="2",
                title="Horror Book",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.UNREAD,
                metadata={"genre": "horror"},
            ),
            ContentItem(
                id="3",
                title="Comedy Book",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.UNREAD,
                metadata={"genre": "comedy"},
            ),
            ContentItem(
                id="4",
                title="Sci-Fi Book",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.UNREAD,
                metadata={"genre": "science fiction"},
            ),
        ]

        mock_storage.get_completed_items = Mock(
            side_effect=lambda content_type=None, **kwargs: [consumed]
        )
        mock_storage.get_unconsumed_items = Mock(return_value=items)

        user_config = UserPreferenceConfig(
            custom_rules=["avoid horror", "prefer sci-fi"]
        )
        recommendations = non_ai_engine.generate_recommendations(
            content_type=ContentType.BOOK,
            count=3,
            user_preference_config=user_config,
        )
        titles = [rec["item"].title for rec in recommendations]
        # Sci-fi should be boosted (first), horror should be penalized (last)
        assert titles[0] == "Sci-Fi Book"
        assert titles[-1] == "Horror Book"

    def test_custom_rules_empty_does_not_add_scorer(self, non_ai_engine, mock_storage):
        """Empty custom_rules list should not affect scoring."""
        consumed = ContentItem(
            id="1",
            title="Consumed",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            rating=4,
            metadata={"genre": "Drama"},
        )
        unconsumed = ContentItem(
            id="2",
            title="Unconsumed",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
            metadata={"genre": "drama"},
        )

        mock_storage.get_completed_items = Mock(
            side_effect=lambda content_type=None, **kwargs: [consumed]
        )
        mock_storage.get_unconsumed_items = Mock(return_value=[unconsumed])

        user_config = UserPreferenceConfig(custom_rules=[])
        recommendations = non_ai_engine.generate_recommendations(
            content_type=ContentType.BOOK,
            count=1,
            user_preference_config=user_config,
        )
        assert len(recommendations) == 1


class TestSeriesOrderingRegression:
    """Regression tests for series ordering bugs.

    These tests document and prevent regressions of bugs found in production.
    """

    def test_series_book_2_not_recommended_when_book_1_unread_regression(
        self, non_ai_engine, mock_storage
    ):
        """Regression test: Book #2 should not be recommended when book #1 exists but is unread.

        Bug reported: "The Black Unicorn (Magic Kingdom of Landover #2)" was
        recommended as #16 when the user had not read book #1.

        Root cause: The engine fetched only 100 unconsumed items sorted by title
        (ignoring articles). "The Black Unicorn" sorted as "Black Unicorn" (B)
        was included, but "Magic Kingdom for Sale—Sold! #1" sorted as "Magic..."
        (M) was position 171, outside the 100-item limit. The series filter
        couldn't find book #1 in the limited list and incorrectly assumed it
        didn't exist.

        Fix: Removed the 100-item limit when fetching unconsumed items for
        recommendations, ensuring all items are available for series checking.
        """
        consumed = ContentItem(
            id="0",
            title="Some Other Book",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            rating=4,
            metadata={"genre": "Fantasy"},
        )

        # Simulate the Landover series scenario - book #1 and #2 both unread
        # With article-stripping sort, "The Black Unicorn" (B) comes before
        # "Magic Kingdom for Sale—Sold!" (M)
        book_1 = ContentItem(
            id="1",
            title="Magic Kingdom for Sale—Sold! (Magic Kingdom of Landover #1)",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
            metadata={"genre": "Fantasy"},
        )
        book_2 = ContentItem(
            id="2",
            title="The Black Unicorn (Magic Kingdom of Landover #2)",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
            metadata={"genre": "Fantasy"},
        )

        # Both books must be in the unconsumed list for correct series filtering
        unconsumed_items = [book_2, book_1]  # Intentionally out of order

        mock_storage.get_completed_items = Mock(
            side_effect=lambda content_type=None, **kwargs: [consumed]
        )
        mock_storage.get_unconsumed_items = Mock(return_value=unconsumed_items)

        recommendations = non_ai_engine.generate_recommendations(
            content_type=ContentType.BOOK,
            count=5,
        )

        recommended_titles = [rec["item"].title for rec in recommendations]

        # Book #1 SHOULD be recommended (first in series, unstarted)
        assert any(
            "Magic Kingdom for Sale" in title for title in recommended_titles
        ), "Book #1 should be recommended"

        # Book #2 should NOT be recommended (book #1 exists but not read)
        assert not any(
            "Black Unicorn" in title for title in recommended_titles
        ), "Book #2 should NOT be recommended when book #1 is unread"

    def test_series_filtering_with_all_items_available(
        self, non_ai_engine, mock_storage
    ):
        """Verify series filtering works correctly when all series items are available.

        This tests the fix ensuring the engine passes all unconsumed items to
        the series filter, not a limited subset.
        """
        consumed = ContentItem(
            id="0",
            title="Consumed Book",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            rating=5,
            metadata={"genre": "Sci-Fi"},
        )

        # Create a series where items would sort in wrong order alphabetically
        # "The Zebra Adventure #1" -> sorts as "Zebra..." (Z)
        # "An Amazing Sequel #2" -> sorts as "Amazing..." (A)
        book_1 = ContentItem(
            id="1",
            title="The Zebra Adventure (Test Series #1)",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
            metadata={"genre": "Sci-Fi"},
        )
        book_2 = ContentItem(
            id="2",
            title="An Amazing Sequel (Test Series #2)",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
            metadata={"genre": "Sci-Fi"},
        )

        # After article stripping: "Amazing Sequel" (A) before "Zebra Adventure" (Z)
        unconsumed_items = [book_2, book_1]  # book_2 sorts first alphabetically

        mock_storage.get_completed_items = Mock(
            side_effect=lambda content_type=None, **kwargs: [consumed]
        )
        mock_storage.get_unconsumed_items = Mock(return_value=unconsumed_items)

        recommendations = non_ai_engine.generate_recommendations(
            content_type=ContentType.BOOK,
            count=5,
        )

        recommended_titles = [rec["item"].title for rec in recommendations]

        # Only book #1 should be recommended, not book #2
        assert any(
            "Zebra Adventure" in title for title in recommended_titles
        ), "Book #1 (Zebra Adventure) should be recommended"
        assert not any(
            "Amazing Sequel" in title for title in recommended_titles
        ), "Book #2 (Amazing Sequel) should NOT be recommended"


# ---------------------------------------------------------------------------
# Ignored Items Tests (Phase 9)
# ---------------------------------------------------------------------------


class TestIgnoredItems:
    """Tests for ignored items filtering in recommendations."""

    def test_ignored_items_filtered_from_recommendations(
        self, non_ai_engine, mock_storage
    ):
        """Ignored unconsumed items should not appear in recommendations."""
        consumed = ContentItem(
            id="1",
            title="Consumed Book",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            rating=5,
            metadata={"genre": "Science Fiction"},
        )

        normal_item = ContentItem(
            id="2",
            title="Normal Book",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
            ignored=False,
            metadata={"genre": "Science Fiction"},
        )

        ignored_item = ContentItem(
            id="3",
            title="Ignored Book",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
            ignored=True,
            metadata={"genre": "Science Fiction"},
        )

        mock_storage.get_completed_items = Mock(
            side_effect=lambda content_type=None, **kwargs: [consumed]
        )
        mock_storage.get_unconsumed_items = Mock(
            return_value=[normal_item, ignored_item]
        )

        recommendations = non_ai_engine.generate_recommendations(
            content_type=ContentType.BOOK,
            count=5,
        )

        recommended_titles = [rec["item"].title for rec in recommendations]

        # Normal item should be recommended
        assert "Normal Book" in recommended_titles

        # Ignored item should NOT be recommended
        assert "Ignored Book" not in recommended_titles

    def test_all_ignored_items_returns_empty(self, non_ai_engine, mock_storage):
        """If all unconsumed items are ignored, return empty recommendations."""
        consumed = ContentItem(
            id="1",
            title="Consumed Book",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            rating=5,
            metadata={"genre": "Drama"},
        )

        ignored_item_1 = ContentItem(
            id="2",
            title="Ignored Book 1",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
            ignored=True,
            metadata={"genre": "Drama"},
        )

        ignored_item_2 = ContentItem(
            id="3",
            title="Ignored Book 2",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
            ignored=True,
            metadata={"genre": "Drama"},
        )

        mock_storage.get_completed_items = Mock(
            side_effect=lambda content_type=None, **kwargs: [consumed]
        )
        mock_storage.get_unconsumed_items = Mock(
            return_value=[ignored_item_1, ignored_item_2]
        )

        recommendations = non_ai_engine.generate_recommendations(
            content_type=ContentType.BOOK,
            count=5,
        )

        # No recommendations should be returned
        assert recommendations == []


class TestContributingReferenceItemsRegression:
    """Regression tests for contributing reference item selection."""

    def test_references_balanced_across_content_types_regression(self) -> None:
        """Regression test: references should be balanced across content types.

        Bug reported: All TV show recommendations said "Recommended because
        you liked 'Firewatch'" (a video game) because it had the highest
        genre overlap and dominated every reference list.

        Root cause: _find_contributing_reference_items() returned items
        sorted purely by overlap score, so one content type could fill
        all slots.

        Fix: Pick the best match from each content type first, then fill
        remaining slots from overall ranking.
        """
        engine = RecommendationEngine.__new__(RecommendationEngine)

        candidate = ContentItem(
            id="breaking_bad",
            title="Breaking Bad",
            content_type=ContentType.TV_SHOW,
            status=ConsumptionStatus.UNREAD,
            metadata={"genres": ["Drama", "Crime", "Thriller"]},
        )

        consumed_tv = ContentItem(
            id="the_wire",
            title="The Wire",
            content_type=ContentType.TV_SHOW,
            status=ConsumptionStatus.COMPLETED,
            rating=5,
            metadata={"genres": ["Drama", "Crime"]},
        )
        consumed_game = ContentItem(
            id="firewatch",
            title="Firewatch",
            content_type=ContentType.VIDEO_GAME,
            status=ConsumptionStatus.COMPLETED,
            rating=5,
            metadata={"genres": ["Drama", "Adventure"]},
        )
        consumed_book = ContentItem(
            id="gone_girl",
            title="Gone Girl",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            rating=5,
            metadata={"genres": ["Thriller", "Crime"]},
        )

        result = engine._find_contributing_reference_items(
            candidate, [consumed_game, consumed_tv, consumed_book]
        )

        # All three content types should be represented
        result_types = {get_enum_value(item.content_type) for item in result}
        assert "tv_show" in result_types
        assert "video_game" in result_types
        assert "book" in result_types
