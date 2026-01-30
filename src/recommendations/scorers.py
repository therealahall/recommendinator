"""Scoring components for the recommendation pipeline.

Each scorer evaluates candidates on a specific dimension (genre match,
creator match, semantic similarity, etc.) and returns a 0.0-1.0 score.
A ScoringContext pre-computes lookup structures so scorers can work
efficiently.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from collections import defaultdict
from dataclasses import dataclass, field

from src.models.content import ContentItem, ContentType
from src.recommendations.preferences import UserPreferences
from src.utils.series import extract_series_info

logger = logging.getLogger(__name__)


def _extract_genres(item: ContentItem) -> list[str]:
    """Extract all genre strings from an item's metadata.

    Handles both single ``genre`` field and ``genres`` list.

    Args:
        item: Content item to extract genres from.

    Returns:
        List of lowercased genre strings.
    """
    genres: list[str] = []
    if not item.metadata:
        return genres
    if "genre" in item.metadata and item.metadata["genre"]:
        genres.append(str(item.metadata["genre"]).lower())
    if "genres" in item.metadata and item.metadata["genres"]:
        raw = item.metadata["genres"]
        if isinstance(raw, list):
            genres.extend(g.lower() for g in raw if g)
        elif isinstance(raw, str):
            genres.extend(g.strip().lower() for g in raw.split(",") if g.strip())
    return genres


def _extract_creator(item: ContentItem) -> str | None:
    """Return the primary creator for *item* (lowercased).

    Falls back to metadata keys ``director``, ``developer``, ``studio``.

    Args:
        item: Content item.

    Returns:
        Lowercased creator name or ``None``.
    """
    if item.author:
        return item.author.lower()
    if item.metadata:
        for key in ("director", "developer", "studio", "creator"):
            value = item.metadata.get(key)
            if value:
                return str(value).lower()
    return None


@dataclass
class ScoringContext:
    """Pre-computed lookup structures shared by all scorers.

    Attributes:
        preferences: Analysed user preferences.
        consumed_items: All consumed items (across content types).
        series_tracking: Series name -> consumed item numbers.
        content_type: The content type being recommended.
        all_unconsumed_items: Unconsumed items of the target content type.
        consumed_genres: Set of all genres from consumed items.
        consumed_creators: Set of all creators from consumed items.
        ratings_by_genre: Genre -> list of ratings from consumed items.
    """

    preferences: UserPreferences
    consumed_items: list[ContentItem]
    series_tracking: dict[str, set[int]]
    content_type: ContentType
    all_unconsumed_items: list[ContentItem]

    # Pre-computed lookups (populated by __post_init__)
    consumed_genres: set[str] = field(default_factory=set)
    consumed_creators: set[str] = field(default_factory=set)
    ratings_by_genre: dict[str, list[int]] = field(default_factory=dict)

    # Pre-computed similarity scores (populated by engine when AI enabled)
    similarity_scores: dict[str | None, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Build lookup structures from consumed items."""
        genre_ratings: dict[str, list[int]] = defaultdict(list)
        creators: set[str] = set()
        genres: set[str] = set()

        for item in self.consumed_items:
            item_genres = _extract_genres(item)
            genres.update(item_genres)
            if item.rating is not None:
                for genre in item_genres:
                    genre_ratings[genre].append(item.rating)

            creator = _extract_creator(item)
            if creator:
                creators.add(creator)

        self.consumed_genres = genres
        self.consumed_creators = creators
        self.ratings_by_genre = dict(genre_ratings)


# ---------------------------------------------------------------------------
# Abstract base scorer
# ---------------------------------------------------------------------------


class Scorer(ABC):
    """Base class for all scorers.

    Attributes:
        weight: Relative importance of this scorer in the pipeline.
    """

    def __init__(self, weight: float = 1.0) -> None:
        self.weight = weight

    @abstractmethod
    def score(self, candidate: ContentItem, context: ScoringContext) -> float:
        """Return a score in ``[0.0, 1.0]`` for *candidate*.

        Args:
            candidate: The item being evaluated.
            context: Shared scoring context.

        Returns:
            A float between 0.0 and 1.0 (inclusive).
        """
        ...


# ---------------------------------------------------------------------------
# Concrete scorers
# ---------------------------------------------------------------------------


class GenreMatchScorer(Scorer):
    """Score based on genre preference.

    Maps the preference score (which is in [-1, 1]) into [0, 1].
    Weight default: 2.0
    """

    def __init__(self, weight: float = 2.0) -> None:
        super().__init__(weight)

    def score(self, candidate: ContentItem, context: ScoringContext) -> float:
        candidate_genres = _extract_genres(candidate)
        if not candidate_genres:
            return 0.5  # neutral when no genre info

        genre_scores = [
            context.preferences.get_genre_score(genre) for genre in candidate_genres
        ]
        # Use the best matching genre
        best = max(genre_scores) if genre_scores else 0.0
        # Map [-1, 1] -> [0, 1]
        return (best + 1.0) / 2.0


class CreatorMatchScorer(Scorer):
    """Score based on whether the candidate's creator is preferred.

    Unifies author / director / developer matching.
    Weight default: 1.5
    """

    def __init__(self, weight: float = 1.5) -> None:
        super().__init__(weight)

    def score(self, candidate: ContentItem, context: ScoringContext) -> float:
        creator = _extract_creator(candidate)
        if not creator:
            return 0.5  # neutral

        # Check via preferences (author scores cover the main path)
        author_score = context.preferences.get_author_score(creator)
        if author_score != 0.0:
            return (author_score + 1.0) / 2.0

        # Fallback: is the creator in the consumed set at all?
        if creator in context.consumed_creators:
            return 0.7  # mild positive – user has consumed this creator before
        return 0.5


class TagOverlapScorer(Scorer):
    """Jaccard-like overlap of candidate genres/tags against consumed genres.

    Weight default: 1.0
    """

    def __init__(self, weight: float = 1.0) -> None:
        super().__init__(weight)

    def score(self, candidate: ContentItem, context: ScoringContext) -> float:
        candidate_genres = set(_extract_genres(candidate))
        if not candidate_genres or not context.consumed_genres:
            return 0.0

        intersection = candidate_genres & context.consumed_genres
        union = candidate_genres | context.consumed_genres
        if not union:
            return 0.0
        return len(intersection) / len(union)


class SeriesOrderScorer(Scorer):
    """Score based on series ordering.

    - 1.0 if candidate is the next item in a started series.
    - 0.8 if candidate is the first item in an unstarted series.
    - 0.3 if candidate is too far ahead in a started series.
    - 0.5 for non-series items (neutral).

    Weight default: 1.5
    """

    def __init__(self, weight: float = 1.5) -> None:
        super().__init__(weight)

    def score(self, candidate: ContentItem, context: ScoringContext) -> float:
        series_info = extract_series_info(
            candidate.title, candidate.metadata, candidate.content_type
        )
        if series_info is None:
            return 0.5  # not in a series – neutral

        series_name, item_number = series_info
        consumed_numbers = context.series_tracking.get(series_name, set())

        if not consumed_numbers:
            # Unstarted series
            if item_number == 1:
                return 0.8  # first item in unstarted series
            return 0.3  # later item with nothing consumed

        max_consumed = max(consumed_numbers)
        if item_number == max_consumed + 1:
            return 1.0  # next in sequence
        if item_number > max_consumed + 1:
            return 0.3  # too far ahead
        # Item is at or before max_consumed (already consumed or earlier)
        return 0.2


class RatingPatternScorer(Scorer):
    """Score based on average rating of consumed items sharing genres.

    If the user rates items in a matching genre highly on average, the
    candidate gets a higher score.

    Weight default: 1.0
    """

    def __init__(self, weight: float = 1.0) -> None:
        super().__init__(weight)

    def score(self, candidate: ContentItem, context: ScoringContext) -> float:
        candidate_genres = _extract_genres(candidate)
        if not candidate_genres or not context.ratings_by_genre:
            return 0.5  # neutral

        matching_ratings: list[int] = []
        for genre in candidate_genres:
            matching_ratings.extend(context.ratings_by_genre.get(genre, []))

        if not matching_ratings:
            return 0.5

        average = sum(matching_ratings) / len(matching_ratings)
        # Map 1-5 rating scale to 0.0-1.0
        return (average - 1.0) / 4.0


class SemanticSimilarityScorer(Scorer):
    """Score based on pre-computed embedding similarity.

    This scorer looks up a candidate's similarity score from a dict that
    the engine populates before running the pipeline.  It is only added to
    the pipeline when AI features are enabled.

    Weight default: 1.5
    """

    def __init__(self, weight: float = 1.5) -> None:
        super().__init__(weight)

    def score(self, candidate: ContentItem, context: ScoringContext) -> float:
        if not context.similarity_scores:
            return 0.0
        return context.similarity_scores.get(candidate.id, 0.0)


class CustomPreferenceScorer(Scorer):
    """Score based on user-defined custom preference rules.

    Applies genre boosts and penalties from interpreted natural language
    rules. The interpreter output is passed at construction time.

    Weight default: 2.0 (strong influence since these are explicit user prefs)
    """

    def __init__(
        self,
        genre_boosts: dict[str, float] | None = None,
        genre_penalties: dict[str, float] | None = None,
        weight: float = 2.0,
    ) -> None:
        """Initialize the custom preference scorer.

        Args:
            genre_boosts: Mapping of genre name to boost factor (0.0-1.0).
            genre_penalties: Mapping of genre name to penalty factor (0.0-1.0).
            weight: Scorer weight in the pipeline.
        """
        super().__init__(weight)
        self.genre_boosts = genre_boosts or {}
        self.genre_penalties = genre_penalties or {}

    def score(self, candidate: ContentItem, context: ScoringContext) -> float:
        """Score candidate based on custom preference rules.

        Returns 0.5 (neutral) if no custom rules apply. Boosts push toward 1.0,
        penalties push toward 0.0.

        Args:
            candidate: The item being evaluated.
            context: Shared scoring context.

        Returns:
            Score between 0.0 and 1.0.
        """
        candidate_genres = _extract_genres(candidate)
        if not candidate_genres:
            return 0.5  # Neutral when no genre info

        # Check for penalties first (avoid rules)
        for genre in candidate_genres:
            genre_lower = genre.lower()
            if genre_lower in self.genre_penalties:
                penalty_factor = self.genre_penalties[genre_lower]
                # Map penalty factor to score: 1.0 penalty -> 0.0 score
                return max(0.0, 0.5 - (penalty_factor * 0.5))

        # Check for boosts (prefer rules)
        max_boost = 0.0
        for genre in candidate_genres:
            genre_lower = genre.lower()
            if genre_lower in self.genre_boosts:
                max_boost = max(max_boost, self.genre_boosts[genre_lower])

        if max_boost > 0:
            # Map boost factor to score: 1.0 boost -> 1.0 score
            return min(1.0, 0.5 + (max_boost * 0.5))

        return 0.5  # Neutral when no rules match


# ---------------------------------------------------------------------------
# Default scorer set
# ---------------------------------------------------------------------------

DEFAULT_SCORERS: list[Scorer] = [
    GenreMatchScorer(),
    CreatorMatchScorer(),
    TagOverlapScorer(),
    SeriesOrderScorer(),
    RatingPatternScorer(),
]


# ---------------------------------------------------------------------------
# Scorer name map and user-override helpers
# ---------------------------------------------------------------------------

SCORER_NAME_MAP: dict[str, type[Scorer]] = {
    "genre_match": GenreMatchScorer,
    "creator_match": CreatorMatchScorer,
    "tag_overlap": TagOverlapScorer,
    "series_order": SeriesOrderScorer,
    "rating_pattern": RatingPatternScorer,
    "semantic_similarity": SemanticSimilarityScorer,
    "custom_preference": CustomPreferenceScorer,
}


def build_scorers_with_overrides(
    base_scorers: list[Scorer],
    scorer_weight_overrides: dict[str, float],
) -> list[Scorer]:
    """Create a new scorer list with per-user weight overrides applied.

    Clones each scorer, applying the overridden weight when a matching key
    is present in *scorer_weight_overrides*. Scorers without an override
    retain their original weight. The original scorers are not mutated.

    Args:
        base_scorers: List of scorer instances (from the engine pipeline).
        scorer_weight_overrides: Sparse dict of scorer config key -> weight.

    Returns:
        New list of scorer instances with overridden weights.
    """
    # Build reverse map: scorer class -> config key
    class_to_name: dict[type[Scorer], str] = {
        scorer_class: name for name, scorer_class in SCORER_NAME_MAP.items()
    }

    overridden: list[Scorer] = []
    for scorer in base_scorers:
        config_key = class_to_name.get(type(scorer))
        if config_key and config_key in scorer_weight_overrides:
            overridden.append(type(scorer)(weight=scorer_weight_overrides[config_key]))
        else:
            # Clone with same weight (new instance, doesn't mutate original)
            overridden.append(type(scorer)(weight=scorer.weight))
    return overridden
