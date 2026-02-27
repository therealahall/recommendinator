"""Main recommendation engine orchestrating all components."""

import logging
import random
import re
from typing import Any

from src.llm.embeddings import EmbeddingGenerator
from src.llm.recommendations import RecommendationGenerator
from src.models.content import ContentItem, ContentType, get_enum_value
from src.models.user_preferences import UserPreferenceConfig
from src.recommendations.genre_clusters import cluster_overlap
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
    extract_series_info,
    find_earliest_recommendable,
    inject_seasons_watched_tracking,
    should_recommend_item,
)
from src.utils.sorting import get_sort_title

logger = logging.getLogger(__name__)

# Human-readable labels for content types used in recommendation reasoning.
_CONTENT_TYPE_LABEL: dict[str, str] = {
    "book": "Book",
    "movie": "Movie",
    "tv_show": "TV Show",
    "video_game": "Video Game",
}

# Natural-language labels for single-item reasoning (e.g. "the book Dune").
_CONTENT_TYPE_NATURAL_LABEL: dict[str, str] = {
    "book": "the book",
    "movie": "the movie",
    "tv_show": "the TV show",
    "video_game": "the video game",
}

# Overlap scores within this tolerance are considered "close enough" to
# shuffle, so the reference item order varies across runs.
_SCORE_PROXIMITY_THRESHOLD = 0.05

# Default diversity weight applied when variety_after_completion is enabled
# but the user hasn't set an explicit diversity_weight.  0.2 gives a subtle
# genre-hopping nudge without overwhelming relevance scores.
_DEFAULT_VARIETY_DIVERSITY_WEIGHT = 0.2


def _shuffle_close_scores(
    items_with_scores: list[tuple[ContentItem, float]],
) -> list[ContentItem]:
    """Shuffle items that have similar overlap scores.

    Items are already sorted by descending score.  Adjacent items whose
    scores differ by at most ``_SCORE_PROXIMITY_THRESHOLD`` are grouped
    together and shuffled, so the ordering feels dynamic across runs
    while still respecting meaningful relevance differences.
    """
    if not items_with_scores:
        return []

    groups: list[list[ContentItem]] = [[items_with_scores[0][0]]]
    group_score = items_with_scores[0][1]

    for item, score in items_with_scores[1:]:
        if group_score - score <= _SCORE_PROXIMITY_THRESHOLD:
            groups[-1].append(item)
        else:
            groups.append([item])
            group_score = score

    result: list[ContentItem] = []
    for group in groups:
        random.shuffle(group)
        result.extend(group)
    return result


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
                "No consumed items found across any content type. "
                "Cannot generate recommendations for %s.",
                content_type.value,
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
            logger.warning("No unconsumed items found for %s", content_type.value)
            return []

        # Build series tracking (content-type specific) — before TV expansion
        # so that inject_seasons_watched_tracking can use the show-level items
        series_tracking = build_series_tracking(consumed_items_of_type)

        # Expand TV shows to season-level for granular recommendations
        # (library stays show-level; expansion is for scoring only)
        if content_type == ContentType.TV_SHOW:
            series_tracking = inject_seasons_watched_tracking(
                unconsumed_items, series_tracking
            )
            unconsumed_items = expand_tv_shows_to_seasons(unconsumed_items)
            logger.info(
                "Expanded TV shows to %d season-level candidates",
                len(unconsumed_items),
            )

        # Interpret custom rules (if present)
        interpreted_prefs: InterpretedPreference | None = None
        if user_preference_config is not None and user_preference_config.custom_rules:
            interpreter = PatternBasedInterpreter()
            interpreted_prefs = interpreter.interpret_all(
                user_preference_config.custom_rules
            )
            logger.info(
                "Interpreted %d custom rules: boosts=%s, penalties=%s",
                len(user_preference_config.custom_rules),
                list(interpreted_prefs.genre_boosts.keys()),
                list(interpreted_prefs.genre_penalties.keys()),
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
                        "Content type exclusions removed %d items",
                        original_count - len(unconsumed_items),
                    )
                else:
                    logger.warning(
                        "Content type exclusion removed all candidates, "
                        "this shouldn't happen for same-type recommendations"
                    )

        # Analyze preferences from ALL consumed content types
        preferences = self.preference_analyzer.analyze(all_consumed_items)

        logger.info(
            "Analyzed preferences from %d consumed items "
            "across all content types to recommend %ss",
            len(all_consumed_items),
            content_type.value,
        )

        # Pre-compute similarity scores (AI path)
        similarity_scores = self._compute_similarity_scores(
            all_consumed_items, content_type, count
        )

        # Score all unconsumed candidates via the pipeline (always runs)
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

        # Filter / substitute candidates based on series rules (when enabled)
        apply_series_rules = (
            user_preference_config is None or user_preference_config.series_in_order
        )

        if apply_series_rules:
            filtered_candidates = self._apply_series_filtering(
                top_candidates, pipeline_scored, series_tracking, unconsumed_items
            )
        else:
            logger.info("Series ordering disabled by user preference")
            filtered_candidates = top_candidates

        # Detect adaptations & find contributing reference items
        candidate_metadata, adaptations_map = self._build_candidate_metadata(
            filtered_candidates, all_consumed_items
        )

        # Rank (adaptation bonus, series bonus, preference adjustments)
        breakdown_by_id: dict[str | None, dict[str, float]] = {
            meta["item"].id: meta["score_breakdown"] for meta in candidate_metadata
        }

        ranker = self._configure_ranker(user_preference_config)
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

        # Format recommendations
        recommendations = self._format_recommendations(
            top_recommendations, candidate_metadata, breakdown_by_id, preferences
        )

        # Optionally enhance with LLM reasoning
        if use_llm:
            self._enhance_with_llm(
                recommendations,
                content_type,
                all_consumed_items,
                unconsumed_items,
                count,
                series_tracking,
            )

        # Final fallback
        if not recommendations and unconsumed_items:
            logger.info("Using fallback: returning unconsumed items as recommendations")
            recommendations = self._build_fallback_recommendations(
                unconsumed_items, series_tracking, count
            )

        return recommendations

    # ------------------------------------------------------------------
    # Extracted steps from generate_recommendations
    # ------------------------------------------------------------------

    def _compute_similarity_scores(
        self,
        all_consumed_items: list[ContentItem],
        content_type: ContentType,
        count: int,
    ) -> dict[str | None, float]:
        """Pre-compute vector-similarity scores for candidate items.

        Selects reference items from highly-rated and low-rated consumed
        content, then searches for similar unconsumed items via embeddings.

        Args:
            all_consumed_items: All consumed items across content types.
            content_type: Target content type for recommendations.
            count: Requested recommendation count (influences search breadth).

        Returns:
            Mapping of item ID to similarity score. Empty when AI is disabled.
        """
        if self.similarity_matcher is None:
            return {}

        rated_items = [item for item in all_consumed_items if item.rating is not None]
        rated_items.sort(key=lambda item: item.rating or 0, reverse=True)

        high_rated_refs = [
            item for item in rated_items if item.rating is not None and item.rating >= 4
        ][:5]
        low_rated_refs = [
            item for item in rated_items if item.rating is not None and item.rating < 3
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
            return {item.id: sim_score for item, sim_score in similar_candidates}
        return {}

    def _apply_series_filtering(
        self,
        top_candidates: list[ScoredCandidate],
        all_scored: list[ScoredCandidate],
        series_tracking: dict[str, set[int]],
        unconsumed_items: list[ContentItem],
    ) -> list[ScoredCandidate]:
        """Filter and substitute candidates based on series ordering rules.

        For each candidate that isn't the earliest recommendable entry in its
        series, attempts to substitute the earliest entry.  This ensures users
        are recommended Book #1 before Book #3, etc.

        Args:
            top_candidates: Top pipeline-scored candidates to filter.
            all_scored: All pipeline-scored candidates (for substitute lookup).
            series_tracking: Series name to consumed item numbers.
            unconsumed_items: All unconsumed items for substitute search.

        Returns:
            Filtered and substituted candidate list.
        """
        scored_by_id: dict[str | None, ScoredCandidate] = {
            scored.item.id: scored for scored in all_scored
        }
        substituted_series: set[str] = set()
        seen_ids: set[str | None] = set()

        filtered_candidates: list[ScoredCandidate] = []
        for scored_candidate in top_candidates:
            if should_recommend_item(
                scored_candidate.item,
                series_tracking,
                unconsumed_items=unconsumed_items,
            ):
                if scored_candidate.item.id not in seen_ids:
                    filtered_candidates.append(scored_candidate)
                    seen_ids.add(scored_candidate.item.id)
            else:
                series_info = extract_series_info(
                    scored_candidate.item.title,
                    scored_candidate.item.metadata,
                    scored_candidate.item.content_type,
                )
                if series_info:
                    candidate_series_name = series_info[0]
                    if candidate_series_name not in substituted_series:
                        substitute = find_earliest_recommendable(
                            candidate_series_name,
                            series_tracking,
                            unconsumed_items,
                        )
                        if substitute is not None and substitute.id not in seen_ids:
                            substitute_scored = scored_by_id.get(substitute.id)
                            if substitute_scored is not None:
                                filtered_candidates.append(substitute_scored)
                                seen_ids.add(substitute.id)
                                logger.debug(
                                    "Substituted %s with %s (earliest in %s)",
                                    scored_candidate.item.title,
                                    substitute.title,
                                    candidate_series_name,
                                )
                        substituted_series.add(candidate_series_name)
                else:
                    logger.debug(
                        "Filtered out %s - doesn't meet series recommendation rules",
                        scored_candidate.item.title,
                    )

        if not filtered_candidates:
            logger.warning(
                "Series filtering removed all candidates, using original candidates"
            )
            return top_candidates

        return filtered_candidates

    def _build_candidate_metadata(
        self,
        filtered_candidates: list[ScoredCandidate],
        all_consumed_items: list[ContentItem],
    ) -> tuple[list[dict[str, Any]], dict[str, list[ContentItem]]]:
        """Build metadata for each candidate including adaptations and references.

        Args:
            filtered_candidates: Scored candidates after series filtering.
            all_consumed_items: All consumed items for adaptation detection.

        Returns:
            Tuple of (candidate_metadata list, adaptations_map by item ID).
        """
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

        return candidate_metadata, adaptations_map

    def _configure_ranker(
        self,
        user_preference_config: UserPreferenceConfig | None,
    ) -> RecommendationRanker:
        """Configure a ranker with per-user diversity weight if applicable.

        Uses the user's explicit diversity_weight, or applies a sensible
        default when variety_after_completion is enabled.

        Args:
            user_preference_config: Optional per-user preference config.

        Returns:
            A RecommendationRanker instance (possibly with custom weights).
        """
        if user_preference_config is None:
            return self.ranker

        effective_diversity_weight = user_preference_config.diversity_weight
        if (
            user_preference_config.variety_after_completion
            and effective_diversity_weight == 0
        ):
            effective_diversity_weight = _DEFAULT_VARIETY_DIVERSITY_WEIGHT

        if effective_diversity_weight > 0:
            return RecommendationRanker(
                similarity_weight=self.ranker.similarity_weight,
                preference_weight=self.ranker.preference_weight,
                diversity_weight=effective_diversity_weight,
            )

        return self.ranker

    def _format_recommendations(
        self,
        ranked_items: list[tuple[ContentItem, float, dict[str, Any]]],
        candidate_metadata: list[dict[str, Any]],
        breakdown_by_id: dict[str | None, dict[str, float]],
        preferences: UserPreferences,
    ) -> list[dict[str, Any]]:
        """Format ranked items into recommendation dictionaries.

        Args:
            ranked_items: Ranked (item, score, rank_metadata) tuples.
            candidate_metadata: Per-candidate metadata from build step.
            breakdown_by_id: Score breakdown keyed by item ID.
            preferences: User preferences for reasoning generation.

        Returns:
            List of recommendation dictionaries.
        """
        candidate_metadata_by_id = {
            meta["item"].id: meta for meta in candidate_metadata
        }

        recommendations: list[dict[str, Any]] = []
        for item, score, rank_metadata in ranked_items:
            item_meta = candidate_metadata_by_id.get(item.id)

            adaptations_list: list[ContentItem] = []
            contributing_list: list[ContentItem] = []
            if item_meta:
                adaptations_list = item_meta.get("adaptations", [])
                contributing_list = item_meta.get("contributing_items", [])

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
                "contributing_items": contributing_list,
                "adaptations": adaptations_list,
            }
            recommendations.append(rec)

        return recommendations

    def _enhance_with_llm(
        self,
        recommendations: list[dict[str, Any]],
        content_type: ContentType,
        all_consumed_items: list[ContentItem],
        unconsumed_items: list[ContentItem],
        count: int,
        series_tracking: dict[str, set[int]],
    ) -> None:
        """Enhance recommendations with LLM-generated reasoning.

        When the pipeline has produced recommendations, the LLM adds natural
        language reasoning to each.  When the pipeline is empty, the LLM
        generates its own recommendations with series order enforcement.

        Modifies ``recommendations`` in place.

        Args:
            recommendations: Current recommendations to enhance (may be empty).
            content_type: Target content type.
            all_consumed_items: All consumed items for LLM context.
            unconsumed_items: Unconsumed items for LLM-only fallback.
            count: Requested recommendation count.
            series_tracking: Series name to consumed item numbers.
        """
        if not self.llm_generator:
            return

        try:
            if recommendations:
                per_item_refs = [
                    list(rec.get("contributing_items") or []) for rec in recommendations
                ]
                llm_recs = self.llm_generator.generate_blurbs(
                    content_type=content_type,
                    selected_items=[rec["item"] for rec in recommendations],
                    consumed_items=all_consumed_items,
                    per_item_references=per_item_refs,
                )
                # Build a lookup of title -> reasoning from LLM results.
                # The LLM returns items in its own preferred order, which
                # differs from the pipeline ranking — match by title, not
                # by index, so each recommendation gets its own reasoning.
                matched_count = sum(1 for r in llm_recs if r.get("item") is not None)
                logger.info(
                    "LLM blurbs: %d returned, %d matched items",
                    len(llm_recs),
                    matched_count,
                )

                llm_reasoning_by_title: dict[str, str] = {}
                for llm_rec in llm_recs:
                    matched_item: ContentItem | None = llm_rec.get("item")
                    if matched_item is not None:
                        key = matched_item.title.lower()
                    else:
                        key = (llm_rec.get("title") or "").lower()
                    if key:
                        llm_reasoning_by_title[key] = llm_rec.get("reasoning", "")

                enhanced_count = 0
                for rec in recommendations:
                    rec_title = rec["item"].title.lower()
                    if rec_title in llm_reasoning_by_title:
                        rec["llm_reasoning"] = llm_reasoning_by_title[rec_title]
                        enhanced_count += 1
                    else:
                        for llm_title, reasoning in llm_reasoning_by_title.items():
                            if llm_title in rec_title or rec_title in llm_title:
                                rec["llm_reasoning"] = reasoning
                                enhanced_count += 1
                                break

                if enhanced_count < len(recommendations):
                    unmatched = [
                        rec["item"].title
                        for rec in recommendations
                        if "llm_reasoning" not in rec
                    ]
                    logger.warning(
                        "LLM blurb matching: %d/%d recommendations got reasoning. "
                        "Unmatched: %s. LLM titles: %s",
                        enhanced_count,
                        len(recommendations),
                        unmatched,
                        list(llm_reasoning_by_title.keys()),
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
            logger.warning("LLM recommendation generation failed: %s", error)

    def _build_fallback_recommendations(
        self,
        unconsumed_items: list[ContentItem],
        series_tracking: dict[str, set[int]],
        count: int,
    ) -> list[dict[str, Any]]:
        """Build fallback recommendations when no scored recommendations exist.

        Returns unconsumed items that pass series ordering checks.

        Args:
            unconsumed_items: Available unconsumed items.
            series_tracking: Series name to consumed item numbers.
            count: Maximum number to return.

        Returns:
            List of fallback recommendation dictionaries.
        """
        recommendations: list[dict[str, Any]] = []
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

        item_title_norm = self._normalize_title_for_comparison(item.title)
        item_author_norm = (
            self._normalize_title_for_comparison(item.author) if item.author else None
        )

        for consumed in consumed_items:
            if consumed.content_type == item.content_type:
                continue

            consumed_title_norm = self._normalize_title_for_comparison(consumed.title)
            consumed_author_norm = (
                self._normalize_title_for_comparison(consumed.author)
                if consumed.author
                else None
            )

            title_match = item_title_norm == consumed_title_norm
            author_match = False
            if item_author_norm and consumed_author_norm:
                author_match = item_author_norm == consumed_author_norm

            if title_match or (
                author_match and self._titles_similar(item.title, consumed.title)
            ):
                if consumed.rating is not None and consumed.rating >= 4:
                    adaptations.append(consumed)

        return adaptations

    @staticmethod
    def _normalize_title_for_comparison(title: str) -> str:
        """Normalize a title for cross-content-type comparison.

        Delegates to get_sort_title which strips leading articles
        (including non-English), lowercases, and trims whitespace.
        """
        return get_sort_title(title)

    def _titles_similar(self, title1: str, title2: str) -> bool:
        """Check if two titles are similar (fuzzy matching).

        Uses get_sort_title to strip leading articles (including non-English)
        and normalize case, then checks substring containment.

        Args:
            title1: First title.
            title2: Second title.

        Returns:
            True if titles are similar.
        """
        if not title1 or not title2:
            return False

        t1_norm = get_sort_title(title1)
        t2_norm = get_sort_title(title2)

        return t1_norm in t2_norm or t2_norm in t1_norm

    # Minimum genre/creator overlap for a cross-type item to qualify as
    # a meaningful reference.  Prevents incidental single-genre matches
    # (e.g., "Drama" alone ≈ 0.2 Jaccard) from producing identical
    # cross-type lists across all recommendations.
    _CROSS_TYPE_MIN_OVERLAP = 0.25

    def _find_contributing_reference_items(
        self,
        candidate: ContentItem,
        all_consumed_items: list[ContentItem],
    ) -> list[ContentItem]:
        """Find consumed items that share metadata with *candidate*.

        Uses genre and creator overlap to identify which consumed items
        are most related — no embeddings required.  Returns up to 3 items
        of the same content type as the candidate and up to 3 from each
        other type that exceeds a minimum overlap threshold.  Cross-type
        items that don't genuinely relate to the candidate are omitted
        rather than padded.

        Items rated below 3 are excluded — they represent content the user
        disliked and should never appear as "you liked".  Unrated items
        (``rating is None``) are kept (benefit of the doubt).

        For same-type items, raw genre Jaccard is used (works well since
        the same vocabulary is shared).  For cross-type items, thematic
        cluster overlap is used instead, which is more discriminating than
        raw Jaccard on broad terms like "drama".

        Args:
            candidate: The recommended item.
            all_consumed_items: All consumed items.

        Returns:
            Contributing consumed items grouped by type: up to 3 same-type,
            up to 3 per other type (only those with meaningful overlap).
        """
        candidate_genres = list(extract_genres(candidate))
        candidate_genres_set = set(candidate_genres)
        candidate_creator = extract_creator(candidate)
        candidate_type = get_enum_value(candidate.content_type)

        scored: list[tuple[ContentItem, float]] = []
        for consumed in all_consumed_items:
            # Skip items the user actively disliked — they should never
            # appear as "recommended because you liked".  Unrated items
            # (rating is None) are kept.
            if consumed.rating is not None and consumed.rating < 3:
                continue
            overlap = 0.0
            consumed_genres = list(extract_genres(consumed))
            consumed_genres_set = set(consumed_genres)
            consumed_type = get_enum_value(consumed.content_type)
            is_same_type = consumed_type == candidate_type

            if candidate_genres_set and consumed_genres_set:
                if is_same_type:
                    # Same type: raw Jaccard (shared vocabulary)
                    intersection = candidate_genres_set & consumed_genres_set
                    if intersection:
                        overlap += len(intersection) / len(
                            candidate_genres_set | consumed_genres_set
                        )
                else:
                    # Cross type: thematic cluster overlap
                    overlap += cluster_overlap(candidate_genres, consumed_genres)

            consumed_creator = extract_creator(consumed)
            if (
                candidate_creator
                and consumed_creator
                and candidate_creator == consumed_creator
            ):
                overlap += 0.5

            # Boost highly-rated items so they surface as references more
            # often, but don't exclude lower-rated or unrated items.
            if consumed.rating and consumed.rating >= 4:
                overlap += 0.15

            if overlap > 0:
                scored.append((consumed, overlap))

        scored.sort(key=lambda pair: pair[1], reverse=True)

        # Group by content type: up to 3 for same type (any overlap),
        # up to 3 for others (only if meaningfully related).
        same_type_limit = 3
        other_type_limit = 3

        by_type: dict[str, list[tuple[ContentItem, float]]] = {}
        for item, score in scored:
            item_type = get_enum_value(item.content_type)
            is_same_type = item_type == candidate_type
            limit = same_type_limit if is_same_type else other_type_limit

            # Cross-type items must clear a minimum overlap threshold
            # to avoid the same broadly-matching items appearing for
            # every recommendation in a category.
            if not is_same_type and score < self._CROSS_TYPE_MIN_OVERLAP:
                continue

            type_list = by_type.setdefault(item_type, [])
            if len(type_list) < limit:
                type_list.append((item, score))

        # Return same type first, then others sorted by their best score.
        # Within each group, shuffle items that have similar overlap scores
        # so the order feels dynamic across runs.
        result: list[ContentItem] = _shuffle_close_scores(
            by_type.pop(candidate_type, [])
        )
        for content_type_items in by_type.values():
            result.extend(_shuffle_close_scores(content_type_items))
        return result

    def _generate_reasoning(
        self,
        item: ContentItem,
        preferences: UserPreferences,
        metadata: dict[str, Any],
        adaptations: list[ContentItem],
        contributing_items: list[ContentItem],
    ) -> str:
        """Generate reasoning for a recommendation.

        Groups references by content type.  For multiple types, each gets
        its own bullet line with comma-separated titles.

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

        if influencing_items:
            # Group by content type
            grouped: dict[str, list[str]] = {}
            for ref in influencing_items:
                type_label = _CONTENT_TYPE_LABEL.get(
                    get_enum_value(ref.content_type), "Item"
                )
                label_key = type_label + "s"  # Pluralize for the header
                titles = grouped.setdefault(label_key, [])
                titles.append(self._strip_series_info(ref.title))

            if len(grouped) == 1 and sum(len(v) for v in grouped.values()) == 1:
                # Single item — natural language format
                ref_item = influencing_items[0]
                ref_type_value = get_enum_value(ref_item.content_type)
                natural_label = _CONTENT_TYPE_NATURAL_LABEL.get(
                    ref_type_value, "the item"
                )
                title = next(iter(grouped.values()))[0]
                return f"Recommended because you liked {natural_label} {title}"

            # Candidate's own content type always listed first
            candidate_label = (
                _CONTENT_TYPE_LABEL.get(get_enum_value(item.content_type), "Item") + "s"
            )
            ordered_keys = []
            if candidate_label in grouped:
                ordered_keys.append(candidate_label)
            for key in grouped:
                if key != candidate_label:
                    ordered_keys.append(key)

            lines = ["Recommended because you liked the following:"]
            for type_label in ordered_keys:
                lines.append(f"  - {type_label}: {', '.join(grouped[type_label])}")
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
        # Remove trailing parenthetical that looks like series info
        # Matches: (Series Name, #N) or (Series Name #N) or (Series, Book N)
        cleaned = re.sub(r"\s*\([^)]*#\d+[^)]*\)\s*$", "", title)
        cleaned = re.sub(r"\s*\([^)]*Book\s+\d+[^)]*\)\s*$", "", cleaned, flags=re.I)
        return cleaned.strip()
