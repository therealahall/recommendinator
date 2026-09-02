"""What is pinned here is the cost: each signal item is derived once per request,
and a candidate reaches its matches through the index rather than by comparing
itself against the whole signal set."""

from __future__ import annotations

import random
from collections import Counter
from typing import Any
from unittest.mock import Mock

import pytest

from src.models.content import (
    ConsumptionStatus,
    ContentItem,
    ContentType,
    get_enum_value,
)
from src.recommendations import reference_index as reference_index_module
from src.recommendations.constants import CROSS_TYPE_MIN_OVERLAP
from src.recommendations.engine import RecommendationEngine
from src.recommendations.reference_index import SignalIndex
from tests.factories import make_item, make_storage_mock

# The functions that derive a matchable value from one item. Each takes the
# item (or one of its strings) and must run once per item per request.
_DERIVERS = ("extract_genres", "extract_creator", "get_series_name", "get_sort_title")


def _signal_item(index: int, *, genre: str, content_type: ContentType) -> ContentItem:
    return make_item(
        item_id=f"signal-{index}",
        db_id=index,
        title=f"Signal Title {index}",
        author=f"Signal Author {index}",
        content_type=content_type,
        status=ConsumptionStatus.COMPLETED,
        rating=5,
        metadata={"genres": [genre]},
    )


def _candidate(index: int, *, genre: str, content_type: ContentType) -> ContentItem:
    return make_item(
        item_id=f"candidate-{index}",
        db_id=1000 + index,
        title=f"Candidate Title {index}",
        author=f"Candidate Author {index}",
        content_type=content_type,
        status=ConsumptionStatus.UNREAD,
        metadata={"genres": [genre]},
    )


def _storage_over(items: list[ContentItem]) -> Mock:
    def completed(content_type: ContentType | None = None, **_: Any) -> list[Any]:
        return [
            item
            for item in items
            if item.status != ConsumptionStatus.UNREAD
            and (content_type is None or item.content_type == content_type)
        ]

    def signal(content_type: ContentType | None = None, **_: Any) -> list[Any]:
        return [item for item in completed(content_type=content_type) if item.rating]

    def unconsumed(content_type: ContentType | None = None, **_: Any) -> list[Any]:
        return [
            item
            for item in items
            if item.status == ConsumptionStatus.UNREAD
            and (content_type is None or item.content_type == content_type)
        ]

    storage = make_storage_mock()
    storage.get_completed_items = Mock(side_effect=completed)
    storage.get_signal_items = Mock(side_effect=signal)
    storage.get_unconsumed_items = Mock(side_effect=unconsumed)
    return storage


class TestDerivedValuesComputedOncePerItemRegression:
    """Symptom: a recommendation request over a real library spent most of its time
    normalising the same handful of consumed items over and over."""

    @pytest.fixture
    def counted_derivers(self, monkeypatch) -> Counter[tuple[str, str]]:
        counts: Counter[tuple[str, str]] = Counter()

        def counting(name: str):
            real = getattr(reference_index_module, name)

            def wrapper(value):
                key = value.id if isinstance(value, ContentItem) else value
                counts[name, key] += 1
                return real(value)

            return wrapper

        for name in _DERIVERS:
            monkeypatch.setattr(reference_index_module, name, counting(name))
        return counts

    def test_each_signal_item_is_derived_once_per_request(self, counted_derivers):
        """Titles and authors are unique per item, so a per-string count is a per-item
        count."""
        signal_items = [
            _signal_item(index, genre="Science Fiction", content_type=ContentType.BOOK)
            for index in range(20)
        ]
        candidates = [
            _candidate(index, genre="Science Fiction", content_type=ContentType.BOOK)
            for index in range(12)
        ]
        engine = RecommendationEngine(
            storage_manager=_storage_over(signal_items + candidates),
            min_rating=4,
        )

        recommendations = engine.generate_recommendations(
            content_type=ContentType.BOOK, count=12
        )

        assert len(recommendations) == len(candidates)
        assert all(rec.contributing_items for rec in recommendations)
        for item in signal_items:
            assert counted_derivers["extract_genres", item.id] == 1
            assert counted_derivers["extract_creator", item.id] == 1
            assert counted_derivers["get_series_name", item.id] == 1
            assert counted_derivers["get_sort_title", item.title] == 1
            assert counted_derivers["get_sort_title", item.author] == 1


class TestMatchingGoesThroughTheIndex:
    """Before the index each candidate ran the full comparison against every citable
    signal item, and normalised every signal title while looking for adaptations."""

    @staticmethod
    def _crowd(genre: str, content_type: ContentType) -> list[ContentItem]:
        return [
            _signal_item(index, genre=genre, content_type=content_type)
            for index in range(200)
        ]

    @staticmethod
    def _cyberpunk_candidate() -> ContentItem:
        return make_item(
            item_id="candidate",
            title="Cyberpunk Candidate",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
            metadata={"genres": ["Cyberpunk"]},
        )

    @pytest.fixture
    def compared_ids(self, monkeypatch) -> list[str]:
        """Scoring happens only during a lookup, never while the index is built, so
        patching here counts lookups whenever the index is constructed."""
        compared: list[str] = []
        real_overlap = reference_index_module._pair_overlap

        def counting(profile, record, *, same_type):
            compared.append(record.item.id)
            return real_overlap(profile, record, same_type=same_type)

        monkeypatch.setattr(reference_index_module, "_pair_overlap", counting)
        return compared

    def test_only_signal_items_a_lookup_reaches_are_compared(self, compared_ids):
        related = make_item(
            item_id="related",
            title="Shared Genre",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            rating=5,
            metadata={"genres": ["Cyberpunk"]},
        )
        index = SignalIndex(self._crowd("Western", ContentType.BOOK) + [related])

        references = index.references_for(self._cyberpunk_candidate(), random.Random(1))

        assert compared_ids == [related.id]
        assert related in references


class TestSameTypeSlotsHoldWhatAFullScanWouldHold:
    """A same-type signal item the user rated highly qualifies on its rating alone,
    so no genre or creator lookup reaches it."""

    @staticmethod
    def _liked_westerns(count: int, content_type: ContentType) -> list[ContentItem]:
        return [
            make_item(
                item_id=f"liked-{index}",
                title=f"Liked Western {index}",
                content_type=content_type,
                status=ConsumptionStatus.COMPLETED,
                rating=5,
                metadata={"genres": ["Western"]},
            )
            for index in range(count)
        ]

    def test_the_fill_skips_the_candidates_own_series_before_taking_three(self) -> None:
        """Filling first and excluding afterwards would return one reference instead
        of three, which is what a full scan returns."""
        expanse = [
            make_item(
                item_id=f"expanse-s{season}",
                title=f"The Expanse (The Expanse, Season {season})",
                content_type=ContentType.TV_SHOW,
                status=ConsumptionStatus.COMPLETED,
                rating=5,
                metadata={"genres": ["Western"]},
            )
            for season in (1, 2)
        ]
        others = self._liked_westerns(3, ContentType.TV_SHOW)
        candidate = make_item(
            item_id="expanse-s4",
            title="The Expanse (The Expanse, Season 4)",
            content_type=ContentType.TV_SHOW,
            status=ConsumptionStatus.UNREAD,
            metadata={"genres": ["Cyberpunk"]},
        )

        references = SignalIndex(expanse + others).references_for(
            candidate, random.Random(7)
        )

        assert {item.id for item in references} == {"liked-0", "liked-1", "liked-2"}


class TestCrossTypeReferencesReachedByCreator:
    """A shared creator is a way in that owes nothing to genres or clusters."""

    def test_a_shared_creator_cites_an_item_of_another_type(self) -> None:
        consumed = make_item(
            item_id="dust-roads",
            title="Dust Roads",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            rating=5,
            author="Mira Vale",
            metadata={"genres": ["Western"]},
        )
        candidate = make_item(
            item_id="neon-rain",
            title="Neon Rain",
            content_type=ContentType.MOVIE,
            status=ConsumptionStatus.UNREAD,
            author="Mira Vale",
            metadata={"genres": ["Cyberpunk"]},
        )

        references = SignalIndex([consumed]).references_for(candidate, random.Random(7))

        assert [item.id for item in references] == [consumed.id]


class TestReferenceGroupOrder:
    """The candidate's own type leads, and the rest follow their best match."""

    def test_own_type_leads_and_other_types_follow_their_best_reference(self) -> None:
        """The movie beats the show on a shared creator, so the groups must come out
        books, movie, show even though the book scores lowest of the three."""
        weak_book = make_item(
            item_id="weak-book",
            title="Four Genres",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            rating=3,
            metadata={"genres": ["Science Fiction", "Western", "Romance", "Horror"]},
        )
        movie = make_item(
            item_id="movie",
            title="Star Film",
            content_type=ContentType.MOVIE,
            status=ConsumptionStatus.COMPLETED,
            rating=5,
            author="Mira Vale",
            metadata={"genres": ["Science Fiction"]},
        )
        show = make_item(
            item_id="show",
            title="Star Show",
            content_type=ContentType.TV_SHOW,
            status=ConsumptionStatus.COMPLETED,
            rating=5,
            author="Someone Else",
            metadata={"genres": ["Science Fiction"]},
        )
        candidate = make_item(
            item_id="candidate",
            title="Neon Rain",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
            author="Mira Vale",
            metadata={"genres": ["Science Fiction"]},
        )

        references = SignalIndex([show, movie, weak_book]).references_for(
            candidate, random.Random(7)
        )

        assert [item.id for item in references] == ["weak-book", "movie", "show"]


class TestAdaptationLookupEdges:
    @staticmethod
    def _book(item_id: str, title: str, *, rating: int, author: str) -> ContentItem:
        return make_item(
            item_id=item_id,
            title=title,
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            rating=rating,
            author=author,
        )

    @staticmethod
    def _film(title: str, *, author: str | None = None) -> ContentItem:
        return make_item(
            item_id="film",
            title=title,
            content_type=ContentType.MOVIE,
            status=ConsumptionStatus.UNREAD,
            author=author,
        )

    def test_a_leading_article_does_not_stop_a_title_matching(self) -> None:
        book = self._book("book", "The Silent Tide", rating=5, author="Mira Vale")

        adaptations = SignalIndex([book]).adaptations_of(self._film("Silent Tide"))

        assert [item.id for item in adaptations] == ["book"]

    def test_a_rating_below_the_liked_floor_is_not_an_adaptation(self) -> None:
        """An adaptation is cited as a recommendation, so 3 is not enough."""
        book = self._book("book", "The Silent Tide", rating=3, author="Mira Vale")

        adaptations = SignalIndex([book]).adaptations_of(
            self._film("The Silent Tide", author="Mira Vale")
        )

        assert adaptations == []

    def test_a_shared_creator_and_a_similar_title_is_an_adaptation(self) -> None:
        """Both sides need a creator for this branch to fire, which for items read
        from storage only became possible once every content type carried one."""
        book = self._book("book", "The Silent Tide", rating=5, author="Mira Vale")

        adaptations = SignalIndex([book]).adaptations_of(
            self._film("The Silent Tide: Part One", author="Mira Vale")
        )

        assert [item.id for item in adaptations] == ["book"]

    def test_a_shared_author_with_an_unrelated_title_is_not_an_adaptation(self) -> None:
        book = self._book("book", "Dust Roads", rating=5, author="Mira Vale")

        adaptations = SignalIndex([book]).adaptations_of(
            self._film("The Silent Tide", author="Mira Vale")
        )

        assert adaptations == []

    def test_a_same_type_signal_item_is_never_an_adaptation(self) -> None:
        """An adaptation is the same work in another medium, not the same medium."""
        book = self._book("book", "The Silent Tide", rating=5, author="Mira Vale")
        candidate = make_item(
            item_id="other-edition",
            title="The Silent Tide",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
            author="Mira Vale",
        )

        assert SignalIndex([book]).adaptations_of(candidate) == []


#: Distinct titles with no digits and no roman numerals, so the randomised
#: library below carries no accidental series membership to exclude.
_NAMES: tuple[str, ...] = (
    "Ashfall",
    "Brightwater",
    "Cinderhold",
    "Dawnreach",
    "Emberlyn",
    "Fernglass",
    "Glasshaven",
    "Hollowmere",
    "Ironbark",
    "Junipergate",
    "Kestrelwind",
    "Larkspur",
    "Moorfen",
    "Nettlebrook",
    "Oakenshade",
    "Pinehurst",
    "Quarrystone",
    "Ravensong",
    "Saltmarsh",
    "Thornwyck",
    "Umberfield",
    "Violetgrove",
    "Whitethorn",
    "Yarrowdale",
)

_GENRE_POOL: tuple[str, ...] = (
    "Science Fiction",
    "Cyberpunk",
    "Space Opera",
    "Western",
    "Romance",
    "Horror",
    "Mystery",
    "Comedy",
    "War",
    "Fantasy",
)

_CREATOR_POOL: tuple[str | None, ...] = (None, "Mira Vale", "Otto Krane", "Sena Ilves")

_TYPE_POOL: tuple[ContentType, ...] = (
    ContentType.BOOK,
    ContentType.MOVIE,
    ContentType.TV_SHOW,
    ContentType.VIDEO_GAME,
)


class TestTheIndexCitesTheTrueTopOfEachType:
    """The index scores only the signal items a lookup reaches, so the failure it
    risks is a reference it never compares and therefore never cites."""

    @staticmethod
    def _library(rng: random.Random) -> list[ContentItem]:
        return [
            make_item(
                item_id=f"signal-{position}",
                title=name,
                content_type=rng.choice(_TYPE_POOL),
                status=ConsumptionStatus.COMPLETED,
                rating=rng.choice((None, 1, 2, 3, 4, 5)),
                author=rng.choice(_CREATOR_POOL),
                metadata={"genres": rng.sample(_GENRE_POOL, rng.randint(0, 4))},
            )
            for position, name in enumerate(_NAMES)
        ]

    @staticmethod
    def _expected_slots(
        profile: Any, records: list[Any], content_type: str, *, same_type: bool
    ) -> set[str]:
        minimum = 0.0 if same_type else CROSS_TYPE_MIN_OVERLAP
        scored: list[tuple[float, int, str]] = []
        for record in records:
            if get_enum_value(record.item.content_type) != content_type:
                continue
            score = reference_index_module._pair_overlap(
                profile, record, same_type=same_type
            )
            if score > 0.0 and score >= minimum:
                scored.append((-score, record.ordinal, record.item.id))
        scored.sort(key=lambda entry: (entry[0], entry[1]))
        return {item_id for _, _, item_id in scored[:3]}

    @pytest.mark.parametrize("seed", [1, 2024])
    def test_every_type_group_holds_what_a_full_scan_would_hold(
        self, seed: int
    ) -> None:
        rng = random.Random(seed)
        signal_items = self._library(rng)
        candidate = make_item(
            item_id="candidate",
            title="Zephyrgate",
            content_type=rng.choice(_TYPE_POOL),
            status=ConsumptionStatus.UNREAD,
            author=rng.choice(_CREATOR_POOL),
            metadata={"genres": rng.sample(_GENRE_POOL, rng.randint(0, 4))},
        )

        cited = SignalIndex(signal_items).references_for(candidate, random.Random(seed))

        # Without this the comparison below could pass on two empty maps.
        assert cited, "this library must produce references for the scan to check"
        records = [
            reference_index_module._record_for(item, ordinal)
            for ordinal, item in enumerate(signal_items)
        ]
        citable = [
            record
            for record in records
            if record.rating is None
            or record.rating >= reference_index_module._DISLIKED_RATING
        ]
        profile = reference_index_module._profile_of(candidate)
        expected = {}
        for content_type in {
            get_enum_value(record.item.content_type) for record in citable
        }:
            slots = self._expected_slots(
                profile,
                citable,
                content_type,
                same_type=content_type == profile.content_type,
            )
            if slots:
                expected[content_type] = slots

        actual: dict[str, set[str]] = {}
        for item in cited:
            actual.setdefault(get_enum_value(item.content_type), set()).add(item.id)
        assert actual == expected


class TestAnEmptySignalSet:
    @staticmethod
    def _candidate() -> ContentItem:
        return make_item(
            item_id="candidate",
            title="Neon Rain",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
            author="Mira Vale",
            metadata={"genres": ["Cyberpunk"]},
        )

    def test_no_references_and_no_adaptations(self) -> None:
        index = SignalIndex([])

        assert index.references_for(self._candidate(), random.Random(7)) == []
        assert index.adaptations_of(self._candidate()) == []


class TestOneSignalItemCitedTwoWays:
    """``_generate_reasoning`` dedupes the two lists by db_id because they
    overlap; anything else naming both owes the same dedup."""

    def test_the_book_a_film_adapts_is_also_a_contributing_reference(self) -> None:
        book = make_item(
            item_id="dune-book",
            db_id=1,
            title="Dune",
            author="Frank Herbert",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            rating=5,
            metadata={"genres": ["Science Fiction"]},
        )
        film = make_item(
            item_id="dune-film",
            db_id=2,
            title="Dune",
            author="Frank Herbert",
            content_type=ContentType.MOVIE,
            status=ConsumptionStatus.UNREAD,
            metadata={"genres": ["Science Fiction"]},
        )
        index = SignalIndex([book])

        assert [item.id for item in index.adaptations_of(film)] == ["dune-book"]
        assert "dune-book" in [
            item.id for item in index.references_for(film, random.Random(7))
        ]
