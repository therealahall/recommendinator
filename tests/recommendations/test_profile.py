import tempfile
from collections.abc import Generator
from pathlib import Path

import pytest

from src.models.content import ConsumptionStatus, ContentItem, ContentType
from src.recommendations.content_length import LengthPreference
from src.recommendations.profile import (
    AFFINITY_LIMIT,
    PreferenceProfile,
    ProfileGenerator,
    profile_payload,
    regenerated_payload,
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
    def test_a_genre_rated_below_the_operators_own_average_is_not_an_anti_preference(
        self, profile_generator: ProfileGenerator, storage_manager: StorageManager
    ) -> None:
        """The panel heads this list "Not your style", and a below-average bar
        puts half of any high-rating library under it."""
        _save_rated(storage_manager, "fantasy", [5, 5, 5, 5])
        _save_rated(storage_manager, "horror", [3, 3])

        profile = profile_generator.generate_profile(user_id=1)

        assert profile.anti_preferences == []

    def test_a_genre_most_of_whose_ratings_reach_the_liked_floor_is_not_anti(
        self, profile_generator: ProfileGenerator, storage_manager: StorageManager
    ) -> None:
        _save_rated(storage_manager, "mystery", [3, 3, 3, 2])
        _save_rated(storage_manager, "horror", [2, 2, 1, 4])

        profile = profile_generator.generate_profile(user_id=1)

        assert profile.anti_preferences == ["horror"]

    def test_an_even_split_is_not_an_anti_preference(
        self, profile_generator: ProfileGenerator, storage_manager: StorageManager
    ) -> None:
        _save_rated(storage_manager, "western", [4, 4, 1, 1])

        profile = profile_generator.generate_profile(user_id=1)

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

        assert {
            "genre": "science fiction",
            "score": generated.genre_affinities["science fiction"],
            "anti": False,
        } in payload["genre_affinities"]
        assert payload["has_content"]
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

        assert profile.anti_preferences == []

    def test_a_genre_rated_once_and_dismissed_twice_is_not_an_anti_preference(
        self, profile_generator: ProfileGenerator, storage_manager: StorageManager
    ) -> None:
        """One rating is under MIN_ITEMS, so the genre joins no bucket, and
        excluding by bucket membership read a 5/5 genre as "not your style"."""
        _save_rated(storage_manager, "western", [5])
        for index in range(2):
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

        assert profile.anti_preferences == []

    def test_a_genre_rated_at_the_liked_floor_and_dismissed_twice_is_not_anti(
        self, profile_generator: ProfileGenerator, storage_manager: StorageManager
    ) -> None:
        _save_rated(storage_manager, "romance", [3])
        for index in range(2):
            db_id = storage_manager.save_content_item(
                ContentItem(
                    id=f"ignored-romance{index}",
                    title=f"Dismissed Romance {index}",
                    content_type=ContentType.BOOK,
                    status=ConsumptionStatus.UNREAD,
                    metadata={"genres": ["romance"]},
                ),
                user_id=1,
            )
            storage_manager.set_item_ignored(db_id, True, user_id=1)

        profile = profile_generator.generate_profile(user_id=1)

        assert profile.anti_preferences == []

    def test_the_dismissal_sample_is_the_latest_not_an_alphabetical_prefix(
        self,
        profile_generator: ProfileGenerator,
        storage_manager: StorageManager,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Past the sample limit a title-ordered read answers with whatever the
        alphabet put first, which is no sample of what the operator dismisses."""
        dismissed: dict[str, list[int]] = {}
        for genre, title in (("western", "Zeta"), ("horror", "Alpha")):
            dismissed[genre] = [
                storage_manager.save_content_item(
                    ContentItem(
                        id=f"{genre}{index}",
                        title=f"{title} {index}",
                        content_type=ContentType.BOOK,
                        status=ConsumptionStatus.UNREAD,
                        metadata={"genres": [genre]},
                    ),
                    user_id=1,
                )
                for index in range(2)
            ]
        # Last dismissed and lowest id, so it heads the read on either side of a
        # clock tick the two writes may fall across.
        for db_id in [*dismissed["horror"], *dismissed["western"]]:
            storage_manager.set_item_ignored(db_id, True, user_id=1)
        monkeypatch.setattr("src.recommendations.profile.SAMPLE_LIMIT", 2)

        profile = profile_generator.generate_profile(user_id=1)

        assert profile.anti_preferences == ["western"]

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


def _stored(**profile: object) -> dict:
    return {"id": 1, "user_id": 1, "profile": profile}


class TestProfilePayload:
    def test_an_anti_preference_is_flagged_in_place_of_a_second_list(self) -> None:
        payload = profile_payload(
            1,
            _stored(
                genre_affinities={"fantasy": 4.5, "horror": 2.0},
                anti_preferences=["horror"],
            ),
        )

        assert payload["genre_affinities"] == [
            {"genre": "fantasy", "score": 4.5, "anti": False},
            {"genre": "horror", "score": 2.0, "anti": True},
        ]

    def test_a_genre_the_operator_only_ignores_carries_no_score(self) -> None:
        payload = profile_payload(
            1, _stored(genre_affinities={}, anti_preferences=["western"])
        )

        assert payload["genre_affinities"] == [
            {"genre": "western", "score": None, "anti": True}
        ]

    def test_each_affinity_list_stops_at_the_shared_bound(self) -> None:
        payload = profile_payload(
            1,
            _stored(
                genre_affinities={f"genre{index}": 5.0 for index in range(40)},
                author_affinities={f"author{index}": 5.0 for index in range(40)},
                anti_preferences=[f"genre{index}" for index in range(20, 40)],
            ),
        )

        genres = payload["genre_affinities"]
        assert len(payload["author_affinities"]) == AFFINITY_LIMIT
        assert sum(1 for entry in genres if not entry["anti"]) == AFFINITY_LIMIT
        assert sum(1 for entry in genres if entry["anti"]) == AFFINITY_LIMIT

    def test_a_library_rated_by_author_with_no_genres_is_not_empty(
        self, storage_manager: StorageManager
    ) -> None:
        for index, rating in enumerate((5, 3)):
            storage_manager.save_content_item(
                ContentItem(
                    id=f"book{index}",
                    title=f"Shannara {index}",
                    content_type=ContentType.BOOK,
                    status=ConsumptionStatus.COMPLETED,
                    rating=rating,
                    author="Terry Brooks",
                ),
                user_id=1,
            )

        payload = regenerated_payload(storage_manager, 1)

        assert payload["has_content"]
        assert payload["genre_affinities"] == []
        assert payload["author_affinities"] == [
            {"author": "Terry Brooks", "score": 4.0}
        ]

    def test_a_stamped_but_vacuous_profile_is_empty(self) -> None:
        payload = profile_payload(1, _stored(generated_at="2026-01-01T00:00:00"))

        assert payload["has_content"] is False
        assert payload["generated_at"] == "2026-01-01T00:00:00"


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

    def test_a_film_director_and_a_show_creator_are_authors(
        self, profile_generator: ProfileGenerator, storage_manager: StorageManager
    ) -> None:
        for index, rating in enumerate((5, 4)):
            storage_manager.save_content_item(
                ContentItem(
                    id=f"movie{index}",
                    title=f"Film {index}",
                    content_type=ContentType.MOVIE,
                    status=ConsumptionStatus.COMPLETED,
                    rating=rating,
                    metadata={"director": "Denis Villeneuve", "genres": ["sci-fi"]},
                ),
                user_id=1,
            )
        for index, rating in enumerate((3, 3)):
            storage_manager.save_content_item(
                ContentItem(
                    id=f"show{index}",
                    title=f"Show {index}",
                    content_type=ContentType.TV_SHOW,
                    status=ConsumptionStatus.COMPLETED,
                    rating=rating,
                    metadata={"creators": "Vince Gilligan", "genres": ["drama"]},
                ),
                user_id=1,
            )

        profile = profile_generator.generate_profile(user_id=1)

        assert profile.author_affinities == {
            "Denis Villeneuve": 4.5,
            "Vince Gilligan": 3.0,
        }
        assert profile.genre_affinities["science fiction"] == 4.5
        assert profile.genre_affinities["drama"] == 3.0

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
