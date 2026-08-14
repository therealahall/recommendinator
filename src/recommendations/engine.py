"""Main recommendation engine orchestrating all components."""

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
    """Keep at most one entry per non-null ``db_id``, preserving order.

    TV shows are expanded to season-level candidates that all share the parent
    show's ``db_id`` (the library tracks TV at show level).  When series
    ordering is disabled the engine skips series filtering, so several seasons
    of one show can co-occur in the ranked list — producing multiple cards that
    collide on the frontend, which keys cards and targets actions by ``db_id``.
    Collapsing keeps the first (highest-ranked) entry per show and lets other
    content backfill the freed slots.

    Entries whose ``db_id`` is ``None`` are each kept — a missing id is not a
    shared identity, so they are never collapsed together.

    Args:
        entries: Ordered entries (best-first) to deduplicate.
        db_id_of: Extracts the entry's ``db_id``.

    Returns:
        The entries with duplicate non-null ``db_id``s removed, order preserved.
    """
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


# Shuffle source for engines built without one.  Shared rather than per-engine
# so seeding it seeds every engine that did not bring its own.
_DEFAULT_RNG = random.Random()


@dataclass(frozen=True)
class _ConfiguredScoring:
    """Every configurable scoring knob, resolved from one read of the config.

    Attributes:
        pipeline: The pipeline at the configured global scorer weights.
        preference_analyzer: The analyser at the configured
            ``min_rating_for_preference``.
        custom_preference_weight: The configured weight for the custom-rule
            scorer, which is built per call from the user's rules.
    """

    pipeline: ScoringPipeline
    preference_analyzer: PreferenceAnalyzer
    custom_preference_weight: float


def _weights_in(section: dict[str, Any]) -> dict[str, float]:
    """Return *section*'s ``scorer_weights``, keyed by known scorer name.

    Names outside :data:`SCORER_NAME_MAP` are dropped: they weight no scorer,
    so a typo in a hand-edited ``config.yaml`` stays as inert here as it was
    when this resolution happened at boot.
    """
    weights = section.get("scorer_weights")
    if not isinstance(weights, dict):
        return {}
    return {
        name: float(weight)
        for name, weight in weights.items()
        if name in SCORER_NAME_MAP
    }


class RecommendationEngine:
    """Main recommendation engine.

    The scoring pipeline **always** runs and its weight-normalised aggregate
    *is* the score clients display: there is no second combination stage.

    When a *config_provider* is supplied, the ``recommendations`` knobs
    (:attr:`pipeline` weights, :attr:`preference_analyzer` and
    :attr:`custom_preference_weight`) resolve from the config it returns rather
    than freezing at construction, so a Settings-page change reaches the next
    ``generate_recommendations`` call without a restart.  The constructor
    arguments stay the baseline the config overlays, keeping the resolution
    order class default < ``config.yaml`` < global settings < per-user
    override.

    A provider rather than the dict itself, because scoring runs off the event
    loop (Starlette runs the synchronous handler in a threadpool worker) while
    config writes run on it.  Holding the dict would mean reading
    one a writer is part-way through rewriting; asking for it each time means
    the writer can only ever swap in a finished one.  One call asks once, at
    its start, and scores on that answer throughout: a save landing mid-request
    therefore reaches the next request whole rather than this one in part.
    """

    def __init__(
        self,
        storage_manager: StorageManager,
        min_rating: int = 4,
        scorers: list[Scorer] | None = None,
        custom_preference_weight: float = 1.0,
        rng: random.Random | None = None,
        config_provider: Callable[[], dict[str, Any] | None] | None = None,
    ) -> None:
        """Initialize recommendation engine.

        Args:
            storage_manager: Storage manager for accessing data.
            min_rating: Minimum rating to consider for preferences.
            scorers: Scorer instances for the pipeline.  Defaults to
                :data:`DEFAULT_SCORERS`.
            custom_preference_weight: Weight for the CustomPreferenceScorer,
                which is built per call from the user's custom rules.
            rng: Randomness used to shuffle equally relevant reference items.
                Pass a seeded instance to make that ordering reproducible.
            config_provider: Returns the **running** config, consulted on every
                read so that a settings change takes effect on the next call.
                Its ``recommendations`` section overlays ``min_rating`` and
                every scorer weight above. ``None`` freezes them as given.
        """
        self.storage = storage_manager
        self.rng = rng if rng is not None else _DEFAULT_RNG
        self._config_provider = config_provider
        self._base_min_rating = min_rating
        self._base_custom_preference_weight = custom_preference_weight
        self._base_scorers = list(scorers if scorers is not None else DEFAULT_SCORERS)

    @property
    def pipeline(self) -> ScoringPipeline:
        """The pipeline at the currently configured global scorer weights."""
        return self._configured_scoring().pipeline

    @property
    def preference_analyzer(self) -> PreferenceAnalyzer:
        """The analyser at the currently configured ``min_rating_for_preference``."""
        return self._configured_scoring().preference_analyzer

    @property
    def custom_preference_weight(self) -> float:
        """The currently configured weight for the custom-rule scorer."""
        return self._configured_scoring().custom_preference_weight

    def _configured_scoring(self) -> _ConfiguredScoring:
        """Resolve every configured scoring knob from a single config read.

        One read, because a request uses all three: reading again part-way
        through would let a save landing in between hand it one leaf from
        before the save and another from after.
        """
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
        """Return the running ``recommendations`` section, or an empty mapping.

        Type-guarded rather than defaulted: a ``recommendations:`` header with
        no children parses to ``None``, which ``dict.get``'s default never
        catches because the key is present.
        """
        config = self._config_provider() if self._config_provider is not None else None
        if config is None:
            return {}
        section = config.get("recommendations")
        return section if isinstance(section, dict) else {}

    def generate_recommendations(
        self,
        content_type: ContentType,
        count: int = 5,
        user_preference_config: UserPreferenceConfig | None = None,
    ) -> list[Recommendation]:
        """Generate recommendations for a content type.

        Uses preferences from ALL consumed content types to provide
        cross-content-type recommendations. For example, if you've read
        sci-fi books, it may recommend sci-fi TV shows or games.

        Args:
            content_type: Type of content to recommend.
            count: Number of recommendations to generate.
            user_preference_config: Optional per-user preference config.
                When provided, scorer weights are overridden for this call.

        Returns:
            The recommendations, best first.
        """
        # The configuration this request runs on, fixed here.  A settings save
        # landing while it scores changes what the next request resolves, and
        # nothing about this one.
        scoring = self._configured_scoring()

        # Taste-signal items (completed, rated, not ignored) shape every
        # recommendation: preference analysis, scoring, similarity seeds, and
        # explanation references (issue #99).
        all_consumed_items = self.storage.get_signal_items(content_type=None)

        # Full completed set of the requested type for series ordering.
        # Deliberately NOT the signal set: whether the user consumed an earlier
        # series entry is a consumption fact independent of rating or ignore
        # state, so an ignored/unrated earlier entry must still block a later
        # one. The signal subset below drives taste-shaped ranking instead.
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
            content_type=content_type, limit=None, include_ignored=False
        )

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

        # Score all unconsumed candidates via the pipeline (always runs)
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
            unconsumed_items,
            has_adaptations=bool(adaptations),
        )

        pipeline_scored = active_pipeline.score_candidates_with_breakdown(
            unconsumed_items, scoring_context
        )

        # Filter / substitute candidates based on series rules (when enabled)
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

        # Apply the stepped genre-fatigue variety penalty (issue #74) when the
        # user has set a non-zero variety_penalty.  This multiplicatively
        # demotes candidates whose genre cluster the user recently finished,
        # so the next entry in a just-completed genre/series no longer
        # automatically tops the list.  The ladder is built from what the user
        # consumed of the *same* content type, so finishing a fantasy book does
        # not suppress fantasy movies or games — each type varies
        # independently, and the ladder narrows that set to completion events
        # itself.  Its item set is neither of the ones fetched above: finishing
        # something causes fatigue whether or not the user rated it, but an
        # ignored item is a request for less of that, not evidence of having
        # had enough.  Fetched inside the branch because variety_penalty
        # defaults to zero and the scan would otherwise be thrown away.
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
        # runs before the slice so freed slots backfill with other content.  It
        # is a no-op when series ordering is on (one season per show already)
        # and for non-TV content (every library item has a unique db_id).
        ranked_items = _collapse_duplicate_db_ids(
            ranked_items, lambda entry: entry[0].db_id
        )

        top_recommendations = ranked_items[:count]

        # Format recommendations
        recommendations = self._format_recommendations(
            top_recommendations,
            breakdown_by_key,
            adaptations,
            signal_index,
            preferences,
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

    def _apply_series_filtering(
        self,
        pipeline_scored: list[ScoredCandidate],
        series_tracking: dict[str, set[float]],
        unconsumed_items: list[ContentItem],
    ) -> list[ScoredCandidate]:
        """Filter and substitute candidates based on series ordering rules.

        For each candidate that isn't the earliest recommendable entry in its
        series, attempts to substitute the earliest entry.  This ensures users
        are recommended Book #1 before Book #3, etc.

        Args:
            pipeline_scored: All pipeline-scored candidates, sorted by score.
            series_tracking: Series name to consumed item numbers.
            unconsumed_items: All unconsumed items for substitute search.

        Returns:
            Filtered and substituted candidate list.
        """
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
        """Map each candidate to the consumed items it adapts.

        Args:
            unconsumed_items: Every candidate the pipeline will score.
            signal_index: The indexed taste-signal set to match against.

        Returns:
            Candidate key -> adaptations, omitting candidates that adapt
            nothing.
        """
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
        unconsumed_items: list[ContentItem],
        *,
        has_adaptations: bool,
    ) -> ScoringPipeline:
        """Assemble the pipeline for one call.

        The custom-rule scorer joins the list *before* the per-user override
        pass, so every scorer's weight resolves through the same chain.  A
        scorer that would score every candidate 0.0 is then dropped: it cannot
        change the ranking and would only clutter the breakdown display.

        Args:
            scoring: The configuration this request resolved at its start.
            user_preference_config: Optional per-user preference config.
            interpreted_prefs: Interpreted custom rules, when the user has any.
            unconsumed_items: The candidates about to be scored.
            has_adaptations: Whether any candidate adapts consumed content.

        Returns:
            The pipeline to score this call's candidates with.
        """
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

        has_active = any(
            item.status == ConsumptionStatus.CURRENTLY_CONSUMING
            for item in unconsumed_items
        )
        inert: tuple[type[Scorer], ...] = tuple(
            scorer_class
            for scorer_class, has_signal in (
                (ContinuationScorer, has_active),
                (AdaptationScorer, has_adaptations),
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
        """Apply the stepped genre-fatigue penalty and re-sort (issue #74).

        Builds a cluster -> penalty ladder from the user's completions
        *of the content type being recommended*, then multiplies each
        candidate's score by ``1 - penalty`` where the penalty is the strongest
        among the candidate's recently finished genre clusters.  Scoping the
        ladder to a single content type keeps genres varying independently per
        type — finishing a fantasy book does not penalise fantasy movies or
        games.  The applied penalty is carried on each ranked candidate for
        display.

        The penalty is softened for an item that continues a series the user is
        actively progressing through (see
        :func:`is_active_series_continuation`): finishing book #1 does not mean
        the user is done with the genre, so the next book is nudged rather than
        buried beneath unrelated content.

        Args:
            ranked_items: Ranked candidates, best first.
            unignored_consumption_of_type: What the user has consumed or is
                consuming (not ignored, rated or not) of the recommended
                content type, used to build the ladder. Only completion events
                among them claim a rung.
            series_tracking: Series name -> consumed item numbers, used to
                detect active series continuations.
            unconsumed_items: Candidate items, used for series ordering checks.
            top_penalty: The ladder's top rung, from
                :func:`top_penalty_for_preference`.

        Returns:
            Re-sorted candidates carrying the penalty they took.  Returns the
            input unchanged when the ladder is empty.
        """
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
        """Format ranked candidates into recommendation records.

        References are looked up here, on the sliced list, rather than for
        every candidate that survived filtering: nothing but these records
        reads them, and the lookup builds a candidate profile and walks the
        index's posting lists, which on a season-expanded TV library is the
        dominant cost of the request.

        Args:
            ranked_items: The ranked candidates being emitted, best first.
            breakdown_by_key: Candidate key -> scorer config key -> raw score.
            adaptations: Pre-computed candidate key -> adaptations map.
            signal_index: The indexed taste-signal set to cite references from.
            preferences: User preferences for reasoning generation.

        Returns:
            The recommendations, in the order given.
        """
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

    def _build_fallback_recommendations(
        self,
        unconsumed_items: list[ContentItem],
        series_tracking: dict[str, set[float]],
        count: int,
    ) -> list[Recommendation]:
        """Build fallback recommendations when no scored recommendations exist.

        Returns unconsumed items that pass series ordering checks, collapsed to
        one entry per non-null ``db_id`` so the result matches the scored path
        (see :func:`_collapse_duplicate_db_ids`).  For TV, ``unconsumed_items``
        is the already-season-expanded list, so several season entries can share
        a parent show's ``db_id``; collapsing keeps each show to one card.

        Args:
            unconsumed_items: Available unconsumed items (season-expanded for TV).
            series_tracking: Series name to consumed item numbers.
            count: Maximum number to return.

        Returns:
            The fallback recommendations, at most ``count`` of them.
        """
        # Collect ALL qualifying candidates before collapsing — do not early-exit
        # at ``count``.  The season entries of one show share a db_id and collapse
        # to a single card, so stopping at ``count`` un-collapsed items could let
        # one show's seasons fill every slot, leaving nothing to backfill and
        # yielding fewer than ``count`` distinct shows after collapse.  Gathering
        # every candidate first guarantees the freed slots are backfilled.  This
        # is a correctness constraint, not an oversight: do not re-add a break.
        recommendations: list[Recommendation] = []
        for item in unconsumed_items:
            if should_recommend_item(
                item, series_tracking, unconsumed_items=unconsumed_items
            ):
                recommendations.append(
                    Recommendation(
                        item=item,
                        score=0.5,
                        reasoning="Available in your library",
                    )
                )
        # Collapse expanded TV seasons sharing a show's db_id before slicing, so
        # each show contributes at most one card and the freed slots backfill.
        recommendations = _collapse_duplicate_db_ids(
            recommendations, lambda rec: rec.item.db_id
        )
        return recommendations[:count]

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _generate_reasoning(
        self,
        item: ContentItem,
        preferences: UserPreferences,
        adaptations: list[ContentItem],
        contributing_items: list[ContentItem],
    ) -> str:
        """Generate reasoning for a recommendation.

        Groups references by content type.  For multiple types, each gets
        its own bullet line with comma-separated titles.

        Args:
            item: Recommended item.
            preferences: User preferences (from all content types).
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
            seen_db_ids = {
                item.db_id for item in influencing_items if item.db_id is not None
            }
            for contrib in contributing_items:
                if contrib.db_id not in seen_db_ids:
                    influencing_items.append(contrib)
                    if contrib.db_id is not None:
                        seen_db_ids.add(contrib.db_id)

        if influencing_items:
            # Group by content type
            grouped: dict[str, list[str]] = {}
            for ref in influencing_items:
                type_label = _CONTENT_TYPE_LABEL.get(
                    get_enum_value(ref.content_type), "Item"
                )
                label_key = type_label + "s"  # Pluralize for the header
                titles = grouped.setdefault(label_key, [])
                titles.append(strip_series_suffix_from_title(ref.title))

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
