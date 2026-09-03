import json
import logging
import re
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any, TypedDict

from src.models.content import ContentItem, ContentType
from src.recommendations.scorers import extract_genres
from src.storage.schema import PreferenceProfileRow

if TYPE_CHECKING:
    from src.storage.manager import StorageManager

logger = logging.getLogger(__name__)


@dataclass
class PreferenceProfile:
    user_id: int
    genre_affinities: dict[str, float] = field(default_factory=dict)
    theme_preferences: list[str] = field(default_factory=list)
    anti_preferences: list[str] = field(default_factory=list)
    cross_media_patterns: list[str] = field(default_factory=list)
    generated_at: datetime | None = None


class ProfilePayload(TypedDict):
    """The JSON shape both interfaces emit, declared by ``ProfileResponse``."""

    user_id: int
    genre_affinities: dict[str, float]
    theme_preferences: list[str]
    anti_preferences: list[str]
    cross_media_patterns: list[str]
    generated_at: str | None


def profile_payload(
    user_id: int, record: PreferenceProfileRow | None
) -> ProfilePayload:
    """Serialise a ``profiles.get`` record; ``None`` is the empty shape."""
    profile: dict[str, Any] = (record["profile"] if record else None) or {}
    return {
        "user_id": user_id,
        "genre_affinities": profile.get("genre_affinities") or {},
        "theme_preferences": profile.get("theme_preferences") or [],
        "anti_preferences": profile.get("anti_preferences") or [],
        "cross_media_patterns": profile.get("cross_media_patterns") or [],
        # The row's column and the blob's field stamp the same generation; the
        # blob's is in the host's own time, which is what a reader expects.
        "generated_at": profile.get("generated_at")
        or (record["generated_at"] if record else None),
    }


# Themes only. Length, player mode, structure and complexity describe the
# artifact rather than what is in it, and length is already its own dimension
# in content_length.py.
THEME_KEYWORDS = {
    "exploration",
    "narrative depth",
    "character development",
    "world building",
    "atmosphere",
    "emotional",
    "challenging",
    "relaxing",
    "thought-provoking",
    "immersive",
    "story-rich",
    "choice-driven",
}

# Whole words only: a prefix inverts the term it is attached to, so a review
# calling a book unemotional must not credit it with "emotional".
_THEME_KEYWORD_PATTERN = re.compile(
    r"\b(?:"
    + "|".join(
        re.escape(keyword) for keyword in sorted(THEME_KEYWORDS, key=len, reverse=True)
    )
    + r")\b"
)

# Broad genre/subgenre categories for user-facing profile display.
# Excludes themes, moods, settings, character archetypes, and game mechanics
# that are useful for item-to-item matching but too granular for a profile.
PROFILE_GENRES = {
    # Core genres
    "action",
    "adventure",
    "animation",
    "biography",
    "comedy",
    "crime",
    "documentary",
    "drama",
    "family",
    "fantasy",
    "history",
    "horror",
    "music",
    "musical",
    "mystery",
    "romance",
    "science fiction",
    "sport",
    "sports",
    "thriller",
    "war",
    "western",
    # Sci-fi subgenres
    "hard science fiction",
    "military science fiction",
    "space opera",
    "cyberpunk",
    "steampunk",
    "biopunk",
    "dieselpunk",
    "solarpunk",
    "post-cyberpunk",
    "climate fiction",
    "science fantasy",
    "alternate history",
    # Fantasy subgenres
    "high fantasy",
    "epic fantasy",
    "low fantasy",
    "dark fantasy",
    "urban fantasy",
    "historical fantasy",
    "grimdark",
    "sword and sorcery",
    "portal fantasy",
    "magical realism",
    "cozy fantasy",
    "romantasy",
    "progression fantasy",
    "litrpg",
    # Horror subgenres
    "cosmic horror",
    "body horror",
    "folk horror",
    "psychological horror",
    "supernatural horror",
    "southern gothic",
    # Mystery / thriller subgenres
    "cozy mystery",
    "police procedural",
    "whodunit",
    "psychological thriller",
    "espionage",
    "noir",
    # Romance subgenres
    "contemporary romance",
    "historical romance",
    "paranormal romance",
    "romantic suspense",
    "romantic comedy",
    "dark romance",
    # Drama / literary
    "literary fiction",
    "family saga",
    "satire",
    "social drama",
    # Western subgenres
    "neo-western",
    "weird western",
    # Nonfiction
    "memoir",
    "autobiography",
    "true crime",
    "narrative nonfiction",
    "popular science",
    # Apocalyptic / dystopian
    "apocalyptic",
    "post-apocalyptic",
    "dystopian",
    # Game genres
    "rpg",
    "action rpg",
    "jrpg",
    "crpg",
    "mmorpg",
    "tactical rpg",
    "strategy",
    "grand strategy",
    "4x",
    "puzzle",
    "platformer",
    "shooter",
    "first person shooter",
    "stealth",
    "roguelike",
    "roguelite",
    "metroidvania",
    "souls-like",
    "sandbox",
    "open world",
    "visual novel",
    "immersive sim",
    "city builder",
    "farming sim",
    "survival crafting",
    # Media formats
    "anime",
    "manga",
    "slice of life",
    # Audience
    "young adult",
    "indie",
}

MIN_ITEMS_PER_GENRE = 2


def _extract_profile_genres(item: ContentItem) -> list[str]:
    return [genre for genre in extract_genres(item) if genre in PROFILE_GENRES]


class ProfileGenerator:
    def __init__(
        self,
        storage_manager: "StorageManager",
    ) -> None:
        self.storage = storage_manager

    def generate_profile(self, user_id: int) -> PreferenceProfile:
        # Only rated, non-ignored items carry a taste signal for the profile;
        # ignored/unrated content must not shape it (issue #99).
        completed_items = self.storage.get_signal_items(user_id=user_id, limit=1000)

        genre_affinities = self._calculate_genre_affinities(completed_items)

        theme_preferences = self._identify_theme_preferences(completed_items)

        anti_preferences = self._identify_anti_preferences(completed_items)

        cross_media_patterns = self._identify_cross_media_patterns(
            completed_items, genre_affinities
        )

        return PreferenceProfile(
            user_id=user_id,
            genre_affinities=genre_affinities,
            theme_preferences=theme_preferences,
            anti_preferences=anti_preferences,
            cross_media_patterns=cross_media_patterns,
            generated_at=datetime.now(),
        )

    def _calculate_genre_affinities(self, items: list[ContentItem]) -> dict[str, float]:
        genre_ratings: dict[str, list[int]] = defaultdict(list)

        for item in items:
            if item.rating is None:
                continue

            genres = _extract_profile_genres(item)

            for genre in genres:
                genre_ratings[genre].append(item.rating)

        affinities: dict[str, float] = {}
        for genre, ratings in genre_ratings.items():
            if len(ratings) >= MIN_ITEMS_PER_GENRE:
                affinities[genre] = round(sum(ratings) / len(ratings), 2)

        return dict(sorted(affinities.items(), key=lambda pair: pair[1], reverse=True))

    def _identify_theme_preferences(self, items: list[ContentItem]) -> list[str]:
        theme_counts: dict[str, int] = defaultdict(int)

        high_rated_items = [
            item for item in items if item.rating is not None and item.rating >= 4
        ]

        for item in high_rated_items:
            themes = self._extract_themes(item)
            for theme in themes:
                theme_counts[theme] += 1

        min_count = 2 if len(high_rated_items) >= 5 else 1
        preferences = [
            theme for theme, count in theme_counts.items() if count >= min_count
        ]

        preferences.sort(key=lambda theme: theme_counts[theme], reverse=True)
        return preferences[:10]

    def _identify_anti_preferences(
        self,
        completed_items: list[ContentItem],
    ) -> list[str]:
        genre_ratings: dict[str, list[int]] = defaultdict(list)

        for item in completed_items:
            if item.rating is None:
                continue
            genres = _extract_profile_genres(item)
            for genre in genres:
                genre_ratings[genre].append(item.rating)

        anti_prefs: dict[str, float] = {}
        for genre, ratings in genre_ratings.items():
            if len(ratings) < MIN_ITEMS_PER_GENRE:
                continue

            average_rating = sum(ratings) / len(ratings)
            if average_rating > 3.0:
                continue

            high_count = sum(1 for rating in ratings if rating >= 4)
            high_ratio = high_count / len(ratings)

            if high_count <= 1 or high_ratio <= 0.2:
                anti_prefs[genre] = average_rating

        sorted_anti = sorted(anti_prefs.items(), key=lambda pair: pair[1])
        return [genre for genre, _average in sorted_anti[:10]]

    def _identify_cross_media_patterns(
        self,
        items: list[ContentItem],
        genre_affinities: dict[str, float],
    ) -> list[str]:
        patterns: list[str] = []

        by_type: dict[str, list[ContentItem]] = defaultdict(list)
        for item in items:
            if item.rating is not None:
                by_type[item.content_type].append(item)

        type_genre_affinities: dict[str, dict[str, float]] = {}
        for content_type, type_items in by_type.items():
            type_affinities = self._calculate_genre_affinities(type_items)
            if type_affinities:
                type_genre_affinities[content_type] = type_affinities

        if len(type_genre_affinities) >= 2:
            patterns.extend(self._find_genre_divergence_patterns(type_genre_affinities))

        type_ratings = self._calculate_type_average_ratings(items)
        patterns.extend(self._find_type_preference_patterns(type_ratings))

        return patterns[:5]

    def _extract_themes(self, item: ContentItem) -> list[str]:
        themes: list[str] = []

        metadata = item.metadata or {}

        theme_fields = ["themes", "tags", "keywords", "features"]

        for theme_field in theme_fields:
            if theme_field in metadata:
                value = metadata[theme_field]
                if isinstance(value, list):
                    themes.extend(str(v).lower() for v in value)
                elif isinstance(value, str):
                    for delimiter in [",", ";", "/", "|"]:
                        if delimiter in value:
                            themes.extend(
                                t.strip().lower() for t in value.split(delimiter)
                            )
                            break
                    else:
                        themes.append(value.lower())

        if item.review:
            themes.extend(_THEME_KEYWORD_PATTERN.findall(item.review.lower()))

        known_themes = [t for t in themes if t in THEME_KEYWORDS]
        return list(set(known_themes))

    def _find_genre_divergence_patterns(
        self, type_genre_affinities: dict[str, dict[str, float]]
    ) -> list[str]:
        patterns: list[str] = []

        types = list(type_genre_affinities.keys())
        for i, type1 in enumerate(types):
            for type2 in types[i + 1 :]:
                affinities1 = type_genre_affinities[type1]
                affinities2 = type_genre_affinities[type2]

                # Only compare genres with data in both types (intersection).
                # Using the union with a 0.0 default produces false patterns
                # when a genre simply has no data in one content type.
                for genre in set(affinities1.keys()) & set(affinities2.keys()):
                    score1 = affinities1[genre]
                    score2 = affinities2[genre]

                    if score1 >= 4.0 and score2 <= 2.5:
                        type1_name = self._format_content_type(type1)
                        type2_name = self._format_content_type(type2)
                        patterns.append(
                            f"Loves {genre} {type1_name} but not {type2_name}"
                        )
                    elif score2 >= 4.0 and score1 <= 2.5:
                        type1_name = self._format_content_type(type1)
                        type2_name = self._format_content_type(type2)
                        patterns.append(
                            f"Loves {genre} {type2_name} but not {type1_name}"
                        )

        return patterns

    def _calculate_type_average_ratings(
        self, items: list[ContentItem]
    ) -> dict[str, float]:
        type_ratings: dict[str, list[int]] = defaultdict(list)

        for item in items:
            if item.rating is not None:
                type_ratings[item.content_type].append(item.rating)

        averages: dict[str, float] = {}
        for content_type, ratings in type_ratings.items():
            if ratings:
                averages[content_type] = round(sum(ratings) / len(ratings), 2)

        return averages

    def _find_type_preference_patterns(
        self, type_ratings: dict[str, float]
    ) -> list[str]:
        patterns: list[str] = []

        if len(type_ratings) < 2:
            return patterns

        sorted_types = sorted(
            type_ratings.items(), key=lambda pair: pair[1], reverse=True
        )

        highest_type, highest_rating = sorted_types[0]
        lowest_type, lowest_rating = sorted_types[-1]

        if highest_rating - lowest_rating >= 0.5:
            highest_name = self._format_content_type(highest_type)
            lowest_name = self._format_content_type(lowest_type)
            patterns.append(f"Generally rates {highest_name} higher than {lowest_name}")

        return patterns

    def _format_content_type(self, content_type: str) -> str:
        type_names = {
            ContentType.BOOK: "books",
            ContentType.MOVIE: "movies",
            ContentType.TV_SHOW: "TV shows",
            ContentType.VIDEO_GAME: "games",
            "book": "books",
            "movie": "movies",
            "tv_show": "TV shows",
            "video_game": "games",
        }
        return type_names.get(content_type, str(content_type))

    def regenerate_and_save(self, user_id: int) -> PreferenceProfile:
        profile = self.generate_profile(user_id)

        profile_dict = asdict(profile)
        if profile_dict.get("generated_at"):
            profile_dict["generated_at"] = profile_dict["generated_at"].isoformat()
        profile_json = json.dumps(profile_dict)

        self.storage.profiles.save(user_id, profile_json)
        return profile
