"""Tests for ranking algorithm."""

import pytest

from src.models.content import ContentItem, ContentType, ConsumptionStatus
from src.recommendations.preferences import UserPreferences
from src.recommendations.ranking import RecommendationRanker


@pytest.fixture
def sample_preferences():
    """Create sample user preferences."""
    return UserPreferences(
        preferred_authors={"author a": 0.8},
        preferred_genres={"science fiction": 0.9},
        preferred_themes={},
        average_rating=4.5,
        total_items=10,
    )


def test_ranker_basic(sample_preferences):
    """Test basic ranking."""
    ranker = RecommendationRanker()

    candidates = [
        (
            ContentItem(
                id="1",
                title="Book 1",
                author="Author A",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.UNREAD,
            ),
            0.8,
        ),
        (
            ContentItem(
                id="2",
                title="Book 2",
                author="Author B",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.UNREAD,
            ),
            0.9,
        ),
    ]

    ranked = ranker.rank(candidates, sample_preferences, ContentType.BOOK)

    assert len(ranked) == 2
    assert ranked[0][1] >= ranked[1][1]  # Sorted by score


def test_ranker_preference_weighting(sample_preferences):
    """Test that preferences affect ranking."""
    ranker = RecommendationRanker(preference_weight=0.5, similarity_weight=0.5)

    candidates = [
        (
            ContentItem(
                id="1",
                title="Book 1",
                author="Author A",  # Preferred author
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.UNREAD,
            ),
            0.7,  # Lower similarity
        ),
        (
            ContentItem(
                id="2",
                title="Book 2",
                author="Author B",  # Not preferred
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.UNREAD,
            ),
            0.8,  # Higher similarity
        ),
    ]

    ranked = ranker.rank(candidates, sample_preferences, ContentType.BOOK)

    # Book 1 should rank higher due to preferred author
    assert ranked[0][0].title == "Book 1"


def test_ranker_with_genre(sample_preferences):
    """Test ranking with genre preferences."""
    ranker = RecommendationRanker()

    candidates = [
        (
            ContentItem(
                id="1",
                title="Book 1",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.UNREAD,
                metadata={"genre": "Science Fiction"},
            ),
            0.7,
        ),
        (
            ContentItem(
                id="2",
                title="Book 2",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.UNREAD,
                metadata={"genre": "Mystery"},
            ),
            0.8,
        ),
    ]

    ranked = ranker.rank(candidates, sample_preferences, ContentType.BOOK)

    # Book 1 should rank higher due to preferred genre
    assert ranked[0][0].title == "Book 1"
    assert ranked[0][2]["preference_score"] > ranked[1][2]["preference_score"]
