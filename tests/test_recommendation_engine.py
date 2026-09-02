import random
from datetime import date
from unittest.mock import Mock

import pytest

from src.models.content import (
    ConsumptionStatus,
    ContentItem,
    ContentType,
    get_enum_value,
)
from src.models.user_preferences import UserPreferenceConfig
from src.recommendations.engine import (
    RecommendationEngine,
    _collapse_duplicate_db_ids,
)
from src.recommendations.identity import candidate_key
from src.recommendations.preferences import PreferenceAnalyzer, UserPreferences
from src.recommendations.record import Recommendation
from src.recommendations.reference_index import SignalIndex, _shuffle_close_scores
from src.recommendations.scorers import SCORER_NAME_MAP
from src.recommendations.scoring_pipeline import ScoredCandidate
from src.recommendations.variety import (
    VARIETY_LADDER_STEPS,
    VARIETY_SERIES_CONTINUATION_FACTOR,
    VARIETY_TOP_PENALTY,
)
from src.storage.item_merges import MergeEvidence
from src.storage.manager import StorageManager
from src.utils.series import (
    expand_tv_shows_to_seasons,
    get_series_item_number,
    get_series_name,
)
from tests.factories import make_item, make_storage_mock


@pytest.fixture
def mock_storage():
    """``get_signal_items`` (completed, rated, not ignored) and
    ``get_consumption_items`` (not ignored, rating irrelevant) both mirror the
    real accessors by filtering whatever ``get_completed_items`` a test sets up."""
    storage = make_storage_mock()

    # Each fake narrows get_completed_items itself, the way the real accessors
    # do, so a call recorded on one is a call the engine made and not one its
    # sibling fake made on its behalf.
    def consumption(user_id=None, content_type=None, limit=None, **kwargs):
        return [
            item
            for item in storage.get_completed_items(
                user_id=user_id, content_type=content_type, limit=limit, **kwargs
            )
            if not item.ignored
        ]

    def signal(user_id=None, content_type=None, limit=None, **kwargs):
        return [
            item
            for item in storage.get_completed_items(
                user_id=user_id, content_type=content_type, limit=limit, **kwargs
            )
            if not item.ignored and item.rating is not None
        ]

    storage.get_consumption_items = Mock(side_effect=consumption)
    storage.get_signal_items = Mock(side_effect=signal)
    return storage


@pytest.fixture
def engine(mock_storage):
    return RecommendationEngine(storage_manager=mock_storage, min_rating=4)


@pytest.fixture
def real_storage(tmp_path):
    return StorageManager(tmp_path / "engine_signal.db")


@pytest.fixture
def real_engine(real_storage):
    return RecommendationEngine(storage_manager=real_storage, min_rating=4)


def _engine_for_helpers(rng: random.Random | None = None) -> RecommendationEngine:
    """``__init__`` wants a storage manager these helpers never touch, so it is
    skipped and only the attributes they read are set."""
    engine = RecommendationEngine.__new__(RecommendationEngine)
    engine.rng = rng if rng is not None else random.Random()
    return engine


def _save_book(
    storage,
    *,
    item_id,
    title,
    status,
    rating=None,
    ignored=False,
    genre="Science Fiction",
    date_completed=None,
    metadata=None,
):
    db_id = storage.save_content_item(
        ContentItem(
            id=item_id,
            title=title,
            content_type=ContentType.BOOK,
            status=status,
            rating=rating,
            metadata={"genre": genre, **(metadata or {})},
            date_completed=date_completed,
        )
    )
    if ignored:
        storage.set_item_ignored(db_id, True)
    return db_id


class TestSingleWeightingStageRegression:
    """Bug reported: turning genre or creator matching down left the signal partly
    on, and a preferred director never lifted a film the way a preferred author
    lifted a book."""

    @staticmethod
    def _scores(engine, config):
        return {
            rec.item.title: rec.score
            for rec in engine.generate_recommendations(
                content_type=ContentType.MOVIE, count=5, user_preference_config=config
            )
        }

    @staticmethod
    def _only(scorer_name):
        return UserPreferenceConfig(
            scorer_weights={key: 0.0 for key in SCORER_NAME_MAP if key != scorer_name}
        )

    def test_one_enabled_scorer_is_the_whole_score_regression(
        self, engine, mock_storage
    ):
        """Any residual term from a second weighting stage would show up as a
        difference between the two, whatever its sign."""
        loved = ContentItem(
            id="loved",
            title="Arrival",
            content_type=ContentType.MOVIE,
            status=ConsumptionStatus.COMPLETED,
            rating=5,
            metadata={"genre": "Science Fiction"},
        )
        candidate = ContentItem(
            id="candidate",
            title="Solaris",
            content_type=ContentType.MOVIE,
            status=ConsumptionStatus.UNREAD,
            metadata={"genre": "Science Fiction"},
        )
        mock_storage.get_completed_items = Mock(
            side_effect=lambda content_type=None, **kwargs: [loved]
        )
        mock_storage.get_unconsumed_items = Mock(return_value=[candidate])

        recs = engine.generate_recommendations(
            content_type=ContentType.MOVIE,
            count=5,
            user_preference_config=self._only("genre_match"),
        )

        assert recs[0].score_breakdown["genre_match"] == pytest.approx(1.0)
        assert recs[0].score == pytest.approx(recs[0].score_breakdown["genre_match"])

    def test_a_preferred_director_scores_on_a_movie_regression(
        self, engine, mock_storage
    ):
        """The two candidates share their genre and differ only in their creator,
        and creator matching is the only scorer left enabled."""
        loved = ContentItem(
            id="loved",
            title="Arrival",
            author="Denis Villeneuve",
            content_type=ContentType.MOVIE,
            status=ConsumptionStatus.COMPLETED,
            rating=5,
            metadata={"genre": "Science Fiction"},
        )
        same_director = ContentItem(
            id="same",
            title="Dune",
            author="Denis Villeneuve",
            content_type=ContentType.MOVIE,
            status=ConsumptionStatus.UNREAD,
            metadata={"genre": "Science Fiction"},
        )
        other_director = ContentItem(
            id="other",
            title="Solaris",
            author="Steven Soderbergh",
            content_type=ContentType.MOVIE,
            status=ConsumptionStatus.UNREAD,
            metadata={"genre": "Science Fiction"},
        )
        mock_storage.get_completed_items = Mock(
            side_effect=lambda content_type=None, **kwargs: [loved]
        )
        mock_storage.get_unconsumed_items = Mock(
            return_value=[same_director, other_director]
        )

        scores = self._scores(engine, self._only("creator_match"))

        assert scores["Dune"] > scores["Solaris"]


class TestScoringPipeline:
    def test_cold_start_returns_empty(self, engine, mock_storage):
        mock_storage.get_completed_items = Mock(return_value=[])

        recommendations = engine.generate_recommendations(
            content_type=ContentType.BOOK, count=5
        )

        assert recommendations == []

    def test_end_to_end_scoring_and_sorting(self, engine, mock_storage):
        consumed_items = [
            ContentItem(
                id="c1",
                title="Foundation",
                author="Isaac Asimov",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.COMPLETED,
                rating=5,
                metadata={"genre": "Science Fiction"},
            ),
            ContentItem(
                id="c2",
                title="Neuromancer",
                author="William Gibson",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.COMPLETED,
                rating=5,
                metadata={"genre": "Science Fiction"},
            ),
        ]

        unconsumed_items = [
            ContentItem(
                id="u1",
                title="Left Hand of Darkness",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.UNREAD,
                metadata={"genre": "Science Fiction"},
            ),
            ContentItem(
                id="u2",
                title="Pride and Prejudice",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.UNREAD,
                metadata={"genre": "Romance"},
            ),
            ContentItem(
                id="u3",
                title="Dracula",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.UNREAD,
                metadata={"genre": "Horror"},
            ),
        ]

        mock_storage.get_completed_items = Mock(
            side_effect=lambda content_type=None, **kwargs: (
                consumed_items if content_type is None else consumed_items
            )
        )
        mock_storage.get_unconsumed_items = Mock(return_value=unconsumed_items)

        recommendations = engine.generate_recommendations(
            content_type=ContentType.BOOK, count=3
        )

        assert len(recommendations) == 3
        assert recommendations[0].item.title == "Left Hand of Darkness"
        for rec in recommendations:
            assert rec.score > 0.0
            assert rec.reasoning


class TestCustomRulesIntegration:
    def test_multiple_custom_rules(self, engine, mock_storage):
        consumed = ContentItem(
            id="1",
            title="Consumed",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            rating=4,
            metadata={"genre": "Drama"},
        )
        items = [
            ContentItem(
                id="2",
                title="Horror Book",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.UNREAD,
                metadata={"genre": "horror"},
            ),
            ContentItem(
                id="3",
                title="Comedy Book",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.UNREAD,
                metadata={"genre": "comedy"},
            ),
            ContentItem(
                id="4",
                title="Sci-Fi Book",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.UNREAD,
                metadata={"genre": "science fiction"},
            ),
        ]

        mock_storage.get_completed_items = Mock(
            side_effect=lambda content_type=None, **kwargs: [consumed]
        )
        mock_storage.get_unconsumed_items = Mock(return_value=items)

        user_config = UserPreferenceConfig(
            custom_rules=["avoid horror", "prefer sci-fi"]
        )
        recommendations = engine.generate_recommendations(
            content_type=ContentType.BOOK,
            count=3,
            user_preference_config=user_config,
        )
        titles = [rec.item.title for rec in recommendations]
        assert titles[0] == "Sci-Fi Book"
        assert titles[-1] == "Horror Book"


class TestTwinnedSeriesEntryRegression:
    """Reported: a run offered book #2 while #1 sat unread, both rows counting."""

    @pytest.mark.parametrize("position_key", ["series_position", "series_index"])
    def test_a_merged_twin_takes_one_place_in_its_series_regression(
        self, real_engine, real_storage, position_key
    ):
        """Calibre-Web writes ``series_index``, the providers ``series_position``."""
        _save_book(
            real_storage,
            item_id="taste",
            title="Ancillary Justice",
            status=ConsumptionStatus.COMPLETED,
            rating=5,
        )
        calibre_id = _save_book(
            real_storage,
            item_id="calibre-1",
            title="All Systems Red: A Murderbot Novella",
            status=ConsumptionStatus.UNREAD,
            metadata={"series": "The Murderbot Diaries", position_key: 1},
        )
        goodreads_id = _save_book(
            real_storage,
            item_id="goodreads-1",
            title="All Systems Red",
            status=ConsumptionStatus.UNREAD,
            metadata={"series": "The Murderbot Diaries", "series_index": 1},
        )
        for position, title in ((2, "Artificial Condition"), (3, "Rogue Protocol")):
            _save_book(
                real_storage,
                item_id=f"goodreads-{position}",
                title=f"{title} (The Murderbot Diaries, #{position})",
                status=ConsumptionStatus.UNREAD,
            )

        real_storage.merge_content_items(calibre_id, goodreads_id, MergeEvidence.MANUAL)

        recommendations = real_engine.generate_recommendations(
            content_type=ContentType.BOOK, count=100
        )
        assert [
            get_series_item_number(rec.item)
            for rec in recommendations
            if get_series_name(rec.item) == "The Murderbot Diaries"
        ] == [1.0]


class TestIgnoredItems:
    def test_ignored_items_filtered_from_recommendations(
        self, real_engine, real_storage
    ):
        _save_book(
            real_storage,
            item_id="1",
            title="Consumed Book",
            status=ConsumptionStatus.COMPLETED,
            rating=5,
        )
        _save_book(
            real_storage,
            item_id="2",
            title="Normal Book",
            status=ConsumptionStatus.UNREAD,
        )
        _save_book(
            real_storage,
            item_id="3",
            title="Ignored Book",
            status=ConsumptionStatus.UNREAD,
            ignored=True,
        )

        recommendations = real_engine.generate_recommendations(
            content_type=ContentType.BOOK,
            count=5,
        )

        recommended_titles = [rec.item.title for rec in recommendations]

        assert "Normal Book" in recommended_titles

        assert "Ignored Book" not in recommended_titles


class TestTvRecommendationCarriesDbIdRegression:
    """Bug reported: TV show recommendations rendered without the "Mark complete"
    and "Ignore" buttons."""

    @pytest.fixture
    def breaking_bad_consumed(self) -> ContentItem:
        return ContentItem(
            id="1",
            db_id=1,
            title="Breaking Bad",
            content_type=ContentType.TV_SHOW,
            status=ConsumptionStatus.COMPLETED,
            rating=5,
            metadata={"genres": ["Drama", "Crime"]},
        )

    @pytest.fixture
    def expanse_show(self) -> ContentItem:
        return ContentItem(
            id="tvdb:280619",
            db_id=42,
            title="The Expanse",
            content_type=ContentType.TV_SHOW,
            status=ConsumptionStatus.UNREAD,
            metadata={"total_seasons": 3, "genres": ["Drama", "Sci-Fi"]},
        )

    def test_tv_show_recommendation_has_non_null_db_id_regression(
        self, engine, mock_storage, breaking_bad_consumed, expanse_show
    ) -> None:
        mock_storage.get_completed_items = Mock(
            side_effect=lambda content_type=None, **kwargs: [breaking_bad_consumed]
        )
        mock_storage.get_unconsumed_items = Mock(return_value=[expanse_show])

        recommendations = engine.generate_recommendations(
            content_type=ContentType.TV_SHOW,
            count=5,
        )

        # The series rules surface only the next-unwatched season, so the show
        # yields exactly one actionable card carrying the show's db_id.
        assert len(recommendations) == 1
        assert recommendations[0].item.db_id == 42

    def test_next_unwatched_season_carries_parent_db_id_regression(
        self, engine, mock_storage, breaking_bad_consumed
    ) -> None:
        """Edge case: when ``seasons_watched`` already contains season 1, the engine
        should surface season 2 as the next actionable card."""
        unconsumed_show = ContentItem(
            id="tvdb:280619",
            db_id=42,
            title="The Expanse",
            content_type=ContentType.TV_SHOW,
            status=ConsumptionStatus.UNREAD,
            metadata={
                "total_seasons": 3,
                "seasons_watched": [1],
                "genres": ["Drama", "Sci-Fi"],
            },
        )

        mock_storage.get_completed_items = Mock(
            side_effect=lambda content_type=None, **kwargs: [breaking_bad_consumed]
        )
        mock_storage.get_unconsumed_items = Mock(return_value=[unconsumed_show])

        recommendations = engine.generate_recommendations(
            content_type=ContentType.TV_SHOW,
            count=5,
        )

        assert len(recommendations) == 1
        rec_item = recommendations[0].item
        assert rec_item.title == "The Expanse (Season 2)"
        assert rec_item.db_id == 42

    def test_series_in_order_false_collapses_seasons_to_one_card_regression(
        self, engine, mock_storage, breaking_bad_consumed, expanse_show
    ) -> None:
        """The frontend keys cards and targets Mark-complete / Ignore actions by
        ``db_id``, so those co-occurring cards would collide."""
        mock_storage.get_completed_items = Mock(
            side_effect=lambda content_type=None, **kwargs: [breaking_bad_consumed]
        )
        mock_storage.get_unconsumed_items = Mock(return_value=[expanse_show])

        recommendations = engine.generate_recommendations(
            content_type=ContentType.TV_SHOW,
            count=5,
            user_preference_config=UserPreferenceConfig(series_in_order=False),
        )

        # The three expanded seasons collapse to a single actionable card that
        # carries the parent show's db_id.
        assert len(recommendations) == 1
        rec_item = recommendations[0].item
        assert rec_item.db_id == 42
        # The survivor's id is asserted with ``in {season ids}`` rather than a
        # specific season because the three seasons score identically and pass
        # through ``_shuffle_close_scores``, so which one survives is
        # non-deterministic at this integration level.
        assert rec_item.id in {"tvdb:280619:s1", "tvdb:280619:s2", "tvdb:280619:s3"}

    def test_series_in_order_false_keeps_distinct_shows_and_backfills_regression(
        self, engine, mock_storage, breaking_bad_consumed, expanse_show
    ) -> None:
        """With series order off, two multi-season shows each collapse to one card
        (so different shows still appear), and the freed slots are backfilled by the
        other show rather than left empty."""
        foundation = ContentItem(
            id="tvdb:355567",
            db_id=99,
            title="Foundation",
            content_type=ContentType.TV_SHOW,
            status=ConsumptionStatus.UNREAD,
            metadata={"total_seasons": 2, "genres": ["Drama", "Sci-Fi"]},
        )

        mock_storage.get_completed_items = Mock(
            side_effect=lambda content_type=None, **kwargs: [breaking_bad_consumed]
        )
        mock_storage.get_unconsumed_items = Mock(
            return_value=[expanse_show, foundation]
        )

        recommendations = engine.generate_recommendations(
            content_type=ContentType.TV_SHOW,
            count=5,
            user_preference_config=UserPreferenceConfig(series_in_order=False),
        )

        # Both distinct shows survive, each exactly once, despite five expanded
        # seasons between them.
        db_ids = sorted(rec.item.db_id for rec in recommendations)
        assert db_ids == [42, 99]

    def test_fallback_collapses_entries_sharing_db_id_regression(self, engine) -> None:
        """For TV the fallback builds recs directly from the expanded season items,
        which share their parent show's ``db_id``."""
        season_one = ContentItem(
            id="tvdb:280619:s1",
            db_id=42,
            title="The Expanse",
            content_type=ContentType.TV_SHOW,
            status=ConsumptionStatus.UNREAD,
            metadata={"genres": ["Drama", "Sci-Fi"]},
        )
        season_two = ContentItem(
            id="tvdb:280619:s2",
            db_id=42,
            title="The Expanse",
            content_type=ContentType.TV_SHOW,
            status=ConsumptionStatus.UNREAD,
            metadata={"genres": ["Drama", "Sci-Fi"]},
        )
        other_show = ContentItem(
            id="tvdb:355567:s1",
            db_id=99,
            title="Foundation",
            content_type=ContentType.TV_SHOW,
            status=ConsumptionStatus.UNREAD,
            metadata={"genres": ["Drama", "Sci-Fi"]},
        )

        recommendations = engine._build_fallback_recommendations(
            [season_one, season_two, other_show],
            series_tracking={},
            count=5,
        )

        # The two seasons of one show collapse to its first occurrence; the
        # distinct show is preserved.
        db_ids = [rec.item.db_id for rec in recommendations]
        assert db_ids == [42, 99]
        assert recommendations[0].item.id == "tvdb:280619:s1"


class TestCollapseDuplicateDbIds:
    """The engine calls it on the already-ranked (descending) list, so "first" means
    "highest-ranked"."""

    def test_keeps_first_occurrence_among_duplicates_preserving_order(self) -> None:
        entries = [
            (42, "expanse-s2"),  # highest-ranked season of show 42
            (99, "foundation-s1"),
            (42, "expanse-s1"),  # lower-ranked duplicate of show 42 -> dropped
            (99, "foundation-s2"),  # lower-ranked duplicate of show 99 -> dropped
            (7, "standalone"),
        ]

        collapsed = _collapse_duplicate_db_ids(entries, lambda entry: entry[0])

        # Only the first occurrence of each db_id is kept, original order intact.
        assert collapsed == [
            (42, "expanse-s2"),
            (99, "foundation-s1"),
            (7, "standalone"),
        ]

    def test_none_db_ids_are_never_collapsed_together(self) -> None:
        """A missing db_id must not act as a collapse key, otherwise distinct
        recommendations that happen to lack a db_id would silently drop to one."""
        entries = [
            (None, "no-id-a"),
            (None, "no-id-b"),
            (5, "has-id"),
            (None, "no-id-c"),
        ]

        collapsed = _collapse_duplicate_db_ids(entries, lambda entry: entry[0])

        # All three None entries survive alongside the single id'd entry.
        assert collapsed == entries


class TestContributingReferenceItemsRegression:
    def test_references_include_all_types_and_same_type_first_regression(
        self,
    ) -> None:
        """Bug reported: All TV show recommendations said "Recommended because you
        liked 'Firewatch'" (a video game) because it had the highest genre overlap
        and dominated every reference list."""
        engine = _engine_for_helpers()

        candidate = ContentItem(
            id="breaking_bad",
            title="Breaking Bad",
            content_type=ContentType.TV_SHOW,
            status=ConsumptionStatus.UNREAD,
            metadata={"genres": ["Drama", "Crime", "Thriller"]},
        )

        consumed_tv = ContentItem(
            id="the_wire",
            title="The Wire",
            content_type=ContentType.TV_SHOW,
            status=ConsumptionStatus.COMPLETED,
            rating=5,
            metadata={"genres": ["Drama", "Crime"]},
        )
        consumed_game = ContentItem(
            id="firewatch",
            title="Firewatch",
            content_type=ContentType.VIDEO_GAME,
            status=ConsumptionStatus.COMPLETED,
            rating=5,
            metadata={"genres": ["Drama", "Adventure"]},
        )
        consumed_book = ContentItem(
            id="gone_girl",
            title="Gone Girl",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            rating=5,
            metadata={"genres": ["Thriller", "Crime"]},
        )

        result = SignalIndex(
            [consumed_game, consumed_tv, consumed_book]
        ).references_for(candidate, engine.rng)

        result_types = {get_enum_value(item.content_type) for item in result}
        assert "tv_show" in result_types
        assert "video_game" in result_types
        assert "book" in result_types

        assert get_enum_value(result[0].content_type) == "tv_show"


class TestCrossTypeClusterOverlapRegression:
    """Bug reported: "1923" (a TV show with only "Drama" as its genre) appeared as
    a cross-type reference for nearly every recommendation, because raw Jaccard
    on ["drama"] gave ~0.2 overlap with almost anything."""

    def test_1923_different_shows_get_different_references_regression(self) -> None:
        """Bug: "1923" (genre: ["Drama"]) was cited for every recommendation."""
        engine = _engine_for_helpers()

        # "1923" has only Drama — should not match sci-fi thematically
        show_1923 = ContentItem(
            id="1923",
            title="1923",
            content_type=ContentType.TV_SHOW,
            status=ConsumptionStatus.COMPLETED,
            rating=4,
            metadata={"genres": ["Drama"]},
        )

        # A sci-fi consumed item — should match sci-fi candidates
        sci_fi_consumed = ContentItem(
            id="expanse",
            title="The Expanse",
            content_type=ContentType.TV_SHOW,
            status=ConsumptionStatus.COMPLETED,
            rating=5,
            metadata={"genres": ["Science Fiction", "Drama"]},
        )

        # Candidate is a sci-fi book
        sci_fi_candidate = ContentItem(
            id="dune",
            title="Dune",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
            metadata={"genres": ["Science Fiction", "Adventure"]},
        )

        references = SignalIndex([show_1923, sci_fi_consumed]).references_for(
            sci_fi_candidate, engine.rng
        )

        reference_titles = [ref.title for ref in references]
        assert "The Expanse" in reference_titles
        assert "1923" not in reference_titles

    def test_cross_type_uses_thematic_matching_regression(self) -> None:
        """Bug: Cross-type matching used raw Jaccard, making any "Drama" show a
        valid reference for any candidate with "Drama" in its genres."""
        engine = _engine_for_helpers()

        candidate = ContentItem(
            id="war_book",
            title="Band of Brothers: The Book",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
            metadata={"genres": ["War", "Historical"]},
        )

        war_tv = ContentItem(
            id="bob_tv",
            title="Band of Brothers",
            content_type=ContentType.TV_SHOW,
            status=ConsumptionStatus.COMPLETED,
            rating=5,
            metadata={"genres": ["War", "Drama"]},
        )

        drama_tv = ContentItem(
            id="crown",
            title="The Crown",
            content_type=ContentType.TV_SHOW,
            status=ConsumptionStatus.COMPLETED,
            rating=4,
            metadata={"genres": ["Drama"]},
        )

        references = SignalIndex([drama_tv, war_tv]).references_for(
            candidate, engine.rng
        )

        reference_titles = [ref.title for ref in references]
        assert "Band of Brothers" in reference_titles
        assert "The Crown" not in reference_titles


class TestReasoningFormatting:
    """Every reference the engine credited is named in the reasoning."""

    def _make_engine(self) -> RecommendationEngine:
        return _engine_for_helpers()

    def _make_empty_preferences(self) -> UserPreferences:
        return PreferenceAnalyzer(min_rating=4).analyze([])

    def test_a_lone_reference_is_named(self) -> None:
        engine = self._make_engine()

        reasoning = engine._generate_reasoning(
            item=ContentItem(
                id="candidate",
                title="Hyperion",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.UNREAD,
            ),
            preferences=self._make_empty_preferences(),
            adaptations=[],
            contributing_items=[
                ContentItem(
                    id="ref",
                    title="Dune",
                    content_type=ContentType.BOOK,
                    status=ConsumptionStatus.COMPLETED,
                    rating=5,
                )
            ],
        )

        assert "Dune" in reasoning
        # The grouped fallback names the same title, so only its shape tells
        # the branches apart: grouped is always multi-line, lone always one.
        assert "\n" not in reasoning

    def test_multiple_items_still_use_grouped_format(self) -> None:
        engine = self._make_engine()
        preferences = self._make_empty_preferences()

        item = ContentItem(
            id="candidate",
            title="Hyperion",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
        )
        ref_a = ContentItem(
            id="ref_a",
            title="Dune",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            rating=5,
        )
        ref_b = ContentItem(
            id="ref_b",
            title="Foundation",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            rating=5,
        )

        reasoning = engine._generate_reasoning(
            item=item,
            preferences=preferences,
            adaptations=[],
            contributing_items=[ref_a, ref_b],
        )

        assert "Dune" in reasoning
        assert "Foundation" in reasoning


class TestContributingReferenceRatingFloorRegression:
    """Bug reported: 'The Crown' rated 1 appeared as 'you liked' in recommendation
    reasoning."""

    def test_low_rated_items_excluded_from_contributing_references_regression(
        self,
    ) -> None:
        engine = _engine_for_helpers()

        candidate = ContentItem(
            id="peaky",
            title="Peaky Blinders",
            content_type=ContentType.TV_SHOW,
            status=ConsumptionStatus.UNREAD,
            metadata={"genres": ["Drama", "Crime"]},
        )

        disliked_item = ContentItem(
            id="the_crown",
            title="The Crown",
            content_type=ContentType.TV_SHOW,
            status=ConsumptionStatus.COMPLETED,
            rating=1,
            metadata={"genres": ["Drama", "Historical"]},
        )

        liked_item = ContentItem(
            id="the_wire",
            title="The Wire",
            content_type=ContentType.TV_SHOW,
            status=ConsumptionStatus.COMPLETED,
            rating=5,
            metadata={"genres": ["Drama", "Crime"]},
        )

        result = SignalIndex([disliked_item, liked_item]).references_for(
            candidate, engine.rng
        )

        result_titles = [item.title for item in result]
        assert (
            "The Crown" not in result_titles
        ), "Items rated 1 should never appear as 'you liked' references"
        assert "The Wire" in result_titles

    def test_unrated_items_included_in_contributing_references(self) -> None:
        engine = _engine_for_helpers()

        candidate = ContentItem(
            id="breaking_bad",
            title="Breaking Bad",
            content_type=ContentType.TV_SHOW,
            status=ConsumptionStatus.UNREAD,
            metadata={"genres": ["Drama", "Crime"]},
        )

        unrated_item = ContentItem(
            id="the_sopranos",
            title="The Sopranos",
            content_type=ContentType.TV_SHOW,
            status=ConsumptionStatus.COMPLETED,
            rating=None,
            metadata={"genres": ["Drama", "Crime"]},
        )

        result = SignalIndex([unrated_item]).references_for(candidate, engine.rng)

        result_titles = [item.title for item in result]
        assert (
            "The Sopranos" in result_titles
        ), "Unrated items should be included (benefit of the doubt)"


class TestSameTypeLimitRegression:
    """Bug: Up to 5 same-type items were shown as references; user wants max 3."""

    def test_same_type_limit_capped_at_3(self) -> None:
        engine = _engine_for_helpers()

        candidate = ContentItem(
            id="candidate",
            title="New Show",
            content_type=ContentType.TV_SHOW,
            status=ConsumptionStatus.UNREAD,
            metadata={"genres": ["Drama", "Crime"]},
        )

        consumed_items = [
            ContentItem(
                id=f"show_{index}",
                title=f"Show {index}",
                content_type=ContentType.TV_SHOW,
                status=ConsumptionStatus.COMPLETED,
                rating=5,
                metadata={"genres": ["Drama", "Crime"]},
            )
            for index in range(6)
        ]

        result = SignalIndex(consumed_items).references_for(candidate, engine.rng)

        same_type_items = [
            item for item in result if get_enum_value(item.content_type) == "tv_show"
        ]
        assert (
            len(same_type_items) <= 3
        ), f"Expected at most 3 same-type references, got {len(same_type_items)}"


class TestShuffleCloseScores:
    def test_mixed_groups_high_items_always_first(self) -> None:
        top = ContentItem(
            id="top",
            title="Top",
            content_type=ContentType.VIDEO_GAME,
            status=ConsumptionStatus.COMPLETED,
        )
        close_items = [
            ContentItem(
                id=f"close_{index}",
                title=f"Close {index}",
                content_type=ContentType.VIDEO_GAME,
                status=ConsumptionStatus.COMPLETED,
            )
            for index in range(3)
        ]
        scored: list[tuple[ContentItem, float]] = [
            (top, 0.9),
            (close_items[0], 0.5),
            (close_items[1], 0.48),
            (close_items[2], 0.47),
        ]

        rng = random.Random()
        for _ in range(20):
            result = _shuffle_close_scores(scored, rng)
            assert result[0] == top
            assert {item.id for item in result[1:]} == {
                "close_0",
                "close_1",
                "close_2",
            }


class TestSeededReferenceOrderRegression:
    """Symptom: two identical recommendation runs listed the contributing reference
    items in different orders, so a user asking "why does it say this" got an
    answer nobody could reproduce."""

    @staticmethod
    def _reference_ids(storage, seed: int | None = None) -> list[str]:
        """The three consumed books share the candidate's only genre and their
        rating, so they score identically and land in one shuffle group."""
        engine = RecommendationEngine(
            storage_manager=storage,
            min_rating=4,
            rng=random.Random(seed) if seed is not None else None,
        )
        candidate = ContentItem(
            id="candidate",
            title="Candidate",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
            metadata={"genres": ["Science Fiction"]},
        )
        consumed = [
            ContentItem(
                id=f"book_{letter}",
                title=f"Book {letter.upper()}",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.COMPLETED,
                rating=5,
                metadata={"genres": ["Science Fiction"]},
            )
            for letter in ("a", "b", "c")
        ]
        references = SignalIndex(consumed).references_for(candidate, engine.rng)
        return [item.id for item in references]

    def test_seeded_engine_repeats_a_pinned_reference_order(self, mock_storage) -> None:
        expected = ["book_c", "book_a", "book_b"]

        assert self._reference_ids(mock_storage, seed=7) == expected
        assert self._reference_ids(mock_storage, seed=7) == expected

    def test_reference_order_follows_the_seed(self, mock_storage) -> None:
        """A fix that dropped the shuffle instead of injecting the rng would satisfy
        the pinned order above, because one fixed order repeats too."""
        orders = {
            tuple(self._reference_ids(mock_storage, seed=seed)) for seed in range(20)
        }

        assert len(orders) > 1

    def test_seeded_engines_repeat_the_whole_explanation(self, mock_storage) -> None:
        """The shuffle is only reproducible if it is reproducible where users read
        it, so this drives ``generate_recommendations`` end to end rather than the
        reference helper alone."""
        candidate = ContentItem(
            id="candidate",
            title="Foundation",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
            metadata={"genres": ["Science Fiction"]},
        )
        consumed = [
            ContentItem(
                id=f"book_{letter}",
                title=f"Book {letter.upper()}",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.COMPLETED,
                rating=5,
                metadata={"genres": ["Science Fiction"]},
            )
            for letter in ("a", "b", "c")
        ]
        mock_storage.get_completed_items = Mock(
            side_effect=lambda content_type=None, **kwargs: consumed
        )
        mock_storage.get_unconsumed_items = Mock(return_value=[candidate])

        runs = [
            RecommendationEngine(
                storage_manager=mock_storage,
                min_rating=4,
                rng=random.Random(11),
            ).generate_recommendations(content_type=ContentType.BOOK, count=1)[0]
            for _ in range(2)
        ]

        assert [item.id for item in runs[0].contributing_items] == [
            item.id for item in runs[1].contributing_items
        ]
        assert {item.id for item in runs[0].contributing_items} == {
            "book_a",
            "book_b",
            "book_c",
        }
        assert runs[0].reasoning == runs[1].reasoning


def _variety_score_for(recs: list[Recommendation], item_id: str) -> float:
    for rec in recs:
        if rec.item.id == item_id:
            return rec.score
    return 0.0


def _variety_rank_of(recs: list[Recommendation], item_id: str) -> int:
    for index, rec in enumerate(recs):
        if rec.item.id == item_id:
            return index
    return len(recs)


class TestVarietyAfterCompletion:
    """The engine divides it by ``MAX_VARIETY_PENALTY`` to get the ladder's top
    penalty fraction, so a preference of 4.0 yields the legacy 0.8 top fraction."""

    def test_variety_penalty_demotes_recently_finished_genre(
        self, engine, mock_storage
    ) -> None:
        consumed = ContentItem(
            id="consumed_1",
            title="Dune",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            rating=5,
            date_completed=date(2026, 1, 1),
            metadata={"genres": ["Science Fiction"]},
        )
        same_genre = ContentItem(
            id="same_genre",
            title="Foundation",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
            metadata={"genres": ["Science Fiction"]},
        )

        mock_storage.get_completed_items = Mock(
            side_effect=lambda content_type=None, **kwargs: [consumed]
        )
        mock_storage.get_unconsumed_items = Mock(return_value=[same_genre])

        recs_off = engine.generate_recommendations(
            content_type=ContentType.BOOK,
            count=1,
            user_preference_config=UserPreferenceConfig(variety_penalty=0.0),
        )
        recs_on = engine.generate_recommendations(
            content_type=ContentType.BOOK,
            count=1,
            user_preference_config=UserPreferenceConfig(variety_penalty=4.0),
        )

        score_off = _variety_score_for(recs_off, "same_genre")
        score_on = _variety_score_for(recs_on, "same_genre")
        # 4.0 / 5.0 == 0.8 top fraction => (1 - top_fraction) of the score retained.
        top_fraction = 4.0 / UserPreferenceConfig.MAX_VARIETY_PENALTY
        assert score_on == pytest.approx(score_off * (1 - top_fraction), rel=1e-6)
        assert recs_on[0].variety_penalty == pytest.approx(top_fraction)
        assert recs_off[0].variety_penalty == 0.0

    def test_variety_penalty_steps_by_recency(self, engine, mock_storage) -> None:
        """With variety_penalty 4.0 (a 0.8 top fraction), finishing fantasy then
        sci-fi puts sci-fi on the top rung (0.8) and fantasy on the next rung
        (0.64)."""
        finished_fantasy = ContentItem(
            id="finished_fantasy",
            title="The Hobbit",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            rating=5,
            date_completed=date(2026, 1, 1),
            metadata={"genres": ["Fantasy"]},
        )
        finished_scifi = ContentItem(
            id="finished_scifi",
            title="Dune",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            rating=5,
            date_completed=date(2026, 1, 2),  # more recent
            metadata={"genres": ["Science Fiction"]},
        )
        fantasy_candidate = ContentItem(
            id="fantasy_candidate",
            title="The Name of the Wind",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
            metadata={"genres": ["Fantasy"]},
        )
        scifi_candidate = ContentItem(
            id="scifi_candidate",
            title="Hyperion",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
            metadata={"genres": ["Science Fiction"]},
        )

        mock_storage.get_completed_items = Mock(
            side_effect=lambda content_type=None, **kwargs: [
                finished_fantasy,
                finished_scifi,
            ]
        )
        mock_storage.get_unconsumed_items = Mock(
            return_value=[fantasy_candidate, scifi_candidate]
        )

        recs = engine.generate_recommendations(
            content_type=ContentType.BOOK,
            count=2,
            user_preference_config=UserPreferenceConfig(variety_penalty=4.0),
        )

        assert _variety_rank_of(recs, "fantasy_candidate") < _variety_rank_of(
            recs, "scifi_candidate"
        )
        fantasy_penalty = next(
            rec.variety_penalty for rec in recs if rec.item.id == "fantasy_candidate"
        )
        scifi_penalty = next(
            rec.variety_penalty for rec in recs if rec.item.id == "scifi_candidate"
        )
        top_fraction = (
            UserPreferenceConfig.LEGACY_VARIETY_ON
            / UserPreferenceConfig.MAX_VARIETY_PENALTY
        )
        assert scifi_penalty == pytest.approx(top_fraction)
        assert fantasy_penalty == pytest.approx(
            top_fraction * (VARIETY_LADDER_STEPS - 1) / VARIETY_LADDER_STEPS
        )

    def test_variety_penalty_is_per_content_type(self, engine, mock_storage) -> None:
        """The penalty ladder is scoped to completed items of the content type being
        recommended, so genres vary independently per type."""
        finished_book = ContentItem(
            id="finished_book",
            title="The Hobbit",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            rating=5,
            date_completed=date(2026, 1, 1),
            metadata={"genres": ["Fantasy"]},
        )
        fantasy_game = ContentItem(
            id="fantasy_game",
            title="Baldur's Gate 3",
            content_type=ContentType.VIDEO_GAME,
            status=ConsumptionStatus.UNREAD,
            metadata={"genres": ["Fantasy"]},
        )

        def completed_items(content_type=None, **kwargs):
            # Cross-type preference analysis sees the book; the game-type
            # query (used to build the ladder) sees no completed games.
            if content_type is None or content_type == ContentType.BOOK:
                return [finished_book]
            return []

        mock_storage.get_completed_items = Mock(side_effect=completed_items)
        mock_storage.get_unconsumed_items = Mock(return_value=[fantasy_game])

        recs = engine.generate_recommendations(
            content_type=ContentType.VIDEO_GAME,
            count=1,
            user_preference_config=UserPreferenceConfig(variety_penalty=4.0),
        )

        # No completed games => empty ladder => the fantasy game is untouched.
        assert recs[0].item.id == "fantasy_game"
        assert recs[0].variety_penalty == 0.0

    def test_full_throttle_variety_zeroes_finished_genre(
        self, engine, mock_storage
    ) -> None:
        """Removing the old 0.8 cap means the maximum preference fully suppresses a
        just-finished genre's same-type candidate — there is no score floor."""
        consumed = ContentItem(
            id="consumed_1",
            title="Dune",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            rating=5,
            date_completed=date(2026, 1, 1),
            metadata={"genres": ["Science Fiction"]},
        )
        same_genre = ContentItem(
            id="same_genre",
            title="Foundation",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
            metadata={"genres": ["Science Fiction"]},
        )

        mock_storage.get_completed_items = Mock(
            side_effect=lambda content_type=None, **kwargs: [consumed]
        )
        mock_storage.get_unconsumed_items = Mock(return_value=[same_genre])

        recs = engine.generate_recommendations(
            content_type=ContentType.BOOK,
            count=1,
            user_preference_config=UserPreferenceConfig(
                variety_penalty=UserPreferenceConfig.MAX_VARIETY_PENALTY
            ),
        )
        assert recs[0].variety_penalty == pytest.approx(1.0)
        assert _variety_score_for(recs, "same_genre") == pytest.approx(0.0)

    def test_strength_above_the_slider_zeroes_rather_than_negates(
        self, engine, mock_storage
    ) -> None:
        """Dividing the raw strength instead gave a fraction of 10.0 here, and
        ``score * (1 - penalty)`` emitted a negative score."""
        consumed = ContentItem(
            id="consumed_1",
            title="Dune",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            rating=5,
            date_completed=date(2026, 1, 1),
            metadata={"genres": ["Science Fiction"]},
        )
        same_genre = ContentItem(
            id="same_genre",
            title="Foundation",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
            metadata={"genres": ["Science Fiction"]},
        )

        mock_storage.get_completed_items = Mock(
            side_effect=lambda content_type=None, **kwargs: [consumed]
        )
        mock_storage.get_unconsumed_items = Mock(return_value=[same_genre])

        recs = engine.generate_recommendations(
            content_type=ContentType.BOOK,
            count=1,
            user_preference_config=UserPreferenceConfig(
                variety_penalty=UserPreferenceConfig.MAX_VARIETY_PENALTY * 10
            ),
        )

        assert recs[0].variety_penalty == pytest.approx(1.0)
        assert _variety_score_for(recs, "same_genre") == pytest.approx(0.0)


@pytest.fixture
def variety_crossover_library(mock_storage):
    """Wire *mock_storage* for the issue #74 series continuation scenario."""
    consumed = ContentItem(
        id="dragonlance_1",
        title="Dragonlance: Dragons of Autumn Twilight",
        content_type=ContentType.BOOK,
        status=ConsumptionStatus.COMPLETED,
        rating=5,
        date_completed=date(2026, 1, 1),
        metadata={
            "franchise": "Dragonlance",
            "series_position": 1,
            "genres": ["Fantasy"],
        },
    )
    next_in_series = ContentItem(
        id="dragonlance_2",
        title="Dragonlance: Dragons of Winter Night",
        content_type=ContentType.BOOK,
        status=ConsumptionStatus.UNREAD,
        metadata={
            "franchise": "Dragonlance",
            "series_position": 2,
            "genres": ["Fantasy"],
        },
    )
    different_genre = ContentItem(
        id="mystery_book",
        title="The Hound of the Baskervilles",
        content_type=ContentType.BOOK,
        status=ConsumptionStatus.UNREAD,
        metadata={"genres": ["Mystery"]},
    )

    mock_storage.get_completed_items = Mock(
        side_effect=lambda content_type=None, **kwargs: [consumed]
    )
    mock_storage.get_unconsumed_items = Mock(
        return_value=[next_in_series, different_genre]
    )
    return mock_storage


class TestVarietyAfterCompletionRegression:
    """Regression tests for the variety_penalty feature (issue #74)."""

    def test_next_in_series_demoted_when_variety_enabled_regression(
        self, engine, variety_crossover_library
    ) -> None:
        """Reported: 'Finished a book, setting turned on, new number 1
        recommendation is the next book in the series.'"""
        recs_off = engine.generate_recommendations(
            content_type=ContentType.BOOK,
            count=2,
            user_preference_config=UserPreferenceConfig(variety_penalty=0.0),
        )
        assert recs_off[0].item.id == "dragonlance_2"

        recs_on = engine.generate_recommendations(
            content_type=ContentType.BOOK,
            count=2,
            user_preference_config=UserPreferenceConfig(
                variety_penalty=UserPreferenceConfig.LEGACY_VARIETY_ON
            ),
        )
        assert recs_on[0].item.id == "mystery_book"
        assert _variety_rank_of(recs_on, "mystery_book") < _variety_rank_of(
            recs_on, "dragonlance_2"
        )

    def test_decimal_novella_below_next_book_with_variety_regression(
        self, engine, mock_storage
    ) -> None:
        """Decimal novella positions parsed as non-series so they dodged the
        too-far-ahead suppression, and the variety penalty hit the legit next
        book at full strength."""
        book_one = ContentItem(
            id="exp1",
            title="Leviathan Wakes (The Expanse, #1)",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            rating=5,
            date_completed=date(2026, 1, 1),
            metadata={"genres": ["Science Fiction"]},
        )
        book_two = ContentItem(
            id="exp2",
            title="Caliban's War (The Expanse, #2)",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
            metadata={"genres": ["Science Fiction"]},
        )
        novella_25 = ContentItem(
            id="exp25",
            title="Gods of Risk (The Expanse, #2.5)",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
            metadata={"genres": ["Science Fiction"]},
        )
        novella_27 = ContentItem(
            id="exp27",
            title="Drive (The Expanse, #2.7)",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
            metadata={"genres": ["Science Fiction"]},
        )

        mock_storage.get_completed_items = Mock(
            side_effect=lambda content_type=None, **kwargs: [book_one]
        )
        mock_storage.get_unconsumed_items = Mock(
            return_value=[novella_27, novella_25, book_two]
        )

        recs = engine.generate_recommendations(
            content_type=ContentType.BOOK,
            count=10,
            user_preference_config=UserPreferenceConfig(
                variety_penalty=UserPreferenceConfig.LEGACY_VARIETY_ON
            ),
        )

        rec_ids = [rec.item.id for rec in recs]
        # The legit next book is recommended; the out-of-order novellas are
        # substituted away by series filtering and never appear.
        assert "exp2" in rec_ids
        assert "exp25" not in rec_ids
        assert "exp27" not in rec_ids

        # The variety layer fired too: Caliban's War shares the just-finished
        # sci-fi cluster, but as an active series continuation its penalty is
        # softened, not applied at full strength.
        top_fraction = (
            UserPreferenceConfig.LEGACY_VARIETY_ON
            / UserPreferenceConfig.MAX_VARIETY_PENALTY
        )
        book_two_rec = next(rec for rec in recs if rec.item.id == "exp2")
        assert book_two_rec.variety_penalty == pytest.approx(
            top_fraction * VARIETY_SERIES_CONTINUATION_FACTOR
        )
        assert book_two_rec.variety_penalty < top_fraction


class TestEngineSeriesSubstitutionRegression:
    """Bug reported: Final Fantasy XII (#12) was recommended as #1 but FFX (#10)
    was #4; Kingdom Hearts III is #5 but KH 2.8 is #7; Dragon Age Inquisition
    recommended without playing Dragon Age 2."""

    def test_later_entry_substituted_with_earliest_regression(
        self, engine, mock_storage
    ) -> None:
        """Bug: FF XII appeared as recommendation #1, FF X was #4."""
        consumed = ContentItem(
            id="consumed",
            title="Chrono Trigger",
            content_type=ContentType.VIDEO_GAME,
            status=ConsumptionStatus.COMPLETED,
            rating=5,
            metadata={"genres": ["RPG"]},
        )

        ff10 = ContentItem(
            id="ff10",
            title="Final Fantasy X",
            content_type=ContentType.VIDEO_GAME,
            status=ConsumptionStatus.UNREAD,
            metadata={
                "franchise": "Final Fantasy",
                "series_position": 10,
                "genres": ["RPG"],
            },
        )
        ff12 = ContentItem(
            id="ff12",
            title="Final Fantasy XII",
            content_type=ContentType.VIDEO_GAME,
            status=ConsumptionStatus.UNREAD,
            metadata={
                "franchise": "Final Fantasy",
                "series_position": 12,
                "genres": ["RPG"],
            },
        )
        other_game = ContentItem(
            id="other",
            title="Standalone RPG",
            content_type=ContentType.VIDEO_GAME,
            status=ConsumptionStatus.UNREAD,
            metadata={"genres": ["RPG"]},
        )

        mock_storage.get_completed_items = Mock(
            side_effect=lambda content_type=None, **kwargs: [consumed]
        )
        mock_storage.get_unconsumed_items = Mock(return_value=[ff12, ff10, other_game])

        recommendations = engine.generate_recommendations(
            content_type=ContentType.VIDEO_GAME,
            count=5,
        )

        recommended_ids = [rec.item.id for rec in recommendations]
        assert (
            "ff10" in recommended_ids
        ), f"FF X should be substituted in; got {recommended_ids}"
        assert (
            "ff12" not in recommended_ids
        ), f"FF XII should be filtered out; got {recommended_ids}"

    def test_series_in_order_false_skips_filtering(self, engine, mock_storage) -> None:
        consumed = ContentItem(
            id="consumed",
            title="Chrono Trigger",
            content_type=ContentType.VIDEO_GAME,
            status=ConsumptionStatus.COMPLETED,
            rating=5,
            metadata={"genres": ["RPG"]},
        )

        ff12 = ContentItem(
            id="ff12",
            title="Final Fantasy XII",
            content_type=ContentType.VIDEO_GAME,
            status=ConsumptionStatus.UNREAD,
            metadata={
                "franchise": "Final Fantasy",
                "series_position": 12,
                "genres": ["RPG"],
            },
        )

        mock_storage.get_completed_items = Mock(
            side_effect=lambda content_type=None, **kwargs: [consumed]
        )
        mock_storage.get_unconsumed_items = Mock(return_value=[ff12])

        user_config = UserPreferenceConfig(series_in_order=False)
        recommendations = engine.generate_recommendations(
            content_type=ContentType.VIDEO_GAME,
            count=5,
            user_preference_config=user_config,
        )

        recommended_ids = [rec.item.id for rec in recommendations]
        assert "ff12" in recommended_ids

    def test_duplicate_substitutions_prevented_regression(
        self, engine, mock_storage
    ) -> None:
        """Bug: Both FF XII and FF XV failing series rules could cause FF X to
        appear twice in recommendations."""
        consumed = ContentItem(
            id="consumed",
            title="Chrono Trigger",
            content_type=ContentType.VIDEO_GAME,
            status=ConsumptionStatus.COMPLETED,
            rating=5,
            metadata={"genres": ["RPG"]},
        )

        ff10 = ContentItem(
            id="ff10",
            title="Final Fantasy X",
            content_type=ContentType.VIDEO_GAME,
            status=ConsumptionStatus.UNREAD,
            metadata={
                "franchise": "Final Fantasy",
                "series_position": 10,
                "genres": ["RPG"],
            },
        )
        ff12 = ContentItem(
            id="ff12",
            title="Final Fantasy XII",
            content_type=ContentType.VIDEO_GAME,
            status=ConsumptionStatus.UNREAD,
            metadata={
                "franchise": "Final Fantasy",
                "series_position": 12,
                "genres": ["RPG"],
            },
        )
        ff15 = ContentItem(
            id="ff15",
            title="Final Fantasy XV",
            content_type=ContentType.VIDEO_GAME,
            status=ConsumptionStatus.UNREAD,
            metadata={
                "franchise": "Final Fantasy",
                "series_position": 15,
                "genres": ["RPG"],
            },
        )

        mock_storage.get_completed_items = Mock(
            side_effect=lambda content_type=None, **kwargs: [consumed]
        )
        mock_storage.get_unconsumed_items = Mock(return_value=[ff15, ff12, ff10])

        recommendations = engine.generate_recommendations(
            content_type=ContentType.VIDEO_GAME,
            count=5,
        )

        recommended_ids = [rec.item.id for rec in recommendations]
        assert (
            recommended_ids.count("ff10") == 1
        ), f"FF X should appear exactly once; got {recommended_ids}"


class TestContinuationScorerExclusion:
    """ContinuationScorer is excluded when no candidates are actively consumed."""

    def test_no_active_items_excludes_continuation_from_breakdown(
        self, engine, mock_storage
    ):
        """When no candidates have CURRENTLY_CONSUMING status, 'continuation' must
        not appear in score_breakdown (it would be all zeros)."""
        consumed = ContentItem(
            id="c1",
            title="Dune",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            rating=5,
            metadata={"genres": ["Science Fiction"]},
        )
        unconsumed = ContentItem(
            id="u1",
            title="Hyperion",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
            metadata={"genres": ["Science Fiction"]},
        )

        mock_storage.get_completed_items = Mock(
            side_effect=lambda content_type=None, **kwargs: [consumed]
        )
        mock_storage.get_unconsumed_items = Mock(return_value=[unconsumed])

        recommendations = engine.generate_recommendations(
            content_type=ContentType.BOOK, count=1
        )

        assert len(recommendations) == 1
        assert "continuation" not in recommendations[0].score_breakdown

    def test_active_item_retains_continuation_in_breakdown(self, engine, mock_storage):
        """When a candidate has CURRENTLY_CONSUMING status, 'continuation' must
        appear in score_breakdown and the active item must score 1.0."""
        consumed = ContentItem(
            id="c1",
            title="Dune",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            rating=5,
            metadata={"genres": ["Science Fiction"]},
        )
        active_book = ContentItem(
            id="u1",
            title="Hyperion",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.CURRENTLY_CONSUMING,
            metadata={"genres": ["Science Fiction"]},
        )
        idle_book = ContentItem(
            id="u2",
            title="Foundation",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
            metadata={"genres": ["Science Fiction"]},
        )

        mock_storage.get_completed_items = Mock(
            side_effect=lambda content_type=None, **kwargs: [consumed]
        )
        mock_storage.get_unconsumed_items = Mock(return_value=[active_book, idle_book])

        recommendations = engine.generate_recommendations(
            content_type=ContentType.BOOK, count=5
        )

        assert len(recommendations) >= 1
        breakdowns = {rec.item.title: rec.score_breakdown for rec in recommendations}
        assert "continuation" in breakdowns["Hyperion"]
        assert breakdowns["Hyperion"]["continuation"] == 1.0
        assert breakdowns["Foundation"]["continuation"] == 0.0


class TestSameSeriesReferenceExclusionRegression:
    """Bug reported: "The Expanse (Season 2)" recommendation showed reasoning
    "Recommended because you liked The Expanse", which is circular self-referencing
    within a series."""

    def test_same_series_consumed_item_excluded_regression(self) -> None:
        engine = _engine_for_helpers()

        candidate = ContentItem(
            id="expanse_s2",
            title="The Expanse (The Expanse, Season 2)",
            content_type=ContentType.TV_SHOW,
            status=ConsumptionStatus.UNREAD,
            metadata={"genres": ["Science Fiction", "Drama"]},
        )

        same_series_consumed = ContentItem(
            id="expanse_s1",
            title="The Expanse (The Expanse, Season 1)",
            content_type=ContentType.TV_SHOW,
            status=ConsumptionStatus.COMPLETED,
            rating=5,
            metadata={"genres": ["Science Fiction", "Drama"]},
        )

        other_consumed = ContentItem(
            id="battlestar",
            title="Battlestar Galactica",
            content_type=ContentType.TV_SHOW,
            status=ConsumptionStatus.COMPLETED,
            rating=5,
            metadata={"genres": ["Science Fiction", "Drama"]},
        )

        result = SignalIndex([same_series_consumed, other_consumed]).references_for(
            candidate, engine.rng
        )

        result_titles = [item.title for item in result]
        assert (
            "The Expanse (The Expanse, Season 1)" not in result_titles
        ), "Same-series items must not appear as contributing references"
        assert "Battlestar Galactica" in result_titles

    def test_show_level_item_excluded_from_season_references_regression(self) -> None:
        """get_series_name() returns None for show-level items
        (no season marker in title, no season number in metadata), so the existing
        same-series check was bypassed."""
        engine = _engine_for_helpers()

        candidate = ContentItem(
            id="expanse_s2",
            title="The Expanse (Season 2)",
            content_type=ContentType.TV_SHOW,
            status=ConsumptionStatus.UNREAD,
            metadata={
                "genres": ["Science Fiction", "Drama"],
                "series_name": "The Expanse",
                "season": 2,
            },
        )

        show_level_consumed = ContentItem(
            id="expanse_show",
            title="The Expanse",
            content_type=ContentType.TV_SHOW,
            status=ConsumptionStatus.COMPLETED,
            rating=5,
            metadata={"genres": ["Science Fiction", "Drama"]},
        )

        other_consumed = ContentItem(
            id="firefly",
            title="Firefly",
            content_type=ContentType.TV_SHOW,
            status=ConsumptionStatus.COMPLETED,
            rating=5,
            metadata={"genres": ["Science Fiction"]},
        )

        result = SignalIndex([show_level_consumed, other_consumed]).references_for(
            candidate, engine.rng
        )

        result_titles = [item.title for item in result]
        assert "The Expanse" not in result_titles, (
            "Show-level items must not appear as contributing references for "
            "their own seasons"
        )
        assert "Firefly" in result_titles

    def test_show_level_metadata_series_name_excluded_regression(self) -> None:
        """Bug reported: consumed item with series_name metadata but a non-matching
        title appeared as a contributing reference for its own series (e.g. "My
        Expanse Review" cited for "The Expanse (Season 3)")."""
        engine = _engine_for_helpers()

        candidate = ContentItem(
            id="expanse_s3",
            title="The Expanse (Season 3)",
            content_type=ContentType.TV_SHOW,
            status=ConsumptionStatus.UNREAD,
            metadata={
                "genres": ["Science Fiction"],
                "series_name": "The Expanse",
                "season": 3,
            },
        )

        consumed_with_meta = ContentItem(
            id="expanse_show",
            title="My Expanse Review",
            content_type=ContentType.TV_SHOW,
            status=ConsumptionStatus.COMPLETED,
            rating=4,
            metadata={
                "genres": ["Science Fiction"],
                "series_name": "The Expanse",
            },
        )

        other = ContentItem(
            id="bsg",
            title="Battlestar Galactica",
            content_type=ContentType.TV_SHOW,
            status=ConsumptionStatus.COMPLETED,
            rating=5,
            metadata={"genres": ["Science Fiction"]},
        )

        result = SignalIndex([consumed_with_meta, other]).references_for(
            candidate, engine.rng
        )

        result_titles = [item.title for item in result]
        assert "My Expanse Review" not in result_titles, (
            "Consumed items with matching metadata series_name must be "
            "excluded from contributing references"
        )
        assert "Battlestar Galactica" in result_titles


class TestInProgressItemsExcludedFromBasisRegression:
    """Bug reported: items with status CURRENTLY_CONSUMING were showing up in the
    "based on" / contributing items list displayed alongside each recommendation."""

    def test_contributing_excludes_currently_consuming(self) -> None:
        engine = _engine_for_helpers()

        candidate = ContentItem(
            id="breaking_bad",
            title="Breaking Bad",
            content_type=ContentType.TV_SHOW,
            status=ConsumptionStatus.UNREAD,
            metadata={"genres": ["Drama", "Crime", "Thriller"]},
        )

        in_progress = ContentItem(
            id="the_wire",
            title="The Wire",
            content_type=ContentType.TV_SHOW,
            status=ConsumptionStatus.CURRENTLY_CONSUMING,
            rating=5,
            metadata={"genres": ["Drama", "Crime"]},
        )
        completed = ContentItem(
            id="sopranos",
            title="The Sopranos",
            content_type=ContentType.TV_SHOW,
            status=ConsumptionStatus.COMPLETED,
            rating=5,
            metadata={"genres": ["Drama", "Crime"]},
        )

        result = SignalIndex([in_progress, completed]).references_for(
            candidate, engine.rng
        )

        result_ids = {item.id for item in result}
        assert result_ids == {"sopranos"}, (
            "CURRENTLY_CONSUMING items must be excluded from contributing "
            "references while completed items must remain"
        )

    def test_adaptations_exclude_currently_consuming(self) -> None:
        candidate = ContentItem(
            id="lotr_movie",
            title="The Fellowship of the Ring",
            content_type=ContentType.MOVIE,
            status=ConsumptionStatus.UNREAD,
            author="J.R.R. Tolkien",
        )

        in_progress_book = ContentItem(
            id="lotr_book_in_progress",
            title="The Fellowship of the Ring",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.CURRENTLY_CONSUMING,
            rating=5,
            author="J.R.R. Tolkien",
        )
        completed_book = ContentItem(
            id="lotr_book_done",
            title="The Fellowship of the Ring",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            rating=5,
            author="J.R.R. Tolkien",
        )

        result = SignalIndex([in_progress_book, completed_book]).adaptations_of(
            candidate
        )

        result_ids = {item.id for item in result}
        assert result_ids == {"lotr_book_done"}, (
            "CURRENTLY_CONSUMING items must be excluded from adaptations "
            "while completed adaptations must still appear"
        )


class TestIgnoredAndUnratedSignalRegression:
    """Bug reported: ignored and completed-but-unrated items shaped recs."""

    def test_ignored_and_unrated_excluded_from_signal_regression(
        self, real_engine, real_storage
    ):
        _save_book(
            real_storage,
            item_id="dune",
            title="Dune",
            status=ConsumptionStatus.COMPLETED,
            rating=5,
        )
        _save_book(
            real_storage,
            item_id="neuro",
            title="Neuromancer",
            status=ConsumptionStatus.COMPLETED,
            rating=5,
            ignored=True,
        )
        _save_book(
            real_storage,
            item_id="snow",
            title="Snow Crash",
            status=ConsumptionStatus.COMPLETED,
            rating=None,
        )
        _save_book(
            real_storage,
            item_id="hyp",
            title="Hyperion",
            status=ConsumptionStatus.UNREAD,
        )
        _save_book(
            real_storage,
            item_id="saga",
            title="Ignored Saga",
            status=ConsumptionStatus.UNREAD,
            ignored=True,
        )

        recs = real_engine.generate_recommendations(
            content_type=ContentType.BOOK, count=5
        )

        titles = {rec.item.title for rec in recs}
        # Candidate pool: the unrated candidate is still recommended (backlog
        # is unrated by nature); the ignored candidate is not.
        assert "Hyperion" in titles
        assert "Ignored Saga" not in titles

        hyperion = next(rec for rec in recs if rec.item.title == "Hyperion")
        contributing = {item.title for item in hyperion.contributing_items}
        # The rated, non-ignored signal item is cited; the ignored and the
        # completed-but-unrated items never appear as "you liked" references.
        assert "Dune" in contributing
        assert "Neuromancer" not in contributing
        assert "Snow Crash" not in contributing


class TestIgnoredAndUnratedItemsDoNotShapeRankingRegression:
    """Bug reported: the same-type completed set was fetched unfiltered and fed to
    taste-shaped ranking, so an ignored or completed-but-unrated book's genre
    demoted an otherwise-top candidate sharing that genre."""

    @staticmethod
    def _order(engine):
        recs = engine.generate_recommendations(content_type=ContentType.BOOK, count=5)
        return [rec.item.title for rec in recs]

    @staticmethod
    def _seed_baseline(storage):
        _save_book(
            storage,
            item_id="sig",
            title="Gone Girl",
            status=ConsumptionStatus.COMPLETED,
            rating=5,
            genre="Mystery",
        )
        _save_book(
            storage,
            item_id="cf",
            title="Fantasy Candidate",
            status=ConsumptionStatus.UNREAD,
            genre="Fantasy",
        )
        _save_book(
            storage,
            item_id="cs",
            title="SciFi Candidate",
            status=ConsumptionStatus.UNREAD,
            genre="Science Fiction",
        )

    def test_ignored_completed_item_does_not_reorder_regression(
        self, real_engine, real_storage
    ):
        self._seed_baseline(real_storage)
        baseline = self._order(real_engine)

        _save_book(
            real_storage,
            item_id="fan",
            title="Ignored Fantasy",
            status=ConsumptionStatus.COMPLETED,
            rating=5,
            ignored=True,
            genre="Fantasy",
        )
        after = self._order(real_engine)

        assert after == baseline, (
            "An ignored completed item changed recommendation order via the "
            "taste signal — issue #99 leak in ranking"
        )

    def test_unrated_completed_item_does_not_reorder_regression(
        self, real_engine, real_storage
    ):
        self._seed_baseline(real_storage)
        baseline = self._order(real_engine)

        _save_book(
            real_storage,
            item_id="fan",
            title="Unrated Fantasy",
            status=ConsumptionStatus.COMPLETED,
            rating=None,
            genre="Fantasy",
        )
        after = self._order(real_engine)

        assert after == baseline, (
            "A completed-but-unrated item changed recommendation order via the "
            "taste signal — issue #99 leak in ranking"
        )

    def test_rated_completion_reorders_positive_control(
        self, real_engine, real_storage
    ):
        """Proves the seed set genuinely drives ranking, so the "order unchanged"
        assertions above are meaningful rather than vacuous."""
        self._seed_baseline(real_storage)
        baseline = self._order(real_engine)

        genre_by_title = {
            "Fantasy Candidate": "Fantasy",
            "SciFi Candidate": "Science Fiction",
        }
        trailing_genre = genre_by_title[baseline[-1]]
        for index in range(2):
            _save_book(
                real_storage,
                item_id=f"pref{index}",
                title=f"Loved {index}",
                status=ConsumptionStatus.COMPLETED,
                rating=5,
                genre=trailing_genre,
            )
        after = self._order(real_engine)

        assert after != baseline, (
            "A rated, non-ignored completion should reorder recommendations — "
            "if it does not, the negative regressions above prove nothing"
        )


class TestVarietyLadderConsumptionRegression:
    """Bug reported: unrated completions caused no genre fatigue at all."""

    # variety_penalty == MAX gives a top-rung penalty fraction of 1.0, which
    # fully zeroes a just-finished genre — the strongest, most observable rung.
    _CONFIG = UserPreferenceConfig(
        variety_penalty=UserPreferenceConfig.MAX_VARIETY_PENALTY
    )

    _SEED_COMPLETED_ON = date(2026, 1, 1)
    _FANTASY_COMPLETED_ON = date(2026, 2, 1)

    @classmethod
    def _seed_baseline(cls, storage):
        # A rated, non-ignored Mystery signal item makes recommendations exist
        # and seeds the ladder with Mystery (a cluster none of the candidates
        # share, so baseline candidate penalties are zero).
        _save_book(
            storage,
            item_id="sig",
            title="Gone Girl",
            status=ConsumptionStatus.COMPLETED,
            rating=5,
            genre="Mystery",
            date_completed=cls._SEED_COMPLETED_ON,
        )
        _save_book(
            storage,
            item_id="cf",
            title="Fantasy Candidate",
            status=ConsumptionStatus.UNREAD,
            genre="Fantasy",
        )
        _save_book(
            storage,
            item_id="cs",
            title="SciFi Candidate",
            status=ConsumptionStatus.UNREAD,
            genre="Science Fiction",
        )

    # After _SEED_COMPLETED_ON, so the show's finished season outranks the
    # Mystery completion on the ladder.
    _SEASON_WATCHED_AT = "2026-02-01T12:00:00Z"

    @classmethod
    def _seed_tv_baseline(cls, storage):
        """The Mystery completion clears the empty-signal guard and holds a rung of
        its own, so an ignored show's zero can be measured against a ladder the same
        request proves is live."""
        storage.save_content_item(
            ContentItem(
                id="tv-seed",
                title="Rated Mystery Show",
                content_type=ContentType.TV_SHOW,
                status=ConsumptionStatus.COMPLETED,
                rating=5,
                metadata={"genre": "Mystery"},
                date_completed=cls._SEED_COMPLETED_ON,
            )
        )
        for genre in ("Fantasy", "Mystery"):
            storage.save_content_item(
                ContentItem(
                    id=f"tv-cand-{genre.lower()}",
                    title=f"{genre} Show Candidate",
                    content_type=ContentType.TV_SHOW,
                    status=ConsumptionStatus.UNREAD,
                    metadata={"genre": genre},
                )
            )

    @classmethod
    def _save_ongoing_fantasy_show(cls, storage, *, ignored):
        db_id = storage.save_content_item(
            ContentItem(
                id="tv-ongoing",
                title="Ongoing Fantasy Show",
                content_type=ContentType.TV_SHOW,
                status=ConsumptionStatus.CURRENTLY_CONSUMING,
                rating=None,
                metadata={
                    "genre": "Fantasy",
                    "seasons_watched": [1],
                    "seasons_watched_dates": {"1": cls._SEASON_WATCHED_AT},
                },
            )
        )
        if ignored:
            storage.set_item_ignored(db_id, True)

    def _penalty_for(self, engine, title, content_type=ContentType.BOOK):
        recs = engine.generate_recommendations(
            content_type=content_type,
            count=5,
            user_preference_config=self._CONFIG,
        )
        candidate = next(rec for rec in recs if rec.item.title == title)
        return candidate.variety_penalty

    def _fantasy_penalty(self, engine):
        return self._penalty_for(engine, "Fantasy Candidate")

    def test_baseline_fantasy_candidate_unpenalised(self, real_engine, real_storage):
        self._seed_baseline(real_storage)
        assert self._fantasy_penalty(real_engine) == 0.0

    def test_rated_completed_fantasy_penalises_positive_control(
        self, real_engine, real_storage
    ):
        """Proves the ladder recognises the Fantasy cluster and applies its top
        rung, so the assertions below measure against a live ladder."""
        self._seed_baseline(real_storage)
        _save_book(
            real_storage,
            item_id="fan",
            title="Rated Fantasy",
            status=ConsumptionStatus.COMPLETED,
            rating=5,
            genre="Fantasy",
            date_completed=self._FANTASY_COMPLETED_ON,
        )
        assert self._fantasy_penalty(real_engine) == pytest.approx(1.0)

    def test_unrated_completed_fantasy_penalises_regression(
        self, real_engine, real_storage
    ):
        self._seed_baseline(real_storage)
        for index in range(3):
            _save_book(
                real_storage,
                item_id=f"unrated{index}",
                title=f"Unrated Fantasy {index}",
                status=ConsumptionStatus.COMPLETED,
                rating=None,
                genre="Fantasy",
                date_completed=self._FANTASY_COMPLETED_ON,
            )

        assert self._fantasy_penalty(real_engine) == pytest.approx(1.0), (
            "Completed-but-unrated books did not fatigue their genre — the "
            "variety ladder is doing nothing for a user who does not rate"
        )

    def test_unrated_first_book_softens_the_penalty_on_book_two_regression(
        self, real_engine, real_storage
    ):
        """The reported library — six fantasy novels torn through unrated — is
        likely a series, and the widening makes that pair reachable: an unrated #1
        used to put nothing on the ladder, so #2 took no penalty at all."""
        self._seed_baseline(real_storage)
        _save_book(
            real_storage,
            item_id="saga1",
            title="Unrated Beginnings (Saga, #1)",
            status=ConsumptionStatus.COMPLETED,
            rating=None,
            genre="Fantasy",
            date_completed=self._FANTASY_COMPLETED_ON,
        )
        _save_book(
            real_storage,
            item_id="saga2",
            title="Unrated Middles (Saga, #2)",
            status=ConsumptionStatus.UNREAD,
            genre="Fantasy",
        )

        assert self._penalty_for(
            real_engine, "Unrated Middles (Saga, #2)"
        ) == pytest.approx(VARIETY_TOP_PENALTY * VARIETY_SERIES_CONTINUATION_FACTOR), (
            "The next book in the series the user is mid-way through took the "
            "full genre-fatigue rung — an unrated #1 must soften it, not bury #2"
        )
        assert self._fantasy_penalty(real_engine) == pytest.approx(
            VARIETY_TOP_PENALTY
        ), "The softening leaked onto a Fantasy candidate that continues nothing"

    def test_undated_unrated_completion_sorts_behind_every_dated_completion_regression(
        self, real_engine, real_storage
    ):
        """Two dated completions straddle the ladder, because with only one an
        ordering that interleaved undated items among dated ones would land on the
        same rung and pass."""
        self._seed_baseline(real_storage)
        _save_book(
            real_storage,
            item_id="scifi",
            title="Newer Dated SciFi",
            status=ConsumptionStatus.COMPLETED,
            rating=None,
            genre="Science Fiction",
            date_completed=date(2026, 3, 1),
        )
        _save_book(
            real_storage,
            item_id="fan",
            title="Undated Unrated Fantasy",
            status=ConsumptionStatus.COMPLETED,
            rating=None,
            genre="Fantasy",
        )

        # Rung 2, behind the 2026-03 Science Fiction and 2026-01 Mystery rungs.
        assert self._fantasy_penalty(real_engine) == pytest.approx(
            (VARIETY_LADDER_STEPS - 2) / VARIETY_LADDER_STEPS
        )

    def test_ignored_completed_fantasy_does_not_penalise_regression(
        self, real_engine, real_storage
    ):
        self._seed_baseline(real_storage)
        _save_book(
            real_storage,
            item_id="fan",
            title="Ignored Fantasy",
            status=ConsumptionStatus.COMPLETED,
            rating=5,
            ignored=True,
            genre="Fantasy",
            date_completed=self._FANTASY_COMPLETED_ON,
        )
        assert self._fantasy_penalty(real_engine) == 0.0, (
            "An ignored completed item entered the variety ladder and "
            "penalised a same-genre candidate — ignoring means less of it"
        )

    def test_unrated_completions_take_rungs_by_recency_not_after_rated_ones(
        self, real_engine, real_storage
    ):
        """Appending unrated completions below the rated ones instead would hand
        Fantasy the top rung."""
        self._seed_baseline(real_storage)
        _save_book(
            real_storage,
            item_id="unrated-fantasy",
            title="Unrated Fantasy",
            status=ConsumptionStatus.COMPLETED,
            rating=None,
            genre="Fantasy",
            date_completed=self._FANTASY_COMPLETED_ON,
        )
        _save_book(
            real_storage,
            item_id="unrated-scifi",
            title="Unrated SciFi",
            status=ConsumptionStatus.COMPLETED,
            rating=None,
            genre="Science Fiction",
            date_completed=date(2026, 3, 1),
        )

        assert self._penalty_for(real_engine, "SciFi Candidate") == pytest.approx(1.0)
        assert self._fantasy_penalty(real_engine) == pytest.approx(
            (VARIETY_LADDER_STEPS - 1) / VARIETY_LADDER_STEPS
        )

    def test_unrated_completion_moves_only_the_penalty_not_the_scores(
        self, real_engine, real_storage
    ):
        """The widened set is the ladder's alone: every scorer and the "because you
        enjoyed" references still read the rated signal set, so the only number an
        unrated completion may move is the variety penalty."""
        self._seed_baseline(real_storage)
        recs = real_engine.generate_recommendations(
            content_type=ContentType.BOOK, count=5, user_preference_config=self._CONFIG
        )
        before = next(rec for rec in recs if rec.item.title == "Fantasy Candidate")
        breakdown_before = dict(before.score_breakdown)
        references_before = {item.title for item in before.contributing_items}

        _save_book(
            real_storage,
            item_id="unrated-fantasy",
            title="Unrated Fantasy",
            status=ConsumptionStatus.COMPLETED,
            rating=None,
            genre="Fantasy",
            date_completed=self._FANTASY_COMPLETED_ON,
        )
        recs = real_engine.generate_recommendations(
            content_type=ContentType.BOOK, count=5, user_preference_config=self._CONFIG
        )
        after = next(rec for rec in recs if rec.item.title == "Fantasy Candidate")

        assert before.variety_penalty == 0.0
        assert after.variety_penalty == pytest.approx(1.0)
        assert after.score_breakdown == breakdown_before, (
            "An unrated completion moved a scorer — the taste signal must stay "
            "rated-only even though the ladder no longer is"
        )
        assert {
            item.title for item in after.contributing_items
        } == references_before, "An unrated completion became a reference item"

    def test_unrated_completion_without_a_genre_claims_no_rung(
        self, real_engine, real_storage
    ):
        """Such an item reaches the ladder now, and a rung counter that advanced for
        it would push the Fantasy completion behind it and weaken every rung below."""
        self._seed_baseline(real_storage)
        _save_book(
            real_storage,
            item_id="unrated-fantasy",
            title="Unrated Fantasy",
            status=ConsumptionStatus.COMPLETED,
            rating=None,
            genre="Fantasy",
            date_completed=self._FANTASY_COMPLETED_ON,
        )
        _save_book(
            real_storage,
            item_id="nogenre",
            title="Genre-less Completion",
            status=ConsumptionStatus.COMPLETED,
            rating=None,
            genre=None,
            date_completed=date(2026, 3, 1),
        )

        assert self._fantasy_penalty(real_engine) == pytest.approx(1.0)

    def test_an_in_progress_unrated_book_claims_no_rung(
        self, real_engine, real_storage
    ):
        """Starting a fantasy novel is not finishing one."""
        self._seed_baseline(real_storage)
        _save_book(
            real_storage,
            item_id="reading",
            title="Fantasy In Progress",
            status=ConsumptionStatus.CURRENTLY_CONSUMING,
            rating=None,
            genre="Fantasy",
        )
        _save_book(
            real_storage,
            item_id="cm",
            title="Mystery Candidate",
            status=ConsumptionStatus.UNREAD,
            genre="Mystery",
        )

        assert self._penalty_for(real_engine, "Mystery Candidate") == pytest.approx(1.0)
        assert self._fantasy_penalty(real_engine) == 0.0, (
            "A book the user is halfway through fatigued its genre — only a "
            "finished one may claim a rung"
        )

    def test_unrated_ongoing_show_with_a_watched_season_penalises_regression(
        self, real_engine, real_storage
    ):
        """The one genuinely new code path: ``_completion_recency`` dates a mid-run
        show by ``latest_season_watched_date``, and an unrated show never reached it
        through the engine before."""
        self._seed_tv_baseline(real_storage)
        self._save_ongoing_fantasy_show(real_storage, ignored=False)

        assert self._penalty_for(
            real_engine, "Fantasy Show Candidate", ContentType.TV_SHOW
        ) == pytest.approx(1.0), (
            "An unrated show mid-run claimed no rung — its finished season is "
            "a completion event the ladder must see"
        )

    def test_a_library_with_no_rating_at_all_still_returns_nothing(
        self, real_engine, real_storage
    ):
        """The engine returns [] on an empty taste signal, before a ladder is ever
        built, so a user who rates *nothing* has no recommendations to spread."""
        _save_book(
            real_storage,
            item_id="unrated",
            title="Unrated Fantasy",
            status=ConsumptionStatus.COMPLETED,
            rating=None,
            genre="Fantasy",
            date_completed=self._FANTASY_COMPLETED_ON,
        )
        _save_book(
            real_storage,
            item_id="cf",
            title="Fantasy Candidate",
            status=ConsumptionStatus.UNREAD,
            genre="Fantasy",
        )

        assert (
            real_engine.generate_recommendations(
                content_type=ContentType.BOOK,
                count=5,
                user_preference_config=self._CONFIG,
            )
            == []
        )


class TestSeriesTrackingFullSetRegression:
    """Series ordering must use the FULL completed set, not the signal set."""

    @staticmethod
    def _titles(engine):
        recs = engine.generate_recommendations(content_type=ContentType.BOOK, count=5)
        return [rec.item.title for rec in recs]

    def test_completed_rated_first_entry_unlocks_second(
        self, real_engine, real_storage
    ):
        _save_book(
            real_storage,
            item_id="s1",
            title="Foundation (Signal Saga #1)",
            status=ConsumptionStatus.COMPLETED,
            rating=5,
        )
        _save_book(
            real_storage,
            item_id="s2",
            title="Foundation and Empire (Signal Saga #2)",
            status=ConsumptionStatus.UNREAD,
        )
        _save_book(
            real_storage,
            item_id="s3",
            title="Second Foundation (Signal Saga #3)",
            status=ConsumptionStatus.UNREAD,
        )

        titles = self._titles(real_engine)

        assert any(
            "Foundation and Empire" in t for t in titles
        ), "#2 should be recommended after completing #1"
        assert not any(
            "Second Foundation" in t for t in titles
        ), "#3 must stay held until #2 is consumed"

    def test_ignored_middle_entry_does_not_strand_series_regression(
        self, real_engine, real_storage
    ):
        """If series tracking used the signal set, ignored #2 would drop out of the
        consumed positions, #3 would be held behind an entry the user already
        finished, and the series would strand."""
        _save_book(
            real_storage,
            item_id="s1",
            title="Foundation (Signal Saga #1)",
            status=ConsumptionStatus.COMPLETED,
            rating=5,
        )
        _save_book(
            real_storage,
            item_id="s2",
            title="Foundation and Empire (Signal Saga #2)",
            status=ConsumptionStatus.COMPLETED,
            rating=5,
            ignored=True,
        )
        _save_book(
            real_storage,
            item_id="s3",
            title="Second Foundation (Signal Saga #3)",
            status=ConsumptionStatus.UNREAD,
        )
        _save_book(
            real_storage,
            item_id="s4",
            title="Foundation's Edge (Signal Saga #4)",
            status=ConsumptionStatus.UNREAD,
        )

        titles = self._titles(real_engine)

        assert any("Second Foundation" in t for t in titles), (
            "#3 must be recommended even when the finished #2 was ignored — "
            "series tracking must use the full completed set (issue #99)"
        )
        assert not any(
            "Foundation's Edge" in t for t in titles
        ), "#4 must stay held until #3 is consumed"


class TestHalfNumberedEntryScoringRegression:
    """SeriesOrderScorer recognised succession as
    ``item_number == max_consumed + 1``, which no fractional position satisfies,
    so #2.5 fell through to the terminal already-consumed branch."""

    @staticmethod
    def _stock_library(storage):
        _save_book(
            storage,
            item_id="x1",
            title="Leviathan Wakes (The Expanse, #1)",
            status=ConsumptionStatus.COMPLETED,
            rating=5,
        )
        _save_book(
            storage,
            item_id="x2",
            title="Caliban's War (The Expanse, #2)",
            status=ConsumptionStatus.COMPLETED,
            rating=5,
        )
        _save_book(
            storage,
            item_id="x25",
            title="Gods of Risk (The Expanse, #2.5)",
            status=ConsumptionStatus.UNREAD,
        )
        _save_book(
            storage,
            item_id="x3",
            title="Abaddon's Gate (The Expanse, #3)",
            status=ConsumptionStatus.UNREAD,
        )

    def test_up_next_novella_scores_as_next_in_sequence_regression(
        self, real_engine, real_storage
    ):
        self._stock_library(real_storage)

        recs = real_engine.generate_recommendations(
            content_type=ContentType.BOOK, count=5
        )
        by_title = {rec.item.title: rec for rec in recs}

        assert "Gods of Risk (The Expanse, #2.5)" in by_title
        assert (
            by_title["Gods of Risk (The Expanse, #2.5)"].score_breakdown["series_order"]
            == 1.0
        )
        assert "Abaddon's Gate (The Expanse, #3)" not in by_title


def _save_movie(storage, *, title, genre, rating):
    return storage.save_content_item(
        ContentItem(
            id=None,
            title=title,
            content_type=ContentType.MOVIE,
            status=ConsumptionStatus.COMPLETED,
            rating=rating,
            metadata={"genre": genre},
        )
    )


class TestIdlessCandidateIdentityRegression:
    """Bug reported: items added without an external id showed each other's reasons."""

    def test_each_id_less_candidate_keeps_its_own_reasoning_regression(
        self, real_engine, real_storage
    ):
        _save_movie(
            real_storage, title="Blade Runner", genre="Science Fiction", rating=5
        )
        _save_movie(real_storage, title="Knives Out", genre="Mystery", rating=5)
        _save_book(
            real_storage,
            item_id=None,
            title="Hyperion",
            status=ConsumptionStatus.UNREAD,
            genre="Science Fiction",
        )
        _save_book(
            real_storage,
            item_id=None,
            title="The Silent Patient",
            status=ConsumptionStatus.CURRENTLY_CONSUMING,
            genre="Mystery",
        )

        recs = real_engine.generate_recommendations(
            content_type=ContentType.BOOK, count=5
        )
        by_title = {rec.item.title: rec for rec in recs}

        assert set(by_title) == {"Hyperion", "The Silent Patient"}
        assert {item.title for item in by_title["Hyperion"].contributing_items} == {
            "Blade Runner"
        }
        assert {
            item.title for item in by_title["The Silent Patient"].contributing_items
        } == {"Knives Out"}
        # Only the in-progress book is a continuation, so the two breakdowns
        # differ and each card must carry its own.
        assert by_title["Hyperion"].score_breakdown["continuation"] == 0.0
        assert by_title["The Silent Patient"].score_breakdown["continuation"] == 1.0


class TestSeasonCandidateIdentityRegression:
    """Bug reported: for a show added without an external id, the recommended
    season's Score Details belonged to a different season of the same show."""

    def test_season_card_keeps_its_own_breakdown_regression(
        self, real_engine, real_storage
    ):
        real_storage.save_content_item(
            ContentItem(
                id=None,
                title="Watched Show",
                content_type=ContentType.TV_SHOW,
                status=ConsumptionStatus.COMPLETED,
                rating=5,
                metadata={"genre": "Drama"},
            )
        )
        real_storage.save_content_item(
            ContentItem(
                id=None,
                title="Uncharted Depths",
                content_type=ContentType.TV_SHOW,
                status=ConsumptionStatus.UNREAD,
                metadata={"genre": "Drama", "total_seasons": 2},
            )
        )

        recs = real_engine.generate_recommendations(
            content_type=ContentType.TV_SHOW,
            count=5,
            user_preference_config=UserPreferenceConfig(series_in_order=False),
        )

        assert [rec.item.title for rec in recs] == ["Uncharted Depths (Season 1)"]
        assert recs[0].score_breakdown["series_order"] == 0.8


class TestSeasonSiblingsStayDistinctInEveryMap:
    """They share the parent show's ``db_id``, and for a show added without an
    external id they share a ``None`` id too, so they are the pair most likely
    to collapse in any per-candidate map."""

    @staticmethod
    def _seasons():
        return expand_tv_shows_to_seasons(
            [
                ContentItem(
                    id=None,
                    db_id=5,
                    title="Uncharted Depths",
                    content_type=ContentType.TV_SHOW,
                    status=ConsumptionStatus.UNREAD,
                    metadata={"total_seasons": 2},
                )
            ]
        )

    def test_each_season_keeps_its_own_breakdown_and_adaptations(self):
        first, second = self._seasons()
        engine = _engine_for_helpers()
        knives_out = make_item(
            item_id="m2", title="Knives Out", content_type=ContentType.MOVIE, rating=5
        )

        recommendations = engine._format_recommendations(
            [(first, 1.0, 0.0), (second, 0.5, 0.0)],
            {
                candidate_key(first): {"series_order": 0.8},
                candidate_key(second): {"series_order": 0.2},
            },
            {candidate_key(second): [knives_out]},
            SignalIndex([]),
            PreferenceAnalyzer(min_rating=4).analyze([]),
        )

        assert [rec.score_breakdown for rec in recommendations] == [
            {"series_order": 0.8},
            {"series_order": 0.2},
        ]
        assert [
            [item.title for item in rec.adaptations] for rec in recommendations
        ] == [[], ["Knives Out"]]

    def test_series_filtering_keeps_a_season_from_each_id_less_show(self):
        """Season 2 of a show is legitimately filtered out behind season 1, so the
        pair that proves the dedup set is not collapsing on a shared ``None`` id is
        one season from each of two different shows."""

        def season_one_of(db_id, title):
            (season,) = expand_tv_shows_to_seasons(
                [
                    ContentItem(
                        id=None,
                        db_id=db_id,
                        title=title,
                        content_type=ContentType.TV_SHOW,
                        status=ConsumptionStatus.UNREAD,
                        metadata={"total_seasons": 1},
                    )
                ]
            )
            return season

        first = season_one_of(5, "Uncharted Depths")
        second = season_one_of(6, "Northern Lights")
        engine = _engine_for_helpers()
        pipeline_scored = [
            ScoredCandidate(item=first, aggregate_score=1.0, score_breakdown={}),
            ScoredCandidate(item=second, aggregate_score=0.5, score_breakdown={}),
        ]

        filtered = engine._apply_series_filtering(pipeline_scored, {}, [first, second])

        assert [scored.item.title for scored in filtered] == [
            "Uncharted Depths (Season 1)",
            "Northern Lights (Season 1)",
        ]


class TestContentTypeExclusions:
    """``tests/test_preference_interpreter.py`` covers the interpreter producing
    ``content_type_exclusions``."""

    @staticmethod
    def _consumed():
        return ContentItem(
            id="c1",
            title="Dune",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            rating=5,
            metadata={"genre": "Science Fiction"},
        )

    @staticmethod
    def _book():
        return ContentItem(
            id="b1",
            title="Hyperion",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
            metadata={"genre": "Science Fiction"},
        )

    @staticmethod
    def _movie(content_type=ContentType.MOVIE):
        return ContentItem(
            id="m1",
            title="Blade Runner",
            content_type=content_type,
            status=ConsumptionStatus.UNREAD,
            metadata={"genre": "Science Fiction"},
        )

    def _recommend(self, engine, mock_storage, candidates, rules):
        mock_storage.get_completed_items = Mock(
            side_effect=lambda content_type=None, **kwargs: [self._consumed()]
        )
        mock_storage.get_unconsumed_items = Mock(return_value=candidates)

        return engine.generate_recommendations(
            content_type=ContentType.BOOK,
            count=5,
            user_preference_config=UserPreferenceConfig(custom_rules=rules),
        )

    def test_an_excluded_type_is_dropped_and_the_rest_survive(
        self, engine, mock_storage
    ):
        recommendations = self._recommend(
            engine, mock_storage, [self._book(), self._movie()], ["avoid movies"]
        )

        assert [rec.item.id for rec in recommendations] == ["b1"]

    def test_a_rule_with_no_exclusion_leaves_every_candidate(
        self, engine, mock_storage
    ):
        recommendations = self._recommend(
            engine, mock_storage, [self._book(), self._movie()], ["avoid horror"]
        )

        assert {rec.item.id for rec in recommendations} == {"b1", "m1"}


class TestCrossTypeRun:
    """Naming no type once meant no run at all, so one ranked list across the
    four types was unreachable from either interface."""

    @staticmethod
    def _candidate(title, content_type, genre):
        return ContentItem(
            id=title,
            title=title,
            content_type=content_type,
            status=ConsumptionStatus.UNREAD,
            metadata={"genre": genre},
        )

    def _back_storage(self, mock_storage, per_type_candidates):
        loved = [
            ContentItem(
                id=f"loved-{content_type.value}",
                title=f"Loved {content_type.value}",
                content_type=content_type,
                status=ConsumptionStatus.COMPLETED,
                rating=5,
                metadata={"genre": "Science Fiction"},
            )
            for content_type in ContentType
        ]
        mock_storage.get_completed_items = Mock(
            side_effect=lambda content_type=None, **kwargs: [
                item
                for item in loved
                if content_type is None or item.content_type == content_type
            ]
        )
        mock_storage.get_unconsumed_items = Mock(
            side_effect=lambda content_type=None, **kwargs: per_type_candidates(
                content_type
            )
        )

    def test_a_run_naming_no_type_ranks_all_four_in_one_list(
        self, engine, mock_storage
    ):
        self._back_storage(
            mock_storage,
            lambda content_type: [
                self._candidate(
                    f"{content_type.value} pick", content_type, "Science Fiction"
                )
            ],
        )

        recommendations = engine.generate_recommendations(count=4)

        assert {get_enum_value(rec.item.content_type) for rec in recommendations} == {
            content_type.value for content_type in ContentType
        }
        scores = [rec.score for rec in recommendations]
        assert scores == sorted(scores, reverse=True)

    def test_count_is_the_size_of_the_merged_list_not_a_count_per_type(
        self, engine, mock_storage
    ):
        self._back_storage(
            mock_storage,
            lambda content_type: [
                self._candidate(
                    f"{content_type.value} {rank}", content_type, "Science Fiction"
                )
                for rank in range(3)
            ],
        )

        recommendations = engine.generate_recommendations(count=2)

        assert len(recommendations) == 2

    def test_naming_a_type_still_ranks_that_type_alone(self, engine, mock_storage):
        self._back_storage(
            mock_storage,
            lambda content_type: [
                self._candidate(
                    f"{content_type.value} pick", content_type, "Science Fiction"
                )
            ],
        )

        recommendations = engine.generate_recommendations(
            content_type=ContentType.MOVIE, count=4
        )

        assert [rec.item.title for rec in recommendations] == ["movie pick"]


class TestObjectShapedGenresFromStorage:
    """A genre column filled with TMDB's objects must not break a run."""

    def test_an_object_shaped_genre_survives_the_reason_path(
        self, real_engine, real_storage
    ):
        _save_movie(
            real_storage, title="Blade Runner", genre="Science Fiction", rating=5
        )
        real_storage.save_content_item(
            ContentItem(
                id="tmdb-275",
                title="The Big Sleep",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.UNREAD,
                metadata={"genres": '[{"id": 80, "name": "Crime"}]'},
            )
        )

        recs = real_engine.generate_recommendations(
            content_type=ContentType.BOOK, count=5
        )

        assert [rec.item.title for rec in recs] == ["The Big Sleep"]
        assert recs[0].item.metadata["genres"] == ["Crime"]
        assert recs[0].reasoning == "Recommended based on your preferences"
