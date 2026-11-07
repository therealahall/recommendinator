from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from collections import defaultdict
from dataclasses import dataclass, field

from src.models.content import ContentItem, ContentType, get_enum_value
from src.recommendations.content_length import LengthPreference, score_length_match
from src.recommendations.genre_clusters import get_clusters_for_terms
from src.recommendations.genre_normalizer import extract_and_normalize_genres
from src.recommendations.identity import candidate_key
from src.recommendations.preferences import UserPreferences, split_creators
from src.utils.series import SeriesOrder

logger = logging.getLogger(__name__)


def extract_genres(item: ContentItem) -> list[str]:
    return extract_and_normalize_genres(item.metadata)


def extract_creators(item: ContentItem) -> frozenset[str]:
    if item.author:
        return split_creators(item.author)
    if item.metadata:
        for key in ("director", "developer", "studio", "creator"):
            value = item.metadata.get(key)
            if value:
                return split_creators(str(value))
    return frozenset()


#: Added to a consumed item's term count, so richer items reach higher and a
#: one-tag item can't give every candidate sharing that tag full marks.
_TAG_SET_PADDING = 2

#: What a consumed term earns when the candidate holds a different term from
#: one of its clusters rather than the term itself.
_RELATED_TERM_WEIGHT = 0.5


def _rating_lift(rating: int) -> float:
    return 0.5 + 0.5 * (rating - 1) / 4


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
    #: Required: an empty one silently hides every series with no ordinal.
    series_order: SeriesOrder

    # Pre-computed lookups (populated by __post_init__)
    tag_set_divisors: list[int] = field(default_factory=list)
    tag_set_lifts: list[float] = field(default_factory=list)
    tag_sets_by_term: dict[str, list[int]] = field(default_factory=dict)
    tag_terms_by_cluster: dict[str, list[tuple[int, str]]] = field(default_factory=dict)
    consumed_creators: set[str] = field(default_factory=set)
    #: Seeded by the caller with ratings that are no taste signal, a show's
    #: between seasons; each rated consumed item's is added to its series.
    series_ratings: dict[str, list[int]] = field(default_factory=dict)

    #: Series positions the user is part-way through, keyed by series. Supplied
    #: rather than derived: what is in progress is never itself a candidate.
    in_progress_positions: dict[str, set[float]] = field(default_factory=dict)

    # Cross-media adaptations the user rated well, keyed by candidate key
    adaptations: dict[str, list[ContentItem]] = field(default_factory=dict)

    # User content-length preferences (e.g. {"book": "short", "movie": "any"})
    content_length_preferences: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        series_ratings: dict[str, list[int]] = defaultdict(list)
        for seeded, ratings in self.series_ratings.items():
            series_ratings[seeded].extend(ratings)
        best_lifts: dict[frozenset[str], float] = {}
        tag_sets_by_term: dict[str, list[int]] = defaultdict(list)
        tag_terms_by_cluster: dict[str, list[tuple[int, str]]] = defaultdict(list)
        creators: set[str] = set()

        for item in self.consumed_items:
            item_genres = extract_genres(item)
            if item.rating is not None:
                terms = frozenset(item_genres)
                lift = _rating_lift(item.rating)
                best_lifts[terms] = max(best_lifts.get(terms, lift), lift)

            creators.update(extract_creators(item))

            series = self.series_order.series_of(item)
            if series is not None and item.rating is not None:
                series_ratings[series].append(item.rating)

        for ordinal, terms in enumerate(best_lifts):
            for term in terms:
                tag_sets_by_term[term].append(ordinal)
                for cluster in get_clusters_for_terms([term]):
                    tag_terms_by_cluster[cluster].append((ordinal, term))

        self.tag_set_divisors = [len(terms) + _TAG_SET_PADDING for terms in best_lifts]
        self.tag_set_lifts = list(best_lifts.values())
        self.tag_sets_by_term = dict(tag_sets_by_term)
        self.tag_terms_by_cluster = dict(tag_terms_by_cluster)
        self.consumed_creators = creators
        self.series_ratings = dict(series_ratings)


class Scorer(ABC):
    def __init__(self, weight: float = 1.0) -> None:
        self.weight = weight

    def clone(self, weight: float) -> Scorer:
        return type(self)(weight=weight)

    def applies(self, candidate: ContentItem, context: ScoringContext) -> bool:
        """False only where the scorer structurally cannot fire or has nothing
        configured to measure, never where it returns a neutral 0.5 for want of
        evidence: an excluded scorer leaves the divisor."""
        return True

    @abstractmethod
    def score(self, candidate: ContentItem, context: ScoringContext) -> float:
        """Return a score in ``[0.0, 1.0]`` for *candidate*."""
        ...


class GenreMatchScorer(Scorer):
    def __init__(self, weight: float = 2.0) -> None:
        super().__init__(weight)

    def score(self, candidate: ContentItem, context: ScoringContext) -> float:
        # Weighted rather than a plain mean: drama on most of the library would
        # otherwise drag every candidate carrying it to neutral, and max() let a
        # loved term hide a disliked one.
        tastes = [
            taste
            for genre in extract_genres(candidate)
            if (taste := context.preferences.genre_tastes.get(genre)) is not None
        ]
        weight = sum(taste.weight for taste in tastes)
        if weight == 0.0:
            return 0.5
        mean = sum(taste.score * taste.weight for taste in tastes) / weight
        return (mean + 1.0) / 2.0


class CreatorMatchScorer(Scorer):
    def __init__(self, weight: float = 1.5) -> None:
        super().__init__(weight)

    def score(self, candidate: ContentItem, context: ScoringContext) -> float:
        return max(
            (
                self._creator_score(creator, context)
                for creator in extract_creators(candidate)
            ),
            default=0.5,
        )

    @staticmethod
    def _creator_score(creator: str, context: ScoringContext) -> float:
        author_score = context.preferences.get_author_score(creator)
        if author_score != 0.0:
            return (author_score + 1.0) / 2.0

        if creator in context.consumed_creators:
            return 0.7  # mild positive – user has consumed this creator before
        return 0.5


class TagOverlapScorer(Scorer):
    def __init__(self, weight: float = 1.0) -> None:
        super().__init__(weight)

    def score(self, candidate: ContentItem, context: ScoringContext) -> float:
        # Divided by the consumed item's terms, not the candidate's: tags the
        # candidate adds beyond the match cost it nothing here. Keyed by consumed
        # term so a term sharing several clusters with the candidate counts once.
        genres = extract_genres(candidate)
        exact = {
            (ordinal, term)
            for term in genres
            for ordinal in context.tag_sets_by_term.get(term, ())
        }
        related = {
            hit
            for cluster in get_clusters_for_terms(genres)
            for hit in context.tag_terms_by_cluster.get(cluster, ())
        }
        hits: dict[int, float] = defaultdict(float)
        for ordinal, _ in exact:
            hits[ordinal] += 1.0
        for ordinal, _ in related - exact:
            hits[ordinal] += _RELATED_TERM_WEIGHT
        return max(
            (
                count
                / context.tag_set_divisors[ordinal]
                * context.tag_set_lifts[ordinal]
                for ordinal, count in hits.items()
            ),
            default=0.0,
        )


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
    """Nudge what continues a series already begun, by how well its consumed
    entries were rated. Silent while an entry of the series is in flight, so
    season 6 cannot jump the queue mid season 5.
    """

    def __init__(self, weight: float = 1.0) -> None:
        super().__init__(weight)

    def applies(self, candidate: ContentItem, context: ScoringContext) -> bool:
        located = context.series_order.locate(candidate)
        if located is None:
            return False
        series, position = located
        if context.in_progress_positions.get(series):
            return False
        return any(
            earlier < position for earlier in context.series_tracking.get(series, ())
        )

    def score(self, candidate: ContentItem, context: ScoringContext) -> float:
        series = context.series_order.series_of(candidate)
        average = _average_series_rating(
            context.series_ratings.get(series, []) if series else []
        )
        if average is None:
            return 0.5
        return (average - 1.0) / 4.0


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

    def applies(self, candidate: ContentItem, context: ScoringContext) -> bool:
        preference = context.content_length_preferences.get(
            get_enum_value(candidate.content_type), LengthPreference.ANY
        )
        return preference != LengthPreference.ANY

    def score(self, candidate: ContentItem, context: ScoringContext) -> float:
        return score_length_match(candidate, context.content_length_preferences)


DEFAULT_SCORERS: list[Scorer] = [
    GenreMatchScorer(),
    CreatorMatchScorer(),
    TagOverlapScorer(),
    ContentLengthScorer(),
    ContinuationScorer(),
    AdaptationScorer(),
]


SCORER_NAME_MAP: dict[str, type[Scorer]] = {
    "genre_match": GenreMatchScorer,
    "creator_match": CreatorMatchScorer,
    "tag_overlap": TagOverlapScorer,
    "custom_preference": CustomPreferenceScorer,
    "content_length": ContentLengthScorer,
    "continuation": ContinuationScorer,
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
