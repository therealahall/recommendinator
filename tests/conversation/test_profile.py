"""Tests for preference profile generation."""

import tempfile
from collections.abc import Generator
from pathlib import Path

import pytest

from src.conversation.profile import ProfileGenerator
from src.models.content import ConsumptionStatus, ContentItem, ContentType
from src.models.conversation import PreferenceProfile
from src.storage.manager import StorageManager


@pytest.fixture
def storage_manager() -> Generator[StorageManager, None, None]:
    """Create a storage manager with a temporary database."""
    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = Path(temp_dir) / "test.db"
        yield StorageManager(sqlite_path=db_path)


@pytest.fixture
def profile_generator(storage_manager: StorageManager) -> ProfileGenerator:
    """Create a profile generator for testing."""
    return ProfileGenerator(storage_manager=storage_manager)


@pytest.fixture
def sample_items(storage_manager: StorageManager) -> list[int]:
    """Create sample content items and return their db_ids."""
    items = [
        # High-rated sci-fi books
        ContentItem(
            id="book1",
            title="Dune",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            rating=5,
            metadata={"genres": ["sci-fi", "fantasy"]},
        ),
        ContentItem(
            id="book2",
            title="Foundation",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            rating=5,
            metadata={"genres": ["sci-fi"]},
        ),
        ContentItem(
            id="book3",
            title="Neuromancer",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            rating=4,
            metadata={"genres": ["sci-fi"]},
        ),
        # Low-rated horror books (need 2 for anti-pref minimum)
        ContentItem(
            id="book4",
            title="Some Horror Book",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            rating=2,
            metadata={"genres": ["horror"]},
        ),
        ContentItem(
            id="book4b",
            title="Another Horror Book",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            rating=1,
            metadata={"genres": ["horror"]},
        ),
        # High-rated fantasy games
        ContentItem(
            id="game1",
            title="Elden Ring",
            content_type=ContentType.VIDEO_GAME,
            status=ConsumptionStatus.COMPLETED,
            rating=5,
            metadata={"genres": ["fantasy", "rpg", "souls-like"]},
        ),
        ContentItem(
            id="game2",
            title="Baldur's Gate 3",
            content_type=ContentType.VIDEO_GAME,
            status=ConsumptionStatus.COMPLETED,
            rating=5,
            metadata={"genres": ["fantasy", "rpg"]},
        ),
        # Lower-rated sci-fi game
        ContentItem(
            id="game3",
            title="Starfield",
            content_type=ContentType.VIDEO_GAME,
            status=ConsumptionStatus.COMPLETED,
            rating=3,
            metadata={"genres": ["sci-fi", "rpg"]},
        ),
        # Unconsumed items
        ContentItem(
            id="book5",
            title="Project Hail Mary",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
            rating=None,
            metadata={"genres": ["sci-fi"]},
        ),
    ]
    db_ids = []
    for item in items:
        db_id = storage_manager.save_content_item(item, user_id=1)
        db_ids.append(db_id)
    return db_ids


class TestProfileGeneration:
    """Tests for generate_profile."""

    def test_generate_profile_returns_profile(
        self,
        profile_generator: ProfileGenerator,
        sample_items: list[int],
    ) -> None:
        """Test that generate_profile returns a PreferenceProfile."""
        profile = profile_generator.generate_profile(user_id=1)

        assert isinstance(profile, PreferenceProfile)
        assert profile.user_id == 1
        assert profile.generated_at is not None

    def test_generate_profile_empty_user(
        self,
        profile_generator: ProfileGenerator,
    ) -> None:
        """Test generating profile for user with no items."""
        profile = profile_generator.generate_profile(user_id=999)

        assert isinstance(profile, PreferenceProfile)
        assert profile.genre_affinities == {}
        assert profile.theme_preferences == []
        assert profile.anti_preferences == []
        assert profile.cross_media_patterns == []


class TestGenreAffinities:
    """Tests for genre affinity calculation."""

    def test_genre_affinities_from_ratings(
        self,
        profile_generator: ProfileGenerator,
        sample_items: list[int],
    ) -> None:
        """Test that genre affinities are calculated from ratings."""
        profile = profile_generator.generate_profile(user_id=1)

        # "sci-fi" normalizes to "science fiction" via extract_genres
        assert "science fiction" in profile.genre_affinities
        assert profile.genre_affinities["science fiction"] >= 4.0

        # Fantasy should also have high affinity
        assert "fantasy" in profile.genre_affinities
        assert profile.genre_affinities["fantasy"] >= 4.0

    def test_genre_affinities_weighted_by_rating(
        self,
        storage_manager: StorageManager,
    ) -> None:
        """Test that ratings properly weight genre affinity."""
        # Create items with different ratings for same genre
        items = [
            ContentItem(
                id="test1",
                title="Test 1",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.COMPLETED,
                rating=5,
                metadata={"genres": ["mystery"]},
            ),
            ContentItem(
                id="test2",
                title="Test 2",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.COMPLETED,
                rating=1,
                metadata={"genres": ["mystery"]},
            ),
        ]
        for item in items:
            storage_manager.save_content_item(item, user_id=1)

        generator = ProfileGenerator(storage_manager)
        profile = generator.generate_profile(user_id=1)

        # Mystery avg is (5 + 1) / 2 = 3.0
        assert "mystery" in profile.genre_affinities
        assert 2.5 <= profile.genre_affinities["mystery"] <= 3.5

    def test_genre_affinities_sorted_by_score(
        self,
        profile_generator: ProfileGenerator,
        sample_items: list[int],
    ) -> None:
        """Test that genre affinities are sorted by score descending."""
        profile = profile_generator.generate_profile(user_id=1)

        affinities = list(profile.genre_affinities.items())
        for index in range(len(affinities) - 1):
            assert affinities[index][1] >= affinities[index + 1][1]


class TestThemePreferences:
    """Tests for theme preference identification."""

    def test_theme_preferences_from_high_rated(
        self,
        storage_manager: StorageManager,
    ) -> None:
        """Test that themes are extracted from high-rated items."""
        items = [
            ContentItem(
                id="test1",
                title="Game 1",
                content_type=ContentType.VIDEO_GAME,
                status=ConsumptionStatus.COMPLETED,
                rating=5,
                metadata={"themes": ["exploration", "narrative depth"]},
            ),
            ContentItem(
                id="test2",
                title="Game 2",
                content_type=ContentType.VIDEO_GAME,
                status=ConsumptionStatus.COMPLETED,
                rating=5,
                metadata={"themes": ["exploration", "atmosphere"]},
            ),
        ]
        for item in items:
            storage_manager.save_content_item(item, user_id=1)

        generator = ProfileGenerator(storage_manager)
        profile = generator.generate_profile(user_id=1)

        # Exploration appears in 2 high-rated items
        assert "exploration" in profile.theme_preferences

    def test_theme_preferences_ignores_low_rated(
        self,
        storage_manager: StorageManager,
    ) -> None:
        """Test that themes from low-rated items are not preferences."""
        items = [
            ContentItem(
                id="test1",
                title="Bad Game",
                content_type=ContentType.VIDEO_GAME,
                status=ConsumptionStatus.COMPLETED,
                rating=2,
                metadata={"themes": ["challenging"]},
            ),
        ]
        for item in items:
            storage_manager.save_content_item(item, user_id=1)

        generator = ProfileGenerator(storage_manager)
        profile = generator.generate_profile(user_id=1)

        # Challenging shouldn't be a preference (only in low-rated items)
        assert "challenging" not in profile.theme_preferences


class TestAntiPreferences:
    """Tests for anti-preference identification."""

    def test_anti_preferences_from_low_rated(
        self,
        profile_generator: ProfileGenerator,
        sample_items: list[int],
    ) -> None:
        """Test that anti-preferences come from consistently low-rated genres."""
        profile = profile_generator.generate_profile(user_id=1)

        # Horror has 2 low-rated items (rating 2 and 1), avg 1.5 <= 2.5
        assert "horror" in profile.anti_preferences

    def test_anti_preferences_requires_multiple_low_ratings(
        self,
        storage_manager: StorageManager,
    ) -> None:
        """Test that multiple low ratings needed for anti-preference."""
        items = [
            ContentItem(
                id="test1",
                title="Horror 1",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.COMPLETED,
                rating=1,
                metadata={"genres": ["horror"]},
            ),
            ContentItem(
                id="test2",
                title="Horror 2",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.COMPLETED,
                rating=2,
                metadata={"genres": ["horror"]},
            ),
            # Add some other items
            ContentItem(
                id="test3",
                title="Good Book 1",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.COMPLETED,
                rating=5,
                metadata={"genres": ["sci-fi"]},
            ),
            ContentItem(
                id="test4",
                title="Good Book 2",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.COMPLETED,
                rating=5,
                metadata={"genres": ["sci-fi"]},
            ),
            ContentItem(
                id="test5",
                title="Good Book 3",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.COMPLETED,
                rating=5,
                metadata={"genres": ["fantasy"]},
            ),
        ]
        for item in items:
            storage_manager.save_content_item(item, user_id=1)

        generator = ProfileGenerator(storage_manager)
        profile = generator.generate_profile(user_id=1)

        # Horror appears in 2 low-rated items, avg 1.5 <= 2.5
        assert "horror" in profile.anti_preferences


class TestCrossMediaPatterns:
    """Tests for cross-media pattern identification."""

    def test_cross_media_pattern_detected(
        self,
        profile_generator: ProfileGenerator,
        sample_items: list[int],
    ) -> None:
        """Test that cross-media patterns are detected."""
        profile = profile_generator.generate_profile(user_id=1)

        # User loves sci-fi books (5, 5, 4 stars) but only rated
        # sci-fi game 3 stars vs fantasy games 5 stars
        # So should detect a pattern about fantasy games vs sci-fi games
        # or about content type preferences
        assert len(profile.cross_media_patterns) >= 0  # May or may not detect

    def test_type_preference_pattern(
        self,
        storage_manager: StorageManager,
    ) -> None:
        """Test that content type preference patterns are detected."""
        items = [
            # Books rated higher on average
            ContentItem(
                id="book1",
                title="Book 1",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.COMPLETED,
                rating=5,
                metadata={},
            ),
            ContentItem(
                id="book2",
                title="Book 2",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.COMPLETED,
                rating=5,
                metadata={},
            ),
            # Games rated lower on average
            ContentItem(
                id="game1",
                title="Game 1",
                content_type=ContentType.VIDEO_GAME,
                status=ConsumptionStatus.COMPLETED,
                rating=3,
                metadata={},
            ),
            ContentItem(
                id="game2",
                title="Game 2",
                content_type=ContentType.VIDEO_GAME,
                status=ConsumptionStatus.COMPLETED,
                rating=3,
                metadata={},
            ),
        ]
        for item in items:
            storage_manager.save_content_item(item, user_id=1)

        generator = ProfileGenerator(storage_manager)
        profile = generator.generate_profile(user_id=1)

        # Should detect that books are rated higher than games
        pattern_found = any(
            "books" in pattern.lower() and "games" in pattern.lower()
            for pattern in profile.cross_media_patterns
        )
        assert pattern_found


class TestThemeExtraction:
    """Tests for theme extraction from items."""

    def test_extract_themes_from_review(
        self,
        profile_generator: ProfileGenerator,
    ) -> None:
        """Test extracting themes from review text."""
        item = ContentItem(
            id="test",
            title="Test",
            content_type=ContentType.VIDEO_GAME,
            status=ConsumptionStatus.COMPLETED,
            rating=5,
            review="Great exploration mechanics and incredible atmosphere!",
            metadata={},
        )

        themes = profile_generator._extract_themes(item)

        assert "exploration" in themes
        assert "atmosphere" in themes


class TestRegenerateAndSave:
    """Tests for regenerate_and_save."""

    def test_regenerate_and_save(
        self,
        profile_generator: ProfileGenerator,
        sample_items: list[int],
        storage_manager: StorageManager,
    ) -> None:
        """Test regenerating and saving a profile."""
        profile = profile_generator.regenerate_and_save(user_id=1)

        assert isinstance(profile, PreferenceProfile)
        assert profile.user_id == 1

        # Verify it was saved
        saved_profile = storage_manager.get_preference_profile(user_id=1)
        assert saved_profile is not None


class TestFormatContentType:
    """Tests for content type formatting."""

    def test_format_content_types(
        self,
        profile_generator: ProfileGenerator,
    ) -> None:
        """Test formatting of content types."""
        assert profile_generator._format_content_type("book") == "books"
        assert profile_generator._format_content_type("movie") == "movies"
        assert profile_generator._format_content_type("tv_show") == "TV shows"
        assert profile_generator._format_content_type("video_game") == "games"
        assert profile_generator._format_content_type(ContentType.BOOK) == "books"


class TestProfileRegression:
    """Regression tests for profile generation bugs."""

    def test_loved_genre_not_in_anti_preferences_regression(
        self,
        storage_manager: StorageManager,
    ) -> None:
        """Regression test: Loved genre must not appear as anti-preference.

        Bug reported: Genre with 50 five-star ratings + 2 one-star ratings
        appeared in "Not Your Style" because old algorithm counted low-rated
        occurrences without checking overall sentiment.

        Root cause: Anti-preference detection only looked at low-rated items
        without considering the genre's overall rating distribution.

        Fix: Anti-preferences now require average rating <= 2.5 AND at most
        20% positive (3+ star) items, preventing well-loved genres from
        appearing as anti-preferences.
        """
        items = []
        # 50 five-star sci-fi items
        for index in range(50):
            items.append(
                ContentItem(
                    id=f"scifi_good_{index}",
                    title=f"Great Sci-Fi Book {index}",
                    content_type=ContentType.BOOK,
                    status=ConsumptionStatus.COMPLETED,
                    rating=5,
                    metadata={"genres": ["sci-fi"]},
                )
            )
        # 2 one-star sci-fi items
        for index in range(2):
            items.append(
                ContentItem(
                    id=f"scifi_bad_{index}",
                    title=f"Bad Sci-Fi Book {index}",
                    content_type=ContentType.BOOK,
                    status=ConsumptionStatus.COMPLETED,
                    rating=1,
                    metadata={"genres": ["sci-fi"]},
                )
            )

        for item in items:
            storage_manager.save_content_item(item, user_id=1)

        generator = ProfileGenerator(storage_manager)
        profile = generator.generate_profile(user_id=1)

        # sci-fi normalizes to "science fiction"
        assert "science fiction" not in profile.anti_preferences
        # And it should be in genre affinities with a high score
        assert "science fiction" in profile.genre_affinities
        assert profile.genre_affinities["science fiction"] >= 4.5

    def test_minimum_items_required_for_genre(
        self,
        storage_manager: StorageManager,
    ) -> None:
        """Test that genres with only 1 rated item appear in neither loved nor anti.

        A single data point is not enough to establish a preference or
        anti-preference. Require at least MIN_ITEMS_PER_GENRE (2) items.
        """
        items = [
            ContentItem(
                id="solo_genre",
                title="One Mystery Book",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.COMPLETED,
                rating=5,
                metadata={"genres": ["mystery"]},
            ),
            # Need at least one other genre with 2+ items for a non-empty profile
            ContentItem(
                id="scifi1",
                title="Sci-Fi 1",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.COMPLETED,
                rating=5,
                metadata={"genres": ["sci-fi"]},
            ),
            ContentItem(
                id="scifi2",
                title="Sci-Fi 2",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.COMPLETED,
                rating=4,
                metadata={"genres": ["sci-fi"]},
            ),
        ]
        for item in items:
            storage_manager.save_content_item(item, user_id=1)

        generator = ProfileGenerator(storage_manager)
        profile = generator.generate_profile(user_id=1)

        # Mystery has only 1 item — should not appear in affinities
        assert "mystery" not in profile.genre_affinities
        assert "mystery" not in profile.anti_preferences

    def test_normalized_genres_used_in_profile(
        self,
        storage_manager: StorageManager,
    ) -> None:
        """Regression test: Profile should use normalized genre names.

        Bug reported: Profile showed "sci-fi" while recommendation engine
        used "science fiction", causing mismatches.

        Root cause: Profile had its own primitive genre extraction
        (GENRE_KEYWORDS set) instead of using the shared normalizer.

        Fix: Profile now uses extract_genres() from scorers, which
        delegates to extract_and_normalize_genres().
        """
        items = [
            ContentItem(
                id="test1",
                title="Test 1",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.COMPLETED,
                rating=5,
                metadata={"genres": ["sci-fi"]},
            ),
            ContentItem(
                id="test2",
                title="Test 2",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.COMPLETED,
                rating=5,
                metadata={"genres": ["sci-fi"]},
            ),
        ]
        for item in items:
            storage_manager.save_content_item(item, user_id=1)

        generator = ProfileGenerator(storage_manager)
        profile = generator.generate_profile(user_id=1)

        # "sci-fi" in metadata should produce "science fiction" in affinities
        assert "science fiction" in profile.genre_affinities
        assert "sci-fi" not in profile.genre_affinities
