import json
import re
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, TypedDict

from src.models.content import ContentItem, ContentType
from src.recommendations.scorers import extract_genres
from src.storage.schema import PreferenceProfileRow

if TYPE_CHECKING:
    from src.storage.manager import StorageManager


@dataclass
class PreferenceProfile:
    user_id: int
    genre_affinities: dict[str, float] = field(default_factory=dict)
    author_affinities: dict[str, float] = field(default_factory=dict)
    liked_genres: list[str] = field(default_factory=list)
    disliked_genres: list[str] = field(default_factory=list)
    theme_preferences: list[str] = field(default_factory=list)
    anti_preferences: list[str] = field(default_factory=list)
    cross_media_patterns: list[str] = field(default_factory=list)
    generated_at: datetime | None = None


class GenreAffinity(TypedDict):
    genre: str
    score: float | None
    anti: bool


class AuthorAffinity(TypedDict):
    author: str
    score: float


class ProfilePayload(TypedDict):
    """The JSON shape both interfaces emit, declared by ``ProfileResponse``."""

    user_id: int
    genre_affinities: list[GenreAffinity]
    author_affinities: list[AuthorAffinity]
    liked_genres: list[str]
    disliked_genres: list[str]
    theme_preferences: list[str]
    cross_media_patterns: list[str]
    has_content: bool
    generated_at: str | None


#: How many entries of each affinity list a surface renders. Decided here so
#: the two cannot drift; a real library yields a couple of hundred genres.
AFFINITY_LIMIT = 12


def _genre_affinities(profile: dict[str, Any]) -> list[GenreAffinity]:
    """Anti-preferences are a filtered view of the same genres, so a second list
    of them printed a disliked genre twice. One the operator only ignores has no
    mean, hence the null score."""
    means: dict[str, float] = profile.get("genre_affinities") or {}
    anti: list[str] = profile.get("anti_preferences") or []
    ranked = sorted(means.items(), key=lambda pair: pair[1], reverse=True)
    flagged = set(anti)
    liked: list[GenreAffinity] = [
        {"genre": genre, "score": score, "anti": False}
        for genre, score in ranked
        if genre not in flagged
    ]
    disliked: list[GenreAffinity] = [
        {"genre": genre, "score": means.get(genre), "anti": True}
        for genre in anti[:AFFINITY_LIMIT]
    ]
    return liked[:AFFINITY_LIMIT] + disliked


def _author_affinities(profile: dict[str, Any]) -> list[AuthorAffinity]:
    means: dict[str, float] = profile.get("author_affinities") or {}
    ranked = sorted(means.items(), key=lambda pair: pair[1], reverse=True)
    return [
        {"author": author, "score": score} for author, score in ranked[:AFFINITY_LIMIT]
    ]


def profile_payload(
    user_id: int, record: PreferenceProfileRow | None
) -> ProfilePayload:
    """Serialise a ``profiles.get`` record; ``None`` is the empty shape."""
    profile: dict[str, Any] = (record["profile"] if record else None) or {}
    genres = _genre_affinities(profile)
    authors = _author_affinities(profile)
    themes: list[str] = profile.get("theme_preferences") or []
    patterns: list[str] = profile.get("cross_media_patterns") or []
    return {
        "user_id": user_id,
        "genre_affinities": genres,
        "author_affinities": authors,
        "liked_genres": profile.get("liked_genres") or [],
        "disliked_genres": profile.get("disliked_genres") or [],
        "theme_preferences": themes,
        "cross_media_patterns": patterns,
        # Regenerating an unrated library stamps generated_at over an empty
        # body, so the stamp cannot tell an absent profile from a vacuous one.
        "has_content": bool(genres or authors or themes or patterns),
        "generated_at": profile.get("generated_at"),
    }


def regenerated_payload(storage: "StorageManager", user_id: int) -> ProfilePayload:
    """Serialise what was stored: a body built from the in-memory profile drifts
    from what the next read answers."""
    ProfileGenerator(storage).regenerate_and_save(user_id)
    return profile_payload(user_id, storage.profiles.get(user_id))


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

#: How many items a genre or an author needs before the profile says anything
#: about it.
MIN_ITEMS = 2

#: The floor for liking something: the operator reads 3 as "liked it but do not
#: love it", so only 1 and 2 are complaints.
LIKED_RATING = 3

#: How many items one profile reads, per set.
SAMPLE_LIMIT = 1000


def _ratings_by_genre(items: list[ContentItem]) -> dict[str, list[int]]:
    ratings: dict[str, list[int]] = defaultdict(list)
    for item in items:
        if item.rating is None:
            continue
        for genre in extract_genres(item):
            ratings[genre].append(item.rating)
    return ratings


def _ratings_by_author(items: list[ContentItem]) -> dict[str, list[int]]:
    """Keyed on the creator column every content type fills — a book's author, a
    film's director, a show's creators, a game's developer."""
    ratings: dict[str, list[int]] = defaultdict(list)
    for item in items:
        author = (item.author or "").strip()
        if item.rating is None or not author:
            continue
        ratings[author].append(item.rating)
    return ratings


def _mean_ratings(ratings: dict[str, list[int]]) -> dict[str, float]:
    means = {
        key: round(sum(values) / len(values), 2)
        for key, values in ratings.items()
        if len(values) >= MIN_ITEMS
    }
    return dict(sorted(means.items(), key=lambda pair: pair[1], reverse=True))


def _bucket_genres(
    genre_ratings: dict[str, list[int]], genre_affinities: dict[str, float]
) -> tuple[list[str], list[str]]:
    """Whichever bucket holds more of a genre's items wins, best mean first. A
    tie is liked: an even split is not a complaint."""
    liked: list[str] = []
    disliked: list[str] = []
    for genre in genre_affinities:
        ratings = genre_ratings[genre]
        likes = sum(1 for rating in ratings if rating >= LIKED_RATING)
        if likes * 2 >= len(ratings):
            liked.append(genre)
        else:
            disliked.append(genre)
    return liked, disliked


def _anti_preferences(
    liked_genres: list[str],
    disliked_genres: list[str],
    genre_ratings: dict[str, list[int]],
    ignored_items: list[ContentItem],
) -> list[str]:
    """The disliked bucket worst mean first, then the genres the operator mostly
    dismisses unrated. A liked genre never lands here whatever its mean: the
    section reads "not your style".
    """
    ignored_counts = Counter(
        genre for item in ignored_items for genre in extract_genres(item)
    )
    # Concentrated, not merely present: a genre the operator rates more often
    # than they dismiss is theirs, ignored copies and all.
    mostly_ignored = [
        genre
        for genre, count in ignored_counts.most_common()
        if count >= MIN_ITEMS
        and count > len(genre_ratings.get(genre, []))
        and genre not in liked_genres
        and genre not in disliked_genres
    ]

    return list(reversed(disliked_genres)) + mostly_ignored


class ProfileGenerator:
    def __init__(
        self,
        storage_manager: "StorageManager",
    ) -> None:
        self.storage = storage_manager

    def generate_profile(self, user_id: int) -> PreferenceProfile:
        # Rated, non-ignored items are the taste signal (issue #99). Ignored
        # items are read separately because dismissing something is its own
        # verdict, and the signal read drops them before anything sees them.
        rated_items = self.storage.get_signal_items(user_id=user_id, limit=SAMPLE_LIMIT)
        ignored_items = self.storage.get_content_items(
            user_id=user_id, ignored_only=True, limit=SAMPLE_LIMIT
        )

        genre_ratings = _ratings_by_genre(rated_items)
        genre_affinities = _mean_ratings(genre_ratings)
        liked_genres, disliked_genres = _bucket_genres(genre_ratings, genre_affinities)

        return PreferenceProfile(
            user_id=user_id,
            genre_affinities=genre_affinities,
            author_affinities=_mean_ratings(_ratings_by_author(rated_items)),
            liked_genres=liked_genres,
            disliked_genres=disliked_genres,
            theme_preferences=self._identify_theme_preferences(rated_items),
            anti_preferences=_anti_preferences(
                liked_genres,
                disliked_genres,
                genre_ratings,
                ignored_items,
            ),
            cross_media_patterns=self._identify_cross_media_patterns(rated_items),
            generated_at=datetime.now(UTC),
        )

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

    def _identify_cross_media_patterns(self, items: list[ContentItem]) -> list[str]:
        patterns: list[str] = []

        by_type: dict[str, list[ContentItem]] = defaultdict(list)
        for item in items:
            if item.rating is not None:
                by_type[item.content_type].append(item)

        type_genre_affinities: dict[str, dict[str, float]] = {}
        for content_type, type_items in by_type.items():
            type_affinities = _mean_ratings(_ratings_by_genre(type_items))
            if type_affinities:
                type_genre_affinities[content_type] = type_affinities

        if len(type_genre_affinities) >= 2:
            patterns.extend(self._find_genre_divergence_patterns(type_genre_affinities))

        type_ratings = self._calculate_type_average_ratings(items)
        patterns.extend(self._find_type_preference_patterns(type_ratings))

        return patterns[:5]

    def _extract_themes(self, item: ContentItem) -> list[str]:
        themes: list[str] = []

        # Only ``tags`` is live: no row in any detail table carries a themes,
        # keywords or features key.
        value = (item.metadata or {}).get("tags")
        if isinstance(value, list):
            themes.extend(str(entry).lower() for entry in value)
        elif isinstance(value, str):
            for delimiter in [",", ";", "/", "|"]:
                if delimiter in value:
                    themes.extend(
                        part.strip().lower() for part in value.split(delimiter)
                    )
                    break
            else:
                themes.append(value.lower())

        if item.review:
            themes.extend(_THEME_KEYWORD_PATTERN.findall(item.review.lower()))

        known_themes = [theme for theme in themes if theme in THEME_KEYWORDS]
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
