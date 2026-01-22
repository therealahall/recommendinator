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
        disliked_authors: Optional[Dict[str, float]] = None,
        disliked_genres: Optional[Dict[str, float]] = None,
    ) -> None:
        """Initialize user preferences.

        Args:
            preferred_authors: Author names to preference scores (positive)
            preferred_genres: Genre names to preference scores (positive)
            preferred_themes: Theme keywords to preference scores
            average_rating: Average rating across all consumed items
            total_items: Total number of consumed items
            disliked_authors: Author names to negative preference scores
            disliked_genres: Genre names to negative preference scores
        """
        self.preferred_authors = preferred_authors
        self.preferred_genres = preferred_genres
        self.preferred_themes = preferred_themes
        self.average_rating = average_rating
        self.total_items = total_items
        self.disliked_authors = disliked_authors or {}
        self.disliked_genres = disliked_genres or {}

    def get_author_score(self, author: Optional[str]) -> float:
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

    def get_genre_score(self, genre: Optional[str]) -> float:
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

    def analyze(self, consumed_items: List[ContentItem]) -> UserPreferences:
        """Analyze consumed items to extract user preferences.

        Args:
            consumed_items: List of consumed ContentItems

        Returns:
            UserPreferences object
        """
        if not consumed_items:
            return UserPreferences({}, {}, {}, 0.0, 0)

        # Separate items by rating
        high_rated = [
            item
            for item in consumed_items
            if item.rating and item.rating >= self.min_rating
        ]
        low_rated = [
            item
            for item in consumed_items
            if item.rating and item.rating < self.min_rating
        ]

        # Calculate average rating
        ratings = [item.rating for item in consumed_items if item.rating]
        avg_rating = sum(ratings) / len(ratings) if ratings else 0.0

        # Extract authors (weighted by rating - positive for high ratings, negative for low)
        author_scores: Dict[str, float] = {}
        disliked_authors: Dict[str, float] = {}

        for item in consumed_items:
            if item.author and item.rating:
                author_lower = item.author.lower()
                if item.rating >= self.min_rating:
                    # Positive weight: 5-star = 1.0, 4-star = 0.5
                    weight = (item.rating - 3) / 2.0  # Maps 4->0.5, 5->1.0
                    author_scores[author_lower] = (
                        author_scores.get(author_lower, 0.0) + weight
                    )
                else:
                    # Negative weight: 1-star = -1.0, 2-star = -0.5, 3-star = 0.0
                    weight = (item.rating - 3) / 2.0  # Maps 1->-1.0, 2->-0.5, 3->0.0
                    disliked_authors[author_lower] = disliked_authors.get(
                        author_lower, 0.0
                    ) + abs(weight)

        # Normalize author scores
        if author_scores:
            max_score = max(author_scores.values()) if author_scores.values() else 1.0
            author_scores = {
                author: score / max_score for author, score in author_scores.items()
            }

        # Normalize disliked authors
        if disliked_authors:
            max_score = (
                max(disliked_authors.values()) if disliked_authors.values() else 1.0
            )
            if max_score > 0:
                disliked_authors = {
                    author: score / max_score
                    for author, score in disliked_authors.items()
                }

        # Extract genres from metadata (positive and negative)
        # Supports both single "genre" field and "genres" list (e.g., Steam games)
        genre_scores: Dict[str, float] = {}
        disliked_genres: Dict[str, float] = {}

        for item in consumed_items:
            if item.metadata and item.rating:
                # Handle both single genre and genres list
                genres = []
                if "genre" in item.metadata and item.metadata["genre"]:
                    genres.append(item.metadata["genre"])
                if "genres" in item.metadata and item.metadata["genres"]:
                    # Handle list of genres (e.g., Steam games)
                    if isinstance(item.metadata["genres"], list):
                        genres.extend(item.metadata["genres"])
                    elif isinstance(item.metadata["genres"], str):
                        # Some sources might store genres as comma-separated string
                        genres.extend(
                            [g.strip() for g in item.metadata["genres"].split(",")]
                        )

                for genre in genres:
                    if not genre:
                        continue
                    genre_lower = genre.lower()
                    if item.rating >= self.min_rating:
                        weight = (item.rating - 3) / 2.0
                        genre_scores[genre_lower] = (
                            genre_scores.get(genre_lower, 0.0) + weight
                        )
                    else:
                        weight = (item.rating - 3) / 2.0
                        disliked_genres[genre_lower] = disliked_genres.get(
                            genre_lower, 0.0
                        ) + abs(weight)

        # Normalize genre scores
        if genre_scores:
            max_score = max(genre_scores.values()) if genre_scores.values() else 1.0
            genre_scores = {
                genre: score / max_score for genre, score in genre_scores.items()
            }

        # Normalize disliked genres
        if disliked_genres:
            max_score = (
                max(disliked_genres.values()) if disliked_genres.values() else 1.0
            )
            if max_score > 0:
                disliked_genres = {
                    genre: score / max_score for genre, score in disliked_genres.items()
                }

        # Extract themes from reviews (only from high-rated items for now)
        theme_scores: Dict[str, float] = {}
        review_words: List[str] = []
        for item in high_rated:
            if item.review and item.rating:
                words = item.review.lower().split()
                weight = (item.rating - 3) / 2.0
                review_words.extend([(w, weight) for w in words if len(w) > 4])

        # Count theme words
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
            disliked_authors=disliked_authors,
            disliked_genres=disliked_genres,
        )
