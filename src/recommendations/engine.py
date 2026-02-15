"""Main recommendation engine orchestrating all components."""

import logging
import re
from typing import Any

from src.llm.embeddings import EmbeddingGenerator
from src.llm.recommendations import RecommendationGenerator
from src.models.content import ContentItem, ContentType, get_enum_value
from src.models.user_preferences import UserPreferenceConfig
from src.recommendations.preference_interpreter import (
    InterpretedPreference,
    PatternBasedInterpreter,
)
from src.recommendations.preferences import PreferenceAnalyzer, UserPreferences
from src.recommendations.ranking import RecommendationRanker
from src.recommendations.scorers import (
    DEFAULT_SCORERS,
    CustomPreferenceScorer,
    Scorer,
    ScoringContext,
    SemanticSimilarityScorer,
    build_scorers_with_overrides,
    extract_creator,
    extract_genres,
)
from src.recommendations.scoring_pipeline import ScoredCandidate, ScoringPipeline
from src.recommendations.similarity import SimilarityMatcher
from src.storage.manager import StorageManager
from src.utils.series import (
    build_series_tracking,
    expand_tv_shows_to_seasons,
    inject_seasons_watched_tracking,
    should_recommend_item,
)

logger = logging.getLogger(__name__)

# Human-readable labels for content types used in recommendation reasoning.
_CONTENT_TYPE_LABEL: dict[str, str] = {
    "book": "Book",
    "movie": "Movie",
    "tv_show": "TV Show",
    "video_game": "Video Game",
}


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
        semantic_similarity_weight: float = 1.5,
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
            semantic_similarity_weight: Weight for the SemanticSimilarityScorer
                when AI is enabled.
        """
        self.storage = storage_manager
        self.embedding_gen = embedding_generator
        self.llm_generator = recommendation_generator
        self.preference_analyzer = PreferenceAnalyzer(min_rating=min_rating)
        self.ranker = RecommendationRanker()
        scorers_list = list(scorers if scorers is not None else DEFAULT_SCORERS)
        if embedding_generator is not None:
            scorers_list.append(
                SemanticSimilarityScorer(weight=semantic_similarity_weight)
            )
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
        user_preference_config: UserPreferenceConfig | None = None,
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
            user_preference_config: Optional per-user preference config.
                When provided, scorer weights are overridden for this call.

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
            return []

        # Get ALL unconsumed items of the requested type.
        # We need the full list for accurate series ordering checks - a limit
        # would break series detection when earlier entries sort after later ones
        # (e.g., "The Black Unicorn #2" sorts before "Magic Kingdom... #1" when
        # ignoring articles). The scoring pipeline limits results after scoring.
        unconsumed_items = self.storage.get_unconsumed_items(
            content_type=content_type, limit=None
        )

        # Filter out ignored items - they should not be recommended
        unconsumed_items = [item for item in unconsumed_items if not item.ignored]

        if not unconsumed_items:
            logger.warning(f"No unconsumed items found for {content_type.value}")
            return []

        # Build series tracking (content-type specific) — before TV expansion
        # so that inject_seasons_watched_tracking can use the show-level items
        series_tracking = build_series_tracking(consumed_items_of_type)

        # -----------------------------------------------------------------
        # Expand TV shows to season-level for granular recommendations
        # (library stays show-level; expansion is for scoring only)
        # -----------------------------------------------------------------
        if content_type == ContentType.TV_SHOW:
            series_tracking = inject_seasons_watched_tracking(
                unconsumed_items, series_tracking
            )
            unconsumed_items = expand_tv_shows_to_seasons(unconsumed_items)
            logger.info(
                f"Expanded TV shows to {len(unconsumed_items)} season-level candidates"
            )

        # -----------------------------------------------------------------
        # Interpret custom rules (if present)
        # -----------------------------------------------------------------
        interpreted_prefs: InterpretedPreference | None = None
        if user_preference_config is not None and user_preference_config.custom_rules:
            interpreter = PatternBasedInterpreter()
            interpreted_prefs = interpreter.interpret_all(
                user_preference_config.custom_rules
            )
            logger.info(
                f"Interpreted {len(user_preference_config.custom_rules)} custom rules: "
                f"boosts={list(interpreted_prefs.genre_boosts.keys())}, "
                f"penalties={list(interpreted_prefs.genre_penalties.keys())}"
            )

            # Apply content type exclusions from interpreted preferences
            if interpreted_prefs.content_type_exclusions:
                original_count = len(unconsumed_items)
                unconsumed_items = [
                    item
                    for item in unconsumed_items
                    if get_enum_value(item.content_type)
                    not in interpreted_prefs.content_type_exclusions
                ]
                if unconsumed_items:
                    logger.info(
                        f"Content type exclusions removed "
                        f"{original_count - len(unconsumed_items)} items"
                    )
                else:
                    logger.warning(
                        "Content type exclusion removed all candidates, "
                        "this shouldn't happen for same-type recommendations"
                    )

        # Analyze preferences from ALL consumed content types
        preferences = self.preference_analyzer.analyze(all_consumed_items)

        logger.info(
            f"Analyzed preferences from {len(all_consumed_items)} consumed items "
            f"across all content types to recommend {content_type.value}s"
        )

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
        content_length_preferences: dict[str, str] = {}
        if user_preference_config is not None:
            content_length_preferences = (
                user_preference_config.content_length_preferences
            )

        scoring_context = ScoringContext(
            preferences=preferences,
            consumed_items=all_consumed_items,
            series_tracking=series_tracking,
            content_type=content_type,
            all_unconsumed_items=unconsumed_items,
            similarity_scores=similarity_scores,
            content_length_preferences=content_length_preferences,
        )

        # Use a temporary pipeline with overridden weights if user prefs given
        if user_preference_config is not None and user_preference_config.scorer_weights:
            overridden_scorers = build_scorers_with_overrides(
                self.pipeline.scorers, user_preference_config.scorer_weights
            )
            active_pipeline = ScoringPipeline(overridden_scorers)
        else:
            active_pipeline = self.pipeline

        # Add CustomPreferenceScorer if we have interpreted custom rules
        if interpreted_prefs is not None and not interpreted_prefs.is_empty():
            custom_scorer = CustomPreferenceScorer(
                genre_boosts=interpreted_prefs.genre_boosts,
                genre_penalties=interpreted_prefs.genre_penalties,
            )
            # Create new pipeline with the custom scorer appended
            active_pipeline = ScoringPipeline(active_pipeline.scorers + [custom_scorer])

        pipeline_scored = active_pipeline.score_candidates_with_breakdown(
            unconsumed_items, scoring_context
        )

        # Take top count*3 from pipeline for further processing
        top_candidates: list[ScoredCandidate] = pipeline_scored[: count * 3]

        # -----------------------------------------------------------------
        # Filter candidates based on series rules (when enabled)
        # -----------------------------------------------------------------
        apply_series_rules = (
            user_preference_config is None or user_preference_config.series_in_order
        )

        if apply_series_rules:
            filtered_candidates: list[ScoredCandidate] = []
            for scored_candidate in top_candidates:
                if should_recommend_item(
                    scored_candidate.item,
                    series_tracking,
                    unconsumed_items=unconsumed_items,
                ):
                    filtered_candidates.append(scored_candidate)
                else:
                    logger.debug(
                        f"Filtered out {scored_candidate.item.title} - doesn't meet series recommendation rules"
                    )

            if not filtered_candidates:
                logger.warning(
                    "Series filtering removed all candidates, using original candidates"
                )
                filtered_candidates = top_candidates
        else:
            logger.info("Series ordering disabled by user preference")
            filtered_candidates = top_candidates

        # -----------------------------------------------------------------
        # Detect adaptations & find contributing reference items
        # -----------------------------------------------------------------
        candidate_metadata: list[dict[str, Any]] = []
        adaptations_map: dict[str, list[ContentItem]] = {}

        for scored_candidate in filtered_candidates:
            item = scored_candidate.item
            adaptations = self._find_direct_adaptations(item, all_consumed_items)
            contributing_items = self._find_contributing_reference_items(
                item, all_consumed_items
            )

            candidate_metadata.append(
                {
                    "item": item,
                    "similarity_score": scored_candidate.aggregate_score,
                    "adaptations": adaptations,
                    "contributing_items": contributing_items,
                    "score_breakdown": scored_candidate.score_breakdown,
                }
            )

            if item.id and adaptations:
                adaptations_map[item.id] = adaptations

        # -----------------------------------------------------------------
        # Rank (adaptation bonus, series bonus, preference adjustments)
        # -----------------------------------------------------------------
        # Build breakdown lookup for post-ranking output
        breakdown_by_id: dict[str | None, dict[str, float]] = {
            meta["item"].id: meta["score_breakdown"] for meta in candidate_metadata
        }

        # Apply per-user diversity weight if configured
        ranker = self.ranker
        if (
            user_preference_config is not None
            and user_preference_config.diversity_weight > 0
        ):
            ranker = RecommendationRanker(
                similarity_weight=self.ranker.similarity_weight,
                preference_weight=self.ranker.preference_weight,
                diversity_weight=user_preference_config.diversity_weight,
            )

        ranked_items = ranker.rank(
            candidates=[
                (meta["item"], meta["similarity_score"]) for meta in candidate_metadata
            ],
            preferences=preferences,
            content_type=content_type,
            adaptations_map=adaptations_map,
            recently_completed=consumed_items_of_type,
        )

        top_recommendations = ranked_items[:count]

        # -----------------------------------------------------------------
        # Format recommendations
        # -----------------------------------------------------------------
        recommendations: list[dict[str, Any]] = []
        for item, score, rank_metadata in top_recommendations:
            item_meta = next(
                (
                    candidate
                    for candidate in candidate_metadata
                    if candidate["item"].id == item.id
                ),
                None,
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
                "score_breakdown": breakdown_by_id.get(item.id, {}),
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
                                    "score_breakdown": {},
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
                            "score_breakdown": {},
                        }
                    )
                    if len(recommendations) >= count:
                        break

        return recommendations

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

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
            normalized = re.sub(r"^(the|a|an)\s+", "", normalized)
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

        t1_norm = re.sub(r"^(the|a|an)\s+", "", t1_norm)
        t2_norm = re.sub(r"^(the|a|an)\s+", "", t2_norm)

        return t1_norm in t2_norm or t2_norm in t1_norm

    def _find_contributing_reference_items(
        self,
        candidate: ContentItem,
        all_consumed_items: list[ContentItem],
    ) -> list[ContentItem]:
        """Find consumed items that share metadata with *candidate*.

        Uses genre and creator overlap to identify which consumed items
        are most related — no embeddings required.  Same-content-type
        references are preferred so that, e.g., TV show recommendations
        cite other TV shows the user enjoyed rather than a video game
        that happens to share genres.

        Args:
            candidate: The recommended item.
            all_consumed_items: All consumed items.

        Returns:
            Top 5 contributing consumed items (by overlap score).
        """
        candidate_genres = set(extract_genres(candidate))
        candidate_creator = extract_creator(candidate)
        candidate_type = get_enum_value(candidate.content_type)

        scored: list[tuple[ContentItem, float]] = []
        for consumed in all_consumed_items:
            overlap = 0.0
            consumed_genres = set(extract_genres(consumed))
            if candidate_genres and consumed_genres:
                intersection = candidate_genres & consumed_genres
                if intersection:
                    overlap += len(intersection) / len(
                        candidate_genres | consumed_genres
                    )

            consumed_creator = extract_creator(consumed)
            if (
                candidate_creator
                and consumed_creator
                and candidate_creator == consumed_creator
            ):
                overlap += 0.5

            # Slight preference for same content type so references feel
            # natural, but cross-type items still surface readily.
            if get_enum_value(consumed.content_type) == candidate_type:
                overlap += 0.1

            # Boost highly-rated items so they surface as references more
            # often, but don't exclude lower-rated or unrated items.
            if consumed.rating and consumed.rating >= 4:
                overlap += 0.15

            if overlap > 0:
                scored.append((consumed, overlap))

        scored.sort(key=lambda pair: pair[1], reverse=True)
        return [item for item, _ in scored[:5]]

    @staticmethod
    def _format_reference_label(item: ContentItem) -> str:
        """Format a reference item with its content type label.

        Args:
            item: Reference content item.

        Returns:
            Formatted string like "TV Show: Mythic Quest".
        """
        type_label = _CONTENT_TYPE_LABEL.get(get_enum_value(item.content_type), "Item")
        return f"{type_label}: {item.title}"

    def _generate_reasoning(
        self,
        item: ContentItem,
        preferences: UserPreferences,
        metadata: dict[str, Any],
        adaptations: list[ContentItem],
        contributing_items: list[ContentItem],
    ) -> str:
        """Generate reasoning for a recommendation.

        Always surfaces 1-5 specific items that contributed to the recommendation.
        Each reference includes its content type for clarity.
        For multiple items, uses a multi-line bullet format for readability.

        Args:
            item: Recommended item.
            preferences: User preferences (from all content types).
            metadata: Recommendation metadata.
            adaptations: List of direct adaptations found in consumed content.
            contributing_items: List of reference items that contributed.

        Returns:
            Reasoning string.
        """
        # Collect all items that influenced this recommendation
        # Prioritize adaptations, then contributing items
        influencing_items: list[ContentItem] = []

        if adaptations:
            influencing_items.extend(adaptations)

        if contributing_items:
            for contrib in contributing_items:
                if contrib not in influencing_items:
                    influencing_items.append(contrib)

        # Take up to 5 items
        influencing_items = influencing_items[:5]

        if influencing_items:
            if len(influencing_items) == 1:
                label = self._format_reference_label(influencing_items[0])
                return f"Recommended because you liked '{label}'"
            else:
                lines = ["Recommended because you liked:"]
                for ref in influencing_items:
                    label = self._format_reference_label(ref)
                    lines.append(f"  • {label}")
                return "\n".join(lines)

        # Fallback: try to mention a matching genre or author
        if item.author and preferences.get_author_score(item.author) > 0.5:
            return f"Recommended because you enjoy works by {item.author}"

        genre = None
        if item.metadata:
            genre = item.metadata.get("genre") or (
                item.metadata.get("genres", [])[0]
                if item.metadata.get("genres")
                else None
            )

        if genre and preferences.get_genre_score(genre) > 0.5:
            return f"Recommended because you enjoy {genre}"

        return "Recommended based on your preferences"

    def _strip_series_info(self, title: str) -> str:
        """Strip series information from a title for cleaner display.

        Removes trailing parenthetical series info like "(Shannara, #2)".

        Args:
            title: Original title string.

        Returns:
            Title without series info.
        """
        import re

        # Remove trailing parenthetical that looks like series info
        # Matches: (Series Name, #N) or (Series Name #N) or (Series, Book N)
        cleaned = re.sub(r"\s*\([^)]*#\d+[^)]*\)\s*$", "", title)
        cleaned = re.sub(r"\s*\([^)]*Book\s+\d+[^)]*\)\s*$", "", cleaned, flags=re.I)
        return cleaned.strip()
