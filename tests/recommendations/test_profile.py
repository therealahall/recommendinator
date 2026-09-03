import tempfile
from collections.abc import Generator
from pathlib import Path

import pytest

from src.models.content import ConsumptionStatus, ContentItem, ContentType
from src.recommendations.content_length import LengthPreference
from src.recommendations.profile import (
    PreferenceProfile,
    ProfileGenerator,
    profile_payload,
)
from src.storage.manager import StorageManager


@pytest.fixture
def storage_manager() -> Generator[StorageManager, None, None]:
    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = Path(temp_dir) / "test.db"
        yield StorageManager(sqlite_path=db_path)


@pytest.fixture
def profile_generator(storage_manager: StorageManager) -> ProfileGenerator:
    return ProfileGenerator(storage_manager=storage_manager)


@pytest.fixture
def sample_items(storage_manager: StorageManager) -> list[int]:
    items = [
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
        ContentItem(
            id="game3",
            title="Starfield",
            content_type=ContentType.VIDEO_GAME,
            status=ConsumptionStatus.COMPLETED,
            rating=3,
            metadata={"genres": ["sci-fi", "rpg"]},
        ),
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
    def test_generate_profile_empty_user(
        self,
        profile_generator: ProfileGenerator,
    ) -> None:
        profile = profile_generator.generate_profile(user_id=999)

        assert isinstance(profile, PreferenceProfile)
        assert profile.genre_affinities == {}
        assert profile.theme_preferences == []
        assert profile.anti_preferences == []
        assert profile.cross_media_patterns == []


class TestGenreAffinities:
    def test_genre_affinities_from_ratings(
        self,
        profile_generator: ProfileGenerator,
        sample_items: list[int],
    ) -> None:
        profile = profile_generator.generate_profile(user_id=1)

        assert "science fiction" in profile.genre_affinities
        assert profile.genre_affinities["science fiction"] >= 4.0

        assert "fantasy" in profile.genre_affinities
        assert profile.genre_affinities["fantasy"] >= 4.0

    def test_genre_affinities_weighted_by_rating(
        self,
        storage_manager: StorageManager,
    ) -> None:
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

        assert "mystery" in profile.genre_affinities
        assert 2.5 <= profile.genre_affinities["mystery"] <= 3.5


class TestThemePreferences:
    def test_theme_preferences_from_high_rated(
        self,
        storage_manager: StorageManager,
    ) -> None:
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

        assert "exploration" in profile.theme_preferences

    def test_length_descriptors_are_not_themes(
        self,
        storage_manager: StorageManager,
    ) -> None:
        length_words = [
            preference.value
            for preference in LengthPreference
            if preference is not LengthPreference.ANY
        ]
        for index in range(2):
            storage_manager.save_content_item(
                ContentItem(
                    id=f"test{index}",
                    title=f"Game {index}",
                    content_type=ContentType.VIDEO_GAME,
                    status=ConsumptionStatus.COMPLETED,
                    rating=5,
                    metadata={"tags": [*length_words, "exploration"]},
                ),
                user_id=1,
            )

        generator = ProfileGenerator(storage_manager)
        profile = generator.generate_profile(user_id=1)

        assert profile.theme_preferences == ["exploration"]

    def test_a_negated_review_word_does_not_credit_its_root_theme(
        self,
        storage_manager: StorageManager,
    ) -> None:
        for index in range(2):
            storage_manager.save_content_item(
                ContentItem(
                    id=f"review{index}",
                    title=f"Book {index}",
                    content_type=ContentType.BOOK,
                    status=ConsumptionStatus.COMPLETED,
                    rating=5,
                    review=(
                        "Unemotional to a fault, but the character development "
                        "and thought-provoking ending stayed with me."
                    ),
                ),
                user_id=1,
            )

        generator = ProfileGenerator(storage_manager)
        profile = generator.generate_profile(user_id=1)

        assert set(profile.theme_preferences) == {
            "character development",
            "thought-provoking",
        }


class TestAntiPreferences:
    def test_anti_preferences_from_low_rated(
        self,
        profile_generator: ProfileGenerator,
        sample_items: list[int],
    ) -> None:
        profile = profile_generator.generate_profile(user_id=1)

        assert "horror" in profile.anti_preferences


class TestCrossMediaPatterns:
    def test_type_preference_pattern(
        self,
        storage_manager: StorageManager,
    ) -> None:
        items = [
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

        pattern_found = any(
            "books" in pattern.lower() and "games" in pattern.lower()
            for pattern in profile.cross_media_patterns
        )
        assert pattern_found


class TestThemeExtraction:
    def test_extract_themes_from_review(
        self,
        profile_generator: ProfileGenerator,
    ) -> None:
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
    def test_regenerate_and_save(
        self,
        profile_generator: ProfileGenerator,
        sample_items: list[int],
        storage_manager: StorageManager,
    ) -> None:
        profile = profile_generator.regenerate_and_save(user_id=1)

        assert isinstance(profile, PreferenceProfile)
        assert profile.user_id == 1

        saved_profile = storage_manager.profiles.get(user_id=1)
        assert saved_profile is not None

    def test_stored_row_carries_the_keys_the_payload_reads(
        self,
        profile_generator: ProfileGenerator,
        sample_items: list[int],
        storage_manager: StorageManager,
    ) -> None:
        generated = profile_generator.regenerate_and_save(user_id=1)

        payload = profile_payload(1, storage_manager.profiles.get(user_id=1))

        assert payload["genre_affinities"] == generated.genre_affinities
        assert payload["genre_affinities"]
        assert payload["generated_at"]


class TestProfileRegression:
    def test_loved_genre_not_in_anti_preferences_regression(
        self,
        storage_manager: StorageManager,
    ) -> None:
        items = []
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

        assert "science fiction" not in profile.anti_preferences
        assert "science fiction" in profile.genre_affinities
        assert profile.genre_affinities["science fiction"] >= 4.5

    def test_minimum_items_required_for_genre(
        self,
        storage_manager: StorageManager,
    ) -> None:
        items = [
            ContentItem(
                id="solo_genre",
                title="One Mystery Book",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.COMPLETED,
                rating=5,
                metadata={"genres": ["mystery"]},
            ),
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

        assert "mystery" not in profile.genre_affinities
        assert "mystery" not in profile.anti_preferences

    def test_normalized_genres_used_in_profile(
        self,
        storage_manager: StorageManager,
    ) -> None:
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

        assert "science fiction" in profile.genre_affinities
        assert "sci-fi" not in profile.genre_affinities

    def test_niche_tags_excluded_from_profile_regression(
        self,
        storage_manager: StorageManager,
    ) -> None:
        items = [
            ContentItem(
                id="test1",
                title="Book 1",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.COMPLETED,
                rating=5,
                metadata={"genres": ["sci-fi"], "tags": ["hacker", "computer"]},
            ),
            ContentItem(
                id="test2",
                title="Book 2",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.COMPLETED,
                rating=5,
                metadata={"genres": ["sci-fi"], "tags": ["hacker", "grand"]},
            ),
            ContentItem(
                id="test3",
                title="Book 3",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.COMPLETED,
                rating=5,
                metadata={"genres": ["fantasy"], "tags": ["wizards", "grand"]},
            ),
            ContentItem(
                id="test4",
                title="Book 4",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.COMPLETED,
                rating=5,
                metadata={"genres": ["fantasy"], "tags": ["wizards"]},
            ),
        ]
        for item in items:
            storage_manager.save_content_item(item, user_id=1)

        generator = ProfileGenerator(storage_manager)
        profile = generator.generate_profile(user_id=1)

        assert "science fiction" in profile.genre_affinities
        assert "fantasy" in profile.genre_affinities

        for niche_tag in ("hacker", "computer", "wizards", "grand"):
            assert niche_tag not in profile.genre_affinities

    def test_divergence_requires_data_in_both_types_regression(
        self,
        storage_manager: StorageManager,
    ) -> None:
        items = [
            ContentItem(
                id="book1",
                title="Fantasy Book 1",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.COMPLETED,
                rating=5,
                metadata={"genres": ["fantasy"]},
            ),
            ContentItem(
                id="book2",
                title="Fantasy Book 2",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.COMPLETED,
                rating=5,
                metadata={"genres": ["fantasy"]},
            ),
            ContentItem(
                id="tv1",
                title="Drama Show 1",
                content_type=ContentType.TV_SHOW,
                status=ConsumptionStatus.COMPLETED,
                rating=4,
                metadata={"genres": ["drama"]},
            ),
            ContentItem(
                id="tv2",
                title="Drama Show 2",
                content_type=ContentType.TV_SHOW,
                status=ConsumptionStatus.COMPLETED,
                rating=4,
                metadata={"genres": ["drama"]},
            ),
        ]
        for item in items:
            storage_manager.save_content_item(item, user_id=1)

        generator = ProfileGenerator(storage_manager)
        profile = generator.generate_profile(user_id=1)

        for pattern in profile.cross_media_patterns:
            assert "fantasy" not in pattern.lower() or "but not" not in pattern.lower()


class TestProfileIgnoredSignalRegression:
    def test_ignored_items_excluded_from_profile_regression(
        self, profile_generator: ProfileGenerator, storage_manager: StorageManager
    ) -> None:
        for index, title in enumerate(("Dune", "Foundation")):
            storage_manager.save_content_item(
                ContentItem(
                    id=f"signal{index}",
                    title=title,
                    content_type=ContentType.BOOK,
                    status=ConsumptionStatus.COMPLETED,
                    rating=5,
                    metadata={"genres": ["sci-fi"]},
                ),
                user_id=1,
            )
        for index in range(2):
            db_id = storage_manager.save_content_item(
                ContentItem(
                    id=f"western{index}",
                    title=f"Western {index}",
                    content_type=ContentType.BOOK,
                    status=ConsumptionStatus.COMPLETED,
                    rating=5,
                    metadata={"genres": ["western"]},
                ),
                user_id=1,
            )
            storage_manager.set_item_ignored(db_id, True, user_id=1)

        profile = profile_generator.generate_profile(user_id=1)

        assert "western" not in profile.genre_affinities
        assert "science fiction" in profile.genre_affinities
