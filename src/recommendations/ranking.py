"""Ranking algorithm for recommendations."""

import logging
from typing import List, Tuple, Dict, Any, Optional

from src.models.content import ContentItem, ContentType
from src.recommendations.preferences import UserPreferences
from src.utils.series import is_first_item_in_series

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
        adaptations_map: Optional[Dict[str, List[ContentItem]]] = None,
    ) -> List[Tuple[ContentItem, float, Dict[str, Any]]]:
        """Rank candidate items.

        Args:
            candidates: List of (ContentItem, similarity_score) tuples
            preferences: User preferences
            content_type: Content type being ranked
            adaptations_map: Optional map of item ID to list of adaptations found

        Returns:
            List of (ContentItem, final_score, metadata) tuples, sorted by score
        """
        if not candidates:
            return []

        if adaptations_map is None:
            adaptations_map = {}

        scored_items = []

        for item, similarity_score in candidates:
            # Calculate preference score
            preference_score = self._calculate_preference_score(
                item, preferences, content_type
            )

            # Calculate diversity bonus (simplified - could be enhanced)
            diversity_bonus = 0.0  # Placeholder for future diversity logic

            # Series bonus: boost first items in unstarted series (all content types)
            series_bonus = 0.0
            if is_first_item_in_series(item.title):
                series_bonus = 0.1  # Small boost for first items

            # Adaptation bonus: boost direct adaptations of consumed content
            # (e.g., LOTR books -> LOTR movies)
            adaptation_bonus = 0.0
            if item.id and item.id in adaptations_map:
                adaptations = adaptations_map[item.id]
                if adaptations:
                    # Boost based on rating of the adaptation
                    # Higher-rated adaptations get bigger boost
                    max_rating = max(
                        (a.rating for a in adaptations if a.rating), default=4
                    )
                    # Boost ranges from 0.15 to 0.25 based on rating
                    adaptation_bonus = 0.15 + (max_rating - 4) * 0.05

            # Combine scores
            # Note: preference_score can be negative (for disliked authors/genres)
            # We need to normalize it to [0, 1] range for combination, but preserve sign
            normalized_preference = (
                preference_score + 1.0
            ) / 2.0  # Map [-1, 1] to [0, 1]

            final_score = (
                self.similarity_weight * similarity_score
                + self.preference_weight * normalized_preference
                + self.diversity_weight * diversity_bonus
                + series_bonus
                + adaptation_bonus
            )

            # Apply penalty if preference_score is negative (disliked)
            if preference_score < 0:
                # Reduce final score proportionally to how much it's disliked
                final_score *= (
                    1.0 + preference_score
                )  # preference_score is negative, so this reduces the score

            metadata = {
                "similarity_score": similarity_score,
                "preference_score": preference_score,
                "diversity_bonus": diversity_bonus,
                "series_bonus": series_bonus,
                "adaptation_bonus": adaptation_bonus,
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
            Preference score (-1.0 to 1.0, where negative means disliked)
        """
        score = 0.0
        factors = 0

        # Author preference (for books) - can be negative if disliked
        if content_type == ContentType.BOOK and item.author:
            author_score = preferences.get_author_score(item.author)
            score += author_score
            factors += 1

        # Genre preference - can be negative if disliked
        # Supports both single "genre" field and "genres" list (e.g., Steam games)
        if item.metadata:
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

            # Use the highest-scoring genre if multiple genres exist
            if genres:
                genre_scores = [preferences.get_genre_score(genre) for genre in genres]
                max_genre_score = max(genre_scores) if genre_scores else 0.0
                score += max_genre_score
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

        # Clamp to [-1, 1] range
        score = max(-1.0, min(1.0, score))

        return score
