"""Tests for recommendation engine cross-content-type recommendations."""

from datetime import date
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
from src.recommendations.engine import (
    RecommendationEngine,
    _collapse_duplicate_db_ids,
    _shuffle_close_scores,
)
from src.recommendations.preferences import PreferenceAnalyzer, UserPreferences
from src.recommendations.variety import (
    VARIETY_LADDER_STEPS,
    VARIETY_SERIES_CONTINUATION_FACTOR,
)
from src.storage.manager import StorageManager
from src.storage.vector_db import VectorDB


@pytest.fixture
def mock_storage():
    """Create a mock storage manager.

    ``get_signal_items`` mirrors the real accessor (completed, rated, not
    ignored) by filtering whatever ``get_completed_items`` a test sets up, so
    existing tests only need to stub ``get_completed_items``. The real
    accessor's own filtering is covered in ``tests/test_storage_manager.py``.
    """
    storage = Mock(spec=StorageManager)
    storage.vector_db = Mock(spec=VectorDB)
    storage.vector_db.has_embedding = Mock(return_value=False)
    storage.vector_db.get_embedding = Mock(return_value=None)
    storage.get_signal_items = Mock(
        side_effect=lambda user_id=None, content_type=None, limit=None, **kwargs: [
            item
            for item in storage.get_completed_items(
                user_id=user_id, content_type=content_type, limit=limit, **kwargs
            )
            if item.rating is not None and not item.ignored
        ]
    )
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


@pytest.fixture
def real_storage(tmp_path):
    """Create a real StorageManager backed by a temporary SQLite database."""
    return StorageManager(tmp_path / "engine_signal.db", ai_enabled=False)


@pytest.fixture
def real_engine(real_storage):
    """Create a non-AI RecommendationEngine over real storage."""
    return RecommendationEngine(
        storage_manager=real_storage,
        embedding_generator=None,
        recommendation_generator=None,
        min_rating=4,
    )


def _save_book(
    storage,
    *,
    item_id,
    title,
    status,
    rating=None,
    ignored=False,
    genre="Science Fiction",
):
    """Persist a book, optionally marking it ignored; return its db_id."""
    db_id = storage.save_content_item(
        ContentItem(
            id=item_id,
            title=title,
            content_type=ContentType.BOOK,
            status=status,
            rating=rating,
            metadata={"genre": genre},
        )
    )
    if ignored:
        storage.set_item_ignored(db_id, True)
    return db_id


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
    """Tests for ignored items filtering in recommendations (real storage)."""

    def test_ignored_items_filtered_from_recommendations(
        self, real_engine, real_storage
    ):
        """Ignored unconsumed items should not appear in recommendations."""
        _save_book(
            real_storage,
            item_id="1",
            title="Consumed Book",
            status=ConsumptionStatus.COMPLETED,
            rating=5,
        )
        _save_book(
            real_storage,
            item_id="2",
            title="Normal Book",
            status=ConsumptionStatus.UNREAD,
        )
        _save_book(
            real_storage,
            item_id="3",
            title="Ignored Book",
            status=ConsumptionStatus.UNREAD,
            ignored=True,
        )

        recommendations = real_engine.generate_recommendations(
            content_type=ContentType.BOOK,
            count=5,
        )

        recommended_titles = [rec["item"].title for rec in recommendations]

        # Normal item should be recommended
        assert "Normal Book" in recommended_titles

        # Ignored item should NOT be recommended
        assert "Ignored Book" not in recommended_titles

    def test_all_ignored_items_returns_empty(self, real_engine, real_storage):
        """If all unconsumed items are ignored, return empty recommendations."""
        _save_book(
            real_storage,
            item_id="1",
            title="Consumed Book",
            status=ConsumptionStatus.COMPLETED,
            rating=5,
            genre="Drama",
        )
        _save_book(
            real_storage,
            item_id="2",
            title="Ignored Book 1",
            status=ConsumptionStatus.UNREAD,
            ignored=True,
            genre="Drama",
        )
        _save_book(
            real_storage,
            item_id="3",
            title="Ignored Book 2",
            status=ConsumptionStatus.UNREAD,
            ignored=True,
            genre="Drama",
        )

        recommendations = real_engine.generate_recommendations(
            content_type=ContentType.BOOK,
            count=5,
        )

        # No recommendations should be returned
        assert recommendations == []


class TestTvRecommendationCarriesDbIdRegression:
    """Regression test: TV recommendations must keep the show's db_id.

    Bug reported: TV show recommendations rendered without the "Mark complete"
    and "Ignore" buttons.  The card only shows those actions when the rec has a
    non-null ``db_id``.

    Root cause: TV shows are expanded into season-level candidates by
    ``expand_tv_shows_to_seasons``, which built each season ``ContentItem``
    without ``db_id``, so the recommendation payload serialized ``db_id: null``.

    Fix: season items now inherit the parent show's ``db_id`` (the library
    tracks TV at show level), so the recommendation stays actionable.
    """

    @pytest.fixture
    def breaking_bad_consumed(self) -> ContentItem:
        """Completed TV show used as shared taste context across these scenarios."""
        return ContentItem(
            id="1",
            db_id=1,
            title="Breaking Bad",
            content_type=ContentType.TV_SHOW,
            status=ConsumptionStatus.COMPLETED,
            rating=5,
            metadata={"genres": ["Drama", "Crime"]},
        )

    @pytest.fixture
    def expanse_show(self) -> ContentItem:
        """Unconsumed three-season Expanse show shared across these scenarios."""
        return ContentItem(
            id="tvdb:280619",
            db_id=42,
            title="The Expanse",
            content_type=ContentType.TV_SHOW,
            status=ConsumptionStatus.UNREAD,
            metadata={"total_seasons": 3, "genres": ["Drama", "Sci-Fi"]},
        )

    def test_tv_show_recommendation_has_non_null_db_id_regression(
        self, non_ai_engine, mock_storage, breaking_bad_consumed, expanse_show
    ) -> None:
        """An expanded TV-show recommendation keeps the parent show's db_id."""
        mock_storage.get_completed_items = Mock(
            side_effect=lambda content_type=None, **kwargs: [breaking_bad_consumed]
        )
        mock_storage.get_unconsumed_items = Mock(return_value=[expanse_show])

        recommendations = non_ai_engine.generate_recommendations(
            content_type=ContentType.TV_SHOW,
            count=5,
        )

        # The series rules surface only the next-unwatched season, so the show
        # yields exactly one actionable card carrying the show's db_id.
        assert len(recommendations) == 1
        assert recommendations[0]["item"].db_id == 42

    def test_next_unwatched_season_carries_parent_db_id_regression(
        self, non_ai_engine, mock_storage, breaking_bad_consumed
    ) -> None:
        """A partially-watched show surfaces its next season with the show db_id.

        Edge case: when ``seasons_watched`` already contains season 1, the
        engine should surface season 2 as the next actionable card, and that
        card must still carry the parent show's ``db_id`` (the library tracks
        TV at show level) so "Mark complete" / "Ignore" resolve correctly.
        """
        unconsumed_show = ContentItem(
            id="tvdb:280619",
            db_id=42,
            title="The Expanse",
            content_type=ContentType.TV_SHOW,
            status=ConsumptionStatus.UNREAD,
            metadata={
                "total_seasons": 3,
                "seasons_watched": [1],
                "genres": ["Drama", "Sci-Fi"],
            },
        )

        mock_storage.get_completed_items = Mock(
            side_effect=lambda content_type=None, **kwargs: [breaking_bad_consumed]
        )
        mock_storage.get_unconsumed_items = Mock(return_value=[unconsumed_show])

        recommendations = non_ai_engine.generate_recommendations(
            content_type=ContentType.TV_SHOW,
            count=5,
        )

        # Season 1 is watched, so the next surfaced season is season 2.
        assert len(recommendations) == 1
        rec_item = recommendations[0]["item"]
        assert rec_item.title == "The Expanse (Season 2)"
        assert rec_item.db_id == 42

    def test_series_rules_surface_one_season_per_show_regression(
        self, non_ai_engine, mock_storage, breaking_bad_consumed
    ) -> None:
        """With series rules on, a multi-season show yields exactly one card.

        Guards against the frontend collision risk: the recommendation card
        keys on ``rec.db_id`` and removal/actions are keyed by ``db_id``, so two
        season cards sharing one show's ``db_id`` would collide.  The series
        ordering rules (``should_recommend_item``) must surface only the single
        next-unwatched season, keeping each show to one db_id in the output.
        """
        unconsumed_show = ContentItem(
            id="tvdb:280619",
            db_id=42,
            title="The Expanse",
            content_type=ContentType.TV_SHOW,
            status=ConsumptionStatus.UNREAD,
            metadata={"total_seasons": 5, "genres": ["Drama", "Sci-Fi"]},
        )

        mock_storage.get_completed_items = Mock(
            side_effect=lambda content_type=None, **kwargs: [breaking_bad_consumed]
        )
        mock_storage.get_unconsumed_items = Mock(return_value=[unconsumed_show])

        recommendations = non_ai_engine.generate_recommendations(
            content_type=ContentType.TV_SHOW,
            count=5,
        )

        # Despite 5 expanded seasons, only the next-unwatched one survives, so
        # the single show contributes exactly one db_id to the output.
        db_ids = [rec["item"].db_id for rec in recommendations]
        assert db_ids == [42]

    def test_series_in_order_false_collapses_seasons_to_one_card_regression(
        self, non_ai_engine, mock_storage, breaking_bad_consumed, expanse_show
    ) -> None:
        """With series order off, a multi-season show yields exactly one card.

        When the user sets ``series_in_order=False`` the engine skips series
        filtering, so a multi-season show would otherwise surface several
        season-level candidates that all carry the same parent ``db_id`` (their
        ``item.id`` differs but the library db_id is shared).  The frontend keys
        cards and targets Mark-complete / Ignore actions by ``db_id``, so those
        co-occurring cards would collide.  The engine collapses them down to the
        single highest-ranked season, keeping each show to one db_id.
        """
        mock_storage.get_completed_items = Mock(
            side_effect=lambda content_type=None, **kwargs: [breaking_bad_consumed]
        )
        mock_storage.get_unconsumed_items = Mock(return_value=[expanse_show])

        recommendations = non_ai_engine.generate_recommendations(
            content_type=ContentType.TV_SHOW,
            count=5,
            user_preference_config=UserPreferenceConfig(series_in_order=False),
        )

        # The three expanded seasons collapse to a single actionable card that
        # carries the parent show's db_id.
        assert len(recommendations) == 1
        rec_item = recommendations[0]["item"]
        assert rec_item.db_id == 42
        # The survivor's id is asserted with ``in {season ids}`` rather than a
        # specific season because the three seasons score identically and pass
        # through ``_shuffle_close_scores``, so which one survives is
        # non-deterministic at this integration level.  The deterministic
        # "first/highest-ranked entry survives" contract is pinned by the
        # ``TestCollapseDuplicateDbIds`` unit test.
        assert rec_item.id in {"tvdb:280619:s1", "tvdb:280619:s2", "tvdb:280619:s3"}

    def test_series_in_order_false_keeps_distinct_shows_and_backfills_regression(
        self, non_ai_engine, mock_storage, breaking_bad_consumed, expanse_show
    ) -> None:
        """Collapsing duplicate db_ids never drops distinct shows.

        With series order off, two multi-season shows each collapse to one card
        (so different shows still appear), and the freed slots are backfilled by
        the other show rather than left empty.
        """
        foundation = ContentItem(
            id="tvdb:355567",
            db_id=99,
            title="Foundation",
            content_type=ContentType.TV_SHOW,
            status=ConsumptionStatus.UNREAD,
            metadata={"total_seasons": 2, "genres": ["Drama", "Sci-Fi"]},
        )

        mock_storage.get_completed_items = Mock(
            side_effect=lambda content_type=None, **kwargs: [breaking_bad_consumed]
        )
        mock_storage.get_unconsumed_items = Mock(
            return_value=[expanse_show, foundation]
        )

        recommendations = non_ai_engine.generate_recommendations(
            content_type=ContentType.TV_SHOW,
            count=5,
            user_preference_config=UserPreferenceConfig(series_in_order=False),
        )

        # Both distinct shows survive, each exactly once, despite five expanded
        # seasons between them.
        db_ids = sorted(rec["item"].db_id for rec in recommendations)
        assert db_ids == [42, 99]

    def test_none_db_id_show_seasons_not_collapsed_regression(
        self, non_ai_engine, mock_storage, breaking_bad_consumed
    ) -> None:
        """A db_id=None show's seasons are NOT collapsed (None is not an identity).

        Guards the bug-class where a missing db_id is mistaken for a shared
        identity: ``_collapse_duplicate_db_ids`` never merges None ids, so a TV
        show that lacks a library db_id keeps every expanded season candidate
        instead of dropping to one card.  This documents the actual behaviour
        through ``generate_recommendations`` so a future change cannot silently
        start collapsing — and discarding — distinct None-id candidates.
        """
        show_without_db_id = ContentItem(
            id="tvdb:280619",
            db_id=None,
            title="The Expanse",
            content_type=ContentType.TV_SHOW,
            status=ConsumptionStatus.UNREAD,
            metadata={"total_seasons": 3, "genres": ["Drama", "Sci-Fi"]},
        )

        mock_storage.get_completed_items = Mock(
            side_effect=lambda content_type=None, **kwargs: [breaking_bad_consumed]
        )
        mock_storage.get_unconsumed_items = Mock(return_value=[show_without_db_id])

        recommendations = non_ai_engine.generate_recommendations(
            content_type=ContentType.TV_SHOW,
            count=5,
            user_preference_config=UserPreferenceConfig(series_in_order=False),
        )

        # All three seasons survive — None is never a shared collapse identity —
        # and every surviving card carries the None db_id.
        assert len(recommendations) == 3
        assert all(rec["item"].db_id is None for rec in recommendations)

    def test_collapse_is_noop_for_books_with_distinct_db_ids_regression(
        self, non_ai_engine, mock_storage
    ) -> None:
        """Non-TV recs with distinct db_ids pass through the collapse unchanged.

        Forward-guard behavioural test: it already passes without this branch's
        change (books each have a unique db_id, so the collapse is a no-op), so
        its role is to guard against a future regression that breaks the no-op
        property for distinct-db_id content.
        """
        consumed = ContentItem(
            id="1",
            db_id=1,
            title="Dune",
            author="Frank Herbert",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            rating=5,
            metadata={"genres": ["Science Fiction"]},
        )
        book_a = ContentItem(
            id="2",
            db_id=10,
            title="Hyperion",
            author="Dan Simmons",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
            metadata={"genres": ["Science Fiction"]},
        )
        book_b = ContentItem(
            id="3",
            db_id=11,
            title="Neuromancer",
            author="William Gibson",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
            metadata={"genres": ["Science Fiction"]},
        )

        mock_storage.get_completed_items = Mock(
            side_effect=lambda content_type=None, **kwargs: [consumed]
        )
        mock_storage.get_unconsumed_items = Mock(return_value=[book_a, book_b])

        recommendations = non_ai_engine.generate_recommendations(
            content_type=ContentType.BOOK,
            count=5,
            user_preference_config=UserPreferenceConfig(series_in_order=False),
        )

        db_ids = sorted(rec["item"].db_id for rec in recommendations)
        assert db_ids == [10, 11]

    def test_collapse_is_noop_with_series_in_order_regression(
        self, non_ai_engine, mock_storage, breaking_bad_consumed, expanse_show
    ) -> None:
        """With series order on, a multi-season show already yields one card.

        Forward-guard behavioural test: it already passes without this branch's
        change (series rules surface a single season, so the collapse has
        nothing to merge), so its role is to guard against a future regression
        that breaks the no-op property when series ordering is enabled.
        """
        mock_storage.get_completed_items = Mock(
            side_effect=lambda content_type=None, **kwargs: [breaking_bad_consumed]
        )
        mock_storage.get_unconsumed_items = Mock(return_value=[expanse_show])

        recommendations = non_ai_engine.generate_recommendations(
            content_type=ContentType.TV_SHOW,
            count=5,
            user_preference_config=UserPreferenceConfig(series_in_order=True),
        )

        assert len(recommendations) == 1
        assert recommendations[0]["item"].db_id == 42

    def test_fallback_collapses_entries_sharing_db_id_regression(
        self, non_ai_engine
    ) -> None:
        """The fallback path emits at most one card per parent show db_id.

        For TV the fallback builds recs directly from the expanded season items,
        which share their parent show's ``db_id``.  The fallback must collapse
        those to one card per show just like the scored path does.
        """
        season_one = ContentItem(
            id="tvdb:280619:s1",
            db_id=42,
            title="The Expanse",
            content_type=ContentType.TV_SHOW,
            status=ConsumptionStatus.UNREAD,
            metadata={"genres": ["Drama", "Sci-Fi"]},
        )
        season_two = ContentItem(
            id="tvdb:280619:s2",
            db_id=42,
            title="The Expanse",
            content_type=ContentType.TV_SHOW,
            status=ConsumptionStatus.UNREAD,
            metadata={"genres": ["Drama", "Sci-Fi"]},
        )
        other_show = ContentItem(
            id="tvdb:355567:s1",
            db_id=99,
            title="Foundation",
            content_type=ContentType.TV_SHOW,
            status=ConsumptionStatus.UNREAD,
            metadata={"genres": ["Drama", "Sci-Fi"]},
        )

        recommendations = non_ai_engine._build_fallback_recommendations(
            [season_one, season_two, other_show],
            series_tracking={},
            count=5,
        )

        # The two seasons of one show collapse to its first occurrence; the
        # distinct show is preserved.
        db_ids = [rec["item"].db_id for rec in recommendations]
        assert db_ids == [42, 99]
        assert recommendations[0]["item"].id == "tvdb:280619:s1"


class TestCollapseDuplicateDbIds:
    """Unit tests for the ``_collapse_duplicate_db_ids`` contract.

    The engine relies on this helper to (a) keep the *first* entry among
    duplicate non-null db_ids and (b) never merge None-db_id entries together.
    The engine calls it on the already-ranked (descending) list, so "first"
    means "highest-ranked".  These tests pin that contract directly rather than
    inferring it through the scored path, where same-show seasons score
    identically and cannot force a deterministic survivor.
    """

    def test_keeps_first_occurrence_among_duplicates_preserving_order(self) -> None:
        """The earliest (highest-ranked) entry per db_id survives, in order."""
        entries = [
            (42, "expanse-s2"),  # highest-ranked season of show 42
            (99, "foundation-s1"),
            (42, "expanse-s1"),  # lower-ranked duplicate of show 42 -> dropped
            (99, "foundation-s2"),  # lower-ranked duplicate of show 99 -> dropped
            (7, "standalone"),
        ]

        collapsed = _collapse_duplicate_db_ids(entries, lambda entry: entry[0])

        # Only the first occurrence of each db_id is kept, original order intact.
        assert collapsed == [
            (42, "expanse-s2"),
            (99, "foundation-s1"),
            (7, "standalone"),
        ]

    def test_none_db_ids_are_never_collapsed_together(self) -> None:
        """Entries with db_id None are each kept — None is not a shared identity.

        A missing db_id must not act as a collapse key, otherwise distinct
        recommendations that happen to lack a db_id would silently drop to one.
        """
        entries = [
            (None, "no-id-a"),
            (None, "no-id-b"),
            (5, "has-id"),
            (None, "no-id-c"),
        ]

        collapsed = _collapse_duplicate_db_ids(entries, lambda entry: entry[0])

        # All three None entries survive alongside the single id'd entry.
        assert collapsed == entries

    def test_single_entry_with_db_id_returned_unchanged(self) -> None:
        """A one-entry list with a non-null db_id returns that entry unchanged."""
        entries = [(42, "only")]
        assert _collapse_duplicate_db_ids(entries, lambda entry: entry[0]) == entries

    def test_empty_input_returns_empty(self) -> None:
        """Collapsing an empty list yields an empty list."""
        entries: list[tuple[int | None, str]] = []
        assert _collapse_duplicate_db_ids(entries, lambda entry: entry[0]) == []


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


def _variety_score_for(recs: list[dict], item_id: str) -> float:
    """Return the score of the recommendation with *item_id* (0.0 if absent)."""
    for rec in recs:
        if rec["item"].id == item_id:
            return rec["score"]
    return 0.0


def _variety_rank_of(recs: list[dict], item_id: str) -> int:
    """Return the index of *item_id* in *recs* (len(recs) if absent)."""
    for index, rec in enumerate(recs):
        if rec["item"].id == item_id:
            return index
    return len(recs)


class TestVarietyAfterCompletion:
    """Behavioural tests for the variety_penalty genre-fatigue penalty.

    The ``variety_penalty`` preference (0.0-5.0) drives a stepped penalty that
    demotes candidates whose genre cluster the user recently finished. The
    engine divides it by ``MAX_VARIETY_PENALTY`` to get the ladder's top penalty
    fraction, so a preference of 4.0 yields the legacy 0.8 top fraction. The
    issue #74 bug regression lives in
    :class:`TestVarietyAfterCompletionRegression`.
    """

    def test_variety_penalty_demotes_recently_finished_genre(
        self, non_ai_engine, mock_storage
    ) -> None:
        """A 4.0 variety_penalty lowers a just-finished genre's candidate score."""
        consumed = ContentItem(
            id="consumed_1",
            title="Dune",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            rating=5,
            date_completed=date(2026, 1, 1),
            metadata={"genres": ["Science Fiction"]},
        )
        same_genre = ContentItem(
            id="same_genre",
            title="Foundation",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
            metadata={"genres": ["Science Fiction"]},
        )

        mock_storage.get_completed_items = Mock(
            side_effect=lambda content_type=None, **kwargs: [consumed]
        )
        mock_storage.get_unconsumed_items = Mock(return_value=[same_genre])

        recs_off = non_ai_engine.generate_recommendations(
            content_type=ContentType.BOOK,
            count=1,
            user_preference_config=UserPreferenceConfig(variety_penalty=0.0),
        )
        recs_on = non_ai_engine.generate_recommendations(
            content_type=ContentType.BOOK,
            count=1,
            user_preference_config=UserPreferenceConfig(variety_penalty=4.0),
        )

        score_off = _variety_score_for(recs_off, "same_genre")
        score_on = _variety_score_for(recs_on, "same_genre")
        # 4.0 / 5.0 == 0.8 top fraction => (1 - top_fraction) of the score retained.
        top_fraction = 4.0 / UserPreferenceConfig.MAX_VARIETY_PENALTY
        assert score_on == pytest.approx(score_off * (1 - top_fraction), rel=1e-6)
        assert recs_on[0]["variety_penalty"] == pytest.approx(top_fraction)
        assert recs_off[0]["variety_penalty"] == 0.0

    def test_variety_penalty_steps_by_recency(
        self, non_ai_engine, mock_storage
    ) -> None:
        """The most recently finished genre is penalised more than an older one.

        With variety_penalty 4.0 (a 0.8 top fraction), finishing fantasy then
        sci-fi puts sci-fi on the top rung (0.8) and fantasy on the next rung
        (0.64), so a fantasy candidate outranks a sci-fi candidate even though
        sci-fi was also recently finished.
        """
        finished_fantasy = ContentItem(
            id="finished_fantasy",
            title="The Hobbit",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            rating=5,
            date_completed=date(2026, 1, 1),
            metadata={"genres": ["Fantasy"]},
        )
        finished_scifi = ContentItem(
            id="finished_scifi",
            title="Dune",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            rating=5,
            date_completed=date(2026, 1, 2),  # more recent
            metadata={"genres": ["Science Fiction"]},
        )
        fantasy_candidate = ContentItem(
            id="fantasy_candidate",
            title="The Name of the Wind",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
            metadata={"genres": ["Fantasy"]},
        )
        scifi_candidate = ContentItem(
            id="scifi_candidate",
            title="Hyperion",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
            metadata={"genres": ["Science Fiction"]},
        )

        mock_storage.get_completed_items = Mock(
            side_effect=lambda content_type=None, **kwargs: [
                finished_fantasy,
                finished_scifi,
            ]
        )
        mock_storage.get_unconsumed_items = Mock(
            return_value=[fantasy_candidate, scifi_candidate]
        )

        recs = non_ai_engine.generate_recommendations(
            content_type=ContentType.BOOK,
            count=2,
            user_preference_config=UserPreferenceConfig(variety_penalty=4.0),
        )

        # Sci-fi finished most recently => stronger penalty => ranked lower.
        assert _variety_rank_of(recs, "fantasy_candidate") < _variety_rank_of(
            recs, "scifi_candidate"
        )
        fantasy_penalty = next(
            rec["variety_penalty"]
            for rec in recs
            if rec["item"].id == "fantasy_candidate"
        )
        scifi_penalty = next(
            rec["variety_penalty"]
            for rec in recs
            if rec["item"].id == "scifi_candidate"
        )
        top_fraction = (
            UserPreferenceConfig.LEGACY_VARIETY_ON
            / UserPreferenceConfig.MAX_VARIETY_PENALTY
        )
        # Sci-fi takes the top rung; fantasy the next rung down the ladder.
        assert scifi_penalty == pytest.approx(top_fraction)
        assert fantasy_penalty == pytest.approx(
            top_fraction * (VARIETY_LADDER_STEPS - 1) / VARIETY_LADDER_STEPS
        )

    def test_intermediate_variety_penalty_scales_top_rung(
        self, non_ai_engine, mock_storage
    ) -> None:
        """A mid-range preference (2.0) becomes the ladder's top penalty.

        The decay shape is unchanged; only the top value is user-driven. The
        preference is divided by ``MAX_VARIETY_PENALTY``, so 2.0 yields a 0.4 top
        fraction and the most recently finished genre retains 60% of its score.
        """
        consumed = ContentItem(
            id="consumed_1",
            title="Dune",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            rating=5,
            date_completed=date(2026, 1, 1),
            metadata={"genres": ["Science Fiction"]},
        )
        same_genre = ContentItem(
            id="same_genre",
            title="Foundation",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
            metadata={"genres": ["Science Fiction"]},
        )

        mock_storage.get_completed_items = Mock(
            side_effect=lambda content_type=None, **kwargs: [consumed]
        )
        mock_storage.get_unconsumed_items = Mock(return_value=[same_genre])

        recs_off = non_ai_engine.generate_recommendations(
            content_type=ContentType.BOOK,
            count=1,
            user_preference_config=UserPreferenceConfig(variety_penalty=0.0),
        )
        recs_mid = non_ai_engine.generate_recommendations(
            content_type=ContentType.BOOK,
            count=1,
            user_preference_config=UserPreferenceConfig(variety_penalty=2.0),
        )

        score_off = _variety_score_for(recs_off, "same_genre")
        score_mid = _variety_score_for(recs_mid, "same_genre")
        # 2.0 / 5.0 == 0.4 top fraction => (1 - top_fraction) of the score retained.
        top_fraction = 2.0 / UserPreferenceConfig.MAX_VARIETY_PENALTY
        assert score_mid == pytest.approx(score_off * (1 - top_fraction), rel=1e-6)
        assert recs_mid[0]["variety_penalty"] == pytest.approx(top_fraction)

    def test_variety_penalty_is_per_content_type(
        self, non_ai_engine, mock_storage
    ) -> None:
        """Finishing a fantasy book must not penalise a fantasy game.

        The penalty ladder is scoped to completed items of the content type
        being recommended, so genres vary independently per type.
        """
        finished_book = ContentItem(
            id="finished_book",
            title="The Hobbit",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            rating=5,
            date_completed=date(2026, 1, 1),
            metadata={"genres": ["Fantasy"]},
        )
        fantasy_game = ContentItem(
            id="fantasy_game",
            title="Baldur's Gate 3",
            content_type=ContentType.VIDEO_GAME,
            status=ConsumptionStatus.UNREAD,
            metadata={"genres": ["Fantasy"]},
        )

        def completed_items(content_type=None, **kwargs):
            # Cross-type preference analysis sees the book; the game-type
            # query (used to build the ladder) sees no completed games.
            if content_type is None or content_type == ContentType.BOOK:
                return [finished_book]
            return []

        mock_storage.get_completed_items = Mock(side_effect=completed_items)
        mock_storage.get_unconsumed_items = Mock(return_value=[fantasy_game])

        recs = non_ai_engine.generate_recommendations(
            content_type=ContentType.VIDEO_GAME,
            count=1,
            user_preference_config=UserPreferenceConfig(variety_penalty=4.0),
        )

        # No completed games => empty ladder => the fantasy game is untouched.
        assert recs[0]["item"].id == "fantasy_game"
        assert recs[0]["variety_penalty"] == 0.0

    def test_no_variety_no_penalty(self, non_ai_engine, mock_storage) -> None:
        """With variety_penalty at 0.0, no penalty is recorded."""
        consumed = ContentItem(
            id="consumed_1",
            title="Dune",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            rating=5,
            date_completed=date(2026, 1, 1),
            metadata={"genres": ["Science Fiction"]},
        )
        candidate = ContentItem(
            id="candidate_1",
            title="Foundation",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
            metadata={"genres": ["Science Fiction"]},
        )

        mock_storage.get_completed_items = Mock(
            side_effect=lambda content_type=None, **kwargs: [consumed]
        )
        mock_storage.get_unconsumed_items = Mock(return_value=[candidate])

        recs = non_ai_engine.generate_recommendations(
            content_type=ContentType.BOOK,
            count=1,
            user_preference_config=UserPreferenceConfig(variety_penalty=0.0),
        )
        assert recs[0]["variety_penalty"] == 0.0

    def test_tiny_positive_variety_activates_penalty(
        self, non_ai_engine, mock_storage
    ) -> None:
        """Any variety_penalty above 0.0 builds a ladder and penalises matches.

        Guards the ``> 0.0`` gate distinct from the larger 2.0/4.0 cases: a
        barely-positive 0.25 still flows through to a 0.05 top fraction, so a
        same-genre candidate receives that penalty rather than 0.0.
        """
        consumed = ContentItem(
            id="consumed_1",
            title="Dune",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            rating=5,
            date_completed=date(2026, 1, 1),
            metadata={"genres": ["Science Fiction"]},
        )
        candidate = ContentItem(
            id="candidate_1",
            title="Foundation",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
            metadata={"genres": ["Science Fiction"]},
        )

        mock_storage.get_completed_items = Mock(
            side_effect=lambda content_type=None, **kwargs: [consumed]
        )
        mock_storage.get_unconsumed_items = Mock(return_value=[candidate])

        recs = non_ai_engine.generate_recommendations(
            content_type=ContentType.BOOK,
            count=1,
            user_preference_config=UserPreferenceConfig(variety_penalty=0.25),
        )
        # 0.25 / 5.0 == 0.05 top fraction.
        assert recs[0]["variety_penalty"] == pytest.approx(0.05)

    def test_four_matches_legacy_constant_behaviour(
        self, non_ai_engine, mock_storage
    ) -> None:
        """LEGACY_VARIETY_ON reproduces the old constant-driven penalty exactly.

        Before the 0.0-5.0 slider, the ladder's top rung was a fixed 0.8 fraction
        and the boolean toggle either applied it or not. On the new scale that
        same fraction is ``LEGACY_VARIETY_ON / MAX_VARIETY_PENALTY``, so the
        migrated strength must yield the identical applied penalty, proving the
        migration preserves behaviour.
        """
        consumed = ContentItem(
            id="consumed_1",
            title="Dune",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            rating=5,
            date_completed=date(2026, 1, 1),
            metadata={"genres": ["Science Fiction"]},
        )
        same_genre = ContentItem(
            id="same_genre",
            title="Foundation",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
            metadata={"genres": ["Science Fiction"]},
        )

        mock_storage.get_completed_items = Mock(
            side_effect=lambda content_type=None, **kwargs: [consumed]
        )
        mock_storage.get_unconsumed_items = Mock(return_value=[same_genre])

        recs_off = non_ai_engine.generate_recommendations(
            content_type=ContentType.BOOK,
            count=1,
            user_preference_config=UserPreferenceConfig(variety_penalty=0.0),
        )
        recs = non_ai_engine.generate_recommendations(
            content_type=ContentType.BOOK,
            count=1,
            user_preference_config=UserPreferenceConfig(
                variety_penalty=UserPreferenceConfig.LEGACY_VARIETY_ON
            ),
        )
        top_fraction = (
            UserPreferenceConfig.LEGACY_VARIETY_ON
            / UserPreferenceConfig.MAX_VARIETY_PENALTY
        )
        assert recs[0]["variety_penalty"] == pytest.approx(top_fraction)
        # The migrated strength must preserve the legacy score impact, not just
        # the reported penalty: the same-genre candidate retains (1 - top_fraction)
        # of the score it has with variety disabled.
        score_off = _variety_score_for(recs_off, "same_genre")
        score_on = _variety_score_for(recs, "same_genre")
        assert score_on == pytest.approx(score_off * (1 - top_fraction), rel=1e-6)

    def test_full_throttle_variety_zeroes_finished_genre(
        self, non_ai_engine, mock_storage
    ) -> None:
        """variety_penalty=5.0 applies a 1.0 fraction, zeroing a finished genre.

        Removing the old 0.8 cap means the maximum preference fully suppresses a
        just-finished genre's same-type candidate — there is no score floor.
        """
        consumed = ContentItem(
            id="consumed_1",
            title="Dune",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            rating=5,
            date_completed=date(2026, 1, 1),
            metadata={"genres": ["Science Fiction"]},
        )
        same_genre = ContentItem(
            id="same_genre",
            title="Foundation",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
            metadata={"genres": ["Science Fiction"]},
        )

        mock_storage.get_completed_items = Mock(
            side_effect=lambda content_type=None, **kwargs: [consumed]
        )
        mock_storage.get_unconsumed_items = Mock(return_value=[same_genre])

        recs = non_ai_engine.generate_recommendations(
            content_type=ContentType.BOOK,
            count=1,
            user_preference_config=UserPreferenceConfig(
                variety_penalty=UserPreferenceConfig.MAX_VARIETY_PENALTY
            ),
        )
        assert recs[0]["variety_penalty"] == pytest.approx(1.0)
        assert _variety_score_for(recs, "same_genre") == pytest.approx(0.0)


class TestVarietyAfterCompletionRegression:
    """Regression tests for the variety_penalty feature (issue #74)."""

    def test_next_in_series_demoted_when_variety_enabled_regression(
        self, non_ai_engine, mock_storage
    ) -> None:
        """The next book in a just-finished series must not be #1 with variety on.

        Reported: 'Finished a book, setting turned on, new number 1
        recommendation is the next book in the series.'

        Root cause: the variety penalty only nudged the legacy ranker's
        weak additive diversity bonus, which could not overcome the strong
        SeriesOrderScorer (next-in-series scores 1.0).

        Fix: variety now multiplicatively penalises recently finished genre
        clusters, demoting the next-in-series fantasy book below a
        different-genre candidate.
        """
        consumed = ContentItem(
            id="dragonlance_1",
            title="Dragonlance: Dragons of Autumn Twilight",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            rating=5,
            date_completed=date(2026, 1, 1),
            metadata={
                "franchise": "Dragonlance",
                "series_position": 1,
                "genres": ["Fantasy"],
            },
        )
        next_in_series = ContentItem(
            id="dragonlance_2",
            title="Dragonlance: Dragons of Winter Night",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
            metadata={
                "franchise": "Dragonlance",
                "series_position": 2,
                "genres": ["Fantasy"],
            },
        )
        different_genre = ContentItem(
            id="mystery_book",
            title="The Hound of the Baskervilles",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
            metadata={"genres": ["Mystery"]},
        )

        mock_storage.get_completed_items = Mock(
            side_effect=lambda content_type=None, **kwargs: [consumed]
        )
        mock_storage.get_unconsumed_items = Mock(
            return_value=[next_in_series, different_genre]
        )

        # Without variety: the next-in-series fantasy book tops the list (bug).
        recs_off = non_ai_engine.generate_recommendations(
            content_type=ContentType.BOOK,
            count=2,
            user_preference_config=UserPreferenceConfig(variety_penalty=0.0),
        )
        assert recs_off[0]["item"].id == "dragonlance_2"

        # With variety: the fantasy continuation is demoted below the mystery.
        recs_on = non_ai_engine.generate_recommendations(
            content_type=ContentType.BOOK,
            count=2,
            user_preference_config=UserPreferenceConfig(
                variety_penalty=UserPreferenceConfig.LEGACY_VARIETY_ON
            ),
        )
        assert recs_on[0]["item"].id == "mystery_book"
        assert _variety_rank_of(recs_on, "mystery_book") < _variety_rank_of(
            recs_on, "dragonlance_2"
        )

    def test_decimal_novella_below_next_book_with_variety_regression(
        self, non_ai_engine, mock_storage
    ) -> None:
        """The unread next book outranks half-numbered novellas end-to-end.

        Reported: a 200-book run with variety enabled put Expanse novellas
        "Drive (#2.7)" and "Gods of Risk (#2.5)" at ranks 45-48 while the
        actual next book "Caliban's War (#2)" sank to 123 under a 48% variety
        penalty — the user had read only book #1.

        Root cause (two compounding bugs): decimal novella positions parsed as
        non-series so they dodged the too-far-ahead suppression, and the
        variety penalty hit the legit next book at full strength.

        Fix: decimal-aware ordering substitutes the novellas with the earliest
        recommendable entry (book #2), and the variety penalty is softened for
        that active series continuation. This test wires both layers together
        through ``generate_recommendations``.
        """
        book_one = ContentItem(
            id="exp1",
            title="Leviathan Wakes (The Expanse, #1)",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            rating=5,
            date_completed=date(2026, 1, 1),
            metadata={"genres": ["Science Fiction"]},
        )
        book_two = ContentItem(
            id="exp2",
            title="Caliban's War (The Expanse, #2)",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
            metadata={"genres": ["Science Fiction"]},
        )
        novella_25 = ContentItem(
            id="exp25",
            title="Gods of Risk (The Expanse, #2.5)",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
            metadata={"genres": ["Science Fiction"]},
        )
        novella_27 = ContentItem(
            id="exp27",
            title="Drive (The Expanse, #2.7)",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
            metadata={"genres": ["Science Fiction"]},
        )

        mock_storage.get_completed_items = Mock(
            side_effect=lambda content_type=None, **kwargs: [book_one]
        )
        mock_storage.get_unconsumed_items = Mock(
            return_value=[novella_27, novella_25, book_two]
        )

        recs = non_ai_engine.generate_recommendations(
            content_type=ContentType.BOOK,
            count=10,
            user_preference_config=UserPreferenceConfig(
                variety_penalty=UserPreferenceConfig.LEGACY_VARIETY_ON
            ),
        )

        rec_ids = [rec["item"].id for rec in recs]
        # The legit next book is recommended; the out-of-order novellas are
        # substituted away by series filtering and never appear.
        assert "exp2" in rec_ids
        assert "exp25" not in rec_ids
        assert "exp27" not in rec_ids

        # The variety layer fired too: Caliban's War shares the just-finished
        # sci-fi cluster, but as an active series continuation its penalty is
        # softened (halved), not applied at full strength. The legacy-on
        # strength gives a 0.8 top fraction, so the softened penalty is
        # 0.8 * the factor.
        top_fraction = (
            UserPreferenceConfig.LEGACY_VARIETY_ON
            / UserPreferenceConfig.MAX_VARIETY_PENALTY
        )
        book_two_rec = next(rec for rec in recs if rec["item"].id == "exp2")
        assert book_two_rec["variety_penalty"] == pytest.approx(
            top_fraction * VARIETY_SERIES_CONTINUATION_FACTOR
        )
        assert book_two_rec["variety_penalty"] < top_fraction


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


class TestInProgressItemsExcludedFromBasisRegression:
    """Regression tests for in-progress items appearing in recommendation basis.

    Bug reported: items with status CURRENTLY_CONSUMING were showing up in
    the "based on" / contributing items list displayed alongside each
    recommendation, even though only completed items should influence the
    displayed reasoning.

    Root cause: `get_completed_items()` correctly includes CURRENTLY_CONSUMING
    so in-progress media still informs preference scoring, but the display
    helpers (_find_contributing_reference_items, _find_direct_adaptations)
    and the LLM-prompt callsites in _enhance_with_llm had no secondary
    status filter, so the in-progress items leaked into the user-visible
    reasoning.

    Fix: the display helpers and the LLM-prompt callsites now skip
    CURRENTLY_CONSUMING items so the "recommended because you liked X"
    surface only cites completed media.
    """

    def test_contributing_excludes_currently_consuming(self) -> None:
        """In-progress items must not appear as contributing references."""
        engine = RecommendationEngine.__new__(RecommendationEngine)

        candidate = ContentItem(
            id="breaking_bad",
            title="Breaking Bad",
            content_type=ContentType.TV_SHOW,
            status=ConsumptionStatus.UNREAD,
            metadata={"genres": ["Drama", "Crime", "Thriller"]},
        )

        in_progress = ContentItem(
            id="the_wire",
            title="The Wire",
            content_type=ContentType.TV_SHOW,
            status=ConsumptionStatus.CURRENTLY_CONSUMING,
            rating=5,
            metadata={"genres": ["Drama", "Crime"]},
        )
        completed = ContentItem(
            id="sopranos",
            title="The Sopranos",
            content_type=ContentType.TV_SHOW,
            status=ConsumptionStatus.COMPLETED,
            rating=5,
            metadata={"genres": ["Drama", "Crime"]},
        )

        result = engine._find_contributing_reference_items(
            candidate, [in_progress, completed]
        )

        result_ids = {item.id for item in result}
        assert result_ids == {"sopranos"}, (
            "CURRENTLY_CONSUMING items must be excluded from contributing "
            "references while completed items must remain"
        )

    def test_adaptations_exclude_currently_consuming(self) -> None:
        """In-progress items must not appear as cross-type adaptations."""
        engine = RecommendationEngine.__new__(RecommendationEngine)

        candidate = ContentItem(
            id="lotr_movie",
            title="The Fellowship of the Ring",
            content_type=ContentType.MOVIE,
            status=ConsumptionStatus.UNREAD,
            author="J.R.R. Tolkien",
        )

        in_progress_book = ContentItem(
            id="lotr_book_in_progress",
            title="The Fellowship of the Ring",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.CURRENTLY_CONSUMING,
            rating=5,
            author="J.R.R. Tolkien",
        )
        completed_book = ContentItem(
            id="lotr_book_done",
            title="The Fellowship of the Ring",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            rating=5,
            author="J.R.R. Tolkien",
        )

        result = engine._find_direct_adaptations(
            candidate, [in_progress_book, completed_book]
        )

        result_ids = {item.id for item in result}
        assert result_ids == {"lotr_book_done"}, (
            "CURRENTLY_CONSUMING items must be excluded from adaptations "
            "while completed adaptations must still appear"
        )

    def test_llm_blurb_call_excludes_currently_consuming(self) -> None:
        """LLM blurb context must not include CURRENTLY_CONSUMING items."""
        engine = RecommendationEngine.__new__(RecommendationEngine)
        engine.llm_generator = Mock(spec=RecommendationGenerator)
        engine.llm_generator.generate_blurbs_per_item.return_value = {}

        completed = ContentItem(
            id="sopranos",
            title="The Sopranos",
            content_type=ContentType.TV_SHOW,
            status=ConsumptionStatus.COMPLETED,
            rating=5,
        )
        in_progress = ContentItem(
            id="the_wire",
            title="The Wire",
            content_type=ContentType.TV_SHOW,
            status=ConsumptionStatus.CURRENTLY_CONSUMING,
            rating=5,
        )
        candidate = ContentItem(
            id="breaking_bad",
            title="Breaking Bad",
            content_type=ContentType.TV_SHOW,
            status=ConsumptionStatus.UNREAD,
        )

        engine._enhance_with_llm(
            recommendations=[{"item": candidate, "contributing_items": []}],
            content_type=ContentType.TV_SHOW,
            all_consumed_items=[completed, in_progress],
            unconsumed_items=[],
            count=1,
            series_tracking={},
        )

        call_kwargs = engine.llm_generator.generate_blurbs_per_item.call_args.kwargs
        consumed_passed = call_kwargs["consumed_items"]
        passed_ids = {item.id for item in consumed_passed}
        assert passed_ids == {"sopranos"}, (
            "LLM blurb call must receive only completed items as taste "
            "context — in-progress items must be filtered out"
        )

    def test_llm_only_fallback_excludes_currently_consuming(self) -> None:
        """LLM-only fallback recs must not see CURRENTLY_CONSUMING items."""
        engine = RecommendationEngine.__new__(RecommendationEngine)
        engine.llm_generator = Mock(spec=RecommendationGenerator)
        engine.llm_generator.generate_recommendations.return_value = []

        completed = ContentItem(
            id="sopranos",
            title="The Sopranos",
            content_type=ContentType.TV_SHOW,
            status=ConsumptionStatus.COMPLETED,
            rating=5,
        )
        in_progress = ContentItem(
            id="the_wire",
            title="The Wire",
            content_type=ContentType.TV_SHOW,
            status=ConsumptionStatus.CURRENTLY_CONSUMING,
            rating=5,
        )

        engine._enhance_with_llm(
            recommendations=[],
            content_type=ContentType.TV_SHOW,
            all_consumed_items=[completed, in_progress],
            unconsumed_items=[],
            count=5,
            series_tracking={},
        )

        call_kwargs = engine.llm_generator.generate_recommendations.call_args.kwargs
        consumed_passed = call_kwargs["consumed_items"]
        passed_ids = {item.id for item in consumed_passed}
        assert passed_ids == {"sopranos"}, (
            "LLM-only fallback must receive only completed items as taste "
            "context — in-progress items must be filtered out"
        )


class TestIgnoredAndUnratedSignalRegression:
    """Bug reported: ignored and completed-but-unrated items shaped recs.

    Bug reported: ignored items were only filtered at the final candidate
    stage, so they still fed preference analysis, scoring, similarity seeds,
    and "since you enjoyed X" explanation references; completed-but-unrated
    items leaked the same way because the signal was fetched with
    min_rating=None.
    Root cause: the engine fetched its signal set with the default
    include_ignored=True and min_rating=None, never narrowing to rated items.
    Fix: the engine draws its signal set from StorageManager.get_signal_items
    (completed, rated, not ignored) and its candidate pool with
    include_ignored=False. Verified end-to-end against real storage so the
    assertions exercise the actual filtering, not mock wiring.
    """

    def test_ignored_and_unrated_excluded_from_signal_regression(
        self, real_engine, real_storage
    ):
        """Ignored/unrated completed items never seed candidates or references."""
        _save_book(
            real_storage,
            item_id="dune",
            title="Dune",
            status=ConsumptionStatus.COMPLETED,
            rating=5,
        )
        _save_book(
            real_storage,
            item_id="neuro",
            title="Neuromancer",
            status=ConsumptionStatus.COMPLETED,
            rating=5,
            ignored=True,
        )
        _save_book(
            real_storage,
            item_id="snow",
            title="Snow Crash",
            status=ConsumptionStatus.COMPLETED,
            rating=None,
        )
        _save_book(
            real_storage,
            item_id="hyp",
            title="Hyperion",
            status=ConsumptionStatus.UNREAD,
        )
        _save_book(
            real_storage,
            item_id="saga",
            title="Ignored Saga",
            status=ConsumptionStatus.UNREAD,
            ignored=True,
        )

        recs = real_engine.generate_recommendations(
            content_type=ContentType.BOOK, count=5
        )

        titles = {rec["item"].title for rec in recs}
        # Candidate pool: the unrated candidate is still recommended (backlog
        # is unrated by nature); the ignored candidate is not.
        assert "Hyperion" in titles
        assert "Ignored Saga" not in titles

        hyperion = next(rec for rec in recs if rec["item"].title == "Hyperion")
        contributing = {item.title for item in hyperion["contributing_items"]}
        # The rated, non-ignored signal item is cited; the ignored and the
        # completed-but-unrated items never appear as "you liked" references.
        assert "Dune" in contributing
        assert "Neuromancer" not in contributing
        assert "Snow Crash" not in contributing

    def test_all_unrated_completed_yields_empty_regression(
        self, real_engine, real_storage
    ):
        """Completed-but-unrated-only libraries produce a graceful empty result."""
        _save_book(
            real_storage,
            item_id="u1",
            title="Unrated One",
            status=ConsumptionStatus.COMPLETED,
            rating=None,
        )
        _save_book(
            real_storage,
            item_id="cand",
            title="Candidate",
            status=ConsumptionStatus.UNREAD,
        )

        recs = real_engine.generate_recommendations(
            content_type=ContentType.BOOK, count=5
        )

        assert recs == []

    def test_all_ignored_completed_yields_empty_regression(
        self, real_engine, real_storage
    ):
        """Libraries whose only completed items are ignored return no recs."""
        _save_book(
            real_storage,
            item_id="i1",
            title="Ignored One",
            status=ConsumptionStatus.COMPLETED,
            rating=5,
            ignored=True,
        )
        _save_book(
            real_storage,
            item_id="cand",
            title="Candidate",
            status=ConsumptionStatus.UNREAD,
        )

        recs = real_engine.generate_recommendations(
            content_type=ContentType.BOOK, count=5
        )

        assert recs == []


class TestSimilaritySeedIgnoredRegression:
    """Bug reported: ignored completed items seeded AI similarity search.

    Bug reported: in AI mode the similarity seeds (reference items whose
    embeddings form the query vector) were drawn from the unfiltered consumed
    set, so an ignored completed item could steer the vector search toward
    content the user explicitly rejected.
    Root cause: ``_compute_similarity_scores`` seeded from the consumed set
    without excluding ignored items, and the lookup fetch defaulted to
    ``include_ignored=True``.
    Fix: seeds are drawn from ``get_signal_items`` (completed, rated, not
    ignored) and ``find_similar`` is called with ``include_ignored=False``.
    This exercises the AI path (embedding_generator set) that the non-AI
    ``real_engine`` regressions cannot reach.
    """

    def test_ignored_completed_item_not_used_as_similarity_seed_regression(
        self, engine, mock_storage, mock_embedding_gen
    ):
        """An ignored completed item never has its embedding generated as a seed."""
        liked = ContentItem(
            id="liked",
            title="Dune",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            rating=5,
            metadata={"genre": "Science Fiction"},
        )
        ignored = ContentItem(
            id="ign",
            title="Ignored Favorite",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            rating=5,
            ignored=True,
            metadata={"genre": "Science Fiction"},
        )
        candidate = ContentItem(
            id="cand",
            title="Hyperion",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
            metadata={"genre": "Science Fiction"},
        )
        mock_storage.get_completed_items = Mock(
            side_effect=lambda content_type=None, **kwargs: [liked, ignored]
        )
        mock_storage.get_unconsumed_items = Mock(return_value=[candidate])
        mock_storage.search_similar = Mock(
            return_value=[{"content_id": "cand", "score": 0.9}]
        )
        mock_storage.get_content_items = Mock(return_value=[candidate])

        engine.generate_recommendations(content_type=ContentType.BOOK, count=1)

        # find_similar embeds each reference (seed) item; the ignored item must
        # never be embedded, while the rated signal item is used as a seed.
        seeded_titles = {
            call.args[0].title
            for call in mock_embedding_gen.generate_content_embedding.call_args_list
            if call.args
        }
        assert "Dune" in seeded_titles
        assert "Ignored Favorite" not in seeded_titles

        # The similarity lookup itself is fetched without ignored items.
        assert any(
            call.kwargs.get("include_ignored") is False
            for call in mock_storage.get_content_items.call_args_list
        )


class TestConsumedItemsOfTypeRankingLeakRegression:
    """Bug reported: ignored/unrated same-type completed items reordered recs.

    Bug reported: ``consumed_items_of_type`` (passed to
    ``ranker.rank(recently_completed=...)`` and the variety penalty) was
    fetched unfiltered, so an ignored or completed-but-unrated book's genre
    entered the ranker's always-on diversity bonus (``diversity_weight=0.1``)
    and demoted an otherwise-top candidate sharing that genre.
    Root cause: the full same-type completed set was reused for taste-shaped
    ranking, not just for series ordering.
    Fix: ranking draws from a signal subset (``get_signal_items(content_type)``)
    while series ordering keeps the full completed set. Adapted from QA's
    reproduction probe and run against real storage so the ranker path is
    genuinely exercised.
    """

    @staticmethod
    def _order(engine):
        recs = engine.generate_recommendations(content_type=ContentType.BOOK, count=5)
        return [rec["item"].title for rec in recs]

    @staticmethod
    def _seed_baseline(storage):
        _save_book(
            storage,
            item_id="sig",
            title="Gone Girl",
            status=ConsumptionStatus.COMPLETED,
            rating=5,
            genre="Mystery",
        )
        _save_book(
            storage,
            item_id="cf",
            title="Fantasy Candidate",
            status=ConsumptionStatus.UNREAD,
            genre="Fantasy",
        )
        _save_book(
            storage,
            item_id="cs",
            title="SciFi Candidate",
            status=ConsumptionStatus.UNREAD,
            genre="Science Fiction",
        )

    def test_ignored_completed_item_does_not_reorder_regression(
        self, real_engine, real_storage
    ):
        """An ignored completed book must not reorder recommendations."""
        self._seed_baseline(real_storage)
        baseline = self._order(real_engine)

        _save_book(
            real_storage,
            item_id="fan",
            title="Ignored Fantasy",
            status=ConsumptionStatus.COMPLETED,
            rating=5,
            ignored=True,
            genre="Fantasy",
        )
        after = self._order(real_engine)

        assert after == baseline, (
            "An ignored completed item changed recommendation order via the "
            "ranker diversity bonus — issue #99 leak in ranking"
        )

    def test_unrated_completed_item_does_not_reorder_regression(
        self, real_engine, real_storage
    ):
        """A completed-but-unrated book must not reorder recommendations."""
        self._seed_baseline(real_storage)
        baseline = self._order(real_engine)

        _save_book(
            real_storage,
            item_id="fan",
            title="Unrated Fantasy",
            status=ConsumptionStatus.COMPLETED,
            rating=None,
            genre="Fantasy",
        )
        after = self._order(real_engine)

        assert after == baseline, (
            "A completed-but-unrated item changed recommendation order via the "
            "ranker diversity bonus — issue #99 leak in ranking"
        )

    def test_rated_completion_reorders_positive_control(
        self, real_engine, real_storage
    ):
        """Positive control: a rated, non-ignored completion DOES change order.

        Proves the seed set genuinely drives ranking, so the "order unchanged"
        assertions above are meaningful rather than vacuous. Two rated
        completions matching the trailing candidate's genre push it up the
        ranking via the (strongly weighted) preference signal.
        """
        self._seed_baseline(real_storage)
        baseline = self._order(real_engine)

        genre_by_title = {
            "Fantasy Candidate": "Fantasy",
            "SciFi Candidate": "Science Fiction",
        }
        trailing_genre = genre_by_title[baseline[-1]]
        for index in range(2):
            _save_book(
                real_storage,
                item_id=f"pref{index}",
                title=f"Loved {index}",
                status=ConsumptionStatus.COMPLETED,
                rating=5,
                genre=trailing_genre,
            )
        after = self._order(real_engine)

        assert after != baseline, (
            "A rated, non-ignored completion should reorder recommendations — "
            "if it does not, the negative regressions above prove nothing"
        )


class TestVarietyPenaltySignalRegression:
    """Bug reported: ignored/unrated items leaked into the variety penalty.

    Bug reported: with a non-zero ``variety_penalty`` the genre-fatigue ladder
    was built from the full same-type completed set, so an ignored or
    completed-but-unrated book still marked its genre "recently finished" and
    demoted an otherwise-unpenalised candidate sharing that genre.
    Root cause: ``_apply_variety_penalty`` received the unfiltered
    ``consumed_items_of_type`` set.
    Fix: the engine passes the signal subset (``get_signal_items(content_type)``)
    to the variety penalty, so only rated, non-ignored completions build the
    ladder. Exercised end-to-end against real storage with a ``variety_penalty``
    config (the path the ranker-diversity regression above does not cover). A
    positive control confirms the ladder is genuinely live for these genres so
    the "penalty stays zero" assertions are meaningful, not vacuous.
    """

    # variety_penalty == MAX gives a top-rung penalty fraction of 1.0, which
    # fully zeroes a just-finished genre — the strongest, most observable rung.
    _CONFIG = UserPreferenceConfig(
        variety_penalty=UserPreferenceConfig.MAX_VARIETY_PENALTY
    )

    @staticmethod
    def _seed_baseline(storage):
        # A rated, non-ignored Mystery signal item makes recommendations exist
        # and seeds the ladder with Mystery (a cluster none of the candidates
        # share, so baseline candidate penalties are zero).
        _save_book(
            storage,
            item_id="sig",
            title="Gone Girl",
            status=ConsumptionStatus.COMPLETED,
            rating=5,
            genre="Mystery",
        )
        _save_book(
            storage,
            item_id="cf",
            title="Fantasy Candidate",
            status=ConsumptionStatus.UNREAD,
            genre="Fantasy",
        )
        _save_book(
            storage,
            item_id="cs",
            title="SciFi Candidate",
            status=ConsumptionStatus.UNREAD,
            genre="Science Fiction",
        )

    def _fantasy_penalty(self, engine):
        recs = engine.generate_recommendations(
            content_type=ContentType.BOOK,
            count=5,
            user_preference_config=self._CONFIG,
        )
        fantasy = next(rec for rec in recs if rec["item"].title == "Fantasy Candidate")
        return fantasy["variety_penalty"]

    def test_baseline_fantasy_candidate_unpenalised(self, real_engine, real_storage):
        """With only a Mystery signal item, the Fantasy candidate has no penalty."""
        self._seed_baseline(real_storage)
        assert self._fantasy_penalty(real_engine) == 0.0

    def test_rated_completed_fantasy_penalises_positive_control(
        self, real_engine, real_storage
    ):
        """Positive control: a rated, non-ignored Fantasy completion penalises it.

        Proves the ladder recognises the Fantasy cluster and applies a penalty,
        so the ignored/unrated "stays zero" assertions below are meaningful.
        """
        self._seed_baseline(real_storage)
        _save_book(
            real_storage,
            item_id="fan",
            title="Rated Fantasy",
            status=ConsumptionStatus.COMPLETED,
            rating=5,
            genre="Fantasy",
        )
        assert self._fantasy_penalty(real_engine) > 0.0

    def test_ignored_completed_fantasy_does_not_penalise_regression(
        self, real_engine, real_storage
    ):
        """An ignored Fantasy completion must not enter the variety ladder."""
        self._seed_baseline(real_storage)
        _save_book(
            real_storage,
            item_id="fan",
            title="Ignored Fantasy",
            status=ConsumptionStatus.COMPLETED,
            rating=5,
            ignored=True,
            genre="Fantasy",
        )
        assert self._fantasy_penalty(real_engine) == 0.0, (
            "An ignored completed item entered the variety ladder and "
            "penalised a same-genre candidate — issue #99 leak in variety penalty"
        )

    def test_unrated_completed_fantasy_does_not_penalise_regression(
        self, real_engine, real_storage
    ):
        """A completed-but-unrated Fantasy book must not enter the ladder."""
        self._seed_baseline(real_storage)
        _save_book(
            real_storage,
            item_id="fan",
            title="Unrated Fantasy",
            status=ConsumptionStatus.COMPLETED,
            rating=None,
            genre="Fantasy",
        )
        assert self._fantasy_penalty(real_engine) == 0.0, (
            "A completed-but-unrated item entered the variety ladder and "
            "penalised a same-genre candidate — issue #99 leak in variety penalty"
        )


class TestSeriesTrackingFullSetRegression:
    """Series ordering must use the FULL completed set, not the signal set.

    Bug guarded: issue #99 narrows taste-shaped inputs to the signal set
    (completed, rated, not ignored). Series ordering must NOT be narrowed the
    same way — whether the user has *consumed* an earlier entry is a fact
    independent of rating or ignore state. If series tracking used the signal
    set, an ignored or completed-but-unrated middle entry would vanish from
    the consumed positions and strand the series: the next entry would be held
    behind an entry the user has actually finished (and which can never
    reappear as a candidate). The engine deliberately builds series tracking
    from the full ``consumed_items_of_type``; these tests lock that in against
    real storage.
    """

    @staticmethod
    def _titles(engine):
        recs = engine.generate_recommendations(content_type=ContentType.BOOK, count=5)
        return [rec["item"].title for rec in recs]

    def test_completed_rated_first_entry_unlocks_second(
        self, real_engine, real_storage
    ):
        """Completing (and rating) #1 recommends #2 and holds #3."""
        _save_book(
            real_storage,
            item_id="s1",
            title="Foundation (Signal Saga #1)",
            status=ConsumptionStatus.COMPLETED,
            rating=5,
        )
        _save_book(
            real_storage,
            item_id="s2",
            title="Foundation and Empire (Signal Saga #2)",
            status=ConsumptionStatus.UNREAD,
        )
        _save_book(
            real_storage,
            item_id="s3",
            title="Second Foundation (Signal Saga #3)",
            status=ConsumptionStatus.UNREAD,
        )

        titles = self._titles(real_engine)

        assert any(
            "Foundation and Empire" in t for t in titles
        ), "#2 should be recommended after completing #1"
        assert not any(
            "Second Foundation" in t for t in titles
        ), "#3 must stay held until #2 is consumed"

    def test_ignored_middle_entry_does_not_strand_series_regression(
        self, real_engine, real_storage
    ):
        """An ignored completed #2 still counts, so #3 is recommended (not stranded).

        If series tracking used the signal set, ignored #2 would drop out of the
        consumed positions, #3 would be held behind an entry the user already
        finished, and the series would strand.
        """
        _save_book(
            real_storage,
            item_id="s1",
            title="Foundation (Signal Saga #1)",
            status=ConsumptionStatus.COMPLETED,
            rating=5,
        )
        _save_book(
            real_storage,
            item_id="s2",
            title="Foundation and Empire (Signal Saga #2)",
            status=ConsumptionStatus.COMPLETED,
            rating=5,
            ignored=True,
        )
        _save_book(
            real_storage,
            item_id="s3",
            title="Second Foundation (Signal Saga #3)",
            status=ConsumptionStatus.UNREAD,
        )
        _save_book(
            real_storage,
            item_id="s4",
            title="Foundation's Edge (Signal Saga #4)",
            status=ConsumptionStatus.UNREAD,
        )

        titles = self._titles(real_engine)

        assert any("Second Foundation" in t for t in titles), (
            "#3 must be recommended even when the finished #2 was ignored — "
            "series tracking must use the full completed set (issue #99)"
        )
        assert not any(
            "Foundation's Edge" in t for t in titles
        ), "#4 must stay held until #3 is consumed"

    def test_unrated_middle_entry_does_not_strand_series_regression(
        self, real_engine, real_storage
    ):
        """A completed-but-unrated #2 still counts, so #3 is recommended."""
        _save_book(
            real_storage,
            item_id="s1",
            title="Foundation (Signal Saga #1)",
            status=ConsumptionStatus.COMPLETED,
            rating=5,
        )
        _save_book(
            real_storage,
            item_id="s2",
            title="Foundation and Empire (Signal Saga #2)",
            status=ConsumptionStatus.COMPLETED,
            rating=None,
        )
        _save_book(
            real_storage,
            item_id="s3",
            title="Second Foundation (Signal Saga #3)",
            status=ConsumptionStatus.UNREAD,
        )
        _save_book(
            real_storage,
            item_id="s4",
            title="Foundation's Edge (Signal Saga #4)",
            status=ConsumptionStatus.UNREAD,
        )

        titles = self._titles(real_engine)

        assert any("Second Foundation" in t for t in titles), (
            "#3 must be recommended even when the finished #2 was unrated — "
            "series tracking must use the full completed set (issue #99)"
        )
        assert not any(
            "Foundation's Edge" in t for t in titles
        ), "#4 must stay held until #3 is consumed"
