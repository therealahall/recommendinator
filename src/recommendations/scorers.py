from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from collections import defaultdict
from dataclasses import dataclass, field

from src.models.content import ConsumptionStatus, ContentItem, ContentType
from src.recommendations.content_length import score_length_match
from src.recommendations.genre_clusters import get_clusters_for_terms
from src.recommendations.genre_normalizer import extract_and_normalize_genres
from src.recommendations.identity import candidate_key
from src.recommendations.preferences import UserPreferences
from src.utils.series import (
    build_series_tracking,
    extract_series_info,
    is_next_after_consumed,
)

logger = logging.getLogger(__name__)


def extract_genres(item: ContentItem) -> list[str]:
    return extract_and_normalize_genres(item.metadata)


def extract_creator(item: ContentItem) -> str | None:
    if item.author:
        return item.author.lower()
    if item.metadata:
        for key in ("director", "developer", "studio", "creator"):
            value = item.metadata.get(key)
            if value:
                return str(value).lower()
    return None


def _average_series_rating(series_ratings: list[int]) -> float | None:
    if not series_ratings:
        return None
    return sum(series_ratings) / len(series_ratings)


@dataclass
class ScoringContext:
    preferences: UserPreferences
    consumed_items: list[ContentItem]
    series_tracking: dict[str, set[float]]
    content_type: ContentType
    all_unconsumed_items: list[ContentItem]

    # Pre-computed lookups (populated by __post_init__)
    consumed_genres: set[str] = field(default_factory=set)
    consumed_clusters: set[str] = field(default_factory=set)
    consumed_creators: set[str] = field(default_factory=set)
    ratings_by_genre: dict[str, list[int]] = field(default_factory=dict)
    series_ratings: dict[str, list[int]] = field(default_factory=dict)
    unconsumed_series_positions: dict[str, set[float]] = field(default_factory=dict)

    # Cross-media adaptations the user rated well, keyed by candidate key
    adaptations: dict[str, list[ContentItem]] = field(default_factory=dict)

    # User content-length preferences (e.g. {"book": "short", "movie": "any"})
    content_length_preferences: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        genre_ratings: dict[str, list[int]] = defaultdict(list)
        series_ratings: dict[str, list[int]] = defaultdict(list)
        creators: set[str] = set()
        genres: set[str] = set()

        for item in self.consumed_items:
            item_genres = extract_genres(item)
            genres.update(item_genres)
            if item.rating is not None:
                for genre in item_genres:
                    genre_ratings[genre].append(item.rating)

            creator = extract_creator(item)
            if creator:
                creators.add(creator)

            series_info = extract_series_info(
                item.title, item.metadata, item.content_type
            )
            if series_info and item.rating is not None:
                series_name, _ = series_info
                series_ratings[series_name].append(item.rating)

        self.consumed_genres = genres
        self.consumed_clusters = get_clusters_for_terms(list(genres))
        self.consumed_creators = creators
        self.ratings_by_genre = dict(genre_ratings)
        self.series_ratings = dict(series_ratings)
        self.unconsumed_series_positions = build_series_tracking(
            self.all_unconsumed_items
        )


class Scorer(ABC):
    def __init__(self, weight: float = 1.0) -> None:
        self.weight = weight

    def clone(self, weight: float) -> Scorer:
        return type(self)(weight=weight)

    def applies(self, candidate: ContentItem, context: ScoringContext) -> bool:
        """False only where the scorer structurally cannot fire, never where it
        returns a neutral 0.5: an excluded scorer leaves the divisor."""
        return True

    @abstractmethod
    def score(self, candidate: ContentItem, context: ScoringContext) -> float:
        """Return a score in ``[0.0, 1.0]`` for *candidate*."""
        ...


class GenreMatchScorer(Scorer):
    def __init__(self, weight: float = 2.0) -> None:
        super().__init__(weight)

    def score(self, candidate: ContentItem, context: ScoringContext) -> float:
        candidate_genres = extract_genres(candidate)
        if not candidate_genres:
            return 0.5  # neutral when no genre info

        genre_scores = [
            context.preferences.get_genre_score(genre) for genre in candidate_genres
        ]
        # Use the best matching genre (genre_scores is non-empty since candidate_genres is)
        best = max(genre_scores)
        # Map [-1, 1] -> [0, 1]
        return (best + 1.0) / 2.0


class CreatorMatchScorer(Scorer):
    def __init__(self, weight: float = 1.5) -> None:
        super().__init__(weight)

    def score(self, candidate: ContentItem, context: ScoringContext) -> float:
        creator = extract_creator(candidate)
        if not creator:
            return 0.5  # neutral

        author_score = context.preferences.get_author_score(creator)
        if author_score != 0.0:
            return (author_score + 1.0) / 2.0

        if creator in context.consumed_creators:
            return 0.7  # mild positive – user has consumed this creator before
        return 0.5


class TagOverlapScorer(Scorer):
    def __init__(self, weight: float = 1.0) -> None:
        super().__init__(weight)

    @staticmethod
    def _threshold_score(matches: int) -> float:
        if matches >= 5:
            return 1.0
        if matches == 4:
            return 0.9
        if matches == 3:
            return 0.8
        if matches == 2:
            return 0.5
        if matches == 1:
            return 0.3
        return 0.0

    def score(self, candidate: ContentItem, context: ScoringContext) -> float:
        candidate_genres = set(extract_genres(candidate))
        if not candidate_genres or not context.consumed_genres:
            return 0.0

        direct_matches = len(candidate_genres & context.consumed_genres)
        direct_score = self._threshold_score(direct_matches)

        candidate_clusters = get_clusters_for_terms(list(candidate_genres))
        if candidate_clusters and context.consumed_clusters:
            cluster_matches = len(candidate_clusters & context.consumed_clusters)
            cluster_score = self._threshold_score(cluster_matches)
        else:
            cluster_score = 0.0

        return max(direct_score, cluster_score)


class SeriesOrderScorer(Scorer):
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
            if item_number == 1:
                return 0.8  # first item in unstarted series
            return 0.3  # later item with nothing consumed

        if is_next_after_consumed(
            item_number,
            consumed_numbers,
            context.unconsumed_series_positions.get(series_name, set()),
        ):
            return self._rating_boosted_score(series_name, context)
        if item_number > max(consumed_numbers):
            return 0.3  # too far ahead
        return 0.2  # already consumed, or an entry the user has moved past

    def _rating_boosted_score(self, series_name: str, context: ScoringContext) -> float:
        avg_rating = _average_series_rating(context.series_ratings.get(series_name, []))

        if avg_rating is None:
            return 0.85

        if avg_rating >= 4.0:
            return 1.0
        elif avg_rating >= 3.0:
            return 0.85 + (avg_rating - 3.0) * 0.15
        elif avg_rating >= 2.0:
            return 0.7 + (avg_rating - 2.0) * 0.15
        else:
            return 0.6 + (avg_rating - 1.0) * 0.1


class RatingPatternScorer(Scorer):
    def __init__(self, weight: float = 1.0) -> None:
        super().__init__(weight)

    def score(self, candidate: ContentItem, context: ScoringContext) -> float:
        candidate_genres = extract_genres(candidate)
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


class CustomPreferenceScorer(Scorer):
    def __init__(
        self,
        genre_boosts: dict[str, float] | None = None,
        genre_penalties: dict[str, float] | None = None,
        weight: float = 1.0,
    ) -> None:
        super().__init__(weight)
        self.genre_boosts = genre_boosts or {}
        self.genre_penalties = genre_penalties or {}

    def clone(self, weight: float) -> CustomPreferenceScorer:
        return CustomPreferenceScorer(
            genre_boosts=dict(self.genre_boosts),
            genre_penalties=dict(self.genre_penalties),
            weight=weight,
        )

    def score(self, candidate: ContentItem, context: ScoringContext) -> float:
        candidate_genres = extract_genres(candidate)
        if not candidate_genres:
            return 0.5  # Neutral when no genre info

        # Check for penalties first (avoid rules)
        for genre in candidate_genres:
            genre_lower = genre.lower()
            if genre_lower in self.genre_penalties:
                penalty_factor = self.genre_penalties[genre_lower]
                return max(0.0, 0.5 - (penalty_factor * 0.5))

        max_boost = 0.0
        for genre in candidate_genres:
            genre_lower = genre.lower()
            if genre_lower in self.genre_boosts:
                max_boost = max(max_boost, self.genre_boosts[genre_lower])

        if max_boost > 0:
            return min(1.0, 0.5 + (max_boost * 0.5))

        return 0.5  # Neutral when no rules match


class ContinuationScorer(Scorer):
    def __init__(self, weight: float = 2.0) -> None:
        super().__init__(weight)

    def applies(self, candidate: ContentItem, context: ScoringContext) -> bool:
        return candidate.status == ConsumptionStatus.CURRENTLY_CONSUMING

    def score(self, candidate: ContentItem, context: ScoringContext) -> float:
        return 1.0


class SeriesAffinityScorer(Scorer):
    def __init__(self, weight: float = 1.0) -> None:
        super().__init__(weight)

    def score(self, candidate: ContentItem, context: ScoringContext) -> float:
        series_info = extract_series_info(
            candidate.title, candidate.metadata, candidate.content_type
        )
        if series_info is None:
            return 0.5  # not in a series – neutral

        series_name, _ = series_info
        avg_rating = _average_series_rating(context.series_ratings.get(series_name, []))
        if avg_rating is None:
            return 0.5  # no consumed entries in this series – neutral

        if avg_rating >= 4.0:
            return 1.0
        return 0.5


class AdaptationScorer(Scorer):
    """Boost a candidate that adapts consumed content the user rated well."""

    def __init__(self, weight: float = 1.5) -> None:
        super().__init__(weight)

    def applies(self, candidate: ContentItem, context: ScoringContext) -> bool:
        return bool(context.adaptations.get(candidate_key(candidate)))

    def score(self, candidate: ContentItem, context: ScoringContext) -> float:
        # A gated signal has to score the top of the scale: the aggregate is a
        # mean over the scorers that apply, so less would demote what it boosts.
        return 1.0


class ContentLengthScorer(Scorer):
    def __init__(self, weight: float = 1.0) -> None:
        super().__init__(weight)

    def score(self, candidate: ContentItem, context: ScoringContext) -> float:
        if not context.content_length_preferences:
            return 0.5  # No preferences set — neutral
        return score_length_match(candidate, context.content_length_preferences)


DEFAULT_SCORERS: list[Scorer] = [
    GenreMatchScorer(),
    CreatorMatchScorer(),
    TagOverlapScorer(),
    SeriesOrderScorer(),
    RatingPatternScorer(),
    ContentLengthScorer(),
    ContinuationScorer(),
    SeriesAffinityScorer(),
    AdaptationScorer(),
]


SCORER_NAME_MAP: dict[str, type[Scorer]] = {
    "genre_match": GenreMatchScorer,
    "creator_match": CreatorMatchScorer,
    "tag_overlap": TagOverlapScorer,
    "series_order": SeriesOrderScorer,
    "rating_pattern": RatingPatternScorer,
    "custom_preference": CustomPreferenceScorer,
    "content_length": ContentLengthScorer,
    "continuation": ContinuationScorer,
    "series_affinity": SeriesAffinityScorer,
    "adaptation": AdaptationScorer,
}


def build_scorers_with_overrides(
    base_scorers: list[Scorer],
    scorer_weight_overrides: dict[str, float],
) -> list[Scorer]:
    class_to_name: dict[type[Scorer], str] = {
        scorer_class: name for name, scorer_class in SCORER_NAME_MAP.items()
    }

    overridden: list[Scorer] = []
    for scorer in base_scorers:
        config_key = class_to_name.get(type(scorer))
        if config_key and config_key in scorer_weight_overrides:
            overridden.append(scorer.clone(weight=scorer_weight_overrides[config_key]))
        else:
            overridden.append(scorer)
    return overridden
