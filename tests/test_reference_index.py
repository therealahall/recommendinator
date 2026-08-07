"""Tests for the taste-signal index behind adaptations and references.

The behaviour these lookups produce is pinned by
``tests/test_recommendation_engine.py`` and
``tests/test_recommendation_characterisation.py``. What is pinned here is the
cost: each signal item is derived once per request, and a candidate reaches
its matches through the index rather than by comparing itself against the
whole signal set.
"""

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
from src.recommendations import engine as engine_module
from src.recommendations import reference_index as reference_index_module
from src.recommendations.constants import CROSS_TYPE_MIN_OVERLAP
from src.recommendations.engine import RecommendationEngine
from src.recommendations.reference_index import SignalIndex
from src.storage.manager import StorageManager
from tests.factories import make_item

# The functions that derive a matchable value from one item. Each takes the
# item (or one of its strings) and must run once per item per request.
_DERIVERS = ("extract_genres", "extract_creator", "get_series_name", "get_sort_title")


def _signal_item(index: int, *, genre: str, content_type: ContentType) -> ContentItem:
    """One consumed item with a title, author and genre unique to *index*."""
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
    """One unconsumed item with a title, author and genre unique to *index*."""
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
    """A spec'd StorageManager serving *items* from memory."""

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

    storage = Mock(spec=StorageManager)
    storage.get_completed_items = Mock(side_effect=completed)
    storage.get_signal_items = Mock(side_effect=signal)
    storage.get_unconsumed_items = Mock(side_effect=unconsumed)
    return storage


class TestDerivedValuesComputedOncePerItemRegression:
    """Regression: matching re-derived every signal item for every candidate.

    Symptom: a recommendation request over a real library spent most of its
    time normalising the same handful of consumed items over and over. A
    season-expanded TV library turns 300 shows into thousands of candidates,
    and each one re-derived the whole signal set.

    Root cause: adaptation and contributing-reference matching each looped over
    the entire signal set per candidate, and called ``get_sort_title``,
    ``extract_genres``, ``extract_creator`` and ``get_series_name`` on the
    consumed side *inside* that inner loop, so every derived value was
    recomputed once per (candidate, consumed item) pair.

    Fix: ``SignalIndex`` derives each signal item's values once when it is
    built, and the engine builds it once per request.
    """

    @pytest.fixture
    def counted_derivers(self, monkeypatch) -> Counter[tuple[str, str]]:
        """Count each deriver's calls, keyed by the argument it was given."""
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
        """20 signal items against 12 candidates: one derivation each, not 240.

        Titles and authors are unique per item, so a per-string count is a
        per-item count. The pre-fix code called every one of these four
        functions once per pair.
        """
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
            embedding_generator=None,
            recommendation_generator=None,
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

    def test_an_in_progress_signal_item_is_not_derived_at_all(self, counted_derivers):
        """Nothing the user is part-way through can be cited, so nothing is derived."""
        in_progress = make_item(
            item_id="in-progress",
            title="Half Read",
            author="Someone",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.CURRENTLY_CONSUMING,
            rating=5,
            metadata={"genres": ["Science Fiction"]},
        )
        completed = _signal_item(
            0, genre="Science Fiction", content_type=ContentType.BOOK
        )
        candidate = _candidate(
            0, genre="Science Fiction", content_type=ContentType.BOOK
        )
        engine = RecommendationEngine(
            storage_manager=_storage_over([in_progress, completed, candidate]),
            embedding_generator=None,
            recommendation_generator=None,
            min_rating=4,
        )

        recommendations = engine.generate_recommendations(
            content_type=ContentType.BOOK, count=5
        )

        assert [item.id for item in recommendations[0].contributing_items] == [
            completed.id
        ]
        for name in ("extract_genres", "extract_creator", "get_series_name"):
            assert counted_derivers[name, in_progress.id] == 0
        assert counted_derivers["get_sort_title", in_progress.title] == 0

    def test_cluster_membership_is_derived_once_per_item(self, monkeypatch) -> None:
        """Clusters take a term list, so they need a count rather than a key.

        One derivation per signal item and one per candidate is 32 here. The
        pre-fix cross-type comparison derived both sides inside the inner loop,
        which over the same library was 480.
        """
        calls: list[list[str]] = []
        real_clusters = reference_index_module.get_clusters_for_terms

        def counting(terms: list[str]) -> set[str]:
            calls.append(terms)
            return real_clusters(terms)

        monkeypatch.setattr(reference_index_module, "get_clusters_for_terms", counting)

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
            embedding_generator=None,
            recommendation_generator=None,
            min_rating=4,
        )

        recommendations = engine.generate_recommendations(
            content_type=ContentType.BOOK, count=12
        )

        assert len(recommendations) == len(candidates)
        assert len(calls) == len(signal_items) + len(candidates)


class TestMatchingGoesThroughTheIndex:
    """A candidate is compared against what the index reaches, not everything.

    Before the index each candidate ran the full comparison against every
    citable signal item, and normalised every signal title while looking for
    adaptations. Both now cost only the items a lookup returns.
    """

    @staticmethod
    def _crowd(genre: str, content_type: ContentType) -> list[ContentItem]:
        """200 highly rated signal items sharing nothing but their genre."""
        return [
            _signal_item(index, genre=genre, content_type=content_type)
            for index in range(200)
        ]

    @staticmethod
    def _cyberpunk_candidate() -> ContentItem:
        """A candidate whose one genre the crowd below does not share."""
        return make_item(
            item_id="candidate",
            title="Cyberpunk Candidate",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
            metadata={"genres": ["Cyberpunk"]},
        )

    @pytest.fixture
    def compared_ids(self, monkeypatch) -> list[str]:
        """Record the id of every signal item a candidate is scored against.

        Scoring happens only during a lookup, never while the index is built,
        so patching here counts lookups whenever the index is constructed.
        """
        compared: list[str] = []
        real_overlap = reference_index_module._pair_overlap

        def counting(profile, record, *, same_type):
            compared.append(record.item.id)
            return real_overlap(profile, record, same_type=same_type)

        monkeypatch.setattr(reference_index_module, "_pair_overlap", counting)
        return compared

    def test_only_signal_items_a_lookup_reaches_are_compared(self, compared_ids):
        """One shared genre in a crowd of 200 costs exactly one comparison."""
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

    def test_an_unrelated_cross_type_crowd_costs_no_comparisons(self, compared_ids):
        """No shared thematic cluster means no cross-type item is even scored."""
        index = SignalIndex(self._crowd("Western", ContentType.MOVIE))

        references = index.references_for(self._cyberpunk_candidate(), random.Random(1))

        assert compared_ids == []
        assert references == []

    def test_an_adaptation_is_found_without_normalising_the_crowd(self, monkeypatch):
        """Only the candidate's own title and author are normalised at lookup time."""
        adapted = make_item(
            item_id="adapted",
            title="The Silent Tide",
            author="Mira Vale",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            rating=5,
        )
        index = SignalIndex(self._crowd("Western", ContentType.BOOK) + [adapted])
        candidate = make_item(
            item_id="candidate",
            title="The Silent Tide",
            author="Mira Vale",
            content_type=ContentType.MOVIE,
            status=ConsumptionStatus.UNREAD,
        )

        # Patched after the index is built, so only the lookup is counted.
        normalised: list[str] = []
        real_sort_title = reference_index_module.get_sort_title

        def counting(value: str) -> str:
            normalised.append(value)
            return real_sort_title(value)

        monkeypatch.setattr(reference_index_module, "get_sort_title", counting)

        adaptations = index.adaptations_of(candidate)

        assert [item.id for item in adaptations] == [adapted.id]
        assert normalised == [candidate.title, candidate.author]

    def test_a_cross_type_item_cannot_qualify_on_its_rating_alone(self):
        """The constant relationship the cross-type shortcut depends on.

        Only the candidate's own type keeps highly rated items on hand for the
        slots a lookup cannot fill, because across types the rating overlap on
        its own does not clear the minimum. Raising ``_LIKED_RATING_OVERLAP``
        past ``CROSS_TYPE_MIN_OVERLAP`` would make every highly rated item of
        every other type a valid reference, and the lookup would start missing
        references it is supposed to return.
        """
        assert reference_index_module._LIKED_RATING_OVERLAP < CROSS_TYPE_MIN_OVERLAP


class TestOneIndexServesTheWholeRequest:
    """The index is a per-request structure, not a per-candidate one."""

    def test_a_request_builds_exactly_one_index(self, monkeypatch) -> None:
        """Ten candidates and one signal set cost one construction, not eleven."""
        built: list[int] = []
        real_index = engine_module.SignalIndex

        def counting(signal_items):
            built.append(len(signal_items))
            return real_index(signal_items)

        monkeypatch.setattr(engine_module, "SignalIndex", counting)

        signal_items = [
            _signal_item(index, genre="Cyberpunk", content_type=ContentType.BOOK)
            for index in range(4)
        ]
        candidates = [
            _candidate(index, genre="Cyberpunk", content_type=ContentType.BOOK)
            for index in range(10)
        ]
        engine = RecommendationEngine(
            storage_manager=_storage_over(signal_items + candidates),
            embedding_generator=None,
            recommendation_generator=None,
            min_rating=4,
        )

        recommendations = engine.generate_recommendations(
            content_type=ContentType.BOOK, count=10
        )

        assert len(recommendations) == len(candidates)
        assert built == [len(signal_items)]


class TestSameTypeSlotsHoldWhatAFullScanWouldHold:
    """The lookup plus its fill must choose the same three items a scan did.

    A same-type signal item the user rated highly qualifies on its rating
    alone, so no genre or creator lookup reaches it. Those are offered from a
    list kept in signal order, and what these pin is that the three slots end
    up holding exactly what comparing the candidate against every signal item
    would have put in them.
    """

    @staticmethod
    def _liked_westerns(count: int, content_type: ContentType) -> list[ContentItem]:
        """*count* highly rated items sharing a genre the candidates never have."""
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

    def test_the_rating_fill_outranks_a_weaker_genre_match(self) -> None:
        """A 1-of-7 genre match scores below the rating overlap and loses its slot.

        The reachable item is the only one a lookup finds, so a fill that
        deferred to the lookup would seat it. A full scan seats the three
        earliest highly rated items instead, because 1/7 is under 0.15.
        """
        liked = self._liked_westerns(5, ContentType.BOOK)
        weak_match = make_item(
            item_id="weak-match",
            title="Everything At Once",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            rating=3,
            metadata={
                "genres": [
                    "Cyberpunk",
                    "Western",
                    "Romance",
                    "Horror",
                    "Mystery",
                    "Musical",
                    "Sport",
                ]
            },
        )
        candidate = make_item(
            item_id="candidate",
            title="Neon Rain",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
            metadata={"genres": ["Cyberpunk"]},
        )

        references = SignalIndex(liked + [weak_match]).references_for(
            candidate, random.Random(7)
        )

        assert {item.id for item in references} == {"liked-0", "liked-1", "liked-2"}

    def test_the_fill_skips_the_candidates_own_series_before_taking_three(self) -> None:
        """Excluded seasons must not consume the three slots they are barred from.

        Filling first and excluding afterwards would return one reference
        instead of three, which is what a full scan returns.
        """
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

    def test_a_candidate_with_no_usable_genres_still_gets_the_fill(self) -> None:
        """Nothing to look up by leaves the rating fill as the only way in."""
        candidate = make_item(
            item_id="candidate",
            title="Untagged",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
            metadata={"genres": ["Blorp"]},
        )

        references = SignalIndex(self._liked_westerns(2, ContentType.BOOK))
        result = references.references_for(candidate, random.Random(7))

        assert {item.id for item in result} == {"liked-0", "liked-1"}

    def test_an_item_the_user_merely_tolerated_needs_a_real_match(self) -> None:
        """A rating of 3 earns no overlap, so nothing reaches it without a match."""
        tolerated = make_item(
            item_id="tolerated",
            title="Fine I Guess",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            rating=3,
            metadata={"genres": ["Western"]},
        )
        candidate = make_item(
            item_id="candidate",
            title="Neon Rain",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
            metadata={"genres": ["Cyberpunk"]},
        )

        references = SignalIndex([tolerated]).references_for(
            candidate, random.Random(7)
        )

        assert references == []


class TestCrossTypeReferencesReachedByCreator:
    """A shared creator is a way in that owes nothing to genres or clusters."""

    def test_a_shared_creator_cites_an_item_of_another_type(self) -> None:
        """No shared cluster, but the same author clears the cross-type minimum."""
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

    def test_a_different_creator_and_no_shared_cluster_cites_nothing(self) -> None:
        """The rating overlap alone leaves a cross-type item under the minimum."""
        consumed = make_item(
            item_id="dust-roads",
            title="Dust Roads",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            rating=5,
            author="Someone Else",
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

        assert references == []


class TestReferenceGroupOrder:
    """The candidate's own type leads, and the rest follow their best match."""

    def test_own_type_leads_and_other_types_follow_their_best_reference(self) -> None:
        """A weaker same-type reference still outranks a stronger cross-type one.

        The movie beats the show on a shared creator, so the groups must come
        out books, movie, show even though the book scores lowest of the three.
        """
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
    """What the title and author indexes must and must not return."""

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

    def test_a_match_on_both_title_and_author_is_cited_once(self) -> None:
        """Two indexes reach the same item, and it must still appear once."""
        book = self._book("book", "The Silent Tide", rating=5, author="Mira Vale")

        adaptations = SignalIndex([book]).adaptations_of(
            self._film("The Silent Tide", author="Mira Vale")
        )

        assert [item.id for item in adaptations] == ["book"]

    def test_two_signal_items_sharing_a_title_are_both_cited(self) -> None:
        """Duplicate titles are two adaptations, returned in signal order."""
        first = self._book("first", "The Silent Tide", rating=5, author="Mira Vale")
        second = self._book("second", "The Silent Tide", rating=4, author="Other Hand")

        adaptations = SignalIndex([second, first]).adaptations_of(
            self._film("The Silent Tide")
        )

        assert [item.id for item in adaptations] == ["second", "first"]

    def test_a_leading_article_does_not_stop_a_title_matching(self) -> None:
        """Both sides normalise through the same article stripping."""
        book = self._book("book", "The Silent Tide", rating=5, author="Mira Vale")

        adaptations = SignalIndex([book]).adaptations_of(self._film("Silent Tide"))

        assert [item.id for item in adaptations] == ["book"]

    def test_a_non_latin_title_matches_its_adaptation(self) -> None:
        """Normalisation is a lowercase, not an ASCII fold."""
        book = self._book("book", "Тихий Прилив", rating=5, author="Мира Вейл")

        adaptations = SignalIndex([book]).adaptations_of(self._film("тихий прилив"))

        assert [item.id for item in adaptations] == ["book"]

    def test_a_rating_below_the_liked_floor_is_not_an_adaptation(self) -> None:
        """An adaptation is cited as a recommendation, so 3 is not enough."""
        book = self._book("book", "The Silent Tide", rating=3, author="Mira Vale")

        adaptations = SignalIndex([book]).adaptations_of(
            self._film("The Silent Tide", author="Mira Vale")
        )

        assert adaptations == []

    def test_a_shared_creator_and_a_similar_title_is_an_adaptation(self) -> None:
        """The author index is the only way in when the titles are not identical.

        Both sides need a creator for this branch to fire, which for items read
        from storage only became possible once every content type carried one.
        """
        book = self._book("book", "The Silent Tide", rating=5, author="Mira Vale")

        adaptations = SignalIndex([book]).adaptations_of(
            self._film("The Silent Tide: Part One", author="Mira Vale")
        )

        assert [item.id for item in adaptations] == ["book"]

    def test_a_shared_creator_within_one_type_is_not_an_adaptation(self) -> None:
        """The author index still has to clear the different-medium check."""
        book = self._book("book", "The Silent Tide", rating=5, author="Mira Vale")
        candidate = make_item(
            item_id="sequel",
            title="The Silent Tide: Part One",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
            author="Mira Vale",
        )

        assert SignalIndex([book]).adaptations_of(candidate) == []

    def test_a_candidate_without_a_creator_never_reaches_the_author_index(self) -> None:
        """A candidate with no creator can only match on its title."""
        book = self._book("book", "The Silent Tide", rating=5, author="Mira Vale")

        adaptations = SignalIndex([book]).adaptations_of(
            self._film("The Silent Tide: Part One")
        )

        assert adaptations == []

    def test_a_shared_author_with_an_unrelated_title_is_not_an_adaptation(self) -> None:
        """The author index still has to clear the title similarity check."""
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
    """Over randomised libraries, the lookup must miss nothing a scan would find.

    The index scores only the signal items a lookup reaches, so the failure it
    risks is a reference it never compares and therefore never cites. These
    walk the whole signal set with the same ``_pair_overlap`` the index itself
    uses and rebuild the selection independently, which pins that the slots
    hold the true top of each content type rather than whatever the lookup
    happened to reach.
    """

    @staticmethod
    def _library(rng: random.Random) -> list[ContentItem]:
        """A signal set of every content type, rating and genre mix."""
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
        """The three best citable records of *content_type*, found by scanning."""
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

    @pytest.mark.parametrize("seed", [1, 2, 3, 17, 2024])
    def test_every_type_group_holds_what_a_full_scan_would_hold(
        self, seed: int
    ) -> None:
        """No lookup-reachable shortcut may change which items get cited."""
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
    """An index over nothing answers both lookups without failing."""

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
