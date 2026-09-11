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


def _save_rated(
    storage: StorageManager,
    genre: str,
    ratings: list[int],
    content_type: ContentType = ContentType.BOOK,
    author: str | None = None,
) -> None:
    for index, rating in enumerate(ratings):
        storage.save_content_item(
            ContentItem(
                id=f"{genre}-{author}-{index}",
                title=f"{genre.title()} {index}",
                content_type=content_type,
                status=ConsumptionStatus.COMPLETED,
                rating=rating,
                author=author,
                metadata={"genres": [genre]},
            ),
            user_id=1,
        )


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
                metadata={"tags": ["exploration", "narrative depth"]},
            ),
            ContentItem(
                id="test2",
                title="Game 2",
                content_type=ContentType.VIDEO_GAME,
                status=ConsumptionStatus.COMPLETED,
                rating=5,
                metadata={"tags": ["exploration", "atmosphere"]},
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
    def test_a_liked_genre_below_the_operators_own_average_is_not_an_anti_preference(
        self, profile_generator: ProfileGenerator, storage_manager: StorageManager
    ) -> None:
        """The panel heads this list "Not your style", and a below-average bar
        puts half of any high-rating library under it."""
        _save_rated(storage_manager, "fantasy", [5, 5, 5, 5])
        _save_rated(storage_manager, "horror", [3, 3])

        profile = profile_generator.generate_profile(user_id=1)

        assert "horror" in profile.liked_genres
        assert profile.anti_preferences == []

    def test_anti_preferences_run_worst_mean_first(
        self, profile_generator: ProfileGenerator, storage_manager: StorageManager
    ) -> None:
        _save_rated(storage_manager, "horror", [1, 1, 2])
        _save_rated(storage_manager, "western", [1, 2, 2, 4])
        _save_rated(storage_manager, "romance", [2, 2, 4])

        profile = profile_generator.generate_profile(user_id=1)

        assert profile.anti_preferences == ["horror", "western", "romance"]


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
        # One clock: the generator's own UTC stamp, not the row's column, which
        # ticks on a different write in a different timezone.
        assert generated.generated_at is not None
        assert payload["generated_at"] == generated.generated_at.isoformat()


class TestProfileRegression:
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

    def test_a_genre_the_operator_only_ignores_is_an_anti_preference(
        self, profile_generator: ProfileGenerator, storage_manager: StorageManager
    ) -> None:
        """Ignoring is a verdict; the signal read drops it before anything sees
        it, so an ignored-only genre used to leave no trace at all."""
        _save_rated(storage_manager, "science fiction", [5, 5])
        for index in range(2):
            db_id = storage_manager.save_content_item(
                ContentItem(
                    id=f"western{index}",
                    title=f"Western {index}",
                    content_type=ContentType.BOOK,
                    status=ConsumptionStatus.UNREAD,
                    metadata={"genres": ["western"]},
                ),
                user_id=1,
            )
            storage_manager.set_item_ignored(db_id, True, user_id=1)

        profile = profile_generator.generate_profile(user_id=1)

        assert "western" in profile.anti_preferences

    def test_a_liked_genre_the_operator_also_ignores_is_not_an_anti_preference(
        self, profile_generator: ProfileGenerator, storage_manager: StorageManager
    ) -> None:
        _save_rated(storage_manager, "western", [5, 5])
        for index in range(3):
            db_id = storage_manager.save_content_item(
                ContentItem(
                    id=f"ignored-western{index}",
                    title=f"Dismissed Western {index}",
                    content_type=ContentType.BOOK,
                    status=ConsumptionStatus.UNREAD,
                    metadata={"genres": ["western"]},
                ),
                user_id=1,
            )
            storage_manager.set_item_ignored(db_id, True, user_id=1)

        profile = profile_generator.generate_profile(user_id=1)

        assert "western" in profile.liked_genres
        assert profile.anti_preferences == []

    def test_a_disliked_genre_the_operator_also_ignores_is_listed_once(
        self, profile_generator: ProfileGenerator, storage_manager: StorageManager
    ) -> None:
        _save_rated(storage_manager, "horror", [1, 2])
        for index in range(3):
            db_id = storage_manager.save_content_item(
                ContentItem(
                    id=f"ignored-horror{index}",
                    title=f"Dismissed Horror {index}",
                    content_type=ContentType.BOOK,
                    status=ConsumptionStatus.UNREAD,
                    metadata={"genres": ["horror"]},
                ),
                user_id=1,
            )
            storage_manager.set_item_ignored(db_id, True, user_id=1)

        profile = profile_generator.generate_profile(user_id=1)

        assert profile.anti_preferences == ["horror"]


class TestGenreVocabulary:
    def test_a_genre_the_profile_allowlist_dropped_reaches_the_profile(
        self, profile_generator: ProfileGenerator, storage_manager: StorageManager
    ) -> None:
        """A second allowlist on top of the genre normalizer silently dropped
        half the game vocabulary, simulation and racing among it."""
        _save_rated(storage_manager, "simulation", [4, 3], ContentType.VIDEO_GAME)
        _save_rated(storage_manager, "racing", [4, 4], ContentType.VIDEO_GAME)
        _save_rated(
            storage_manager, "walking simulator", [5, 4], ContentType.VIDEO_GAME
        )

        profile = profile_generator.generate_profile(user_id=1)

        assert profile.genre_affinities["simulation"] == 3.5
        assert profile.genre_affinities["racing"] == 4.0
        assert profile.genre_affinities["walking simulator"] == 4.5


class TestLikedAndDislikedBuckets:
    def test_a_genre_the_liked_bucket_holds_more_of_is_liked(
        self, profile_generator: ProfileGenerator, storage_manager: StorageManager
    ) -> None:
        """3 is "liked it but do not love it", so three 3s against one 2 is a
        liked genre however low its mean reads."""
        _save_rated(storage_manager, "mystery", [3, 3, 3, 2])
        _save_rated(storage_manager, "horror", [2, 2, 1, 4])

        profile = profile_generator.generate_profile(user_id=1)

        assert profile.liked_genres == ["mystery"]
        assert profile.disliked_genres == ["horror"]

    def test_an_even_split_is_liked(
        self, profile_generator: ProfileGenerator, storage_manager: StorageManager
    ) -> None:
        _save_rated(storage_manager, "western", [4, 4, 1, 1])

        profile = profile_generator.generate_profile(user_id=1)

        assert profile.liked_genres == ["western"]
        assert profile.disliked_genres == []


class TestAuthorAffinities:
    def test_mean_rating_per_author_above_the_item_floor(
        self, profile_generator: ProfileGenerator, storage_manager: StorageManager
    ) -> None:
        """The most-read author must not net out neutral, which is what the
        scorer's max-normalized sums do to them."""
        _save_rated(storage_manager, "fantasy", [5, 4, 3], author="Terry Brooks")
        _save_rated(storage_manager, "fantasy", [5], author="One Book Author")

        profile = profile_generator.generate_profile(user_id=1)

        assert profile.author_affinities == {"Terry Brooks": 4.0}

    def test_a_game_developer_is_an_author(
        self, profile_generator: ProfileGenerator, storage_manager: StorageManager
    ) -> None:
        """Game, film and show plugins state the creator in metadata rather than
        ``author``, so reading a book's field alone empties three content types."""
        for index, rating in enumerate((5, 3)):
            storage_manager.save_content_item(
                ContentItem(
                    id=f"game{index}",
                    title=f"Game {index}",
                    content_type=ContentType.VIDEO_GAME,
                    status=ConsumptionStatus.COMPLETED,
                    rating=rating,
                    metadata={"developer": "Larian Studios", "genres": ["rpg"]},
                ),
                user_id=1,
            )

        profile = profile_generator.generate_profile(user_id=1)

        assert profile.author_affinities == {"Larian Studios": 4.0}
