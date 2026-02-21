"""Preference analysis from consumed content."""

import logging
from collections.abc import Sequence

from src.models.content import ContentItem
from src.recommendations.genre_normalizer import extract_and_normalize_genres

logger = logging.getLogger(__name__)


class UserPreferences:
    """User preferences extracted from consumption history."""

    def __init__(
        self,
        preferred_authors: dict[str, float],
        preferred_genres: dict[str, float],
        average_rating: float,
        total_items: int,
        disliked_authors: dict[str, float] | None = None,
        disliked_genres: dict[str, float] | None = None,
    ) -> None:
        """Initialize user preferences.

        Args:
            preferred_authors: Author names to preference scores (positive)
            preferred_genres: Genre names to preference scores (positive)
            average_rating: Average rating across all consumed items
            total_items: Total number of consumed items
            disliked_authors: Author names to negative preference scores
            disliked_genres: Genre names to negative preference scores
        """
        self.preferred_authors = preferred_authors
        self.preferred_genres = preferred_genres
        self.average_rating = average_rating
        self.total_items = total_items
        self.disliked_authors = disliked_authors or {}
        self.disliked_genres = disliked_genres or {}

    def get_author_score(self, author: str | None) -> float:
        """Get preference score for an author.

        Args:
            author: Author name

        Returns:
            Preference score (-1.0 to 1.0, where negative means disliked)
        """
        if not author:
            return 0.0
        author_lower = author.lower()
        # Positive preference minus negative preference
        positive = self.preferred_authors.get(author_lower, 0.0)
        negative = self.disliked_authors.get(author_lower, 0.0)
        return positive - negative

    def get_genre_score(self, genre: str | None) -> float:
        """Get preference score for a genre.

        Args:
            genre: Genre name

        Returns:
            Preference score (-1.0 to 1.0, where negative means disliked)
        """
        if not genre:
            return 0.0
        genre_lower = genre.lower()
        # Positive preference minus negative preference
        positive = self.preferred_genres.get(genre_lower, 0.0)
        negative = self.disliked_genres.get(genre_lower, 0.0)
        return positive - negative


class PreferenceAnalyzer:
    """Analyze user preferences from consumed content."""

    def __init__(self, min_rating: int = 4) -> None:
        """Initialize preference analyzer.

        Args:
            min_rating: Minimum rating to consider for preferences (default: 4)
        """
        self.min_rating = min_rating

    def analyze(self, consumed_items: list[ContentItem]) -> UserPreferences:
        """Analyze consumed items to extract user preferences.

        Args:
            consumed_items: List of consumed ContentItems

        Returns:
            UserPreferences object
        """
        if not consumed_items:
            return UserPreferences({}, {}, 0.0, 0)

        # Calculate average rating
        ratings = [item.rating for item in consumed_items if item.rating is not None]
        avg_rating = sum(ratings) / len(ratings) if ratings else 0.0

        # Extract author preferences
        author_ratings = [
            (item.author.lower(), item.rating)
            for item in consumed_items
            if item.author and item.rating is not None
        ]
        author_scores, disliked_authors = self._score_attributes(author_ratings)

        # Extract genre preferences
        genre_ratings: list[tuple[str, float]] = []
        for item in consumed_items:
            if item.metadata and item.rating is not None:
                for genre in extract_and_normalize_genres(item.metadata):
                    if genre:
                        genre_ratings.append((genre.lower(), item.rating))
        genre_scores, disliked_genres = self._score_attributes(genre_ratings)

        return UserPreferences(
            preferred_authors=author_scores,
            preferred_genres=genre_scores,
            average_rating=avg_rating,
            total_items=len(consumed_items),
            disliked_authors=disliked_authors,
            disliked_genres=disliked_genres,
        )

    def _score_attributes(
        self, attribute_ratings: Sequence[tuple[str, int | float]]
    ) -> tuple[dict[str, float], dict[str, float]]:
        """Accumulate and normalize attribute scores based on ratings.

        Positive weights (rating >= min_rating): maps 4->0.5, 5->1.0
        Negative weights (rating < min_rating): maps 1->1.0, 2->0.5, 3->0.0

        Args:
            attribute_ratings: List of (attribute_value, rating) pairs.

        Returns:
            Tuple of (preferred, disliked) normalized score dicts.
        """
        preferred: dict[str, float] = {}
        disliked: dict[str, float] = {}

        for value, rating in attribute_ratings:
            weight = (rating - 3) / 2.0
            if rating >= self.min_rating:
                preferred[value] = preferred.get(value, 0.0) + weight
            else:
                disliked[value] = disliked.get(value, 0.0) + abs(weight)

        # Normalize preferred scores
        if preferred:
            max_score = max(preferred.values())
            preferred = {k: v / max_score for k, v in preferred.items()}

        # Normalize disliked scores
        if disliked:
            max_score = max(disliked.values())
            if max_score > 0:
                disliked = {k: v / max_score for k, v in disliked.items()}

        return preferred, disliked
