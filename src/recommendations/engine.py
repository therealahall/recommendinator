"""Main recommendation engine orchestrating all components."""

import logging
from typing import Any

from src.llm.embeddings import EmbeddingGenerator
from src.llm.recommendations import RecommendationGenerator
from src.models.content import ContentItem, ContentType
from src.recommendations.preferences import PreferenceAnalyzer, UserPreferences
from src.recommendations.ranking import RecommendationRanker
from src.recommendations.scorers import (
    DEFAULT_SCORERS,
    Scorer,
    ScoringContext,
    SemanticSimilarityScorer,
    _extract_creator,
    _extract_genres,
)
from src.recommendations.scoring_pipeline import ScoringPipeline
from src.recommendations.similarity import SimilarityMatcher
from src.storage.manager import StorageManager
from src.utils.series import build_series_tracking, should_recommend_item

logger = logging.getLogger(__name__)


class RecommendationEngine:
    """Main recommendation engine.

    The scoring pipeline **always** runs.  When an ``embedding_generator``
    is provided the engine pre-computes vector-similarity scores and adds
    a ``SemanticSimilarityScorer`` to the pipeline so that AI scores
    participate in weighted aggregation alongside all other scorers.
    """

    def __init__(
        self,
        storage_manager: StorageManager,
        embedding_generator: EmbeddingGenerator | None = None,
        recommendation_generator: RecommendationGenerator | None = None,
        min_rating: int = 4,
        scorers: list[Scorer] | None = None,
    ) -> None:
        """Initialize recommendation engine.

        Args:
            storage_manager: Storage manager for accessing data.
            embedding_generator: Optional generator for creating embeddings.
                When ``None`` the engine operates in pure non-AI mode.
            recommendation_generator: Optional LLM-based recommendation generator.
            min_rating: Minimum rating to consider for preferences.
            scorers: Scorer instances for the pipeline.  Defaults to
                :data:`DEFAULT_SCORERS`.
        """
        self.storage = storage_manager
        self.embedding_gen = embedding_generator
        self.llm_generator = recommendation_generator
        self.preference_analyzer = PreferenceAnalyzer(min_rating=min_rating)
        self.ranker = RecommendationRanker()
        scorers_list = list(scorers if scorers is not None else DEFAULT_SCORERS)
        if embedding_generator is not None:
            scorers_list.append(SemanticSimilarityScorer())
        self.pipeline = ScoringPipeline(scorers_list)

        # Only create SimilarityMatcher when embeddings are available
        self.similarity_matcher: SimilarityMatcher | None = None
        if embedding_generator is not None:
            self.similarity_matcher = SimilarityMatcher(
                storage_manager, embedding_generator
            )

    def generate_recommendations(
        self,
        content_type: ContentType,
        count: int = 5,
        use_llm: bool = False,
    ) -> list[dict[str, Any]]:
        """Generate recommendations for a content type.

        Uses preferences from ALL consumed content types to provide
        cross-content-type recommendations. For example, if you've read
        sci-fi books, it may recommend sci-fi TV shows or games.

        The scoring pipeline always runs.  When ``embedding_generator``
        is set, vector-similarity search supplements the pipeline scores.

        Args:
            content_type: Type of content to recommend.
            count: Number of recommendations to generate.
            use_llm: Whether to use LLM for final recommendation generation.

        Returns:
            List of recommendation dictionaries.
        """
        # Get consumed items from ALL content types for preference analysis
        all_consumed_items = self.storage.get_completed_items(
            content_type=None, min_rating=None
        )

        # Get consumed items of the requested type for series tracking
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
        unconsumed_items = self.storage.get_unconsumed_items(
            content_type=content_type, limit=100
        )

        if not unconsumed_items:
            logger.warning(f"No unconsumed items found for {content_type.value}")
            return []

        # Analyze preferences from ALL consumed content types
        preferences = self.preference_analyzer.analyze(all_consumed_items)

        logger.info(
            f"Analyzed preferences from {len(all_consumed_items)} consumed items "
            f"across all content types to recommend {content_type.value}s"
        )

        # Build series tracking (content-type specific)
        series_tracking = build_series_tracking(consumed_items_of_type)

        # -----------------------------------------------------------------
        # Pre-compute similarity scores (AI path)
        # -----------------------------------------------------------------
        similarity_scores: dict[str | None, float] = {}
        if self.similarity_matcher is not None:
            rated_items = [
                item for item in all_consumed_items if item.rating is not None
            ]
            rated_items.sort(key=lambda x: x.rating or 0, reverse=True)

            high_rated_refs = [
                item for item in rated_items if item.rating and item.rating >= 4
            ][:5]
            low_rated_refs = [
                item for item in rated_items if item.rating and item.rating < 3
            ][:3]

            reference_items = high_rated_refs + low_rated_refs
            if not reference_items:
                reference_items = all_consumed_items[:5]

            exclude_ids = [item.id for item in all_consumed_items if item.id]

            similar_candidates = self.similarity_matcher.find_similar(
                reference_items=reference_items,
                content_type=content_type,
                exclude_ids=exclude_ids,
                limit=count * 3,
            )

            if similar_candidates:
                similarity_scores = {
                    item.id: sim_score for item, sim_score in similar_candidates
                }

        # -----------------------------------------------------------------
        # Score all unconsumed candidates via the pipeline (always runs)
        # -----------------------------------------------------------------
        scoring_context = ScoringContext(
            preferences=preferences,
            consumed_items=all_consumed_items,
            series_tracking=series_tracking,
            content_type=content_type,
            all_unconsumed_items=unconsumed_items,
            similarity_scores=similarity_scores,
        )

        pipeline_scored = self.pipeline.score_candidates(
            unconsumed_items, scoring_context
        )

        # Take top count*3 from pipeline for further processing
        top_candidates: list[tuple[ContentItem, float]] = pipeline_scored[: count * 3]

        # -----------------------------------------------------------------
        # Filter candidates based on series rules
        # -----------------------------------------------------------------
        filtered_candidates: list[tuple[ContentItem, float]] = []
        for item, score in top_candidates:
            if should_recommend_item(
                item, series_tracking, unconsumed_items=unconsumed_items
            ):
                filtered_candidates.append((item, score))
            else:
                logger.debug(
                    f"Filtered out {item.title} - doesn't meet series recommendation rules"
                )

        if not filtered_candidates:
            logger.warning(
                "Series filtering removed all candidates, using original candidates"
            )
            filtered_candidates = top_candidates

        # -----------------------------------------------------------------
        # Detect adaptations & find contributing reference items
        # -----------------------------------------------------------------
        candidate_metadata: list[dict[str, Any]] = []
        adaptations_map: dict[str, list[ContentItem]] = {}

        for item, similarity_score in filtered_candidates:
            adaptations = self._find_direct_adaptations(item, all_consumed_items)
            contributing_items = self._find_contributing_reference_items(
                item, all_consumed_items
            )

            candidate_metadata.append(
                {
                    "item": item,
                    "similarity_score": similarity_score,
                    "adaptations": adaptations,
                    "contributing_items": contributing_items,
                }
            )

            if item.id and adaptations:
                adaptations_map[item.id] = adaptations

        # -----------------------------------------------------------------
        # Rank (adaptation bonus, series bonus, preference adjustments)
        # -----------------------------------------------------------------
        ranked_items = self.ranker.rank(
            candidates=[
                (meta["item"], meta["similarity_score"]) for meta in candidate_metadata
            ],
            preferences=preferences,
            content_type=content_type,
            adaptations_map=adaptations_map,
        )

        top_recommendations = ranked_items[:count]

        # -----------------------------------------------------------------
        # Format recommendations
        # -----------------------------------------------------------------
        recommendations: list[dict[str, Any]] = []
        for item, score, rank_metadata in top_recommendations:
            item_meta = next(
                (m for m in candidate_metadata if m["item"].id == item.id), None
            )

            adaptations_list: list[ContentItem] = []
            contributing_list: list[ContentItem] = []
            if item_meta:
                adaptations_list = item_meta.get("adaptations", []) or []
                contributing_list = item_meta.get("contributing_items", []) or []

            rec: dict[str, Any] = {
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

        # -----------------------------------------------------------------
        # Optionally enhance with LLM reasoning
        # -----------------------------------------------------------------
        if use_llm and self.llm_generator:
            try:
                if recommendations:
                    llm_recs = self.llm_generator.generate_recommendations(
                        content_type=content_type,
                        consumed_items=all_consumed_items,
                        unconsumed_items=[rec["item"] for rec in recommendations],
                        count=count,
                    )
                    for index, llm_rec in enumerate(llm_recs[:count]):
                        if index < len(recommendations):
                            recommendations[index]["llm_reasoning"] = llm_rec.get(
                                "reasoning", ""
                            )
                else:
                    logger.info("Using LLM-only recommendations")
                    llm_recs = self.llm_generator.generate_recommendations(
                        content_type=content_type,
                        consumed_items=all_consumed_items,
                        unconsumed_items=unconsumed_items,
                        count=count,
                    )
                    for llm_rec in llm_recs:
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
                                    "score": 0.8,
                                    "similarity_score": 0.0,
                                    "preference_score": 0.5,
                                    "reasoning": llm_rec.get("reasoning", ""),
                                    "llm_reasoning": llm_rec.get("reasoning", ""),
                                }
                            )
            except Exception as error:
                logger.warning(f"LLM recommendation generation failed: {error}")

        # -----------------------------------------------------------------
        # Final fallback
        # -----------------------------------------------------------------
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

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _handle_cold_start(
        self, content_type: ContentType, count: int
    ) -> list[dict[str, Any]]:
        """Handle cold start scenario (no consumed items across any content type).

        Args:
            content_type: Content type.
            count: Number of recommendations.

        Returns:
            List of recommendations (empty or generic).
        """
        logger.info(
            f"Cold start: no consumed items across any content type. "
            f"Cannot generate recommendations for {content_type.value}."
        )
        return []

    def _find_direct_adaptations(
        self, item: ContentItem, consumed_items: list[ContentItem]
    ) -> list[ContentItem]:
        """Find direct adaptations of this item in consumed content.

        An adaptation is when the same title/author exists in a different content type.
        For example, "Lord of the Rings" book -> "Lord of the Rings" movie.

        Args:
            item: Item to check for adaptations.
            consumed_items: List of consumed items to check against.

        Returns:
            List of consumed items that are adaptations of this item.
        """
        adaptations = []

        def normalize_title(title: str) -> str:
            if not title:
                return ""
            normalized = title.lower().strip()
            normalized = (
                normalized.replace("the ", "").replace("a ", "").replace("an ", "")
            )
            return normalized

        item_title_norm = normalize_title(item.title)
        item_author_norm = normalize_title(item.author) if item.author else None

        for consumed in consumed_items:
            if consumed.content_type == item.content_type:
                continue

            consumed_title_norm = normalize_title(consumed.title)
            consumed_author_norm = (
                normalize_title(consumed.author) if consumed.author else None
            )

            title_match = item_title_norm == consumed_title_norm
            author_match = False
            if item_author_norm and consumed_author_norm:
                author_match = item_author_norm == consumed_author_norm

            if title_match or (
                author_match and self._titles_similar(item.title, consumed.title)
            ):
                if consumed.rating and consumed.rating >= 4:
                    adaptations.append(consumed)

        return adaptations

    def _titles_similar(self, title1: str, title2: str) -> bool:
        """Check if two titles are similar (fuzzy matching).

        Args:
            title1: First title.
            title2: Second title.

        Returns:
            True if titles are similar.
        """
        if not title1 or not title2:
            return False

        t1_norm = title1.lower().strip()
        t2_norm = title2.lower().strip()

        for word in ["the", "a", "an"]:
            t1_norm = t1_norm.replace(f"{word} ", "")
            t2_norm = t2_norm.replace(f"{word} ", "")

        return t1_norm in t2_norm or t2_norm in t1_norm

    def _find_contributing_reference_items(
        self,
        candidate: ContentItem,
        all_consumed_items: list[ContentItem],
    ) -> list[ContentItem]:
        """Find consumed items that share metadata with *candidate*.

        Uses genre and creator overlap to identify which consumed items
        are most related — no embeddings required.

        Args:
            candidate: The recommended item.
            all_consumed_items: All consumed items.

        Returns:
            Top 3 contributing consumed items (by overlap score).
        """
        candidate_genres = set(_extract_genres(candidate))
        candidate_creator = _extract_creator(candidate)

        scored: list[tuple[ContentItem, float]] = []
        for consumed in all_consumed_items:
            if not (consumed.rating and consumed.rating >= 4):
                continue

            overlap = 0.0
            consumed_genres = set(_extract_genres(consumed))
            if candidate_genres and consumed_genres:
                intersection = candidate_genres & consumed_genres
                if intersection:
                    overlap += len(intersection) / len(
                        candidate_genres | consumed_genres
                    )

            consumed_creator = _extract_creator(consumed)
            if (
                candidate_creator
                and consumed_creator
                and candidate_creator == consumed_creator
            ):
                overlap += 0.5

            if overlap > 0:
                scored.append((consumed, overlap))

        scored.sort(key=lambda pair: pair[1], reverse=True)
        return [item for item, _ in scored[:3]]

    def _generate_reasoning(
        self,
        item: ContentItem,
        preferences: UserPreferences,
        metadata: dict[str, Any],
        adaptations: list[ContentItem],
        contributing_items: list[ContentItem],
    ) -> str:
        """Generate reasoning for a recommendation.

        Args:
            item: Recommended item.
            preferences: User preferences (from all content types).
            metadata: Recommendation metadata.
            adaptations: List of direct adaptations found in consumed content.
            contributing_items: List of reference items that contributed.

        Returns:
            Reasoning string.
        """
        reasons: list[str] = []

        # Check for direct adaptations first (highest priority)
        if adaptations:
            adaptation = adaptations[0]
            adaptation_type = (
                adaptation.content_type.lower()
                if isinstance(adaptation.content_type, str)
                else adaptation.content_type.value.lower()
            )
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
            cross_type_items = [
                ref
                for ref in contributing_items
                if ref.content_type != item.content_type
            ]

            if cross_type_items:
                item_refs = []
                for ref in cross_type_items[:2]:
                    ref_type = (
                        ref.content_type.value.lower()
                        if hasattr(ref.content_type, "value")
                        else str(ref.content_type).lower()
                    )
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
