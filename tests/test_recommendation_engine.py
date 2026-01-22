"""Tests for recommendation engine cross-content-type recommendations."""

import pytest
from unittest.mock import Mock, patch

from src.models.content import ContentItem, ContentType, ConsumptionStatus
from src.recommendations.engine import RecommendationEngine
from src.storage.manager import StorageManager
from src.llm.embeddings import EmbeddingGenerator


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
    """Create a recommendation engine with mocked dependencies."""
    return RecommendationEngine(
        storage_manager=mock_storage,
        embedding_generator=mock_embedding_gen,
        recommendation_generator=None,
        min_rating=4,
    )


def test_cross_content_type_preferences(engine, mock_storage, mock_embedding_gen):
    """Test that preferences are extracted from all content types."""
    # Consumed items across multiple content types
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

    # Unconsumed game to recommend
    unconsumed_game = ContentItem(
        id="4",
        title="Mass Effect 2",
        content_type=ContentType.VIDEO_GAME,
        status=ConsumptionStatus.UNREAD,
        metadata={"genres": ["Action", "RPG", "Science Fiction"]},
    )

    # Mock storage to return all consumed items (cross-content-type)
    mock_storage.get_completed_items = Mock(
        side_effect=lambda content_type=None, **kwargs: (
            [sci_fi_book, sci_fi_game, sci_fi_tv]
            if content_type is None
            else ([sci_fi_game] if content_type == ContentType.VIDEO_GAME else [])
        )
    )

    # Mock storage to return unconsumed games
    mock_storage.get_unconsumed_items = Mock(return_value=[unconsumed_game])

    # Mock similarity search
    mock_storage.search_similar = Mock(
        return_value=[
            {
                "content_id": "4",
                "score": 0.85,
                "metadata": {"title": "Mass Effect 2"},
            }
        ]
    )

    # Mock get_content_items for similarity matcher
    mock_storage.get_content_items = Mock(return_value=[unconsumed_game])

    # Mock vector DB
    mock_storage.vector_db.has_embedding = Mock(return_value=False)

    recommendations = engine.generate_recommendations(
        content_type=ContentType.VIDEO_GAME, count=1
    )

    # Should use preferences from all content types
    assert mock_storage.get_completed_items.call_count >= 1
    # First call should be with content_type=None to get all items
    call_args = mock_storage.get_completed_items.call_args_list
    assert any(
        call.kwargs.get("content_type") is None for call in call_args
    ), "Should fetch consumed items from all content types"


def test_cross_content_type_similarity(engine, mock_storage, mock_embedding_gen):
    """Test that similarity search uses reference items from all content types."""
    # Sci-fi book (consumed)
    sci_fi_book = ContentItem(
        id="1",
        title="Dune",
        author="Frank Herbert",
        content_type=ContentType.BOOK,
        status=ConsumptionStatus.COMPLETED,
        rating=5,
        metadata={"genre": "Science Fiction"},
    )

    # Sci-fi game (consumed)
    sci_fi_game = ContentItem(
        id="2",
        title="Mass Effect",
        content_type=ContentType.VIDEO_GAME,
        status=ConsumptionStatus.COMPLETED,
        rating=5,
        metadata={"genres": ["Science Fiction"]},
    )

    # Unconsumed TV show to recommend
    unconsumed_tv = ContentItem(
        id="3",
        title="The Expanse",
        content_type=ContentType.TV_SHOW,
        status=ConsumptionStatus.UNREAD,
        metadata={"genre": "Science Fiction"},
    )

    # Mock storage
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

    recommendations = engine.generate_recommendations(
        content_type=ContentType.TV_SHOW, count=1
    )

    # Verify that similarity search was called (it uses all consumed items)
    assert mock_storage.search_similar.called
    # The similarity search should use embeddings from both book and game
    assert mock_embedding_gen.generate_content_embedding.call_count >= 1


def test_cold_start_with_other_content_types(engine, mock_storage):
    """Test cold start when requesting type has no items but other types do."""
    # Consumed book (different type)
    sci_fi_book = ContentItem(
        id="1",
        title="Dune",
        content_type=ContentType.BOOK,
        status=ConsumptionStatus.COMPLETED,
        rating=5,
    )

    # Mock: no consumed games, but books exist
    mock_storage.get_completed_items = Mock(
        side_effect=lambda content_type=None, **kwargs: (
            [sci_fi_book] if content_type is None else []
        )
    )
    mock_storage.get_unconsumed_items = Mock(return_value=[])

    recommendations = engine.generate_recommendations(
        content_type=ContentType.VIDEO_GAME, count=5
    )

    # Should return empty (no unconsumed games to recommend)
    assert recommendations == []


def test_reasoning_mentions_cross_content_type(
    engine, mock_storage, mock_embedding_gen
):
    """Test that reasoning mentions cross-content-type preferences."""
    # Consumed sci-fi book
    sci_fi_book = ContentItem(
        id="1",
        title="Dune",
        content_type=ContentType.BOOK,
        status=ConsumptionStatus.COMPLETED,
        rating=5,
        metadata={"genre": "Science Fiction"},
    )

    # Unconsumed sci-fi game
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
        # Reasoning should mention cross-content-type preferences
        assert (
            "all content types" in reasoning.lower()
            or "preferences" in reasoning.lower()
        )
