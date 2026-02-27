"""Tests for ranking algorithm."""

import pytest

from src.models.content import ConsumptionStatus, ContentItem, ContentType
from src.recommendations.preferences import UserPreferences
from src.recommendations.ranking import RecommendationRanker
from tests.factories import make_item


@pytest.fixture
def sample_preferences():
    """Create sample user preferences."""
    return UserPreferences(
        preferred_authors={"author a": 0.8},
        preferred_genres={"science fiction": 0.9},
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


class TestDiversityScoring:
    """Tests for the genre-diversity bonus feature."""

    @pytest.fixture
    def neutral_preferences(self):
        """Preferences with no strong author or genre signals."""
        return UserPreferences(
            preferred_authors={},
            preferred_genres={},
            average_rating=4.0,
            total_items=5,
        )

    def test_diversity_score_different_genres(self) -> None:
        """Items with genres unlike recently completed get a high diversity score."""
        recently_completed = [
            make_item(
                item_id="c1",
                genres="Science Fiction",
                status=ConsumptionStatus.COMPLETED,
            ),
            make_item(
                item_id="c2",
                genres="Science Fiction",
                status=ConsumptionStatus.COMPLETED,
            ),
        ]

        # Mystery candidate is different from sci-fi completed items
        mystery_item = make_item(item_id="u1", genres="Mystery")
        score = RecommendationRanker._calculate_diversity_score(
            mystery_item,
            RecommendationRanker._collect_recent_genres(recently_completed),
        )
        assert score > 0.5  # High diversity

    def test_diversity_score_same_genres(self) -> None:
        """Items matching recently completed genres get a low diversity score."""
        recently_completed = [
            make_item(
                item_id="c1",
                genres="Science Fiction",
                status=ConsumptionStatus.COMPLETED,
            ),
        ]

        scifi_item = make_item(item_id="u1", genres="Science Fiction")
        score = RecommendationRanker._calculate_diversity_score(
            scifi_item,
            RecommendationRanker._collect_recent_genres(recently_completed),
        )
        assert score < 0.5  # Low diversity (same genre)

    def test_diversity_score_no_recent_items(self) -> None:
        """Without recent items, diversity score is neutral."""
        item = make_item(item_id="u1", genres="Mystery")
        score = RecommendationRanker._calculate_diversity_score(item, set())
        assert score == 0.5

    def test_diversity_score_no_genres_on_item(self) -> None:
        """Items without genre metadata get a neutral diversity score."""
        recently_completed = [
            make_item(
                item_id="c1",
                genres="Science Fiction",
                status=ConsumptionStatus.COMPLETED,
            ),
        ]
        no_genre_item = make_item(item_id="u1")
        score = RecommendationRanker._calculate_diversity_score(
            no_genre_item,
            RecommendationRanker._collect_recent_genres(recently_completed),
        )
        assert score == 0.5

    def test_diversity_weight_affects_ranking(self, neutral_preferences) -> None:
        """When diversity_weight > 0, genre-different items rank higher."""
        recently_completed = [
            make_item(
                item_id="c1",
                genres="Science Fiction",
                status=ConsumptionStatus.COMPLETED,
            ),
            make_item(
                item_id="c2",
                genres="Science Fiction",
                status=ConsumptionStatus.COMPLETED,
            ),
        ]

        # Two candidates with equal similarity
        scifi_item = make_item(
            item_id="u1", title="Sci-Fi Book", genres="Science Fiction"
        )
        mystery_item = make_item(item_id="u2", title="Mystery Book", genres="Mystery")

        ranker = RecommendationRanker(
            similarity_weight=0.5, preference_weight=0.0, diversity_weight=0.5
        )

        ranked = ranker.rank(
            candidates=[(scifi_item, 0.8), (mystery_item, 0.8)],
            preferences=neutral_preferences,
            content_type=ContentType.BOOK,
            recently_completed=recently_completed,
        )

        # Mystery should rank higher due to diversity bonus
        assert ranked[0][0].title == "Mystery Book"
        assert ranked[0][2]["diversity_bonus"] > ranked[1][2]["diversity_bonus"]

    def test_zero_diversity_weight_no_effect(self, neutral_preferences) -> None:
        """When diversity_weight is 0, diversity bonus has no effect on ranking."""
        recently_completed = [
            make_item(
                item_id="c1",
                genres="Science Fiction",
                status=ConsumptionStatus.COMPLETED,
            ),
        ]

        ranker = RecommendationRanker(
            similarity_weight=1.0, preference_weight=0.0, diversity_weight=0.0
        )

        scifi_item = make_item(
            item_id="u1", title="Sci-Fi Book", genres="Science Fiction"
        )
        mystery_item = make_item(item_id="u2", title="Mystery Book", genres="Mystery")

        ranked = ranker.rank(
            candidates=[(scifi_item, 0.9), (mystery_item, 0.7)],
            preferences=neutral_preferences,
            content_type=ContentType.BOOK,
            recently_completed=recently_completed,
        )

        # Sci-fi should still rank first due to higher similarity with no diversity weight
        assert ranked[0][0].title == "Sci-Fi Book"
