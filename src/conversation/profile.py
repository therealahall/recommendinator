"""Preference profile generation from user data."""

import json
import logging
from collections import defaultdict
from dataclasses import asdict
from datetime import datetime
from typing import TYPE_CHECKING

from src.models.content import ContentItem, ContentType
from src.models.conversation import PreferenceProfile
from src.recommendations.scorers import extract_genres

if TYPE_CHECKING:
    from src.storage.manager import StorageManager

logger = logging.getLogger(__name__)


# Theme keywords that indicate preference signals
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
    "replayable",
    "short",
    "long",
    "complex",
    "simple",
    "multiplayer",
    "single-player",
    "cooperative",
    "competitive",
    "story-rich",
    "choice-driven",
    "linear",
    "open-ended",
}

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
    "dystopia",
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

# Minimum number of rated items per genre to include in profile
MIN_ITEMS_PER_GENRE = 2


def _extract_profile_genres(item: ContentItem) -> list[str]:
    """Extract genres from an item, filtered to broad genre categories.

    Uses the shared normalizer for extraction and normalization, then
    filters to PROFILE_GENRES to show only meaningful genre/subgenre
    categories in user-facing profile summaries. Excludes themes, moods,
    settings, character archetypes, and game mechanics.

    Args:
        item: Content item to extract genres from

    Returns:
        List of normalized genre strings from the profile genre set
    """
    return [genre for genre in extract_genres(item) if genre in PROFILE_GENRES]


class ProfileGenerator:
    """Generates distilled preference profiles from user data."""

    def __init__(
        self,
        storage_manager: "StorageManager",
    ) -> None:
        """Initialize the profile generator.

        Args:
            storage_manager: Storage manager for database operations
        """
        self.storage = storage_manager

    def generate_profile(self, user_id: int) -> PreferenceProfile:
        """Generate a preference profile from all user data.

        Analyzes:
        - Genre affinities from average ratings (1-5 scale)
        - Theme preferences from high-rated content metadata
        - Anti-preferences using consistency checks (avg + positive ratio)
        - Cross-media patterns (e.g., "loves sci-fi books, prefers fantasy games")

        Args:
            user_id: User ID to generate profile for

        Returns:
            PreferenceProfile with computed preferences
        """
        completed_items = self.storage.get_completed_items(user_id=user_id, limit=1000)

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
        """Calculate genre preference scores from rated items.

        Uses average rating per genre on a 1.0-5.0 scale. Requires at least
        MIN_ITEMS_PER_GENRE rated items per genre to avoid small-sample bias.

        Args:
            items: List of completed content items

        Returns:
            Dictionary mapping genre to average rating (1.0 to 5.0), sorted descending
        """
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

        return dict(sorted(affinities.items(), key=lambda x: x[1], reverse=True))

    def _identify_theme_preferences(self, items: list[ContentItem]) -> list[str]:
        """Identify theme preferences from high-rated content.

        Args:
            items: List of completed content items

        Returns:
            List of identified theme preferences
        """
        theme_counts: dict[str, int] = defaultdict(int)

        high_rated_items = [
            item for item in items if item.rating is not None and item.rating >= 4
        ]

        for item in high_rated_items:
            themes = self._extract_themes(item)
            for theme in themes:
                theme_counts[theme] += 1

        # Return themes that appear in at least 2 high-rated items
        # or if user has few items, themes that appear at least once
        min_count = 2 if len(high_rated_items) >= 5 else 1
        preferences = [
            theme for theme, count in theme_counts.items() if count >= min_count
        ]

        # Sort by count descending, take top 10
        preferences.sort(key=lambda x: theme_counts[x], reverse=True)
        return preferences[:10]

    def _identify_anti_preferences(
        self,
        completed_items: list[ContentItem],
    ) -> list[str]:
        """Identify anti-preferences using consistency-based checks.

        A genre is an anti-preference only if:
        1. It has at least MIN_ITEMS_PER_GENRE rated items
        2. Its average rating is <= 2.5
        3. At most 1 item OR at most 20% of items rated 3+ stars
           (prevents genres the user mostly loves from appearing here)

        Args:
            completed_items: List of completed content items

        Returns:
            List of identified anti-preferences
        """
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
            if average_rating > 2.5:
                continue

            positive_count = sum(1 for rating in ratings if rating >= 3)
            positive_ratio = positive_count / len(ratings)

            if positive_count <= 1 or positive_ratio <= 0.2:
                anti_prefs[genre] = average_rating

        # Sort by average rating ascending (worst first), take top 10
        sorted_anti = sorted(anti_prefs.items(), key=lambda x: x[1])
        return [genre for genre, _average in sorted_anti[:10]]

    def _identify_cross_media_patterns(
        self,
        items: list[ContentItem],
        genre_affinities: dict[str, float],
    ) -> list[str]:
        """Find patterns across content types.

        Args:
            items: List of completed content items
            genre_affinities: Overall genre affinity scores

        Returns:
            List of cross-media pattern descriptions
        """
        patterns: list[str] = []

        # Group items by content type
        by_type: dict[str, list[ContentItem]] = defaultdict(list)
        for item in items:
            if item.rating is not None:
                by_type[item.content_type].append(item)

        # Calculate genre affinities per content type
        type_genre_affinities: dict[str, dict[str, float]] = {}
        for content_type, type_items in by_type.items():
            type_affinities = self._calculate_genre_affinities(type_items)
            if type_affinities:
                type_genre_affinities[content_type] = type_affinities

        # Look for interesting patterns
        if len(type_genre_affinities) >= 2:
            patterns.extend(self._find_genre_divergence_patterns(type_genre_affinities))

        # Look for content type preferences
        type_ratings = self._calculate_type_average_ratings(items)
        patterns.extend(self._find_type_preference_patterns(type_ratings))

        return patterns[:5]  # Limit to top 5 patterns

    def _extract_themes(self, item: ContentItem) -> list[str]:
        """Extract theme keywords from item metadata.

        Args:
            item: Content item to extract themes from

        Returns:
            List of theme keywords found
        """
        themes: list[str] = []

        metadata = item.metadata or {}

        # Check theme/tag fields
        theme_fields = ["themes", "tags", "keywords", "features"]

        for field in theme_fields:
            if field in metadata:
                value = metadata[field]
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

        # Check review for theme keywords
        if item.review:
            review_lower = item.review.lower()
            for keyword in THEME_KEYWORDS:
                if keyword in review_lower:
                    themes.append(keyword)

        # Filter to known keywords and deduplicate
        known_themes = [t for t in themes if t in THEME_KEYWORDS]
        return list(set(known_themes))

    def _find_genre_divergence_patterns(
        self, type_genre_affinities: dict[str, dict[str, float]]
    ) -> list[str]:
        """Find patterns where genre preferences differ across content types.

        Args:
            type_genre_affinities: Genre affinities by content type

        Returns:
            List of pattern descriptions
        """
        patterns: list[str] = []

        # Compare each pair of content types
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

                    # Significant divergence
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
        """Calculate average rating per content type.

        Args:
            items: List of completed content items

        Returns:
            Dictionary mapping content type to average rating
        """
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
        """Find patterns in content type preferences.

        Args:
            type_ratings: Average ratings by content type

        Returns:
            List of pattern descriptions
        """
        patterns: list[str] = []

        if len(type_ratings) < 2:
            return patterns

        # Find the highest and lowest rated types
        sorted_types = sorted(type_ratings.items(), key=lambda x: x[1], reverse=True)

        highest_type, highest_rating = sorted_types[0]
        lowest_type, lowest_rating = sorted_types[-1]

        # Only report if there's a significant difference
        if highest_rating - lowest_rating >= 0.5:
            highest_name = self._format_content_type(highest_type)
            lowest_name = self._format_content_type(lowest_type)
            patterns.append(f"Generally rates {highest_name} higher than {lowest_name}")

        return patterns

    def _format_content_type(self, content_type: str) -> str:
        """Format content type for human-readable output.

        Args:
            content_type: Raw content type string

        Returns:
            Formatted content type name
        """
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
        """Generate a new profile and save it to the database.

        Args:
            user_id: User ID to generate profile for

        Returns:
            The generated and saved PreferenceProfile
        """
        profile = self.generate_profile(user_id)

        # Convert to JSON for storage
        profile_dict = asdict(profile)
        # Convert datetime to ISO format string for JSON serialization
        if profile_dict.get("generated_at"):
            profile_dict["generated_at"] = profile_dict["generated_at"].isoformat()
        profile_json = json.dumps(profile_dict)

        self.storage.save_preference_profile(user_id, profile_json)
        return profile
