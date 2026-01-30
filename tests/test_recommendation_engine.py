"""Tests for recommendation engine cross-content-type recommendations."""

from unittest.mock import Mock

import pytest

from src.llm.embeddings import EmbeddingGenerator
from src.models.content import ConsumptionStatus, ContentItem, ContentType
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
        assert (
            "all content types" in reasoning.lower()
            or "preferences" in reasoning.lower()
            or "(book)" in reasoning.lower()
        )


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
