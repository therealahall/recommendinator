import logging
import random
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TypeVar

from src.models.content import (
    ConsumptionStatus,
    ContentItem,
    ContentType,
    get_enum_value,
)
from src.models.user_preferences import UserPreferenceConfig
from src.recommendations.identity import candidate_key
from src.recommendations.preference_interpreter import (
    InterpretedPreference,
    PatternBasedInterpreter,
)
from src.recommendations.preferences import PreferenceAnalyzer, UserPreferences
from src.recommendations.record import Recommendation
from src.recommendations.reference_index import SignalIndex
from src.recommendations.scorers import (
    DEFAULT_SCORERS,
    SCORER_NAME_MAP,
    AdaptationScorer,
    ContinuationScorer,
    CustomPreferenceScorer,
    Scorer,
    ScoringContext,
    build_scorers_with_overrides,
)
from src.recommendations.scoring_pipeline import ScoredCandidate, ScoringPipeline
from src.recommendations.variety import (
    PenaltyFraction,
    build_variety_ladder,
    top_penalty_for_preference,
    variety_penalty_for,
)
from src.storage.manager import StorageManager
from src.utils.series import (
    build_series_tracking,
    expand_tv_shows_to_seasons,
    extract_series_info,
    find_earliest_recommendable,
    inject_seasons_watched_tracking,
    is_active_series_continuation,
    should_recommend_item,
    strip_series_suffix_from_title,
)

logger = logging.getLogger(__name__)

_T = TypeVar("_T")

#: A candidate after ranking: the item, its score, and the variety penalty
#: fraction applied to it (0.0 when the penalty is off or did not bite).
_RankedCandidate = tuple[ContentItem, float, float]


def _collapse_duplicate_db_ids(
    entries: list[_T], db_id_of: Callable[[_T], int | None]
) -> list[_T]:
    seen: set[int] = set()
    collapsed: list[_T] = []
    for entry in entries:
        db_id = db_id_of(entry)
        if db_id is not None:
            if db_id in seen:
                continue
            seen.add(db_id)
        collapsed.append(entry)
    return collapsed


_CONTENT_TYPE_LABEL: dict[str, str] = {
    "book": "Book",
    "movie": "Movie",
    "tv_show": "TV Show",
    "video_game": "Video Game",
}

_CONTENT_TYPE_NATURAL_LABEL: dict[str, str] = {
    "book": "the book",
    "movie": "the movie",
    "tv_show": "the TV show",
    "video_game": "the video game",
}


# Shuffle source for engines built without one.  Shared rather than per-engine
# so seeding it seeds every engine that did not bring its own.
_DEFAULT_RNG = random.Random()


@dataclass(frozen=True)
class _ConfiguredScoring:
    """Every configurable scoring knob, resolved from one read of the config."""

    pipeline: ScoringPipeline
    preference_analyzer: PreferenceAnalyzer
    custom_preference_weight: float


@dataclass(frozen=True)
class _SignalFlags:
    """Dropping a scorer shrinks the weight every score divides by, so a merged
    run resolves these once for all four types."""

    has_active: bool
    has_adaptations: bool


def _signals_in(
    unconsumed_items: list[ContentItem],
    adaptations: dict[str, list[ContentItem]],
) -> _SignalFlags:
    return _SignalFlags(
        has_active=any(
            item.status == ConsumptionStatus.CURRENTLY_CONSUMING
            for item in unconsumed_items
        ),
        has_adaptations=bool(adaptations),
    )


def _weights_in(section: dict[str, Any]) -> dict[str, float]:
    weights = section.get("scorer_weights")
    if not isinstance(weights, dict):
        return {}
    return {
        name: float(weight)
        for name, weight in weights.items()
        if name in SCORER_NAME_MAP
    }


class RecommendationEngine:
    def __init__(
        self,
        storage_manager: StorageManager,
        min_rating: int = 4,
        scorers: list[Scorer] | None = None,
        custom_preference_weight: float = 1.0,
        rng: random.Random | None = None,
        config_provider: Callable[[], dict[str, Any] | None] | None = None,
    ) -> None:
        self.storage = storage_manager
        self.rng = rng if rng is not None else _DEFAULT_RNG
        self._config_provider = config_provider
        self._base_min_rating = min_rating
        self._base_custom_preference_weight = custom_preference_weight
        self._base_scorers = list(scorers if scorers is not None else DEFAULT_SCORERS)

    @property
    def pipeline(self) -> ScoringPipeline:
        return self._configured_scoring().pipeline

    @property
    def preference_analyzer(self) -> PreferenceAnalyzer:
        return self._configured_scoring().preference_analyzer

    @property
    def custom_preference_weight(self) -> float:
        return self._configured_scoring().custom_preference_weight

    def _configured_scoring(self) -> _ConfiguredScoring:
        section = self._recommendations_config()
        weights = _weights_in(section)
        return _ConfiguredScoring(
            pipeline=ScoringPipeline(
                build_scorers_with_overrides(self._base_scorers, weights)
            ),
            preference_analyzer=PreferenceAnalyzer(
                min_rating=section.get(
                    "min_rating_for_preference", self._base_min_rating
                )
            ),
            custom_preference_weight=weights.get(
                "custom_preference", self._base_custom_preference_weight
            ),
        )

    def _recommendations_config(self) -> dict[str, Any]:
        config = self._config_provider() if self._config_provider is not None else None
        if config is None:
            return {}
        section = config.get("recommendations")
        return section if isinstance(section, dict) else {}

    def generate_recommendations(
        self,
        content_type: ContentType | None = None,
        count: int = 5,
        user_preference_config: UserPreferenceConfig | None = None,
    ) -> list[Recommendation]:
        if content_type is None:
            return self._generate_across_types(count, user_preference_config)
        return self._rank_one_type(
            content_type,
            count,
            user_preference_config,
            self._configured_scoring(),
            None,
        )

    def _rank_one_type(
        self,
        content_type: ContentType,
        count: int,
        user_preference_config: UserPreferenceConfig | None,
        scoring: _ConfiguredScoring,
        signals: _SignalFlags | None,
    ) -> list[Recommendation]:
        # Taste-signal items (completed, rated, not ignored) shape preference
        # analysis, scoring, similarity seeds and references alike (issue #99).
        all_consumed_items = self.storage.get_signal_items(content_type=None)

        # Deliberately NOT the signal set: an ignored or unrated earlier entry
        # is still consumed, and must still block a later one.
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

        # Unlimited: series detection breaks when an earlier entry sorts after a
        # later one, as "The Black Unicorn #2" does before "Magic Kingdom... #1".
        unconsumed_items = self.storage.get_unconsumed_items(
            content_type=content_type, limit=None, include_ignored=False
        )

        if not unconsumed_items:
            logger.warning("No unconsumed items found for %s", content_type.value)
            return []

        # Before TV expansion: inject_seasons_watched_tracking needs show-level items.
        series_tracking = build_series_tracking(consumed_items_of_type)

        # The library stays show-level; season expansion is for scoring only.
        if content_type == ContentType.TV_SHOW:
            series_tracking = inject_seasons_watched_tracking(
                unconsumed_items, series_tracking
            )
            unconsumed_items = expand_tv_shows_to_seasons(unconsumed_items)
            logger.info(
                "Expanded TV shows to %d season-level candidates",
                len(unconsumed_items),
            )

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

        preferences = scoring.preference_analyzer.analyze(all_consumed_items)

        logger.info(
            "Analyzed preferences from %d consumed items "
            "across all content types to recommend %ss",
            len(all_consumed_items),
            content_type.value,
        )

        # One index over the signal set answers both the adaptation lookup
        # below and the reference lookup after filtering, so neither re-derives
        # a consumed item's title, genres, creator or series per candidate.
        signal_index = SignalIndex(all_consumed_items)

        # Detect cross-media adaptations before scoring: AdaptationScorer reads
        # them from the context, and the formatted output cites them as reasons.
        adaptations = self._build_adaptations(unconsumed_items, signal_index)

        content_length_preferences = (
            user_preference_config.content_length_preferences
            if user_preference_config is not None
            else {}
        )

        scoring_context = ScoringContext(
            preferences=preferences,
            consumed_items=all_consumed_items,
            series_tracking=series_tracking,
            content_type=content_type,
            all_unconsumed_items=unconsumed_items,
            content_length_preferences=content_length_preferences,
            adaptations=adaptations,
        )

        active_pipeline = self._build_active_pipeline(
            scoring,
            user_preference_config,
            interpreted_prefs,
            (
                signals
                if signals is not None
                else _signals_in(unconsumed_items, adaptations)
            ),
        )

        pipeline_scored = active_pipeline.score_candidates_with_breakdown(
            unconsumed_items, scoring_context
        )

        apply_series_rules = (
            user_preference_config is None or user_preference_config.series_in_order
        )

        if apply_series_rules:
            filtered_candidates = self._apply_series_filtering(
                pipeline_scored, series_tracking, unconsumed_items
            )
        else:
            logger.info("Series ordering disabled by user preference")
            filtered_candidates = pipeline_scored

        # Ranking drops down to (item, score, penalty), so the rows behind each
        # score are kept here for the formatting step to look up.
        breakdown_by_key = {
            candidate_key(scored.item): scored.score_breakdown
            for scored in filtered_candidates
        }

        # The pipeline aggregate is the score, and the pipeline already sorted
        # on it, so ranking is the filtered order with no penalty applied yet.
        ranked_items: list[_RankedCandidate] = [
            (scored.item, scored.aggregate_score, 0.0) for scored in filtered_candidates
        ]

        if (
            user_preference_config is not None
            and user_preference_config.variety_penalty > 0.0
        ):
            unignored_consumption_of_type = self.storage.get_consumption_items(
                content_type=content_type
            )
            ranked_items = self._apply_variety_penalty(
                ranked_items,
                unignored_consumption_of_type,
                series_tracking,
                unconsumed_items,
                top_penalty=top_penalty_for_preference(
                    user_preference_config.variety_penalty
                ),
            )

        # Collapse season-level candidates that share a parent show's db_id
        # down to their single highest-ranked representative (issue #44).  This
        # runs before the slice so freed slots backfill with other content.
        ranked_items = _collapse_duplicate_db_ids(
            ranked_items, lambda entry: entry[0].db_id
        )

        return self._format_recommendations(
            ranked_items[:count],
            breakdown_by_key,
            adaptations,
            signal_index,
            preferences,
        )

    def _generate_across_types(
        self,
        count: int,
        user_preference_config: UserPreferenceConfig | None,
    ) -> list[Recommendation]:
        # Once, above the loop: a settings save landing mid-run would otherwise
        # score books on the old weights and games on the new ones.
        scoring = self._configured_scoring()
        signals = self._signals_across_types()

        # One type-scoped run each, because season expansion and series tracking
        # only mean anything inside a type. No type can hold more than `count` of
        # the merged top `count`, so its own top `count` is all that can matter.
        candidates = [
            recommendation
            for content_type in ContentType
            for recommendation in self._rank_one_type(
                content_type, count, user_preference_config, scoring, signals
            )
        ]
        candidates.sort(key=lambda recommendation: recommendation.score, reverse=True)
        return candidates[:count]

    def _signals_across_types(self) -> _SignalFlags:
        unconsumed_items = self.storage.get_unconsumed_items(
            content_type=None, limit=None, include_ignored=False
        )
        signal_index = SignalIndex(self.storage.get_signal_items(content_type=None))
        return _signals_in(
            unconsumed_items, self._build_adaptations(unconsumed_items, signal_index)
        )

    def _apply_series_filtering(
        self,
        pipeline_scored: list[ScoredCandidate],
        series_tracking: dict[str, set[float]],
        unconsumed_items: list[ContentItem],
    ) -> list[ScoredCandidate]:
        scored_by_key: dict[str, ScoredCandidate] = {
            candidate_key(scored.item): scored for scored in pipeline_scored
        }
        substituted_series: set[str] = set()
        seen_keys: set[str] = set()

        filtered_candidates: list[ScoredCandidate] = []
        for scored_candidate in pipeline_scored:
            if should_recommend_item(
                scored_candidate.item,
                series_tracking,
                unconsumed_items=unconsumed_items,
            ):
                key = candidate_key(scored_candidate.item)
                if key not in seen_keys:
                    filtered_candidates.append(scored_candidate)
                    seen_keys.add(key)
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
                        if substitute is not None:
                            substitute_key = candidate_key(substitute)
                            substitute_scored = scored_by_key.get(substitute_key)
                            if (
                                substitute_key not in seen_keys
                                and substitute_scored is not None
                            ):
                                filtered_candidates.append(substitute_scored)
                                seen_keys.add(substitute_key)
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
            return pipeline_scored

        return filtered_candidates

    @staticmethod
    def _build_adaptations(
        unconsumed_items: list[ContentItem],
        signal_index: SignalIndex,
    ) -> dict[str, list[ContentItem]]:
        adaptations: dict[str, list[ContentItem]] = {}
        for item in unconsumed_items:
            found = signal_index.adaptations_of(item)
            if found:
                adaptations[candidate_key(item)] = found
        return adaptations

    def _build_active_pipeline(
        self,
        scoring: _ConfiguredScoring,
        user_preference_config: UserPreferenceConfig | None,
        interpreted_prefs: InterpretedPreference | None,
        signals: _SignalFlags,
    ) -> ScoringPipeline:
        scorers = list(scoring.pipeline.scorers)

        if interpreted_prefs is not None and not interpreted_prefs.is_empty():
            scorers.append(
                CustomPreferenceScorer(
                    genre_boosts=interpreted_prefs.genre_boosts,
                    genre_penalties=interpreted_prefs.genre_penalties,
                    weight=scoring.custom_preference_weight,
                )
            )

        if user_preference_config is not None and user_preference_config.scorer_weights:
            scorers = build_scorers_with_overrides(
                scorers, user_preference_config.scorer_weights
            )

        inert: tuple[type[Scorer], ...] = tuple(
            scorer_class
            for scorer_class, has_signal in (
                (ContinuationScorer, signals.has_active),
                (AdaptationScorer, signals.has_adaptations),
            )
            if not has_signal
        )

        return ScoringPipeline(
            [scorer for scorer in scorers if not isinstance(scorer, inert)]
        )

    @staticmethod
    def _apply_variety_penalty(
        ranked_items: list[_RankedCandidate],
        unignored_consumption_of_type: list[ContentItem],
        series_tracking: dict[str, set[float]],
        unconsumed_items: list[ContentItem],
        *,
        top_penalty: PenaltyFraction,
    ) -> list[_RankedCandidate]:
        ladder = build_variety_ladder(
            unignored_consumption_of_type, top_penalty=top_penalty
        )
        if not ladder:
            return ranked_items

        penalised: list[_RankedCandidate] = []
        for item, score, _ in ranked_items:
            is_continuation = is_active_series_continuation(
                item, series_tracking, unconsumed_items
            )
            penalty = variety_penalty_for(
                item, ladder, is_series_continuation=is_continuation
            )
            penalised.append((item, score * (1.0 - penalty), penalty))

        penalised.sort(key=lambda entry: entry[1], reverse=True)
        return penalised

    def _format_recommendations(
        self,
        ranked_items: list[_RankedCandidate],
        breakdown_by_key: dict[str, dict[str, float]],
        adaptations: dict[str, list[ContentItem]],
        signal_index: SignalIndex,
        preferences: UserPreferences,
    ) -> list[Recommendation]:
        recommendations: list[Recommendation] = []
        for item, score, variety_penalty in ranked_items:
            key = candidate_key(item)
            item_adaptations = adaptations.get(key, [])
            contributing_items = signal_index.references_for(item, self.rng)

            recommendations.append(
                Recommendation(
                    item=item,
                    score=score,
                    reasoning=self._generate_reasoning(
                        item,
                        preferences,
                        item_adaptations,
                        contributing_items,
                    ),
                    score_breakdown=breakdown_by_key[key],
                    variety_penalty=variety_penalty,
                    contributing_items=contributing_items,
                    adaptations=item_adaptations,
                )
            )

        return recommendations

    def _generate_reasoning(
        self,
        item: ContentItem,
        preferences: UserPreferences,
        adaptations: list[ContentItem],
        contributing_items: list[ContentItem],
    ) -> str:
        influencing_items: list[ContentItem] = []

        if adaptations:
            influencing_items.extend(adaptations)

        if contributing_items:
            seen_db_ids = {
                item.db_id for item in influencing_items if item.db_id is not None
            }
            for contrib in contributing_items:
                if contrib.db_id not in seen_db_ids:
                    influencing_items.append(contrib)
                    if contrib.db_id is not None:
                        seen_db_ids.add(contrib.db_id)

        if influencing_items:
            grouped: dict[str, list[str]] = {}
            for ref in influencing_items:
                type_label = _CONTENT_TYPE_LABEL.get(
                    get_enum_value(ref.content_type), "Item"
                )
                label_key = type_label + "s"
                titles = grouped.setdefault(label_key, [])
                titles.append(strip_series_suffix_from_title(ref.title))

            if len(grouped) == 1 and sum(len(v) for v in grouped.values()) == 1:
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
