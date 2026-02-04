"""Preference profile generation from user data."""

import json
import logging
from collections import defaultdict
from dataclasses import asdict
from datetime import datetime
from typing import TYPE_CHECKING

from src.models.content import ContentItem, ContentType
from src.models.conversation import PreferenceProfile

if TYPE_CHECKING:
    from src.storage.manager import StorageManager

logger = logging.getLogger(__name__)


# Genre/theme keywords to extract from metadata
GENRE_KEYWORDS = {
    "sci-fi",
    "science fiction",
    "fantasy",
    "horror",
    "mystery",
    "thriller",
    "romance",
    "comedy",
    "drama",
    "action",
    "adventure",
    "rpg",
    "strategy",
    "simulation",
    "puzzle",
    "platformer",
    "roguelike",
    "metroidvania",
    "souls-like",
    "open world",
    "sandbox",
    "survival",
    "stealth",
    "racing",
    "sports",
    "fighting",
    "shooter",
    "fps",
    "mmo",
    "mmorpg",
    "indie",
    "narrative",
    "story-driven",
    "exploration",
    "cozy",
    "relaxing",
    "historical",
    "literary fiction",
    "non-fiction",
    "biography",
    "memoir",
    "self-help",
    "true crime",
    "documentary",
    "animated",
    "anime",
}

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


class ProfileGenerator:
    """Generates distilled preference profiles from user data."""

    def __init__(
        self,
        storage_manager: "StorageManager",
        min_rating_for_preference: int = 4,
        max_rating_for_anti_preference: int = 2,
    ) -> None:
        """Initialize the profile generator.

        Args:
            storage_manager: Storage manager for database operations
            min_rating_for_preference: Minimum rating to consider as positive signal
            max_rating_for_anti_preference: Maximum rating to consider as negative signal
        """
        self.storage = storage_manager
        self.min_rating_for_preference = min_rating_for_preference
        self.max_rating_for_anti_preference = max_rating_for_anti_preference

    def generate_profile(self, user_id: int) -> PreferenceProfile:
        """Generate a preference profile from all user data.

        Analyzes:
        - Genre affinities from ratings (weighted by rating value)
        - Theme preferences from high-rated content metadata
        - Anti-preferences from abandoned/low-rated content
        - Cross-media patterns (e.g., "loves sci-fi books, prefers fantasy games")

        Args:
            user_id: User ID to generate profile for

        Returns:
            PreferenceProfile with computed preferences
        """
        # Get all completed items with ratings
        completed_items = self.storage.get_completed_items(user_id=user_id, limit=1000)

        # Get unconsumed items for anti-preference detection
        unconsumed_items = self.storage.get_unconsumed_items(user_id=user_id, limit=500)

        # Calculate genre affinities
        genre_affinities = self._calculate_genre_affinities(completed_items)

        # Identify theme preferences from high-rated items
        theme_preferences = self._identify_theme_preferences(completed_items)

        # Identify anti-preferences from low-rated or abandoned items
        anti_preferences = self._identify_anti_preferences(
            completed_items, unconsumed_items
        )

        # Find cross-media patterns
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

        Uses weighted scoring where rating affects the affinity score:
        - 5 stars = +1.0
        - 4 stars = +0.6
        - 3 stars = +0.2
        - 2 stars = -0.3
        - 1 star = -0.6

        Args:
            items: List of completed content items

        Returns:
            Dictionary mapping genre to affinity score (0.0 to 1.0)
        """
        genre_scores: dict[str, list[float]] = defaultdict(list)

        for item in items:
            if item.rating is None:
                continue

            # Extract genres from item
            genres = self._extract_genres(item)

            # Calculate weight based on rating
            weight = self._rating_to_weight(item.rating)

            for genre in genres:
                genre_scores[genre].append(weight)

        # Convert to averaged affinities
        affinities: dict[str, float] = {}
        for genre, scores in genre_scores.items():
            if scores:
                # Average the scores and normalize to 0-1 range
                avg_score = sum(scores) / len(scores)
                # Convert from -0.6 to 1.0 range to 0.0 to 1.0 range
                normalized = (avg_score + 0.6) / 1.6
                affinities[genre] = round(max(0.0, min(1.0, normalized)), 2)

        # Sort by affinity score descending
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
            item
            for item in items
            if item.rating is not None and item.rating >= self.min_rating_for_preference
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
        unconsumed_items: list[ContentItem],
    ) -> list[str]:
        """Identify anti-preferences from low-rated or abandoned content.

        Args:
            completed_items: List of completed content items
            unconsumed_items: List of unconsumed (potentially abandoned) items

        Returns:
            List of identified anti-preferences
        """
        anti_counts: dict[str, int] = defaultdict(int)

        # Low-rated completed items
        low_rated = [
            item
            for item in completed_items
            if item.rating is not None
            and item.rating <= self.max_rating_for_anti_preference
        ]

        for item in low_rated:
            genres = self._extract_genres(item)
            themes = self._extract_themes(item)
            for genre in genres:
                anti_counts[genre] += 1
            for theme in themes:
                anti_counts[theme] += 1

        # Items that have been in backlog for a long time could indicate
        # genres the user is avoiding, but we don't track add dates
        # So we'll just use low ratings for now

        # Return anti-preferences that appear in at least 2 low-rated items
        min_count = 2 if len(low_rated) >= 5 else 1
        anti_prefs = [pref for pref, count in anti_counts.items() if count >= min_count]

        # Sort by count descending, take top 10
        anti_prefs.sort(key=lambda x: anti_counts[x], reverse=True)
        return anti_prefs[:10]

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

    def _extract_genres(self, item: ContentItem) -> list[str]:
        """Extract genre keywords from item metadata.

        Args:
            item: Content item to extract genres from

        Returns:
            List of genre keywords found
        """
        genres: list[str] = []

        # Check metadata for genre field
        metadata = item.metadata or {}

        # Common genre field names
        genre_fields = ["genre", "genres", "category", "categories", "tags"]

        for field in genre_fields:
            if field in metadata:
                value = metadata[field]
                if isinstance(value, list):
                    genres.extend(str(v).lower() for v in value)
                elif isinstance(value, str):
                    # Split on common delimiters
                    for delimiter in [",", ";", "/", "|"]:
                        if delimiter in value:
                            genres.extend(
                                g.strip().lower() for g in value.split(delimiter)
                            )
                            break
                    else:
                        genres.append(value.lower())

        # Also check title for obvious genre indicators
        title_lower = item.title.lower()
        for keyword in GENRE_KEYWORDS:
            if keyword in title_lower:
                genres.append(keyword)

        # Filter to known keywords and deduplicate
        known_genres = [g for g in genres if g in GENRE_KEYWORDS]
        return list(set(known_genres))

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

    def _rating_to_weight(self, rating: int) -> float:
        """Convert a 1-5 rating to a preference weight.

        Args:
            rating: Rating from 1 to 5

        Returns:
            Weight from -0.6 to 1.0
        """
        weights = {
            5: 1.0,
            4: 0.6,
            3: 0.2,
            2: -0.3,
            1: -0.6,
        }
        return weights.get(rating, 0.0)

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

                # Find genres that are strong in one type but not the other
                for genre in set(affinities1.keys()) | set(affinities2.keys()):
                    score1 = affinities1.get(genre, 0.0)
                    score2 = affinities2.get(genre, 0.0)

                    # Significant divergence (0.4+ difference)
                    if score1 >= 0.7 and score2 <= 0.3:
                        type1_name = self._format_content_type(type1)
                        type2_name = self._format_content_type(type2)
                        patterns.append(
                            f"Loves {genre} {type1_name} but not {type2_name}"
                        )
                    elif score2 >= 0.7 and score1 <= 0.3:
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
