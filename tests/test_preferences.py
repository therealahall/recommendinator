"""Tests for preference analysis."""

from src.models.content import ConsumptionStatus, ContentItem, ContentType
from src.recommendations.preferences import PreferenceAnalyzer, UserPreferences


def test_preference_analyzer_empty():
    """Test preference analyzer with empty list."""
    analyzer = PreferenceAnalyzer()
    preferences = analyzer.analyze([])

    assert preferences.total_items == 0
    assert preferences.average_rating == 0.0
    assert len(preferences.preferred_authors) == 0


def test_preference_analyzer_basic():
    """Test basic preference analysis."""
    analyzer = PreferenceAnalyzer(min_rating=4)

    items = [
        ContentItem(
            id="1",
            title="Book 1",
            author="Author A",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            rating=5,
        ),
        ContentItem(
            id="2",
            title="Book 2",
            author="Author A",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            rating=4,
        ),
        ContentItem(
            id="3",
            title="Book 3",
            author="Author B",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            rating=3,
        ),
    ]

    preferences = analyzer.analyze(items)

    assert preferences.total_items == 3
    assert preferences.average_rating == 4.0
    assert "author a" in preferences.preferred_authors
    assert preferences.preferred_authors["author a"] > 0


def test_preference_analyzer_with_genre():
    """Test preference analysis with genre metadata."""
    analyzer = PreferenceAnalyzer(min_rating=4)

    items = [
        ContentItem(
            id="1",
            title="Book 1",
            author="Author A",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            rating=5,
            metadata={"genre": "Science Fiction"},
        ),
        ContentItem(
            id="2",
            title="Book 2",
            author="Author B",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            rating=4,
            metadata={"genre": "Science Fiction"},
        ),
    ]

    preferences = analyzer.analyze(items)

    assert "science fiction" in preferences.preferred_genres
    assert preferences.preferred_genres["science fiction"] > 0


def test_user_preferences_get_author_score():
    """Test getting author preference score."""
    preferences = UserPreferences(
        preferred_authors={"author a": 0.8, "author b": 0.5},
        preferred_genres={},
        average_rating=4.5,
        total_items=10,
    )

    assert preferences.get_author_score("Author A") == 0.8
    assert preferences.get_author_score("Author B") == 0.5
    assert preferences.get_author_score("Unknown Author") == 0.0
    assert preferences.get_author_score(None) == 0.0


def test_user_preferences_get_genre_score():
    """Test getting genre preference score."""
    preferences = UserPreferences(
        preferred_authors={},
        preferred_genres={"science fiction": 0.9, "fantasy": 0.6},
        average_rating=4.5,
        total_items=10,
    )

    assert preferences.get_genre_score("Science Fiction") == 0.9
    assert preferences.get_genre_score("Fantasy") == 0.6
    assert preferences.get_genre_score("Unknown Genre") == 0.0
    assert preferences.get_genre_score(None) == 0.0


def test_preference_analyzer_steam_genres():
    """Test preference analysis with Steam game genres (list format)."""
    analyzer = PreferenceAnalyzer(min_rating=4)

    items = [
        ContentItem(
            id="1",
            title="Mass Effect",
            content_type=ContentType.VIDEO_GAME,
            status=ConsumptionStatus.COMPLETED,
            rating=5,
            metadata={"genres": ["Action", "RPG", "Science Fiction"]},
        ),
        ContentItem(
            id="2",
            title="The Expanse",
            content_type=ContentType.TV_SHOW,
            status=ConsumptionStatus.COMPLETED,
            rating=5,
            metadata={"genre": "Science Fiction"},
        ),
    ]

    preferences = analyzer.analyze(items)

    # Should extract genres from both Steam games (list) and TV shows (single)
    assert "science fiction" in preferences.preferred_genres
    assert "action" in preferences.preferred_genres
    assert "rpg" in preferences.preferred_genres
    assert preferences.preferred_genres["science fiction"] > 0


def test_preference_analyzer_cross_content_type():
    """Test preference analysis across multiple content types."""
    analyzer = PreferenceAnalyzer(min_rating=4)

    items = [
        # Sci-fi books
        ContentItem(
            id="1",
            title="Dune",
            author="Frank Herbert",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            rating=5,
            metadata={"genre": "Science Fiction"},
        ),
        # Sci-fi games
        ContentItem(
            id="2",
            title="Mass Effect",
            content_type=ContentType.VIDEO_GAME,
            status=ConsumptionStatus.COMPLETED,
            rating=5,
            metadata={"genres": ["Action", "RPG", "Science Fiction"]},
        ),
        # Sci-fi TV show
        ContentItem(
            id="3",
            title="The Expanse",
            content_type=ContentType.TV_SHOW,
            status=ConsumptionStatus.COMPLETED,
            rating=4,
            metadata={"genre": "Science Fiction"},
        ),
    ]

    preferences = analyzer.analyze(items)

    # Should extract preferences from all content types
    assert preferences.total_items == 3
    assert "science fiction" in preferences.preferred_genres
    assert "frank herbert" in preferences.preferred_authors
    # Sci-fi should have high score from multiple sources
    assert preferences.preferred_genres["science fiction"] > 0.5
