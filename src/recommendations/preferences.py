"""Preference analysis from consumed content."""

import logging
from typing import List, Dict, Set, Optional
from collections import Counter

from src.models.content import ContentItem, ContentType

logger = logging.getLogger(__name__)


class UserPreferences:
    """User preferences extracted from consumption history."""

    def __init__(
        self,
        preferred_authors: Dict[str, float],
        preferred_genres: Dict[str, float],
        preferred_themes: Dict[str, float],
        average_rating: float,
        total_items: int,
    ) -> None:
        """Initialize user preferences.

        Args:
            preferred_authors: Author names to preference scores
            preferred_genres: Genre names to preference scores
            preferred_themes: Theme keywords to preference scores
            average_rating: Average rating across all consumed items
            total_items: Total number of consumed items
        """
        self.preferred_authors = preferred_authors
        self.preferred_genres = preferred_genres
        self.preferred_themes = preferred_themes
        self.average_rating = average_rating
        self.total_items = total_items

    def get_author_score(self, author: Optional[str]) -> float:
        """Get preference score for an author.

        Args:
            author: Author name

        Returns:
            Preference score (0.0 to 1.0)
        """
        if not author:
            return 0.0
        return self.preferred_authors.get(author.lower(), 0.0)

    def get_genre_score(self, genre: Optional[str]) -> float:
        """Get preference score for a genre.

        Args:
            genre: Genre name

        Returns:
            Preference score (0.0 to 1.0)
        """
        if not genre:
            return 0.0
        return self.preferred_genres.get(genre.lower(), 0.0)


class PreferenceAnalyzer:
    """Analyze user preferences from consumed content."""

    def __init__(self, min_rating: int = 4) -> None:
        """Initialize preference analyzer.

        Args:
            min_rating: Minimum rating to consider for preferences (default: 4)
        """
        self.min_rating = min_rating

    def analyze(self, consumed_items: List[ContentItem]) -> UserPreferences:
        """Analyze consumed items to extract user preferences.

        Args:
            consumed_items: List of consumed ContentItems

        Returns:
            UserPreferences object
        """
        if not consumed_items:
            return UserPreferences({}, {}, {}, 0.0, 0)

        # Filter high-rated items
        high_rated = [
            item
            for item in consumed_items
            if item.rating and item.rating >= self.min_rating
        ]

        # Calculate average rating
        ratings = [item.rating for item in consumed_items if item.rating]
        avg_rating = sum(ratings) / len(ratings) if ratings else 0.0

        # Extract authors (weighted by rating)
        author_scores: Dict[str, float] = {}
        for item in high_rated:
            if item.author and item.rating:
                author_lower = item.author.lower()
                # Weight by rating (5-star = 1.0, 4-star = 0.8)
                weight = (item.rating - 3) / 2.0  # Maps 4->0.5, 5->1.0
                author_scores[author_lower] = (
                    author_scores.get(author_lower, 0.0) + weight
                )

        # Normalize author scores
        if author_scores:
            max_score = max(author_scores.values())
            author_scores = {
                author: score / max_score for author, score in author_scores.items()
            }

        # Extract genres from metadata
        genre_scores: Dict[str, float] = {}
        for item in high_rated:
            if item.metadata and item.rating:
                genre = item.metadata.get("genre")
                if genre:
                    genre_lower = genre.lower()
                    weight = (item.rating - 3) / 2.0
                    genre_scores[genre_lower] = (
                        genre_scores.get(genre_lower, 0.0) + weight
                    )

        # Normalize genre scores
        if genre_scores:
            max_score = max(genre_scores.values())
            genre_scores = {
                genre: score / max_score for genre, score in genre_scores.items()
            }

        # Extract themes from reviews (simple keyword extraction)
        theme_scores: Dict[str, float] = {}
        # This is a simplified version - could be enhanced with NLP
        # For now, we'll extract common words from high-rated reviews
        review_words: List[str] = []
        for item in high_rated:
            if item.review and item.rating:
                # Simple word extraction (could be improved)
                words = item.review.lower().split()
                # Weight words by rating
                weight = (item.rating - 3) / 2.0
                review_words.extend([(w, weight) for w in words if len(w) > 4])

        # Count theme words (simplified)
        word_counts: Counter = Counter()
        for word, weight in review_words:
            word_counts[word] += weight

        # Normalize theme scores (top 20 themes)
        if word_counts:
            max_count = max(word_counts.values())
            top_themes = word_counts.most_common(20)
            theme_scores = {
                word: count / max_count for word, count in top_themes if count > 0
            }

        return UserPreferences(
            preferred_authors=author_scores,
            preferred_genres=genre_scores,
            preferred_themes=theme_scores,
            average_rating=avg_rating,
            total_items=len(consumed_items),
        )
