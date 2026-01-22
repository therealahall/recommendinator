"""Main recommendation engine orchestrating all components."""

import logging
from typing import List, Dict, Any, Optional

from src.models.content import ContentItem, ContentType
from src.storage.manager import StorageManager
from src.llm.embeddings import EmbeddingGenerator
from src.llm.recommendations import RecommendationGenerator
from src.recommendations.preferences import PreferenceAnalyzer, UserPreferences
from src.recommendations.similarity import SimilarityMatcher
from src.recommendations.ranking import RecommendationRanker
from src.utils.series import build_series_tracking, should_recommend_item

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

        Uses preferences from ALL consumed content types to provide
        cross-content-type recommendations. For example, if you've read
        sci-fi books, it may recommend sci-fi TV shows or games.

        Args:
            content_type: Type of content to recommend
            count: Number of recommendations to generate
            use_llm: Whether to use LLM for final recommendation generation

        Returns:
            List of recommendation dictionaries
        """
        # Get consumed items from ALL content types for preference analysis
        # This allows cross-content-type recommendations (e.g., sci-fi books
        # can influence sci-fi game/TV recommendations)
        all_consumed_items = self.storage.get_completed_items(
            content_type=None, min_rating=None
        )

        # Get consumed items of the requested type for series tracking
        # (series tracking is content-type specific)
        consumed_items_of_type = self.storage.get_completed_items(
            content_type=content_type, min_rating=None
        )

        if not all_consumed_items:
            logger.warning(
                f"No consumed items found across any content type. "
                f"Cannot generate recommendations for {content_type.value}."
            )
            return self._handle_cold_start(content_type, count)

        # Get unconsumed items of the requested type
        # (we're recommending this specific content type)
        unconsumed_items = self.storage.get_unconsumed_items(
            content_type=content_type, limit=100
        )

        if not unconsumed_items:
            logger.warning(f"No unconsumed items found for {content_type.value}")
            return []

        # Analyze preferences from ALL consumed content types
        # This captures themes, genres, and preferences across books, games, TV, etc.
        preferences = self.preference_analyzer.analyze(all_consumed_items)

        logger.info(
            f"Analyzed preferences from {len(all_consumed_items)} consumed items "
            f"across all content types to recommend {content_type.value}s"
        )

        # Find similar items using vector similarity
        # Use ALL consumed items from ALL content types as reference
        # This enables cross-content-type similarity (e.g., sci-fi books can
        # find similar sci-fi games/TV shows)
        rated_items = [item for item in all_consumed_items if item.rating is not None]

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
            reference_items = all_consumed_items[:5]

        # Exclude all consumed items (across all types) from recommendations
        exclude_ids = [item.id for item in all_consumed_items if item.id]

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
        # (series tracking is content-type specific, so use items of requested type)
        # Apply series filtering to ALL content types
        series_tracking = build_series_tracking(consumed_items_of_type)

        # Filter candidates based on series rules (all content types)
        # Check if previous items in series exist in unconsumed data
        filtered_candidates = []
        for item, score in similar_candidates:
            if should_recommend_item(
                item, series_tracking, unconsumed_items=unconsumed_items
            ):
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

        # Detect direct adaptations and find contributing reference items
        # This helps us provide better reasoning (e.g., "because you read LOTR books")
        candidate_metadata: List[Dict[str, Any]] = []
        adaptations_map: Dict[str, List[ContentItem]] = {}

        for item, similarity_score in filtered_candidates:
            # Check for direct adaptations (same title/author across content types)
            adaptations = self._find_direct_adaptations(item, all_consumed_items)

            # Find which reference items contributed to this recommendation
            # (items that are similar to this candidate)
            contributing_items = self._find_contributing_reference_items(
                item, reference_items, all_consumed_items
            )

            candidate_metadata.append(
                {
                    "item": item,
                    "similarity_score": similarity_score,
                    "adaptations": adaptations,
                    "contributing_items": contributing_items,
                }
            )

            # Build adaptations map for ranker
            if item.id and adaptations:
                adaptations_map[item.id] = adaptations

        # Rank candidates (with adaptation boost applied in ranker)
        ranked_items = self.ranker.rank(
            candidates=[
                (meta["item"], meta["similarity_score"]) for meta in candidate_metadata
            ],
            preferences=preferences,
            content_type=content_type,
            adaptations_map=adaptations_map,
        )

        # Take top N
        top_recommendations = ranked_items[:count]

        # Format recommendations with enhanced reasoning
        recommendations = []
        for item, score, rank_metadata in top_recommendations:
            # Find the metadata for this item
            item_meta = next(
                (m for m in candidate_metadata if m["item"].id == item.id), None
            )

            adaptations_list: List[ContentItem] = []
            contributing_list: List[ContentItem] = []
            if item_meta:
                adaptations_list = item_meta.get("adaptations", []) or []
                contributing_list = item_meta.get("contributing_items", []) or []

            rec = {
                "item": item,
                "score": score,
                "similarity_score": rank_metadata["similarity_score"],
                "preference_score": rank_metadata["preference_score"],
                "reasoning": self._generate_reasoning(
                    item,
                    preferences,
                    rank_metadata,
                    adaptations_list,
                    contributing_list,
                ),
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
                        consumed_items=all_consumed_items,
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
                        consumed_items=all_consumed_items,
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

                        if matching_item and should_recommend_item(
                            matching_item,
                            series_tracking,
                            unconsumed_items=unconsumed_items,
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
        # (filtered by series rules for all content types)
        if not recommendations and unconsumed_items:
            logger.info("Using fallback: returning unconsumed items as recommendations")
            for item in unconsumed_items:
                if should_recommend_item(
                    item, series_tracking, unconsumed_items=unconsumed_items
                ):
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
        """Handle cold start scenario (no consumed items across any content type).

        Args:
            content_type: Content type
            count: Number of recommendations

        Returns:
            List of recommendations (empty or generic)
        """
        # For cold start, return empty or use some default strategy
        logger.info(
            f"Cold start: no consumed items across any content type. "
            f"Cannot generate recommendations for {content_type.value}."
        )
        return []

    def _find_direct_adaptations(
        self, item: ContentItem, consumed_items: List[ContentItem]
    ) -> List[ContentItem]:
        """Find direct adaptations of this item in consumed content.

        An adaptation is when the same title/author exists in a different content type.
        For example, "Lord of the Rings" book -> "Lord of the Rings" movie.

        Args:
            item: Item to check for adaptations
            consumed_items: List of consumed items to check against

        Returns:
            List of consumed items that are adaptations of this item
        """
        adaptations = []

        # Normalize title for comparison (lowercase, remove common punctuation)
        def normalize_title(title: str) -> str:
            if not title:
                return ""
            # Remove common punctuation and extra spaces
            normalized = title.lower().strip()
            # Remove common words that might differ (e.g., "The", "A")
            normalized = (
                normalized.replace("the ", "").replace("a ", "").replace("an ", "")
            )
            return normalized

        item_title_norm = normalize_title(item.title)
        item_author_norm = normalize_title(item.author) if item.author else None

        for consumed in consumed_items:
            # Skip if same content type (not an adaptation)
            if consumed.content_type == item.content_type:
                continue

            consumed_title_norm = normalize_title(consumed.title)
            consumed_author_norm = (
                normalize_title(consumed.author) if consumed.author else None
            )

            # Check if titles match (allowing for minor variations)
            title_match = item_title_norm == consumed_title_norm

            # Check if authors match (if both have authors)
            author_match = False
            if item_author_norm and consumed_author_norm:
                author_match = item_author_norm == consumed_author_norm

            # Consider it an adaptation if:
            # 1. Titles match exactly (normalized)
            # 2. Or titles are very similar and authors match
            if title_match or (
                author_match and self._titles_similar(item.title, consumed.title)
            ):
                # Only include high-rated adaptations (we want to boost things they enjoyed)
                if consumed.rating and consumed.rating >= 4:
                    adaptations.append(consumed)

        return adaptations

    def _titles_similar(self, title1: str, title2: str) -> bool:
        """Check if two titles are similar (fuzzy matching).

        Args:
            title1: First title
            title2: Second title

        Returns:
            True if titles are similar
        """
        if not title1 or not title2:
            return False

        # Simple similarity check: if one title contains the other (normalized)
        t1_norm = title1.lower().strip()
        t2_norm = title2.lower().strip()

        # Remove common words
        for word in ["the", "a", "an"]:
            t1_norm = t1_norm.replace(f"{word} ", "")
            t2_norm = t2_norm.replace(f"{word} ", "")

        # Check if one contains the other (for cases like "LOTR" vs "Lord of the Rings")
        return t1_norm in t2_norm or t2_norm in t1_norm

    def _find_contributing_reference_items(
        self,
        candidate: ContentItem,
        reference_items: List[ContentItem],
        all_consumed_items: List[ContentItem],
    ) -> List[ContentItem]:
        """Find which reference items contributed to this recommendation.

        Uses semantic similarity to find the top reference items that are most
        similar to the candidate. This helps explain why an item was recommended.

        Args:
            candidate: The recommended item
            reference_items: Reference items used for similarity search
            all_consumed_items: All consumed items (for context)

        Returns:
            List of contributing reference items (top 3 most similar)
        """
        if not reference_items:
            return []

        # Generate embedding for candidate
        try:
            candidate_embedding = self.embedding_gen.generate_content_embedding(
                candidate
            )
        except Exception as e:
            logger.warning(f"Failed to generate embedding for {candidate.title}: {e}")
            return []

        # Calculate similarity to each reference item
        similarities = []
        for ref_item in reference_items:
            try:
                ref_embedding = self.embedding_gen.generate_content_embedding(ref_item)

                # Calculate cosine similarity
                import numpy as np

                similarity = np.dot(candidate_embedding, ref_embedding) / (
                    np.linalg.norm(candidate_embedding) * np.linalg.norm(ref_embedding)
                )

                # Only include high-rated items that are similar
                if ref_item.rating and ref_item.rating >= 4 and similarity > 0.5:
                    similarities.append((ref_item, similarity))
            except Exception as e:
                logger.debug(
                    f"Failed to calculate similarity for {ref_item.title}: {e}"
                )
                continue

        # Sort by similarity and return top 3
        similarities.sort(key=lambda x: x[1], reverse=True)
        return [item for item, _ in similarities[:3]]

    def _generate_reasoning(
        self,
        item: ContentItem,
        preferences: UserPreferences,
        metadata: Dict[str, Any],
        adaptations: List[ContentItem],
        contributing_items: List[ContentItem],
    ) -> str:
        """Generate reasoning for a recommendation.

        Reasoning considers preferences from all consumed content types,
        enabling cross-content-type recommendations. Now includes specific
        references to source items that influenced the recommendation.

        Args:
            item: Recommended item
            preferences: User preferences (from all content types)
            metadata: Recommendation metadata
            adaptations: List of direct adaptations found in consumed content
            contributing_items: List of reference items that contributed to this recommendation

        Returns:
            Reasoning string
        """
        reasons = []

        # Check for direct adaptations first (highest priority)
        if adaptations:
            adaptation = adaptations[0]  # Use the first/highest-rated adaptation
            adaptation_type = adaptation.content_type.value.lower()
            if adaptation.author:
                reasons.append(
                    f"because you read and enjoyed '{adaptation.title}' "
                    f"by {adaptation.author} ({adaptation_type})"
                )
            else:
                reasons.append(
                    f"because you enjoyed '{adaptation.title}' ({adaptation_type})"
                )

        # Mention specific contributing items from other content types
        elif contributing_items:
            # Filter to items from different content types
            cross_type_items = [
                ref
                for ref in contributing_items
                if ref.content_type != item.content_type
            ]

            if cross_type_items:
                # Format the contributing items
                item_refs = []
                for ref in cross_type_items[:2]:  # Limit to top 2
                    ref_type = ref.content_type.value.lower()
                    if ref.author:
                        item_refs.append(f"'{ref.title}' by {ref.author} ({ref_type})")
                    else:
                        item_refs.append(f"'{ref.title}' ({ref_type})")

                if len(item_refs) == 1:
                    reasons.append(f"based on your enjoyment of {item_refs[0]}")
                else:
                    reasons.append(
                        f"based on your enjoyment of {', '.join(item_refs[:-1])} and {item_refs[-1]}"
                    )

        # Fall back to general similarity/preference reasoning
        if not reasons:
            if metadata["similarity_score"] > 0.7:
                reasons.append(
                    "highly similar to items you've enjoyed across all content types"
                )

            if metadata["preference_score"] > 0.5:
                if item.author and preferences.get_author_score(item.author) > 0.5:
                    reasons.append(f"by author {item.author}")

                # Check for genre preference (works across content types)
                # Steam games use "genres" (plural) in metadata
                genre = None
                if item.metadata:
                    genre = item.metadata.get("genre") or (
                        item.metadata.get("genres", [])[0]
                        if item.metadata.get("genres")
                        else None
                    )

                if genre and preferences.get_genre_score(genre) > 0.5:
                    reasons.append(f"in the {genre} genre")

        if not reasons:
            reasons.append("based on your preferences across all content types")

        return "Recommended " + " and ".join(reasons)
