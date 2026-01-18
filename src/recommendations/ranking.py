"""Ranking algorithm for recommendations."""

import logging
from typing import List, Tuple, Dict, Any

from src.models.content import ContentItem, ContentType
from src.recommendations.preferences import UserPreferences

logger = logging.getLogger(__name__)


class RecommendationRanker:
    """Rank recommendations using multiple factors."""

    def __init__(
        self,
        similarity_weight: float = 0.6,
        preference_weight: float = 0.3,
        diversity_weight: float = 0.1,
    ) -> None:
        """Initialize ranker.

        Args:
            similarity_weight: Weight for similarity scores (0.0-1.0)
            preference_weight: Weight for preference scores (0.0-1.0)
            diversity_weight: Weight for diversity bonus (0.0-1.0)
        """
        self.similarity_weight = similarity_weight
        self.preference_weight = preference_weight
        self.diversity_weight = diversity_weight

        # Normalize weights
        total = similarity_weight + preference_weight + diversity_weight
        if total > 0:
            self.similarity_weight /= total
            self.preference_weight /= total
            self.diversity_weight /= total

    def rank(
        self,
        candidates: List[Tuple[ContentItem, float]],
        preferences: UserPreferences,
        content_type: ContentType,
    ) -> List[Tuple[ContentItem, float, Dict[str, Any]]]:
        """Rank candidate items.

        Args:
            candidates: List of (ContentItem, similarity_score) tuples
            preferences: User preferences
            content_type: Content type being ranked

        Returns:
            List of (ContentItem, final_score, metadata) tuples, sorted by score
        """
        if not candidates:
            return []

        scored_items = []

        for item, similarity_score in candidates:
            # Calculate preference score
            preference_score = self._calculate_preference_score(
                item, preferences, content_type
            )

            # Calculate diversity bonus (simplified - could be enhanced)
            diversity_bonus = 0.0  # Placeholder for future diversity logic

            # Combine scores
            final_score = (
                self.similarity_weight * similarity_score
                + self.preference_weight * preference_score
                + self.diversity_weight * diversity_bonus
            )

            metadata = {
                "similarity_score": similarity_score,
                "preference_score": preference_score,
                "diversity_bonus": diversity_bonus,
            }

            scored_items.append((item, final_score, metadata))

        # Sort by final score (descending)
        scored_items.sort(key=lambda x: x[1], reverse=True)

        return scored_items

    def _calculate_preference_score(
        self, item: ContentItem, preferences: UserPreferences, content_type: ContentType
    ) -> float:
        """Calculate preference score for an item.

        Args:
            item: ContentItem to score
            preferences: User preferences
            content_type: Content type

        Returns:
            Preference score (0.0 to 1.0)
        """
        score = 0.0
        factors = 0

        # Author preference (for books)
        if content_type == ContentType.BOOK and item.author:
            author_score = preferences.get_author_score(item.author)
            score += author_score
            factors += 1

        # Genre preference
        if item.metadata and "genre" in item.metadata:
            genre_score = preferences.get_genre_score(item.metadata["genre"])
            score += genre_score
            factors += 1

        # Average rating preference (prefer items similar to user's average)
        # This is a simplified version
        if preferences.average_rating > 0:
            # Could compare item's average rating if available
            # For now, just use a small bonus
            pass

        # Normalize by number of factors
        if factors > 0:
            score /= factors

        return score
