"""Ranking algorithm for recommendations."""

import logging
from typing import Any

from src.models.content import ContentItem, ContentType
from src.recommendations.genre_normalizer import extract_and_normalize_genres
from src.recommendations.preferences import UserPreferences

logger = logging.getLogger(__name__)


# Adaptation bonus range: base + (rating - 4) * per_star.
# For a 5-star rated adaptation: 0.15 + 1*0.05 = 0.20
# For a 3-star rated adaptation: 0.15 + (-1)*0.05 = 0.10
_ADAPTATION_BONUS_BASE = 0.15
_ADAPTATION_BONUS_PER_STAR = 0.05


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
        candidates: list[tuple[ContentItem, float]],
        preferences: UserPreferences,
        content_type: ContentType,
        adaptations_map: dict[str, list[ContentItem]] | None = None,
        recently_completed: list[ContentItem] | None = None,
    ) -> list[tuple[ContentItem, float, dict[str, Any]]]:
        """Rank candidate items.

        Args:
            candidates: List of (ContentItem, similarity_score) tuples
            preferences: User preferences
            content_type: Content type being ranked
            adaptations_map: Optional map of item ID to list of adaptations found
            recently_completed: Recently completed items for diversity scoring

        Returns:
            List of (ContentItem, final_score, metadata) tuples, sorted by score
        """
        if not candidates:
            return []

        if adaptations_map is None:
            adaptations_map = {}

        # Pre-compute recent genres for diversity scoring
        recent_genres = self._collect_recent_genres(recently_completed)

        scored_items = []

        for item, similarity_score in candidates:
            # Calculate preference score
            preference_score = self._calculate_preference_score(
                item, preferences, content_type
            )

            # Calculate diversity bonus based on genre difference from recent items
            diversity_bonus = self._calculate_diversity_score(item, recent_genres)

            # Adaptation bonus: boost direct adaptations of consumed content
            # (e.g., LOTR books -> LOTR movies)
            adaptation_bonus = 0.0
            if item.id and item.id in adaptations_map:
                adaptations = adaptations_map[item.id]
                if adaptations:
                    max_rating = max(
                        (a.rating for a in adaptations if a.rating), default=4
                    )
                    adaptation_bonus = (
                        _ADAPTATION_BONUS_BASE
                        + (max_rating - 4) * _ADAPTATION_BONUS_PER_STAR
                    )

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
                "adaptation_bonus": adaptation_bonus,
            }

            scored_items.append((item, final_score, metadata))

        # Sort by final score (descending)
        scored_items.sort(key=lambda entry: entry[1], reverse=True)

        return scored_items

    @staticmethod
    def _collect_recent_genres(
        recently_completed: list[ContentItem] | None,
        limit: int = 20,
    ) -> set[str]:
        """Collect normalized genres from recently completed items.

        Args:
            recently_completed: List of recently completed items.
            limit: Maximum number of items to consider.

        Returns:
            Set of normalized genre strings.
        """
        if not recently_completed:
            return set()

        genres: set[str] = set()
        for item in recently_completed[:limit]:
            item_genres = (
                extract_and_normalize_genres(item.metadata) if item.metadata else []
            )
            genres.update(item_genres)
        return genres

    @staticmethod
    def _calculate_diversity_score(item: ContentItem, recent_genres: set[str]) -> float:
        """Score how different an item's genres are from recently completed genres.

        Returns a score between 0.0 (identical genres) and 1.0 (completely
        different genres). If there are no recent genres or the item has no
        genres, returns 0.5 (neutral).

        Args:
            item: Candidate item to score.
            recent_genres: Set of genres from recently completed items.

        Returns:
            Diversity score in [0.0, 1.0].
        """
        if not recent_genres:
            return 0.5

        candidate_genres = set(
            extract_and_normalize_genres(item.metadata) if item.metadata else []
        )
        if not candidate_genres:
            return 0.5

        # Jaccard distance: 1 - |intersection| / |union|
        intersection = candidate_genres & recent_genres
        union = candidate_genres | recent_genres
        if not union:
            return 0.5

        return 1.0 - len(intersection) / len(union)

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
        genres = extract_and_normalize_genres(item.metadata) if item.metadata else []
        if genres:
            genre_scores = [preferences.get_genre_score(genre) for genre in genres]
            max_genre_score = max(genre_scores) if genre_scores else 0.0
            score += max_genre_score
            factors += 1

        # Normalize by number of factors
        if factors > 0:
            score /= factors

        # Clamp to [-1, 1] range
        score = max(-1.0, min(1.0, score))

        return score
