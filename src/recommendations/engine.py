"""Main recommendation engine orchestrating all components."""

import logging
from typing import List, Dict, Any, Optional

from src.models.content import ContentItem, ContentType, ConsumptionStatus
from src.storage.manager import StorageManager
from src.llm.embeddings import EmbeddingGenerator
from src.llm.recommendations import RecommendationGenerator
from src.recommendations.preferences import PreferenceAnalyzer, UserPreferences
from src.recommendations.similarity import SimilarityMatcher
from src.recommendations.ranking import RecommendationRanker
from src.utils.series import build_series_tracking, should_recommend_book

logger = logging.getLogger(__name__)


class RecommendationEngine:
    """Main recommendation engine."""

    def __init__(
        self,
        storage_manager: StorageManager,
        embedding_generator: EmbeddingGenerator,
        recommendation_generator: Optional[RecommendationGenerator] = None,
        min_rating: int = 4,
    ) -> None:
        """Initialize recommendation engine.

        Args:
            storage_manager: Storage manager for accessing data
            embedding_generator: Generator for creating embeddings
            recommendation_generator: Optional LLM-based recommendation generator
            min_rating: Minimum rating to consider for preferences
        """
        self.storage = storage_manager
        self.embedding_gen = embedding_generator
        self.llm_generator = recommendation_generator
        self.preference_analyzer = PreferenceAnalyzer(min_rating=min_rating)
        self.similarity_matcher = SimilarityMatcher(
            storage_manager, embedding_generator
        )
        self.ranker = RecommendationRanker()

    def generate_recommendations(
        self,
        content_type: ContentType,
        count: int = 5,
        use_llm: bool = False,
    ) -> List[Dict[str, Any]]:
        """Generate recommendations for a content type.

        Args:
            content_type: Type of content to recommend
            count: Number of recommendations to generate
            use_llm: Whether to use LLM for final recommendation generation

        Returns:
            List of recommendation dictionaries
        """
        # Get consumed items (all completed items, not just high-rated)
        # We'll filter for preference analysis later
        consumed_items = self.storage.get_completed_items(
            content_type=content_type, min_rating=None
        )

        if not consumed_items:
            logger.warning(f"No consumed items found for {content_type.value}")
            return self._handle_cold_start(content_type, count)

        # Get unconsumed items
        unconsumed_items = self.storage.get_unconsumed_items(
            content_type=content_type, limit=100
        )

        if not unconsumed_items:
            logger.warning(f"No unconsumed items found for {content_type.value}")
            return []

        # Analyze preferences
        preferences = self.preference_analyzer.analyze(consumed_items)

        # Find similar items using vector similarity
        # Use ALL consumed items weighted by rating (high ratings = positive, low ratings = negative)
        # Sort by absolute rating value, prioritizing both high and low ratings
        rated_items = [item for item in consumed_items if item.rating is not None]

        # Sort by rating (descending), so we get both high-rated (positive) and low-rated (negative) items
        rated_items.sort(key=lambda x: x.rating or 0, reverse=True)

        # Use top-rated items (for positive similarity) and low-rated items (to avoid similar content)
        # Take top 5 high-rated and bottom 3 low-rated
        high_rated_refs = [
            item for item in rated_items if item.rating and item.rating >= 4
        ][:5]
        low_rated_refs = [
            item for item in rated_items if item.rating and item.rating < 3
        ][:3]

        reference_items = high_rated_refs + low_rated_refs

        # If no rated items, use all consumed items
        if not reference_items:
            logger.info("No rated items, using all consumed items for similarity")
            reference_items = consumed_items[:5]

        exclude_ids = [item.id for item in consumed_items if item.id]

        similar_candidates = self.similarity_matcher.find_similar(
            reference_items=reference_items,
            content_type=content_type,
            exclude_ids=exclude_ids,
            limit=count * 3,  # Get more candidates for ranking
        )

        # If similarity search returned no candidates, fall back to using unconsumed items directly
        if not similar_candidates:
            logger.warning(
                "Similarity search returned no candidates, using unconsumed items directly"
            )
            # Use unconsumed items as candidates with default scores
            similar_candidates = [(item, 0.5) for item in unconsumed_items[: count * 3]]

        # Build series tracking to filter recommendations
        series_tracking = build_series_tracking(consumed_items)

        # Filter candidates based on series rules
        # Only recommend first books in unstarted series or next books in started series
        filtered_candidates = []
        for item, score in similar_candidates:
            if should_recommend_book(item, series_tracking):
                filtered_candidates.append((item, score))
            else:
                logger.debug(
                    f"Filtered out {item.title} - doesn't meet series recommendation rules"
                )

        # If filtering removed all candidates, use original candidates (fallback)
        if not filtered_candidates:
            logger.warning(
                "Series filtering removed all candidates, using original candidates"
            )
            filtered_candidates = similar_candidates

        # Rank candidates
        ranked_items = self.ranker.rank(
            candidates=filtered_candidates,
            preferences=preferences,
            content_type=content_type,
        )

        # Take top N
        top_recommendations = ranked_items[:count]

        # Format recommendations
        recommendations = []
        for item, score, metadata in top_recommendations:
            rec = {
                "item": item,
                "score": score,
                "similarity_score": metadata["similarity_score"],
                "preference_score": metadata["preference_score"],
                "reasoning": self._generate_reasoning(item, preferences, metadata),
            }
            recommendations.append(rec)

        # Optionally use LLM to refine recommendations
        # If we have no recommendations yet, try LLM-only approach
        if use_llm and self.llm_generator:
            try:
                # If we have recommendations, enhance them with LLM reasoning
                # Otherwise, generate recommendations purely from LLM
                if recommendations:
                    llm_recs = self.llm_generator.generate_recommendations(
                        content_type=content_type,
                        consumed_items=consumed_items,
                        unconsumed_items=[rec["item"] for rec in recommendations],
                        count=count,
                    )
                    # Merge LLM reasoning with similarity-based recommendations
                    for i, llm_rec in enumerate(llm_recs[:count]):
                        if i < len(recommendations):
                            recommendations[i]["llm_reasoning"] = llm_rec.get(
                                "reasoning", ""
                            )
                else:
                    # No similarity-based recommendations, use LLM directly
                    logger.info("Using LLM-only recommendations")
                    llm_recs = self.llm_generator.generate_recommendations(
                        content_type=content_type,
                        consumed_items=consumed_items,
                        unconsumed_items=unconsumed_items,
                        count=count,
                    )
                    # Convert LLM recommendations to our format
                    for llm_rec in llm_recs:
                        # Find the matching item from unconsumed_items
                        matching_item = None
                        for item in unconsumed_items:
                            if item.title == llm_rec.get("title") and (
                                not llm_rec.get("author")
                                or item.author == llm_rec.get("author")
                            ):
                                matching_item = item
                                break

                        if matching_item and should_recommend_book(
                            matching_item, series_tracking
                        ):
                            recommendations.append(
                                {
                                    "item": matching_item,
                                    "score": 0.8,  # Default score for LLM recommendations
                                    "similarity_score": 0.0,
                                    "preference_score": 0.5,
                                    "reasoning": llm_rec.get("reasoning", ""),
                                    "llm_reasoning": llm_rec.get("reasoning", ""),
                                }
                            )
            except Exception as e:
                logger.warning(f"LLM recommendation generation failed: {e}")

        # Final fallback: if we still have no recommendations, return some unconsumed items
        # (filtered by series rules)
        if not recommendations and unconsumed_items:
            logger.info("Using fallback: returning unconsumed items as recommendations")
            for item in unconsumed_items:
                if should_recommend_book(item, series_tracking):
                    recommendations.append(
                        {
                            "item": item,
                            "score": 0.5,
                            "similarity_score": 0.0,
                            "preference_score": 0.0,
                            "reasoning": "Available in your library",
                        }
                    )
                    if len(recommendations) >= count:
                        break

        return recommendations

    def _handle_cold_start(
        self, content_type: ContentType, count: int
    ) -> List[Dict[str, Any]]:
        """Handle cold start scenario (no consumed items).

        Args:
            content_type: Content type
            count: Number of recommendations

        Returns:
            List of recommendations (empty or generic)
        """
        # For cold start, return empty or use some default strategy
        logger.info(f"Cold start: no consumed items for {content_type.value}")
        return []

    def _generate_reasoning(
        self, item: ContentItem, preferences: UserPreferences, metadata: Dict[str, Any]
    ) -> str:
        """Generate reasoning for a recommendation.

        Args:
            item: Recommended item
            preferences: User preferences
            metadata: Recommendation metadata

        Returns:
            Reasoning string
        """
        reasons = []

        if metadata["similarity_score"] > 0.7:
            reasons.append("highly similar to items you've enjoyed")

        if metadata["preference_score"] > 0.5:
            if item.author and preferences.get_author_score(item.author) > 0.5:
                reasons.append(f"by author {item.author}")

            if item.metadata and "genre" in item.metadata:
                genre = item.metadata["genre"]
                if preferences.get_genre_score(genre) > 0.5:
                    reasons.append(f"in the {genre} genre")

        if not reasons:
            reasons.append("based on your preferences")

        return "Recommended " + " and ".join(reasons)
