"""Tests for recommendation engine cross-content-type recommendations."""

from unittest.mock import Mock

import pytest

from src.llm.embeddings import EmbeddingGenerator
from src.llm.recommendations import RecommendationGenerator
from src.models.content import (
    ConsumptionStatus,
    ContentItem,
    ContentType,
    get_enum_value,
)
from src.models.user_preferences import UserPreferenceConfig
from src.recommendations.engine import RecommendationEngine, _shuffle_close_scores
from src.recommendations.preferences import PreferenceAnalyzer, UserPreferences
from src.storage.manager import StorageManager
from src.storage.vector_db import VectorDB


@pytest.fixture
def mock_storage():
    """Create a mock storage manager."""
    storage = Mock(spec=StorageManager)
    storage.vector_db = Mock(spec=VectorDB)
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
# ---------------------------------------------------------------------------
# AI engine tests (engine with embedding_generator)
# ---------------------------------------------------------------------------


class TestAIEngine:
    """Tests for the recommendation engine with embeddings enabled."""

    def test_cross_content_type_preferences(
        self, engine, mock_storage, mock_embedding_gen
    ):
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

    def test_cross_content_type_similarity(
        self, engine, mock_storage, mock_embedding_gen
    ):
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

    def test_cold_start_with_other_content_types(self, engine, mock_storage):
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
        self, engine, mock_storage, mock_embedding_gen
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

        assert len(recommendations) == 1
        reasoning = recommendations[0].get("reasoning", "")
        # Reasoning should mention the specific cross-type item that contributed
        assert "dune" in reasoning.lower()


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

    def test_references_include_all_types_and_same_type_first_regression(
        self,
    ) -> None:
        """Regression test: references should include all types, same type first.

        Bug reported: All TV show recommendations said "Recommended because
        you liked 'Firewatch'" (a video game) because it had the highest
        genre overlap and dominated every reference list.

        Root cause: _find_contributing_reference_items() returned items
        sorted purely by overlap score, so one content type could fill
        all slots.

        Fix: Return up to 5 same-type items first, then up to 3 per other
        type.  Reasoning groups items by type with the candidate's type
        listed first.
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

        # Same type (TV show) should come first
        assert get_enum_value(result[0].content_type) == "tv_show"


class TestCrossTypeClusterOverlapRegression:
    """Regression tests for cross-type reference selection using cluster overlap.

    Bug reported: "1923" (a TV show with only "Drama" as its genre) appeared
    as a cross-type reference for nearly every recommendation, because raw
    Jaccard on ["drama"] gave ~0.2 overlap with almost anything.

    Root cause: Cross-type matching used raw genre Jaccard, which is too
    coarse for broad terms — "drama" alone would weakly match any item
    that includes "drama" among its genres.

    Fix: Cross-type matching now uses cluster_overlap() instead of raw
    Jaccard, which groups terms by thematic clusters and produces more
    discriminating scores.
    """

    def test_1923_different_shows_get_different_references_regression(self) -> None:
        """A sci-fi show and a historical drama should NOT both cite
        the same broadly-matching 'drama-only' item as a reference.

        Bug: "1923" (genre: ["Drama"]) was cited for every recommendation.
        """
        engine = RecommendationEngine.__new__(RecommendationEngine)

        # "1923" has only Drama — should not match sci-fi thematically
        show_1923 = ContentItem(
            id="1923",
            title="1923",
            content_type=ContentType.TV_SHOW,
            status=ConsumptionStatus.COMPLETED,
            rating=4,
            metadata={"genres": ["Drama"]},
        )

        # A sci-fi consumed item — should match sci-fi candidates
        sci_fi_consumed = ContentItem(
            id="expanse",
            title="The Expanse",
            content_type=ContentType.TV_SHOW,
            status=ConsumptionStatus.COMPLETED,
            rating=5,
            metadata={"genres": ["Science Fiction", "Drama"]},
        )

        # Candidate is a sci-fi book
        sci_fi_candidate = ContentItem(
            id="dune",
            title="Dune",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
            metadata={"genres": ["Science Fiction", "Adventure"]},
        )

        references = engine._find_contributing_reference_items(
            sci_fi_candidate, [show_1923, sci_fi_consumed]
        )

        reference_titles = [ref.title for ref in references]
        # The Expanse should be a reference (sci-fi cluster overlap)
        assert "The Expanse" in reference_titles
        # 1923 should NOT be a reference (only drama, no sci-fi cluster)
        assert "1923" not in reference_titles

    def test_cross_type_uses_thematic_matching_regression(self) -> None:
        """A war-themed book should reference Band of Brothers (war TV),
        not just any Drama show.

        Bug: Cross-type matching used raw Jaccard, making any "Drama"
        show a valid reference for any candidate with "Drama" in its genres.
        """
        engine = RecommendationEngine.__new__(RecommendationEngine)

        # War-themed candidate book
        candidate = ContentItem(
            id="war_book",
            title="Band of Brothers: The Book",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
            metadata={"genres": ["War", "Historical"]},
        )

        # War-themed TV show — should be a good cross-type match
        war_tv = ContentItem(
            id="bob_tv",
            title="Band of Brothers",
            content_type=ContentType.TV_SHOW,
            status=ConsumptionStatus.COMPLETED,
            rating=5,
            metadata={"genres": ["War", "Drama"]},
        )

        # Pure drama TV show — should NOT match a war book thematically
        drama_tv = ContentItem(
            id="crown",
            title="The Crown",
            content_type=ContentType.TV_SHOW,
            status=ConsumptionStatus.COMPLETED,
            rating=4,
            metadata={"genres": ["Drama"]},
        )

        references = engine._find_contributing_reference_items(
            candidate, [drama_tv, war_tv]
        )

        reference_titles = [ref.title for ref in references]
        # Band of Brothers (war cluster) should be referenced
        assert "Band of Brothers" in reference_titles
        # The Crown (drama only) should not match war + historical
        assert "The Crown" not in reference_titles


class TestReasoningFormatting:
    """Tests for natural language reasoning formatting.

    The single-item reasoning should read "Recommended because you liked
    the book Dune" instead of "Recommended because you liked Book: Dune".
    """

    def _make_engine(self) -> RecommendationEngine:
        """Create an engine instance for testing reasoning generation."""
        return RecommendationEngine.__new__(RecommendationEngine)

    def _make_empty_preferences(self) -> UserPreferences:
        """Create empty user preferences for testing."""
        return PreferenceAnalyzer(min_rating=4).analyze([])

    def test_single_book_reference_natural_format(self) -> None:
        """Single book reference should use 'the book' format."""
        engine = self._make_engine()
        preferences = self._make_empty_preferences()

        item = ContentItem(
            id="candidate",
            title="Hyperion",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
        )
        reference = ContentItem(
            id="ref",
            title="Dune",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            rating=5,
        )

        reasoning = engine._generate_reasoning(
            item=item,
            preferences=preferences,
            metadata={},
            adaptations=[],
            contributing_items=[reference],
        )

        assert reasoning == "Recommended because you liked the book Dune"

    def test_single_tv_show_reference_natural_format(self) -> None:
        """Single TV show reference should use 'the TV show' format."""
        engine = self._make_engine()
        preferences = self._make_empty_preferences()

        item = ContentItem(
            id="candidate",
            title="The Expanse",
            content_type=ContentType.TV_SHOW,
            status=ConsumptionStatus.UNREAD,
        )
        reference = ContentItem(
            id="ref",
            title="Breaking Bad",
            content_type=ContentType.TV_SHOW,
            status=ConsumptionStatus.COMPLETED,
            rating=5,
        )

        reasoning = engine._generate_reasoning(
            item=item,
            preferences=preferences,
            metadata={},
            adaptations=[],
            contributing_items=[reference],
        )

        assert reasoning == "Recommended because you liked the TV show Breaking Bad"

    def test_single_video_game_reference_natural_format(self) -> None:
        """Single video game reference should use 'the video game' format."""
        engine = self._make_engine()
        preferences = self._make_empty_preferences()

        item = ContentItem(
            id="candidate",
            title="Mass Effect 2",
            content_type=ContentType.VIDEO_GAME,
            status=ConsumptionStatus.UNREAD,
        )
        reference = ContentItem(
            id="ref",
            title="Mass Effect",
            content_type=ContentType.VIDEO_GAME,
            status=ConsumptionStatus.COMPLETED,
            rating=5,
        )

        reasoning = engine._generate_reasoning(
            item=item,
            preferences=preferences,
            metadata={},
            adaptations=[],
            contributing_items=[reference],
        )

        assert reasoning == "Recommended because you liked the video game Mass Effect"

    def test_single_movie_reference_natural_format(self) -> None:
        """Single movie reference should use 'the movie' format."""
        engine = self._make_engine()
        preferences = self._make_empty_preferences()

        item = ContentItem(
            id="candidate",
            title="Blade Runner 2049",
            content_type=ContentType.MOVIE,
            status=ConsumptionStatus.UNREAD,
        )
        reference = ContentItem(
            id="ref",
            title="Blade Runner",
            content_type=ContentType.MOVIE,
            status=ConsumptionStatus.COMPLETED,
            rating=5,
        )

        reasoning = engine._generate_reasoning(
            item=item,
            preferences=preferences,
            metadata={},
            adaptations=[],
            contributing_items=[reference],
        )

        assert reasoning == "Recommended because you liked the movie Blade Runner"

    def test_multiple_items_still_use_grouped_format(self) -> None:
        """Multiple reference items should still use the grouped format."""
        engine = self._make_engine()
        preferences = self._make_empty_preferences()

        item = ContentItem(
            id="candidate",
            title="Hyperion",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
        )
        ref_a = ContentItem(
            id="ref_a",
            title="Dune",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            rating=5,
        )
        ref_b = ContentItem(
            id="ref_b",
            title="Foundation",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            rating=5,
        )

        reasoning = engine._generate_reasoning(
            item=item,
            preferences=preferences,
            metadata={},
            adaptations=[],
            contributing_items=[ref_a, ref_b],
        )

        assert "Recommended because you liked the following:" in reasoning
        assert "Books:" in reasoning


class TestContributingReferenceRatingFloorRegression:
    """Regression tests for rating floor in contributing reference items.

    Bug reported: 'The Crown' rated 1 appeared as 'you liked' in
    recommendation reasoning.

    Root cause: _find_contributing_reference_items had no rating floor,
    so items the user actively disliked showed up in 'Recommended because
    you liked the following:'.

    Fix: Skip items with rating < 3 in the contributing items loop.
    """

    def test_low_rated_items_excluded_from_contributing_references_regression(
        self,
    ) -> None:
        """Regression test: Item rated 1 with matching genres must NOT appear.

        Bug reported: 'The Crown' rated 1 appeared in 'you liked' references.
        """
        engine = RecommendationEngine.__new__(RecommendationEngine)

        candidate = ContentItem(
            id="peaky",
            title="Peaky Blinders",
            content_type=ContentType.TV_SHOW,
            status=ConsumptionStatus.UNREAD,
            metadata={"genres": ["Drama", "Crime"]},
        )

        disliked_item = ContentItem(
            id="the_crown",
            title="The Crown",
            content_type=ContentType.TV_SHOW,
            status=ConsumptionStatus.COMPLETED,
            rating=1,
            metadata={"genres": ["Drama", "Historical"]},
        )

        liked_item = ContentItem(
            id="the_wire",
            title="The Wire",
            content_type=ContentType.TV_SHOW,
            status=ConsumptionStatus.COMPLETED,
            rating=5,
            metadata={"genres": ["Drama", "Crime"]},
        )

        result = engine._find_contributing_reference_items(
            candidate, [disliked_item, liked_item]
        )

        result_titles = [item.title for item in result]
        assert (
            "The Crown" not in result_titles
        ), "Items rated 1 should never appear as 'you liked' references"
        assert "The Wire" in result_titles

    def test_unrated_items_included_in_contributing_references(self) -> None:
        """Unrated items (rating=None) should still be included as references."""
        engine = RecommendationEngine.__new__(RecommendationEngine)

        candidate = ContentItem(
            id="breaking_bad",
            title="Breaking Bad",
            content_type=ContentType.TV_SHOW,
            status=ConsumptionStatus.UNREAD,
            metadata={"genres": ["Drama", "Crime"]},
        )

        unrated_item = ContentItem(
            id="the_sopranos",
            title="The Sopranos",
            content_type=ContentType.TV_SHOW,
            status=ConsumptionStatus.COMPLETED,
            rating=None,
            metadata={"genres": ["Drama", "Crime"]},
        )

        result = engine._find_contributing_reference_items(candidate, [unrated_item])

        result_titles = [item.title for item in result]
        assert (
            "The Sopranos" in result_titles
        ), "Unrated items should be included (benefit of the doubt)"


class TestSameTypeLimitRegression:
    """Regression test for same-type reference limit.

    Bug: Up to 5 same-type items were shown as references; user wants max 3.

    Fix: Changed same_type_limit from 5 to 3.
    """

    def test_same_type_limit_capped_at_3(self) -> None:
        """6 same-type consumed items should produce max 3 references."""
        engine = RecommendationEngine.__new__(RecommendationEngine)

        candidate = ContentItem(
            id="candidate",
            title="New Show",
            content_type=ContentType.TV_SHOW,
            status=ConsumptionStatus.UNREAD,
            metadata={"genres": ["Drama", "Crime"]},
        )

        consumed_items = [
            ContentItem(
                id=f"show_{index}",
                title=f"Show {index}",
                content_type=ContentType.TV_SHOW,
                status=ConsumptionStatus.COMPLETED,
                rating=5,
                metadata={"genres": ["Drama", "Crime"]},
            )
            for index in range(6)
        ]

        result = engine._find_contributing_reference_items(candidate, consumed_items)

        same_type_items = [
            item for item in result if get_enum_value(item.content_type) == "tv_show"
        ]
        assert (
            len(same_type_items) <= 3
        ), f"Expected at most 3 same-type references, got {len(same_type_items)}"


class TestShuffleCloseScores:
    """Tests for _shuffle_close_scores reference ordering."""

    def test_empty_input(self) -> None:
        assert _shuffle_close_scores([]) == []

    def test_single_item(self) -> None:
        item = ContentItem(
            id="a",
            title="Alpha",
            content_type=ContentType.VIDEO_GAME,
            status=ConsumptionStatus.COMPLETED,
        )
        result = _shuffle_close_scores([(item, 0.8)])
        assert result == [item]

    def test_distant_scores_preserve_order(self) -> None:
        """Items with very different scores should always stay in order."""
        items = [
            ContentItem(
                id=f"item_{index}",
                title=title,
                content_type=ContentType.VIDEO_GAME,
                status=ConsumptionStatus.COMPLETED,
            )
            for index, title in enumerate(["High", "Medium", "Low"])
        ]
        scored = list(zip(items, [0.9, 0.5, 0.1], strict=True))

        # Run many times — order should never change
        for _ in range(20):
            result = _shuffle_close_scores(scored)
            assert result == items

    def test_close_scores_all_items_present(self) -> None:
        """Items with close scores are shuffled but all remain present."""
        items = [
            ContentItem(
                id=f"item_{index}",
                title=title,
                content_type=ContentType.VIDEO_GAME,
                status=ConsumptionStatus.COMPLETED,
            )
            for index, title in enumerate(["A", "B", "C"])
        ]
        # All within 0.05 threshold
        scored = list(zip(items, [0.80, 0.78, 0.76], strict=True))

        for _ in range(20):
            result = _shuffle_close_scores(scored)
            assert {item.id for item in result} == {"item_0", "item_1", "item_2"}

    def test_mixed_groups_high_items_always_first(self) -> None:
        """A clearly higher-scored item always comes before a lower group."""
        top = ContentItem(
            id="top",
            title="Top",
            content_type=ContentType.VIDEO_GAME,
            status=ConsumptionStatus.COMPLETED,
        )
        close_items = [
            ContentItem(
                id=f"close_{index}",
                title=f"Close {index}",
                content_type=ContentType.VIDEO_GAME,
                status=ConsumptionStatus.COMPLETED,
            )
            for index in range(3)
        ]
        scored: list[tuple[ContentItem, float]] = [
            (top, 0.9),
            (close_items[0], 0.5),
            (close_items[1], 0.48),
            (close_items[2], 0.47),
        ]

        for _ in range(20):
            result = _shuffle_close_scores(scored)
            assert result[0] == top
            assert {item.id for item in result[1:]} == {
                "close_0",
                "close_1",
                "close_2",
            }


class TestVarietyAfterCompletion:
    """Tests for the variety_after_completion toggle wiring."""

    def test_variety_toggle_enables_diversity_bonus(
        self, non_ai_engine, mock_storage
    ) -> None:
        """variety_after_completion=True should boost genre-diverse items.

        When a user has completed Sci-Fi items and variety is enabled,
        a Mystery candidate should be boosted relative to another Sci-Fi
        candidate compared to when variety is disabled.
        """
        consumed = ContentItem(
            id="consumed_1",
            title="Dune",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            rating=5,
            metadata={"genres": ["Science Fiction"]},
        )

        same_genre = ContentItem(
            id="same_genre",
            title="Foundation",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
            metadata={"genres": ["Science Fiction"]},
        )

        different_genre = ContentItem(
            id="diff_genre",
            title="Sherlock Holmes",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
            metadata={"genres": ["Mystery"]},
        )

        mock_storage.get_completed_items = Mock(
            side_effect=lambda content_type=None, **kwargs: [consumed]
        )
        mock_storage.get_unconsumed_items = Mock(
            return_value=[same_genre, different_genre]
        )

        # Without variety: get baseline scores
        prefs_no_variety = UserPreferenceConfig(variety_after_completion=False)
        recs_no_variety = non_ai_engine.generate_recommendations(
            content_type=ContentType.BOOK,
            count=2,
            user_preference_config=prefs_no_variety,
        )

        # With variety: genre-diverse item should get a boost
        prefs_with_variety = UserPreferenceConfig(variety_after_completion=True)
        recs_with_variety = non_ai_engine.generate_recommendations(
            content_type=ContentType.BOOK,
            count=2,
            user_preference_config=prefs_with_variety,
        )

        # Find the different-genre item's score in each run
        def score_for(recs: list[dict], item_id: str) -> float:
            for rec in recs:
                if rec["item"].id == item_id:
                    return rec["score"]
            return 0.0

        diff_score_without = score_for(recs_no_variety, "diff_genre")
        diff_score_with = score_for(recs_with_variety, "diff_genre")

        # The diversity bonus should increase the different-genre item's score
        assert diff_score_with > diff_score_without, (
            f"Expected variety toggle to boost genre-diverse item: "
            f"without={diff_score_without:.4f}, with={diff_score_with:.4f}"
        )

    def test_variety_toggle_respects_explicit_diversity_weight(
        self, non_ai_engine, mock_storage
    ) -> None:
        """When diversity_weight is explicitly set, variety toggle doesn't override it."""
        consumed = ContentItem(
            id="consumed_1",
            title="Dune",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            rating=5,
            metadata={"genres": ["Science Fiction"]},
        )

        candidate = ContentItem(
            id="candidate_1",
            title="Sherlock Holmes",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
            metadata={"genres": ["Mystery"]},
        )

        mock_storage.get_completed_items = Mock(
            side_effect=lambda content_type=None, **kwargs: [consumed]
        )
        mock_storage.get_unconsumed_items = Mock(return_value=[candidate])

        # Explicit diversity_weight=0.5 with variety toggle on
        prefs_explicit = UserPreferenceConfig(
            variety_after_completion=True,
            diversity_weight=0.5,
        )
        recs_explicit = non_ai_engine.generate_recommendations(
            content_type=ContentType.BOOK,
            count=1,
            user_preference_config=prefs_explicit,
        )

        # Variety toggle on with default (0.0) diversity_weight → uses 0.2
        prefs_default = UserPreferenceConfig(
            variety_after_completion=True,
            diversity_weight=0.0,
        )
        recs_default = non_ai_engine.generate_recommendations(
            content_type=ContentType.BOOK,
            count=1,
            user_preference_config=prefs_default,
        )

        # The explicit 0.5 weight should produce a higher score than the
        # default 0.2 that the toggle applies
        score_explicit = recs_explicit[0]["score"]
        score_default = recs_default[0]["score"]
        assert score_explicit > score_default, (
            f"Expected explicit diversity_weight=0.5 to produce higher score "
            f"than default 0.2: explicit={score_explicit:.4f}, "
            f"default={score_default:.4f}"
        )


class TestEngineSeriesSubstitutionRegression:
    """Regression tests for series substitution in the recommendation engine.

    Bug reported: Final Fantasy XII (#12) was recommended as #1 but FFX (#10)
    was #4; Kingdom Hearts III is #5 but KH 2.8 is #7; Dragon Age Inquisition
    recommended without playing Dragon Age 2.

    Root cause: The engine filtered out later series entries entirely, rather
    than substituting them with the earliest playable entry.

    Fix: When a candidate fails should_recommend_item(), the engine now finds
    the earliest recommendable entry in the same series and substitutes it,
    using the substitute's own pipeline score.
    """

    def test_later_entry_substituted_with_earliest_regression(
        self, non_ai_engine, mock_storage
    ) -> None:
        """FF XII should be substituted with earliest unplayed FF entry.

        Bug: FF XII appeared as recommendation #1, FF X was #4.
        Fix: FF XII is substituted with the earliest recommendable FF entry.
        """
        consumed = ContentItem(
            id="consumed",
            title="Chrono Trigger",
            content_type=ContentType.VIDEO_GAME,
            status=ConsumptionStatus.COMPLETED,
            rating=5,
            metadata={"genres": ["RPG"]},
        )

        ff10 = ContentItem(
            id="ff10",
            title="Final Fantasy X",
            content_type=ContentType.VIDEO_GAME,
            status=ConsumptionStatus.UNREAD,
            metadata={
                "franchise": "Final Fantasy",
                "series_position": 10,
                "genres": ["RPG"],
            },
        )
        ff12 = ContentItem(
            id="ff12",
            title="Final Fantasy XII",
            content_type=ContentType.VIDEO_GAME,
            status=ConsumptionStatus.UNREAD,
            metadata={
                "franchise": "Final Fantasy",
                "series_position": 12,
                "genres": ["RPG"],
            },
        )
        other_game = ContentItem(
            id="other",
            title="Standalone RPG",
            content_type=ContentType.VIDEO_GAME,
            status=ConsumptionStatus.UNREAD,
            metadata={"genres": ["RPG"]},
        )

        mock_storage.get_completed_items = Mock(
            side_effect=lambda content_type=None, **kwargs: [consumed]
        )
        mock_storage.get_unconsumed_items = Mock(return_value=[ff12, ff10, other_game])

        recommendations = non_ai_engine.generate_recommendations(
            content_type=ContentType.VIDEO_GAME,
            count=5,
        )

        recommended_ids = [rec["item"].id for rec in recommendations]
        # FF X should appear (earliest recommendable FF entry)
        assert (
            "ff10" in recommended_ids
        ), f"FF X should be substituted in; got {recommended_ids}"
        # FF XII should NOT appear (it fails series rules)
        assert (
            "ff12" not in recommended_ids
        ), f"FF XII should be filtered out; got {recommended_ids}"

    def test_series_in_order_false_skips_filtering(
        self, non_ai_engine, mock_storage
    ) -> None:
        """series_in_order=False should skip all series filtering/substitution."""
        consumed = ContentItem(
            id="consumed",
            title="Chrono Trigger",
            content_type=ContentType.VIDEO_GAME,
            status=ConsumptionStatus.COMPLETED,
            rating=5,
            metadata={"genres": ["RPG"]},
        )

        ff12 = ContentItem(
            id="ff12",
            title="Final Fantasy XII",
            content_type=ContentType.VIDEO_GAME,
            status=ConsumptionStatus.UNREAD,
            metadata={
                "franchise": "Final Fantasy",
                "series_position": 12,
                "genres": ["RPG"],
            },
        )

        mock_storage.get_completed_items = Mock(
            side_effect=lambda content_type=None, **kwargs: [consumed]
        )
        mock_storage.get_unconsumed_items = Mock(return_value=[ff12])

        user_config = UserPreferenceConfig(series_in_order=False)
        recommendations = non_ai_engine.generate_recommendations(
            content_type=ContentType.VIDEO_GAME,
            count=5,
            user_preference_config=user_config,
        )

        recommended_ids = [rec["item"].id for rec in recommendations]
        # FF XII should appear (no filtering)
        assert "ff12" in recommended_ids

    def test_duplicate_substitutions_prevented_regression(
        self, non_ai_engine, mock_storage
    ) -> None:
        """Two FF entries in top candidates produce only one substitution.

        Bug: Both FF XII and FF XV failing series rules could cause FF X
        to appear twice in recommendations.
        Fix: substituted_series set prevents duplicate substitutions.
        """
        consumed = ContentItem(
            id="consumed",
            title="Chrono Trigger",
            content_type=ContentType.VIDEO_GAME,
            status=ConsumptionStatus.COMPLETED,
            rating=5,
            metadata={"genres": ["RPG"]},
        )

        ff10 = ContentItem(
            id="ff10",
            title="Final Fantasy X",
            content_type=ContentType.VIDEO_GAME,
            status=ConsumptionStatus.UNREAD,
            metadata={
                "franchise": "Final Fantasy",
                "series_position": 10,
                "genres": ["RPG"],
            },
        )
        ff12 = ContentItem(
            id="ff12",
            title="Final Fantasy XII",
            content_type=ContentType.VIDEO_GAME,
            status=ConsumptionStatus.UNREAD,
            metadata={
                "franchise": "Final Fantasy",
                "series_position": 12,
                "genres": ["RPG"],
            },
        )
        ff15 = ContentItem(
            id="ff15",
            title="Final Fantasy XV",
            content_type=ContentType.VIDEO_GAME,
            status=ConsumptionStatus.UNREAD,
            metadata={
                "franchise": "Final Fantasy",
                "series_position": 15,
                "genres": ["RPG"],
            },
        )

        mock_storage.get_completed_items = Mock(
            side_effect=lambda content_type=None, **kwargs: [consumed]
        )
        mock_storage.get_unconsumed_items = Mock(return_value=[ff15, ff12, ff10])

        recommendations = non_ai_engine.generate_recommendations(
            content_type=ContentType.VIDEO_GAME,
            count=5,
        )

        recommended_ids = [rec["item"].id for rec in recommendations]
        # FF X should appear exactly once
        assert (
            recommended_ids.count("ff10") == 1
        ), f"FF X should appear exactly once; got {recommended_ids}"


class TestPipelineOutputKeys:
    """Tests for pipeline output dict containing all expected keys."""

    def test_output_includes_contributing_items_and_adaptations(
        self, non_ai_engine, mock_storage
    ):
        """Pipeline output dicts include contributing_items and adaptations keys."""
        consumed = ContentItem(
            id="c1",
            title="Dune",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            rating=5,
            metadata={"genres": ["Science Fiction"]},
        )
        unconsumed = ContentItem(
            id="u1",
            title="Foundation",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
            metadata={"genres": ["Science Fiction"]},
        )

        mock_storage.get_completed_items = Mock(
            side_effect=lambda content_type=None, **kwargs: [consumed]
        )
        mock_storage.get_unconsumed_items = Mock(return_value=[unconsumed])

        recommendations = non_ai_engine.generate_recommendations(
            content_type=ContentType.BOOK,
            count=5,
        )

        assert len(recommendations) >= 1
        for recommendation in recommendations:
            assert "contributing_items" in recommendation
            assert "adaptations" in recommendation
            assert isinstance(recommendation["contributing_items"], list)
            assert isinstance(recommendation["adaptations"], list)


# ---------------------------------------------------------------------------
# ContinuationScorer exclusion tests
# ---------------------------------------------------------------------------


class TestContinuationScorerExclusion:
    """ContinuationScorer is excluded when no candidates are actively consumed."""

    def test_no_active_items_excludes_continuation_from_breakdown(
        self, non_ai_engine, mock_storage
    ):
        """When no candidates have CURRENTLY_CONSUMING status, 'continuation'
        must not appear in score_breakdown (it would be all zeros)."""
        consumed = ContentItem(
            id="c1",
            title="Dune",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            rating=5,
            metadata={"genres": ["Science Fiction"]},
        )
        unconsumed = ContentItem(
            id="u1",
            title="Hyperion",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
            metadata={"genres": ["Science Fiction"]},
        )

        mock_storage.get_completed_items = Mock(
            side_effect=lambda content_type=None, **kwargs: [consumed]
        )
        mock_storage.get_unconsumed_items = Mock(return_value=[unconsumed])

        recommendations = non_ai_engine.generate_recommendations(
            content_type=ContentType.BOOK, count=1
        )

        assert len(recommendations) == 1
        assert "continuation" not in recommendations[0]["score_breakdown"]

    def test_active_item_retains_continuation_in_breakdown(
        self, non_ai_engine, mock_storage
    ):
        """When a candidate has CURRENTLY_CONSUMING status, 'continuation'
        must appear in score_breakdown and the active item must score 1.0."""
        consumed = ContentItem(
            id="c1",
            title="Dune",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            rating=5,
            metadata={"genres": ["Science Fiction"]},
        )
        active_book = ContentItem(
            id="u1",
            title="Hyperion",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.CURRENTLY_CONSUMING,
            metadata={"genres": ["Science Fiction"]},
        )
        idle_book = ContentItem(
            id="u2",
            title="Foundation",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
            metadata={"genres": ["Science Fiction"]},
        )

        mock_storage.get_completed_items = Mock(
            side_effect=lambda content_type=None, **kwargs: [consumed]
        )
        mock_storage.get_unconsumed_items = Mock(return_value=[active_book, idle_book])

        recommendations = non_ai_engine.generate_recommendations(
            content_type=ContentType.BOOK, count=5
        )

        assert len(recommendations) >= 1
        breakdowns = {
            rec["item"].title: rec["score_breakdown"] for rec in recommendations
        }
        assert "continuation" in breakdowns["Hyperion"]
        assert breakdowns["Hyperion"]["continuation"] == 1.0
        assert breakdowns["Foundation"]["continuation"] == 0.0

    def test_tv_show_without_active_excludes_continuation(
        self, non_ai_engine, mock_storage
    ):
        """TV shows also exclude ContinuationScorer when nothing is active."""
        consumed = ContentItem(
            id="c1",
            title="The Wire",
            content_type=ContentType.TV_SHOW,
            status=ConsumptionStatus.COMPLETED,
            rating=5,
            metadata={
                "genres": ["Drama"],
                "total_seasons": 5,
                "seasons_watched": [1, 2, 3, 4, 5],
            },
        )
        unconsumed = ContentItem(
            id="u1",
            title="The Sopranos",
            content_type=ContentType.TV_SHOW,
            status=ConsumptionStatus.UNREAD,
            metadata={"genres": ["Drama"], "total_seasons": 6},
        )

        mock_storage.get_completed_items = Mock(
            side_effect=lambda content_type=None, **kwargs: [consumed]
        )
        mock_storage.get_unconsumed_items = Mock(return_value=[unconsumed])

        recommendations = non_ai_engine.generate_recommendations(
            content_type=ContentType.TV_SHOW, count=1
        )

        assert len(recommendations) >= 1
        assert "continuation" not in recommendations[0]["score_breakdown"]


# ---------------------------------------------------------------------------
# generate_blurb_for_item tests
# ---------------------------------------------------------------------------


class TestGenerateBlurbForItem:
    """Tests for RecommendationEngine.generate_blurb_for_item."""

    def test_success_path_returns_blurb(self, mock_storage, mock_embedding_gen) -> None:
        """generate_blurb_for_item returns blurb text on success."""
        mock_llm_gen = Mock(spec=RecommendationGenerator)
        mock_llm_gen.generate_single_blurb.return_value = "A gripping sci-fi epic."

        engine = RecommendationEngine(
            storage_manager=mock_storage,
            embedding_generator=mock_embedding_gen,
            recommendation_generator=mock_llm_gen,
        )

        item = ContentItem(
            id="1",
            title="Dune",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
        )
        consumed = [
            ContentItem(
                id="2",
                title="Foundation",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.COMPLETED,
                rating=5,
            )
        ]

        result = engine.generate_blurb_for_item(
            content_type=ContentType.BOOK,
            item=item,
            consumed_items=consumed,
        )

        assert result == "A gripping sci-fi epic."
        mock_llm_gen.generate_single_blurb.assert_called_once_with(
            content_type=ContentType.BOOK,
            item=item,
            consumed_items=consumed,
            references=None,
        )

    def test_failure_returns_none(self, mock_storage, mock_embedding_gen) -> None:
        """generate_blurb_for_item returns None when LLM raises."""
        mock_llm_gen = Mock(spec=RecommendationGenerator)
        mock_llm_gen.generate_single_blurb.side_effect = RuntimeError("LLM down")

        engine = RecommendationEngine(
            storage_manager=mock_storage,
            embedding_generator=mock_embedding_gen,
            recommendation_generator=mock_llm_gen,
        )

        item = ContentItem(
            id="1",
            title="Dune",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
        )

        result = engine.generate_blurb_for_item(
            content_type=ContentType.BOOK,
            item=item,
            consumed_items=[],
        )

        assert result is None

    def test_references_forwarded_to_llm(
        self, mock_storage, mock_embedding_gen
    ) -> None:
        """generate_blurb_for_item forwards references to the LLM generator."""
        mock_llm_gen = Mock(spec=RecommendationGenerator)
        mock_llm_gen.generate_single_blurb.return_value = "Blurb with refs."

        engine = RecommendationEngine(
            storage_manager=mock_storage,
            embedding_generator=mock_embedding_gen,
            recommendation_generator=mock_llm_gen,
        )

        item = ContentItem(
            id="1",
            title="Dune",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
        )
        refs = [
            ContentItem(
                id="3",
                title="Foundation",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.COMPLETED,
                rating=5,
            )
        ]

        result = engine.generate_blurb_for_item(
            content_type=ContentType.BOOK,
            item=item,
            consumed_items=[],
            references=refs,
        )

        assert result == "Blurb with refs."
        mock_llm_gen.generate_single_blurb.assert_called_once_with(
            content_type=ContentType.BOOK,
            item=item,
            consumed_items=[],
            references=refs,
        )

    def test_no_llm_returns_none(self, mock_storage, mock_embedding_gen) -> None:
        """generate_blurb_for_item returns None when no LLM generator."""
        engine = RecommendationEngine(
            storage_manager=mock_storage,
            embedding_generator=mock_embedding_gen,
            recommendation_generator=None,
        )

        item = ContentItem(
            id="1",
            title="Dune",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
        )

        result = engine.generate_blurb_for_item(
            content_type=ContentType.BOOK,
            item=item,
            consumed_items=[],
        )

        assert result is None


class TestSameSeriesReferenceExclusionRegression:
    """Regression tests for same-series exclusion in contributing references.

    Bug reported: "The Expanse (Season 2)" recommendation showed reasoning
    "Recommended because you liked The Expanse", which is circular
    self-referencing within a series.

    Root cause: _find_contributing_reference_items() did not check whether
    a consumed item belonged to the same series as the candidate, so earlier
    entries in a series could appear as the "why" for recommending a later
    entry.

    Fix: get_series_name() is called on the candidate and on each consumed
    item; items sharing the candidate's series name are skipped.

    Extended fix: when get_series_name() returns None for a consumed item
    (show-level items with no season marker), fall back to comparing the
    consumed item's title and metadata series_name directly against the
    candidate's series name via get_series_name_from_metadata().
    """

    def test_same_series_consumed_item_excluded_regression(self) -> None:
        """Regression: Series S1 must NOT appear as a reference for S2."""
        engine = RecommendationEngine.__new__(RecommendationEngine)

        candidate = ContentItem(
            id="expanse_s2",
            title="The Expanse (The Expanse, Season 2)",
            content_type=ContentType.TV_SHOW,
            status=ConsumptionStatus.UNREAD,
            metadata={"genres": ["Science Fiction", "Drama"]},
        )

        same_series_consumed = ContentItem(
            id="expanse_s1",
            title="The Expanse (The Expanse, Season 1)",
            content_type=ContentType.TV_SHOW,
            status=ConsumptionStatus.COMPLETED,
            rating=5,
            metadata={"genres": ["Science Fiction", "Drama"]},
        )

        other_consumed = ContentItem(
            id="battlestar",
            title="Battlestar Galactica",
            content_type=ContentType.TV_SHOW,
            status=ConsumptionStatus.COMPLETED,
            rating=5,
            metadata={"genres": ["Science Fiction", "Drama"]},
        )

        result = engine._find_contributing_reference_items(
            candidate, [same_series_consumed, other_consumed]
        )

        result_titles = [item.title for item in result]
        assert (
            "The Expanse (The Expanse, Season 1)" not in result_titles
        ), "Same-series items must not appear as contributing references"
        assert "Battlestar Galactica" in result_titles

    def test_non_series_candidate_unaffected(self) -> None:
        """Candidate with no series membership must not filter any consumed item."""
        engine = RecommendationEngine.__new__(RecommendationEngine)

        candidate = ContentItem(
            id="interstellar",
            title="Interstellar",
            content_type=ContentType.MOVIE,
            status=ConsumptionStatus.UNREAD,
            metadata={"genres": ["Science Fiction"]},
        )

        series_consumed = ContentItem(
            id="godfather_2",
            title="The Godfather (The Godfather, Part 2)",
            content_type=ContentType.MOVIE,
            status=ConsumptionStatus.COMPLETED,
            rating=5,
            metadata={"genres": ["Drama", "Crime"]},
        )

        result = engine._find_contributing_reference_items(candidate, [series_consumed])

        result_titles = [item.title for item in result]
        assert "The Godfather (The Godfather, Part 2)" in result_titles, (
            "Series-member consumed items must not be filtered when the "
            "candidate is not part of any series"
        )

    def test_case_insensitive_series_name_matching(self) -> None:
        """Series name comparison must be case-insensitive."""
        engine = RecommendationEngine.__new__(RecommendationEngine)

        candidate = ContentItem(
            id="expanse_s2",
            title="The Expanse (The Expanse, Season 2)",
            content_type=ContentType.TV_SHOW,
            status=ConsumptionStatus.UNREAD,
            metadata={"genres": ["Science Fiction"]},
        )

        # Title in all-lowercase — verifies case-insensitive series name extraction
        consumed = ContentItem(
            id="expanse_s1",
            title="the expanse (the expanse, Season 1)",
            content_type=ContentType.TV_SHOW,
            status=ConsumptionStatus.COMPLETED,
            rating=5,
            metadata={"genres": ["Science Fiction"]},
        )

        other = ContentItem(
            id="firefly",
            title="Firefly",
            content_type=ContentType.TV_SHOW,
            status=ConsumptionStatus.COMPLETED,
            rating=5,
            metadata={"genres": ["Science Fiction"]},
        )

        result = engine._find_contributing_reference_items(candidate, [consumed, other])

        result_titles = [item.title for item in result]
        assert (
            "the expanse (the expanse, Season 1)" not in result_titles
        ), "Case-insensitive series name match should still exclude"
        assert "Firefly" in result_titles

    def test_show_level_item_excluded_from_season_references_regression(self) -> None:
        """Regression: show-level "The Expanse" must not be a reference for Season 2.

        Bug reported: "The Expanse (Season 2)" recommendation showed reasoning
        "Recommended because you liked The Expanse", which is circular.

        Root cause: get_series_name() returns None for show-level items (no
        season marker in title, no season number in metadata), so the existing
        same-series check was bypassed.  The consumed item's title IS the
        series name — we must compare it directly.

        Fix: after the get_series_name comparison, fall back to comparing the
        consumed item's title against the candidate's series name.
        """
        engine = RecommendationEngine.__new__(RecommendationEngine)

        # Candidate: expanded season with series metadata
        candidate = ContentItem(
            id="expanse_s2",
            title="The Expanse (Season 2)",
            content_type=ContentType.TV_SHOW,
            status=ConsumptionStatus.UNREAD,
            metadata={
                "genres": ["Science Fiction", "Drama"],
                "series_name": "The Expanse",
                "season": 2,
            },
        )

        # Consumed: show-level item — no season marker in title
        show_level_consumed = ContentItem(
            id="expanse_show",
            title="The Expanse",
            content_type=ContentType.TV_SHOW,
            status=ConsumptionStatus.COMPLETED,
            rating=5,
            metadata={"genres": ["Science Fiction", "Drama"]},
        )

        other_consumed = ContentItem(
            id="firefly",
            title="Firefly",
            content_type=ContentType.TV_SHOW,
            status=ConsumptionStatus.COMPLETED,
            rating=5,
            metadata={"genres": ["Science Fiction"]},
        )

        result = engine._find_contributing_reference_items(
            candidate, [show_level_consumed, other_consumed]
        )

        result_titles = [item.title for item in result]
        assert "The Expanse" not in result_titles, (
            "Show-level items must not appear as contributing references for "
            "their own seasons"
        )
        assert "Firefly" in result_titles

    def test_show_level_metadata_series_name_excluded_regression(self) -> None:
        """Regression: metadata series_name triggers exclusion when title does not match.

        Bug reported: consumed item with series_name metadata but a
        non-matching title appeared as a contributing reference for its own
        series (e.g. "My Expanse Review" cited for "The Expanse (Season 3)").

        Root cause: get_series_name() requires both a series name AND a
        numeric position — without a season key it returns None, bypassing
        the primary same-series check.

        Fix: after the title comparison, fall back to
        get_series_name_from_metadata() to check metadata series_name/series/
        series_title/franchise fields.
        """
        engine = RecommendationEngine.__new__(RecommendationEngine)

        candidate = ContentItem(
            id="expanse_s3",
            title="The Expanse (Season 3)",
            content_type=ContentType.TV_SHOW,
            status=ConsumptionStatus.UNREAD,
            metadata={
                "genres": ["Science Fiction"],
                "series_name": "The Expanse",
                "season": 3,
            },
        )

        # Consumed item has series_name metadata but no season number
        consumed_with_meta = ContentItem(
            id="expanse_show",
            title="My Expanse Review",
            content_type=ContentType.TV_SHOW,
            status=ConsumptionStatus.COMPLETED,
            rating=4,
            metadata={
                "genres": ["Science Fiction"],
                "series_name": "The Expanse",
            },
        )

        other = ContentItem(
            id="bsg",
            title="Battlestar Galactica",
            content_type=ContentType.TV_SHOW,
            status=ConsumptionStatus.COMPLETED,
            rating=5,
            metadata={"genres": ["Science Fiction"]},
        )

        result = engine._find_contributing_reference_items(
            candidate, [consumed_with_meta, other]
        )

        result_titles = [item.title for item in result]
        assert "My Expanse Review" not in result_titles, (
            "Consumed items with matching metadata series_name must be "
            "excluded from contributing references"
        )
        assert "Battlestar Galactica" in result_titles

    @pytest.mark.parametrize(
        "metadata_key",
        ["series_name", "series", "series_title", "franchise"],
    )
    def test_all_metadata_series_keys_trigger_exclusion(
        self, metadata_key: str
    ) -> None:
        """All supported metadata keys must trigger same-series exclusion."""
        engine = RecommendationEngine.__new__(RecommendationEngine)

        candidate = ContentItem(
            id="expanse_s3",
            title="The Expanse (Season 3)",
            content_type=ContentType.TV_SHOW,
            status=ConsumptionStatus.UNREAD,
            metadata={
                "genres": ["Science Fiction"],
                "series_name": "The Expanse",
                "season": 3,
            },
        )

        consumed = ContentItem(
            id="expanse_show",
            title="My Expanse Review",
            content_type=ContentType.TV_SHOW,
            status=ConsumptionStatus.COMPLETED,
            rating=4,
            metadata={"genres": ["Science Fiction"], metadata_key: "The Expanse"},
        )

        result = engine._find_contributing_reference_items(candidate, [consumed])

        result_titles = [item.title for item in result]
        assert "My Expanse Review" not in result_titles, (
            f"Consumed item with {metadata_key!r} metadata key must be "
            f"excluded from contributing references"
        )
