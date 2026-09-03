"""Matching a candidate against the taste-signal set by lookup."""

from __future__ import annotations

import random
from collections.abc import Sequence
from dataclasses import dataclass
from itertools import islice

from src.models.content import ConsumptionStatus, ContentItem, get_enum_value
from src.recommendations.constants import (
    CROSS_TYPE_MIN_OVERLAP,
    SCORE_PROXIMITY_THRESHOLD,
)
from src.recommendations.genre_clusters import (
    cluster_similarity,
    get_clusters_for_terms,
)
from src.recommendations.scorers import extract_creator, extract_genres
from src.utils.series import get_series_name, get_series_name_from_metadata
from src.utils.sorting import get_sort_title, titles_similar

#: References cited per content type: the candidate's own type, and each other.
_REFERENCES_PER_TYPE = 3

#: Rating at or above which a consumed item counts as one the user liked. It is
#: the floor for being cited as an adaptation, and earns a reference boost.
_LIKED_RATING = 4

#: Rating below which a consumed item is never cited as a reference — it is
#: content the user disliked, so "because you liked" would be a lie.
_DISLIKED_RATING = 3

#: Overlap a highly rated consumed item earns for its rating alone, so good
#: ratings surface as references more often without excluding the rest.
_LIKED_RATING_OVERLAP = 0.15

_SHARED_CREATOR_OVERLAP = 0.5


def _shuffle_close_scores(
    items_with_scores: list[tuple[ContentItem, float]],
    rng: random.Random,
) -> list[ContentItem]:
    """Items are already sorted by descending score."""
    if not items_with_scores:
        return []

    groups: list[list[ContentItem]] = [[items_with_scores[0][0]]]
    group_score = items_with_scores[0][1]

    for item, score in items_with_scores[1:]:
        if group_score - score <= SCORE_PROXIMITY_THRESHOLD:
            groups[-1].append(item)
        else:
            groups.append([item])
            group_score = score

    result: list[ContentItem] = []
    for group in groups:
        rng.shuffle(group)
        result.extend(group)
    return result


@dataclass(frozen=True)
class _SignalRecord:
    """One taste-signal item with every value matching needs, derived once."""

    item: ContentItem
    ordinal: int
    rating: int | None
    sort_title: str
    sort_author: str | None
    genre_set: frozenset[str]
    clusters: frozenset[str]
    creator: str | None
    series_keys: frozenset[str]

    @property
    def liked(self) -> bool:
        return self.rating is not None and self.rating >= _LIKED_RATING


@dataclass(frozen=True)
class _CandidateProfile:
    content_type: str
    genre_set: frozenset[str]
    clusters: frozenset[str]
    creator: str | None
    series_key: str | None


class _TypeBucket:
    """The citable signal items of one content type, indexed by what finds them."""

    def __init__(self) -> None:
        self.by_genre: dict[str, list[_SignalRecord]] = {}
        self.by_cluster: dict[str, list[_SignalRecord]] = {}
        self.by_creator: dict[str, list[_SignalRecord]] = {}
        self.liked: list[_SignalRecord] = []

    def add(self, record: _SignalRecord) -> None:
        """Index *record*, which must be citable and of this bucket's type."""
        for genre in record.genre_set:
            self.by_genre.setdefault(genre, []).append(record)
        for cluster in record.clusters:
            self.by_cluster.setdefault(cluster, []).append(record)
        if record.creator:
            self.by_creator.setdefault(record.creator, []).append(record)
        if record.liked:
            self.liked.append(record)


def _record_for(item: ContentItem, ordinal: int) -> _SignalRecord:
    genres = extract_genres(item)

    series_name = get_series_name(item)
    if series_name is not None:
        series_keys = {series_name.lower()}
    else:
        # A show-level entry carries no season marker, so its own title and its
        # metadata series name are what tie it to a candidate's series.
        series_keys = {item.title.strip().lower()}
        metadata_series = get_series_name_from_metadata(item.metadata)
        if metadata_series is not None:
            series_keys.add(metadata_series.lower())

    return _SignalRecord(
        item=item,
        ordinal=ordinal,
        rating=item.rating,
        sort_title=get_sort_title(item.title),
        sort_author=get_sort_title(item.author) if item.author else None,
        genre_set=frozenset(genres),
        clusters=frozenset(get_clusters_for_terms(genres)),
        creator=extract_creator(item),
        series_keys=frozenset(series_keys),
    )


def _profile_of(candidate: ContentItem) -> _CandidateProfile:
    genres = extract_genres(candidate)
    series_name = get_series_name(candidate)
    return _CandidateProfile(
        content_type=get_enum_value(candidate.content_type),
        genre_set=frozenset(genres),
        clusters=frozenset(get_clusters_for_terms(genres)),
        creator=extract_creator(candidate),
        series_key=series_name.lower() if series_name is not None else None,
    )


def _pair_overlap(
    profile: _CandidateProfile, record: _SignalRecord, *, same_type: bool
) -> float:
    overlap = 0.0

    if profile.genre_set and record.genre_set:
        if same_type:
            shared = profile.genre_set & record.genre_set
            if shared:
                overlap += len(shared) / len(profile.genre_set | record.genre_set)
        else:
            overlap += cluster_similarity(profile.clusters, record.clusters)

    if profile.creator and record.creator and profile.creator == record.creator:
        overlap += _SHARED_CREATOR_OVERLAP

    if record.liked:
        overlap += _LIKED_RATING_OVERLAP

    return overlap


class SignalIndex:
    """Lookup structures over one request's taste-signal set."""

    def __init__(self, signal_items: Sequence[ContentItem]) -> None:
        records = [
            _record_for(item, ordinal)
            for ordinal, item in enumerate(
                item
                for item in signal_items
                if item.status != ConsumptionStatus.CURRENTLY_CONSUMING
            )
        ]

        self._adaptations_by_title: dict[str, list[_SignalRecord]] = {}
        self._adaptations_by_author: dict[str, list[_SignalRecord]] = {}
        self._references_by_type: dict[str, _TypeBucket] = {}
        self._excluded_by_series: dict[str, set[int]] = {}

        for record in records:
            if record.liked:
                self._adaptations_by_title.setdefault(record.sort_title, []).append(
                    record
                )
                if record.sort_author:
                    self._adaptations_by_author.setdefault(
                        record.sort_author, []
                    ).append(record)

            if record.rating is not None and record.rating < _DISLIKED_RATING:
                continue
            content_type = get_enum_value(record.item.content_type)
            self._references_by_type.setdefault(content_type, _TypeBucket()).add(record)
            for series_key in record.series_keys:
                self._excluded_by_series.setdefault(series_key, set()).add(
                    record.ordinal
                )

    def adaptations_of(self, candidate: ContentItem) -> list[ContentItem]:
        """Consumed items of another type that *candidate* adapts."""
        sort_title = get_sort_title(candidate.title)
        sort_author = get_sort_title(candidate.author) if candidate.author else None

        matched: dict[int, ContentItem] = {}
        for record in self._adaptations_by_title.get(sort_title, ()):
            if record.item.content_type != candidate.content_type:
                matched[record.ordinal] = record.item

        if sort_author:
            for record in self._adaptations_by_author.get(sort_author, ()):
                if (
                    record.item.content_type != candidate.content_type
                    and titles_similar(candidate.title, record.item.title)
                ):
                    matched[record.ordinal] = record.item

        return [matched[ordinal] for ordinal in sorted(matched)]

    def references_for(
        self, candidate: ContentItem, rng: random.Random
    ) -> list[ContentItem]:
        """Consumed items that explain *candidate*, strongest first whatever
        their content type."""
        profile = _profile_of(candidate)
        excluded: set[int] = (
            self._excluded_by_series.get(profile.series_key, set())
            if profile.series_key is not None
            else set()
        )

        scored: list[tuple[float, int, _SignalRecord]] = []
        for content_type, bucket in self._references_by_type.items():
            scored.extend(
                self._top_of_type(
                    profile,
                    bucket,
                    excluded,
                    same_type=content_type == profile.content_type,
                )
            )

        scored.sort(key=lambda entry: (-entry[0], entry[1]))
        return _shuffle_close_scores(
            [(record.item, score) for score, _, record in scored], rng
        )

    @staticmethod
    def _top_of_type(
        profile: _CandidateProfile,
        bucket: _TypeBucket,
        excluded: set[int],
        *,
        same_type: bool,
    ) -> list[tuple[float, int, _SignalRecord]]:
        """The best references of one content type, strongest first."""
        reachable: dict[int, _SignalRecord] = {}
        if profile.genre_set:
            index = bucket.by_genre if same_type else bucket.by_cluster
            terms = profile.genre_set if same_type else profile.clusters
            for term in terms:
                for record in index.get(term, ()):
                    reachable[record.ordinal] = record
        if profile.creator:
            for record in bucket.by_creator.get(profile.creator, ()):
                reachable[record.ordinal] = record

        minimum = 0.0 if same_type else CROSS_TYPE_MIN_OVERLAP
        scored: list[tuple[float, int, _SignalRecord]] = []
        for ordinal, record in reachable.items():
            if ordinal in excluded:
                continue
            score = _pair_overlap(profile, record, same_type=same_type)
            if score > 0.0 and score >= minimum:
                scored.append((score, ordinal, record))

        if same_type:
            unreachable = (
                record
                for record in bucket.liked
                if record.ordinal not in reachable and record.ordinal not in excluded
            )
            for record in islice(unreachable, _REFERENCES_PER_TYPE):
                scored.append((_LIKED_RATING_OVERLAP, record.ordinal, record))

        scored.sort(key=lambda entry: (-entry[0], entry[1]))
        return scored[:_REFERENCES_PER_TYPE]
