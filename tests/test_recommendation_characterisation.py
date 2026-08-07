"""Characterisation harness for end-to-end recommendation output.

Drives one fixed library through
:meth:`RecommendationEngine.generate_recommendations` and pins the ordering and
the emitted scores the engine produces today, for all four content types.

Every expected value here is an observation of current behaviour, not a
specification. When a scoring stage changes, these constants change with it and
the behavioural delta is reviewed rather than guessed.

Storage is a spec'd mock over the module-level library and the engine runs
without an embedding generator, so the harness needs no LLM, no vector database
and no network.
"""

from __future__ import annotations

import copy
import inspect
import random
from collections.abc import Sequence
from typing import Any
from unittest.mock import Mock, patch

import pytest

from src.models.content import ConsumptionStatus, ContentItem, ContentType
from src.recommendations import engine as engine_module
from src.recommendations.engine import RecommendationEngine
from src.recommendations.scorers import (
    DEFAULT_SCORERS,
    SCORER_NAME_MAP,
    build_scorers_with_overrides,
)
from src.settings.metadata import get_entry
from src.storage.manager import StorageManager

_COMPLETED = ConsumptionStatus.COMPLETED
_UNREAD = ConsumptionStatus.UNREAD


def _item(
    *,
    item_id: str,
    db_id: int,
    title: str,
    content_type: ContentType,
    status: ConsumptionStatus,
    genres: tuple[str, ...],
    rating: int | None = None,
    author: str | None = None,
    ignored: bool = False,
    total_seasons: int | None = None,
) -> ContentItem:
    """Build one library item, with *genres* placed in metadata as sources do."""
    metadata: dict[str, Any] = {"genres": list(genres)}
    if total_seasons is not None:
        metadata["total_seasons"] = total_seasons
    return ContentItem(
        id=item_id,
        db_id=db_id,
        title=title,
        content_type=content_type,
        status=status,
        rating=rating,
        author=author,
        ignored=ignored,
        metadata=metadata,
    )


# The library every characterisation test runs against. Changing it invalidates
# every baseline below, so prefer adding a separate library for a new scenario.
LIBRARY: tuple[ContentItem, ...] = (
    # --- consumed books -------------------------------------------------
    _item(
        item_id="b-dune",
        db_id=1,
        title="Dune",
        content_type=ContentType.BOOK,
        status=_COMPLETED,
        rating=5,
        author="Frank Herbert",
        genres=("science fiction", "space opera"),
    ),
    _item(
        item_id="b-neuromancer",
        db_id=2,
        title="Neuromancer",
        content_type=ContentType.BOOK,
        status=_COMPLETED,
        rating=5,
        author="William Gibson",
        genres=("science fiction", "cyberpunk"),
    ),
    _item(
        item_id="b-kings",
        db_id=3,
        title="The Way of Kings (The Stormlight Archive, #1)",
        content_type=ContentType.BOOK,
        status=_COMPLETED,
        rating=5,
        author="Brandon Sanderson",
        genres=("fantasy", "epic fantasy"),
    ),
    _item(
        item_id="b-leviathan",
        db_id=4,
        title="Leviathan Wakes (The Expanse, #1)",
        content_type=ContentType.BOOK,
        status=_COMPLETED,
        rating=4,
        author="James S. A. Corey",
        genres=("science fiction", "space opera"),
    ),
    _item(
        item_id="b-small-planet",
        db_id=5,
        title="The Long Way to a Small Angry Planet",
        content_type=ContentType.BOOK,
        status=_COMPLETED,
        rating=4,
        author="Becky Chambers",
        genres=("science fiction", "space opera"),
    ),
    _item(
        item_id="b-ancillary",
        db_id=6,
        title="Ancillary Justice",
        content_type=ContentType.BOOK,
        status=_COMPLETED,
        rating=3,
        author="Ann Leckie",
        genres=("science fiction",),
    ),
    _item(
        item_id="b-grimoire",
        db_id=7,
        title="Endless Grimoire",
        content_type=ContentType.BOOK,
        status=_COMPLETED,
        rating=1,
        author="Peregrine Vale",
        genres=("fantasy",),
    ),
    _item(
        item_id="b-harvest",
        db_id=8,
        title="Rotten Harvest",
        content_type=ContentType.BOOK,
        status=_COMPLETED,
        rating=1,
        author="Marla Quint",
        genres=("horror",),
    ),
    # --- consumed movies ------------------------------------------------
    _item(
        item_id="m-blade-runner",
        db_id=9,
        title="Blade Runner",
        content_type=ContentType.MOVIE,
        status=_COMPLETED,
        rating=5,
        author="Ridley Scott",
        genres=("science fiction", "cyberpunk"),
    ),
    _item(
        item_id="m-arrival",
        db_id=10,
        title="Arrival",
        content_type=ContentType.MOVIE,
        status=_COMPLETED,
        rating=5,
        author="Denis Villeneuve",
        genres=("science fiction", "drama"),
    ),
    _item(
        item_id="m-knives-out",
        db_id=11,
        title="Knives Out",
        content_type=ContentType.MOVIE,
        status=_COMPLETED,
        rating=4,
        author="Rian Johnson",
        genres=("mystery", "comedy"),
    ),
    _item(
        item_id="m-fury-road",
        db_id=12,
        title="Mad Max: Fury Road",
        content_type=ContentType.MOVIE,
        status=_COMPLETED,
        rating=5,
        author="George Miller",
        genres=("action", "post-apocalyptic"),
    ),
    _item(
        item_id="m-gnawing-dark",
        db_id=13,
        title="The Gnawing Dark",
        content_type=ContentType.MOVIE,
        status=_COMPLETED,
        rating=1,
        author="Otto Reiss",
        genres=("horror",),
    ),
    # --- consumed TV ----------------------------------------------------
    _item(
        item_id="t-expanse",
        db_id=14,
        title="The Expanse",
        content_type=ContentType.TV_SHOW,
        status=_COMPLETED,
        rating=5,
        author="Mark Fergus",
        genres=("science fiction", "space opera"),
    ),
    _item(
        item_id="t-chernobyl",
        db_id=15,
        title="Chernobyl",
        content_type=ContentType.TV_SHOW,
        status=_COMPLETED,
        rating=5,
        author="Craig Mazin",
        genres=("drama", "history"),
    ),
    _item(
        item_id="t-stranger-things",
        db_id=16,
        title="Stranger Things",
        content_type=ContentType.TV_SHOW,
        status=_COMPLETED,
        rating=3,
        author="The Duffer Brothers",
        genres=("science fiction", "horror"),
    ),
    # --- consumed games -------------------------------------------------
    _item(
        item_id="g-mass-effect",
        db_id=17,
        title="Mass Effect",
        content_type=ContentType.VIDEO_GAME,
        status=_COMPLETED,
        rating=5,
        author="BioWare",
        genres=("rpg", "science fiction"),
    ),
    _item(
        item_id="g-disco",
        db_id=18,
        title="Disco Elysium",
        content_type=ContentType.VIDEO_GAME,
        status=_COMPLETED,
        rating=5,
        author="ZA/UM",
        genres=("rpg", "mystery"),
    ),
    _item(
        item_id="g-hades",
        db_id=19,
        title="Hades",
        content_type=ContentType.VIDEO_GAME,
        status=_COMPLETED,
        rating=4,
        author="Supergiant Games",
        genres=("roguelike", "action"),
    ),
    _item(
        item_id="g-cyberpunk",
        db_id=20,
        title="Cyberpunk 2077",
        content_type=ContentType.VIDEO_GAME,
        status=_COMPLETED,
        rating=2,
        author="CD Projekt Red",
        genres=("rpg", "cyberpunk"),
    ),
    # --- ignored: excluded from both the signal set and the candidates ---
    _item(
        item_id="m-grim-tide",
        db_id=21,
        title="Grim Tide",
        content_type=ContentType.MOVIE,
        status=_COMPLETED,
        rating=5,
        author="Otto Reiss",
        genres=("horror", "thriller"),
        ignored=True,
    ),
    _item(
        item_id="b-colour",
        db_id=22,
        title="The Colour Out of Space",
        content_type=ContentType.BOOK,
        status=_UNREAD,
        author="Marla Quint",
        genres=("cosmic horror",),
        ignored=True,
    ),
    # --- candidate books ------------------------------------------------
    _item(
        item_id="b-calibans-war",
        db_id=23,
        title="Caliban's War (The Expanse, #2)",
        content_type=ContentType.BOOK,
        status=_UNREAD,
        author="James S. A. Corey",
        genres=("science fiction", "space opera"),
    ),
    _item(
        item_id="b-abaddons-gate",
        db_id=24,
        title="Abaddon's Gate (The Expanse, #3)",
        content_type=ContentType.BOOK,
        status=_UNREAD,
        author="James S. A. Corey",
        genres=("science fiction", "space opera"),
    ),
    _item(
        item_id="b-radiance",
        db_id=25,
        title="Words of Radiance (The Stormlight Archive, #2)",
        content_type=ContentType.BOOK,
        status=_UNREAD,
        author="Brandon Sanderson",
        genres=("fantasy", "epic fantasy"),
    ),
    _item(
        item_id="b-second-grimoire",
        db_id=26,
        title="The Second Grimoire",
        content_type=ContentType.BOOK,
        status=_UNREAD,
        author="Peregrine Vale",
        genres=("fantasy",),
    ),
    _item(
        item_id="b-memory-empire",
        db_id=27,
        title="A Memory Called Empire",
        content_type=ContentType.BOOK,
        status=_UNREAD,
        author="Arkady Martine",
        genres=("science fiction", "space opera"),
    ),
    _item(
        item_id="b-silent-patient",
        db_id=28,
        title="The Silent Patient",
        content_type=ContentType.BOOK,
        status=_UNREAD,
        author="Alex Michaelides",
        genres=("thriller", "mystery"),
    ),
    _item(
        item_id="b-piranesi",
        db_id=29,
        title="Piranesi",
        content_type=ContentType.BOOK,
        status=_UNREAD,
        author="Susanna Clarke",
        genres=("fantasy", "mystery"),
    ),
    # --- candidate movies -----------------------------------------------
    # "Dune" and "Arrakis Dreaming" are deliberately identical to every
    # scorer: same genres, no creator, no series. The only difference the
    # engine can see is that "Dune" adapts a 5-star consumed book.
    _item(
        item_id="m-dune",
        db_id=30,
        title="Dune",
        content_type=ContentType.MOVIE,
        status=_UNREAD,
        genres=("science fiction", "space opera"),
    ),
    _item(
        item_id="m-arrakis-dreaming",
        db_id=31,
        title="Arrakis Dreaming",
        content_type=ContentType.MOVIE,
        status=_UNREAD,
        genres=("science fiction", "space opera"),
    ),
    _item(
        item_id="m-blood-chapel",
        db_id=32,
        title="Blood Chapel",
        content_type=ContentType.MOVIE,
        status=_UNREAD,
        genres=("horror",),
    ),
    _item(
        item_id="m-nice-guys",
        db_id=33,
        title="The Nice Guys",
        content_type=ContentType.MOVIE,
        status=_UNREAD,
        author="Shane Black",
        genres=("comedy", "crime"),
    ),
    _item(
        item_id="m-everything",
        db_id=34,
        title="Everything Everywhere All at Once",
        content_type=ContentType.MOVIE,
        status=_UNREAD,
        author="Daniel Kwan",
        genres=("science fiction", "comedy"),
    ),
    # --- candidate TV ---------------------------------------------------
    _item(
        item_id="t-severance",
        db_id=35,
        title="Severance",
        content_type=ContentType.TV_SHOW,
        status=_UNREAD,
        author="Ben Stiller",
        genres=("science fiction", "thriller"),
        total_seasons=2,
    ),
    _item(
        item_id="t-andor",
        db_id=36,
        title="Andor",
        content_type=ContentType.TV_SHOW,
        status=_UNREAD,
        author="Tony Gilroy",
        genres=("science fiction", "drama"),
        total_seasons=2,
    ),
    # --- candidate games ------------------------------------------------
    _item(
        item_id="g-mass-effect-2",
        db_id=37,
        title="Mass Effect 2",
        content_type=ContentType.VIDEO_GAME,
        status=_UNREAD,
        author="BioWare",
        genres=("rpg", "science fiction"),
    ),
    _item(
        item_id="g-outer-wilds",
        db_id=38,
        title="Outer Wilds",
        content_type=ContentType.VIDEO_GAME,
        status=_UNREAD,
        author="Mobius Digital",
        genres=("adventure", "science fiction", "exploration"),
    ),
)


# A second library for the sparse case: one rated book, and one candidate the
# engine knows nothing about beyond a non-ASCII title.
NO_GENRE_LIBRARY: tuple[ContentItem, ...] = (
    _item(
        item_id="b-signal",
        db_id=1,
        title="Dune",
        content_type=ContentType.BOOK,
        status=_COMPLETED,
        rating=5,
        author="Frank Herbert",
        genres=("science fiction",),
    ),
    _item(
        item_id="b-untagged",
        db_id=2,
        title="Ünicode Träumerei",
        content_type=ContentType.BOOK,
        status=_UNREAD,
        genres=(),
    ),
)


# --------------------------------------------------------------------------
# Characterisation baselines: what the engine emits today, not a specification.
# --------------------------------------------------------------------------

# Ordering and scores for ``generate_recommendations(ContentType.MOVIE)``.
MOVIE_ORDER: tuple[str, ...] = (
    "Dune",
    "Arrakis Dreaming",
    "Everything Everywhere All at Once",
    "The Nice Guys",
    "Blood Chapel",
)
MOVIE_SCORES: tuple[float, ...] = (
    0.9793650793650794,
    0.7793650793650795,
    0.7643939393939394,
    0.5710317460317461,
    0.0,
)

# Ordering and scores for ``generate_recommendations(ContentType.BOOK)``.
BOOK_ORDER: tuple[str, ...] = (
    "Caliban's War (The Expanse, #2)",
    "Words of Radiance (The Stormlight Archive, #2)",
    "A Memory Called Empire",
    "The Silent Patient",
    "Piranesi",
    "The Second Grimoire",
)
BOOK_SCORES: tuple[float, ...] = (
    0.8279761904761906,
    0.7563492063492064,
    0.6821428571428572,
    0.5920238095238096,
    0.5785714285714287,
    0.11070153061224491,
)

# Ordering and scores for ``generate_recommendations(ContentType.TV_SHOW)``,
# after season expansion and the collapse to one entry per show.
TV_ORDER: tuple[str, ...] = (
    "Severance (Season 1)",
    "Andor (Season 1)",
)
TV_SCORES: tuple[float, ...] = (
    0.8033333333333336,
    0.7816666666666668,
)

# Ordering and scores for ``generate_recommendations(ContentType.VIDEO_GAME)``.
GAME_ORDER: tuple[str, ...] = (
    "Mass Effect 2",
    "Outer Wilds",
)
GAME_SCORES: tuple[float, ...] = (
    0.7851282051282051,
    0.7641666666666668,
)

# Score of the only candidate in ``NO_GENRE_LIBRARY``, which carries no genres
# at all and so reaches the ranker with nothing but its title.
NO_GENRE_CANDIDATE_SCORE = 0.46666666666666673

# The adaptation bonus a 5-star consumed adaptation adds to a candidate's score.
ADAPTATION_BONUS_FIVE_STAR = 0.2

# Score, preference score and rank of a candidate whose author the user rated
# 1 star on every consumed work.
DISLIKED_AUTHOR_SCORE = 0.11070153061224491
DISLIKED_AUTHOR_PREFERENCE_SCORE = -0.6785714285714286
DISLIKED_AUTHOR_POSITION = 5

# Tolerance for every float comparison: tight enough that any real change in
# the arithmetic breaks the assertion.
SCORE_TOLERANCE = 1e-9


def _library_storage(items: Sequence[ContentItem]) -> Mock:
    """Return a spec'd StorageManager serving *items* from memory.

    Mirrors the three accessors the engine reads: completed items include
    in-progress ones, signal items are the rated, non-ignored subset, and
    unconsumed items are everything not yet completed.
    """

    def completed(
        content_type: ContentType | None = None,
        min_rating: int | None = None,
        limit: int | None = None,
        include_ignored: bool = True,
    ) -> list[ContentItem]:
        matches = [
            item
            for item in items
            if item.status
            in (ConsumptionStatus.COMPLETED, ConsumptionStatus.CURRENTLY_CONSUMING)
            and (content_type is None or item.content_type == content_type)
            and (
                min_rating is None
                or (item.rating is not None and item.rating >= min_rating)
            )
            and (include_ignored or not item.ignored)
        ]
        return matches[:limit] if limit is not None else matches

    def signal(
        content_type: ContentType | None = None,
        limit: int | None = None,
    ) -> list[ContentItem]:
        return [
            item
            for item in completed(
                content_type=content_type,
                limit=limit,
                include_ignored=False,
            )
            if item.rating is not None
        ]

    def unconsumed(
        content_type: ContentType | None = None,
        limit: int | None = None,
        include_ignored: bool = True,
    ) -> list[ContentItem]:
        matches = [
            item
            for item in items
            if item.status
            in (ConsumptionStatus.UNREAD, ConsumptionStatus.CURRENTLY_CONSUMING)
            and (content_type is None or item.content_type == content_type)
            and (include_ignored or not item.ignored)
        ]
        return matches[:limit] if limit is not None else matches

    storage = Mock(spec=StorageManager)
    storage.get_completed_items = Mock(side_effect=completed)
    storage.get_signal_items = Mock(side_effect=signal)
    storage.get_unconsumed_items = Mock(side_effect=unconsumed)
    return storage


def _engine_over(items: Sequence[ContentItem]) -> RecommendationEngine:
    """Build a non-AI engine over *items* (no LLM, no vector DB, no network)."""
    return RecommendationEngine(
        storage_manager=_library_storage(items),
        embedding_generator=None,
        recommendation_generator=None,
        min_rating=4,
    )


@pytest.fixture
def engine() -> RecommendationEngine:
    """Engine over the full characterisation library."""
    return _engine_over(LIBRARY)


def _titles(recommendations: list[dict[str, Any]]) -> list[str]:
    """Recommended titles, best first."""
    return [rec["item"].title for rec in recommendations]


def _scores(recommendations: list[dict[str, Any]]) -> list[float]:
    """Emitted final scores, best first."""
    return [rec["score"] for rec in recommendations]


def _by_title(recommendations: list[dict[str, Any]], title: str) -> dict[str, Any]:
    """The single recommendation for *title*."""
    matches = [rec for rec in recommendations if rec["item"].title == title]
    assert len(matches) == 1, f"expected exactly one {title!r}, got {len(matches)}"
    return matches[0]


class TestMovieBaseline:
    """Current end-to-end output when recommending movies."""

    def test_ordering_and_scores(self, engine):
        """The full movie ranking and every emitted score."""
        recommendations = engine.generate_recommendations(
            content_type=ContentType.MOVIE, count=20
        )

        assert tuple(_titles(recommendations)) == MOVIE_ORDER
        assert _scores(recommendations) == pytest.approx(
            list(MOVIE_SCORES), abs=SCORE_TOLERANCE
        )

    def test_adaptation_outranks_equivalent_non_adaptation(self, engine):
        """An adaptation of a 5-star consumed book outranks its identical twin.

        Both candidates carry the same genres, no creator and no series, so the
        pipeline scores them identically. The whole difference in the emitted
        score is the adaptation bonus.
        """
        recommendations = engine.generate_recommendations(
            content_type=ContentType.MOVIE, count=20
        )
        adaptation = _by_title(recommendations, "Dune")
        twin = _by_title(recommendations, "Arrakis Dreaming")

        titles = _titles(recommendations)
        assert titles.index("Dune") < titles.index("Arrakis Dreaming")
        assert adaptation["similarity_score"] == pytest.approx(
            twin["similarity_score"], abs=SCORE_TOLERANCE
        )
        assert adaptation["score"] - twin["score"] == pytest.approx(
            ADAPTATION_BONUS_FIVE_STAR, abs=SCORE_TOLERANCE
        )
        assert [item.id for item in adaptation["adaptations"]] == ["b-dune"]

    def test_item_with_every_genre_disliked_scores_zero(self, engine):
        """Horror is the user's only 1-star genre, and it is this item's only one.

        A preference score of exactly -1.0 makes the ranker's multiplicative
        penalty zero the score outright, which is the only way today's ranking
        reaches zero.
        """
        recommendations = engine.generate_recommendations(
            content_type=ContentType.MOVIE, count=20
        )
        zeroed = _by_title(recommendations, "Blood Chapel")

        assert zeroed["preference_score"] == pytest.approx(-1.0, abs=SCORE_TOLERANCE)
        assert zeroed["score"] == pytest.approx(0.0, abs=SCORE_TOLERANCE)
        assert _titles(recommendations)[-1] == "Blood Chapel"


class TestBookBaseline:
    """Current end-to-end output when recommending books."""

    def test_ordering_and_scores(self, engine):
        """The full book ranking and every emitted score.

        Series ordering drops "Abaddon's Gate (#3)" because "Caliban's War
        (#2)" is the earliest unread entry in that series.
        """
        recommendations = engine.generate_recommendations(
            content_type=ContentType.BOOK, count=20
        )

        assert tuple(_titles(recommendations)) == BOOK_ORDER
        assert _scores(recommendations) == pytest.approx(
            list(BOOK_SCORES), abs=SCORE_TOLERANCE
        )

    def test_disliked_author_position_and_score(self, engine):
        """A book by an author the user rated 1 star lands near the bottom."""
        recommendations = engine.generate_recommendations(
            content_type=ContentType.BOOK, count=20
        )
        disliked = _by_title(recommendations, "The Second Grimoire")

        assert _titles(recommendations).index("The Second Grimoire") == (
            DISLIKED_AUTHOR_POSITION
        )
        assert disliked["preference_score"] == pytest.approx(
            DISLIKED_AUTHOR_PREFERENCE_SCORE, abs=SCORE_TOLERANCE
        )
        assert disliked["score"] == pytest.approx(
            DISLIKED_AUTHOR_SCORE, abs=SCORE_TOLERANCE
        )


class TestTvBaseline:
    """Current end-to-end output when recommending TV, which expands seasons."""

    def test_ordering_and_scores(self, engine):
        """Season expansion, series ordering and the collapse to one per show."""
        recommendations = engine.generate_recommendations(
            content_type=ContentType.TV_SHOW, count=20
        )

        assert tuple(_titles(recommendations)) == TV_ORDER
        assert _scores(recommendations) == pytest.approx(
            list(TV_SCORES), abs=SCORE_TOLERANCE
        )


class TestGameBaseline:
    """Current end-to-end output when recommending video games."""

    def test_ordering_and_scores(self, engine):
        """The full video game ranking and every emitted score."""
        recommendations = engine.generate_recommendations(
            content_type=ContentType.VIDEO_GAME, count=20
        )

        assert tuple(_titles(recommendations)) == GAME_ORDER
        assert _scores(recommendations) == pytest.approx(
            list(GAME_SCORES), abs=SCORE_TOLERANCE
        )


class TestIgnoredItems:
    """Ignored items are neither candidates nor taste signal."""

    def test_ignored_item_is_never_recommended_or_cited(self, engine):
        """The ignored book is not ranked, and the ignored movie is not a reason.

        "Grim Tide" is the only 5-star horror item in the library, so if the
        ignore flag stopped short of the signal set it would surface as a
        contributing reference for the horror candidate.
        """
        books = engine.generate_recommendations(content_type=ContentType.BOOK, count=20)
        movies = engine.generate_recommendations(
            content_type=ContentType.MOVIE, count=20
        )

        assert "The Colour Out of Space" not in _titles(books)
        cited = {
            reference.title
            for recommendation in movies
            for reference in recommendation["contributing_items"]
        }
        assert cited
        assert "Grim Tide" not in cited


class TestCountBoundary:
    """``count`` slices the ranking without reshaping it."""

    def test_count_smaller_than_the_ranking_keeps_the_top_entries(self, engine):
        """Asking for one movie returns the top of the full ranking, unchanged."""
        recommendations = engine.generate_recommendations(
            content_type=ContentType.MOVIE, count=1
        )

        assert tuple(_titles(recommendations)) == MOVIE_ORDER[:1]
        assert _scores(recommendations) == pytest.approx(
            list(MOVIE_SCORES[:1]), abs=SCORE_TOLERANCE
        )

    def test_zero_count_returns_empty(self, engine):
        """``count=0`` yields nothing, and does not fall through to the fallback."""
        assert (
            engine.generate_recommendations(content_type=ContentType.MOVIE, count=0)
            == []
        )

    def test_zero_count_still_consults_the_library_fallback(self, engine):
        """The empty result at ``count=0`` is not a short circuit.

        An empty formatted list plus available candidates is exactly the
        trigger for the engine's library fallback, so ``count=0`` reaches it
        and gets nothing back only because the fallback slices to the same
        ``count``. Pinning that keeps a later change to either the trigger or
        the slice from turning ``count=0`` into a full library dump.
        """
        with patch.object(
            engine,
            "_build_fallback_recommendations",
            wraps=engine._build_fallback_recommendations,
        ) as fallback:
            recommendations = engine.generate_recommendations(
                content_type=ContentType.MOVIE, count=0
            )

        assert recommendations == []
        assert fallback.call_count == 1


class TestSparseCandidates:
    """A candidate the engine has almost nothing to go on."""

    def test_candidate_without_genres_still_ranks(self):
        """An untagged, non-ASCII-titled candidate is scored rather than dropped."""
        recommendations = _engine_over(NO_GENRE_LIBRARY).generate_recommendations(
            content_type=ContentType.BOOK, count=5
        )

        assert _titles(recommendations) == ["Ünicode Träumerei"]
        assert recommendations[0]["score"] == pytest.approx(
            NO_GENRE_CANDIDATE_SCORE, abs=SCORE_TOLERANCE
        )


class TestBaselineStability:
    """What the baselines above must not depend on."""

    def test_ordering_is_independent_of_library_order(self):
        """Feeding the library back to front changes nothing.

        Storage decides the order it returns rows in, so a baseline that moved
        with it would be pinning this fixture's declaration order rather than
        the engine.
        """
        reversed_engine = _engine_over(tuple(reversed(LIBRARY)))

        movies = reversed_engine.generate_recommendations(
            content_type=ContentType.MOVIE, count=20
        )
        books = reversed_engine.generate_recommendations(
            content_type=ContentType.BOOK, count=20
        )

        assert tuple(_titles(movies)) == MOVIE_ORDER
        assert _scores(movies) == pytest.approx(list(MOVIE_SCORES), abs=SCORE_TOLERANCE)
        assert tuple(_titles(books)) == BOOK_ORDER
        assert _scores(books) == pytest.approx(list(BOOK_SCORES), abs=SCORE_TOLERANCE)

    @pytest.mark.parametrize("seed", [1, 2, 3, 17, 2024])
    def test_ordering_survives_any_library_permutation(self, seed):
        """No storage row order changes the ranking, for any content type.

        Reversing the fixture exercises one arrangement out of 38 factorial,
        and leaves every tie still broken the same way. Real storage picks its
        own order, so a baseline that only survives the reverse is still
        pinning this file's declaration order.
        """
        shuffled = list(LIBRARY)
        random.Random(seed).shuffle(shuffled)
        shuffled_engine = _engine_over(shuffled)

        for content_type, order, scores in (
            (ContentType.MOVIE, MOVIE_ORDER, MOVIE_SCORES),
            (ContentType.BOOK, BOOK_ORDER, BOOK_SCORES),
            (ContentType.TV_SHOW, TV_ORDER, TV_SCORES),
            (ContentType.VIDEO_GAME, GAME_ORDER, GAME_SCORES),
        ):
            recommendations = shuffled_engine.generate_recommendations(
                content_type=content_type, count=20
            )

            assert tuple(_titles(recommendations)) == order
            assert _scores(recommendations) == pytest.approx(
                list(scores), abs=SCORE_TOLERANCE
            )

    def test_ordering_is_independent_of_the_shuffle(self, engine, monkeypatch):
        """The engine shuffles reference items; ranking must not move with it."""
        monkeypatch.setattr(
            engine_module.random, "shuffle", lambda sequence: sequence.reverse()
        )

        recommendations = engine.generate_recommendations(
            content_type=ContentType.MOVIE, count=20
        )

        assert tuple(_titles(recommendations)) == MOVIE_ORDER
        assert _scores(recommendations) == pytest.approx(
            list(MOVIE_SCORES), abs=SCORE_TOLERANCE
        )

    def test_generating_does_not_mutate_the_library(self, engine):
        """Every test shares one module-level library, so a write would leak.

        TV expansion and series tracking both derive from the library items,
        and a baseline that depended on which test ran first would be worse
        than no baseline.
        """
        before = copy.deepcopy(LIBRARY)

        for content_type in ContentType:
            engine.generate_recommendations(content_type=content_type, count=20)

        assert LIBRARY == before

    def test_no_storage_call_is_answered_by_the_mock(self, engine):
        """The baselines come from library data, never from a stub return value.

        Only three accessors are modelled. A fourth storage read would be
        answered by the spec'd mock with a Mock instance, and the baselines
        would quietly start characterising that instead.
        """
        engine.generate_recommendations(content_type=ContentType.MOVIE, count=20)

        assert [call[0] for call in engine.storage.method_calls] == [
            "get_signal_items",
            "get_completed_items",
            "get_signal_items",
            "get_unconsumed_items",
        ]


class TestBaselineSensitivity:
    """A baseline nothing can break is not characterising anything."""

    def test_moving_one_scorer_weight_breaks_the_movie_baseline(self):
        """The same library and call, with genre matching weighted differently.

        Everything above asserts the numbers stay put. This asserts they can
        move, so a green run is evidence the scoring is unchanged rather than
        evidence the assertions are slack.
        """
        reweighted = RecommendationEngine(
            storage_manager=_library_storage(LIBRARY),
            embedding_generator=None,
            recommendation_generator=None,
            min_rating=4,
            scorers=build_scorers_with_overrides(
                list(DEFAULT_SCORERS), {"genre_match": 8.0}
            ),
        )

        recommendations = reweighted.generate_recommendations(
            content_type=ContentType.MOVIE, count=20
        )

        assert _scores(recommendations) != pytest.approx(
            list(MOVIE_SCORES), abs=SCORE_TOLERANCE
        )


class TestHarnessMatchesAFreshInstall:
    """The engine under test must be configured the way a real one is.

    ``create_recommendation_engine`` builds the pipeline from the global
    settings registry, whose const defaults are what a fresh install runs on.
    The harness builds the engine directly, so the two agree only for as long
    as each scorer's class default equals its registry default. Should they
    diverge, these baselines would describe a configuration nobody runs.
    """

    def test_every_scorer_weight_matches_its_registry_default(self, engine):
        """Each pipeline scorer carries the weight a fresh install gives it."""
        config_key_of = {
            scorer_class: name for name, scorer_class in SCORER_NAME_MAP.items()
        }

        checked = 0
        for scorer in engine.pipeline.scorers:
            entry = get_entry(
                f"recommendations.scorer_weights.{config_key_of[type(scorer)]}"
            )
            assert entry is not None
            assert scorer.weight == entry.default
            checked += 1

        assert checked == len(engine.pipeline.scorers)

    def test_min_rating_matches_the_registry_default(self, engine):
        """The preference analyzer's rating floor is the fresh-install one."""
        entry = get_entry("recommendations.min_rating_for_preference")

        assert entry is not None
        assert engine.preference_analyzer.min_rating == entry.default


class TestFakeStorageMatchesReal:
    """The fake library must not drift from the storage it stands in for."""

    @pytest.mark.parametrize(
        "accessor",
        ["get_completed_items", "get_signal_items", "get_unconsumed_items"],
    )
    def test_fake_accessor_matches_the_real_parameters(self, accessor):
        """Every parameter the fake declares exists on StorageManager, same default.

        A baseline taken through a fake that filters differently from the real
        accessor characterises the fake, not the product.
        """
        real = inspect.signature(getattr(StorageManager, accessor)).parameters
        fake = inspect.signature(
            getattr(_library_storage(()), accessor).side_effect
        ).parameters

        assert set(fake) <= set(real)
        for name, parameter in fake.items():
            assert parameter.default == real[name].default


class TestEmptyCandidates:
    """The paths where there is nothing to rank."""

    def test_no_candidates_returns_empty(self):
        """A library with nothing unconsumed yields no recommendations."""
        consumed_only = [item for item in LIBRARY if item.status == _COMPLETED]

        assert (
            _engine_over(consumed_only).generate_recommendations(
                content_type=ContentType.BOOK, count=5
            )
            == []
        )

    def test_no_consumed_items_returns_empty(self):
        """With no taste signal at all there is nothing to rank against."""
        unconsumed_only = [item for item in LIBRARY if item.status != _COMPLETED]

        assert (
            _engine_over(unconsumed_only).generate_recommendations(
                content_type=ContentType.BOOK, count=5
            )
            == []
        )

    def test_every_candidate_ignored_returns_empty(self):
        """Ignoring every unconsumed item leaves nothing to rank.

        The taste signal is untouched here, so this is the one empty path the
        user can reach from a full library: candidates exist, and the ignore
        flag removes all of them before scoring.
        """
        ignored_candidates = [
            (
                item.model_copy(update={"ignored": True})
                if item.status != _COMPLETED
                else item
            )
            for item in LIBRARY
        ]

        assert (
            _engine_over(ignored_candidates).generate_recommendations(
                content_type=ContentType.BOOK, count=5
            )
            == []
        )

    def test_empty_library_returns_empty(self):
        """An empty library takes the no-signal path rather than raising."""
        assert (
            _engine_over(()).generate_recommendations(
                content_type=ContentType.BOOK, count=5
            )
            == []
        )
