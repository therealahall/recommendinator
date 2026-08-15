"""Tests for the individual scorers and ScoringContext."""

from src.models.content import ConsumptionStatus, ContentItem, ContentType
from src.recommendations.identity import candidate_key
from src.recommendations.preferences import PreferenceAnalyzer
from src.recommendations.scorers import (
    AdaptationScorer,
    ContentLengthScorer,
    ContinuationScorer,
    CreatorMatchScorer,
    CustomPreferenceScorer,
    GenreMatchScorer,
    RatingPatternScorer,
    ScoringContext,
    SeriesAffinityScorer,
    SeriesOrderScorer,
    TagOverlapScorer,
    build_scorers_with_overrides,
    extract_creator,
    extract_genres,
)
from src.utils.series import (
    build_series_tracking,
    should_recommend_item,
)
from tests.factories import make_item


def _build_context(
    consumed: list[ContentItem] | None = None,
    unconsumed: list[ContentItem] | None = None,
    content_type: ContentType = ContentType.BOOK,
) -> ScoringContext:
    consumed = consumed or []
    unconsumed = unconsumed or []
    analyzer = PreferenceAnalyzer(min_rating=4)
    preferences = analyzer.analyze(consumed)
    series_tracking = build_series_tracking(consumed)
    return ScoringContext(
        preferences=preferences,
        consumed_items=consumed,
        series_tracking=series_tracking,
        content_type=content_type,
        all_unconsumed_items=unconsumed,
    )


# ---------------------------------------------------------------------------
# Helper function tests
# ---------------------------------------------------------------------------


class TestExtractGenres:
    def test_both_genre_and_genres(self) -> None:
        item = make_item(metadata={"genre": "Sci-Fi", "genres": ["Action"]})
        result = extract_genres(item)
        # "sci-fi" is normalized to "science fiction"
        assert "science fiction" in result
        assert "action" in result

    def test_tags_included_for_cross_content_matching(self) -> None:
        """Tags should be extracted alongside genres for cross-content-type matching."""
        item = make_item(
            metadata={"genres": ["Fantasy"], "tags": ["epic", "adventure"]}
        )
        result = extract_genres(item)
        assert "fantasy" in result
        assert "epic" in result
        assert "adventure" in result

    def test_tags_list_as_string(self) -> None:
        """Tags as comma-separated string should be extracted."""
        item = make_item(metadata={"tags": "sci-fi, space opera"})
        result = extract_genres(item)
        # "sci-fi" is normalized to "science fiction"
        assert "science fiction" in result
        assert "space opera" in result


class TestExtractCreator:
    def test_author_field(self) -> None:
        item = make_item(author="Brandon Sanderson")
        assert extract_creator(item) == "brandon sanderson"

    def test_director_metadata(self) -> None:
        item = make_item(metadata={"director": "Christopher Nolan"})
        assert extract_creator(item) == "christopher nolan"


# ---------------------------------------------------------------------------
# ScoringContext tests
# ---------------------------------------------------------------------------


class TestScoringContext:
    def test_ratings_by_genre(self) -> None:
        consumed = [
            make_item(rating=5, metadata={"genre": "Fantasy"}),
            make_item(rating=3, metadata={"genre": "Fantasy"}),
        ]
        context = _build_context(consumed=consumed)
        assert context.ratings_by_genre["fantasy"] == [5, 3]


# ---------------------------------------------------------------------------
# GenreMatchScorer tests
# ---------------------------------------------------------------------------


class TestGenreMatchScorer:
    def test_preferred_genre_scores_high(self) -> None:
        consumed = [make_item(rating=5, metadata={"genre": "Fantasy"})]
        context = _build_context(consumed=consumed)
        candidate = make_item(
            status=ConsumptionStatus.UNREAD, metadata={"genre": "Fantasy"}
        )
        scorer = GenreMatchScorer()
        score = scorer.score(candidate, context)
        assert score > 0.5

    def test_disliked_genre_scores_low(self) -> None:
        consumed = [make_item(rating=1, metadata={"genre": "Romance"})]
        context = _build_context(consumed=consumed)
        candidate = make_item(
            status=ConsumptionStatus.UNREAD, metadata={"genre": "Romance"}
        )
        scorer = GenreMatchScorer()
        score = scorer.score(candidate, context)
        assert score < 0.5

    def test_no_genre_returns_neutral(self) -> None:
        context = _build_context(consumed=[])
        candidate = make_item(status=ConsumptionStatus.UNREAD)
        scorer = GenreMatchScorer()
        assert scorer.score(candidate, context) == 0.5


# ---------------------------------------------------------------------------
# CreatorMatchScorer tests
# ---------------------------------------------------------------------------


class TestCreatorMatchScorer:
    def test_preferred_author_scores_high(self) -> None:
        consumed = [make_item(author="Brandon Sanderson", rating=5)]
        context = _build_context(consumed=consumed)
        candidate = make_item(
            author="Brandon Sanderson", status=ConsumptionStatus.UNREAD
        )
        scorer = CreatorMatchScorer()
        score = scorer.score(candidate, context)
        assert score > 0.5


# ---------------------------------------------------------------------------
# TagOverlapScorer tests
# ---------------------------------------------------------------------------


class TestTagOverlapScorer:
    """Tests for threshold-based tag overlap scoring.

    Scoring thresholds:
    - 5+ matches: 1.0
    - 4 matches: 0.9
    - 3 matches: 0.8
    - 2 matches: 0.5
    - 1 match: 0.3
    - 0 matches: 0.0
    """

    def test_no_overlap(self) -> None:
        """No matching genres should score 0.0."""
        consumed = [make_item(metadata={"genre": "Fantasy"}, rating=5)]
        context = _build_context(consumed=consumed)
        candidate = make_item(
            status=ConsumptionStatus.UNREAD, metadata={"genre": "Comedy"}
        )
        scorer = TagOverlapScorer()
        assert scorer.score(candidate, context) == 0.0

    def test_two_matches_scores_medium(self) -> None:
        """Two matching genres should score 0.5."""
        consumed = [make_item(metadata={"genres": ["Fantasy", "Action"]}, rating=5)]
        context = _build_context(consumed=consumed)
        candidate = make_item(
            status=ConsumptionStatus.UNREAD,
            metadata={"genres": ["Action", "Fantasy", "Horror"]},
        )
        scorer = TagOverlapScorer()
        assert scorer.score(candidate, context) == 0.5

    def test_cluster_match_provides_semantic_floor(self) -> None:
        """Candidate with 'space warfare' should score well against consumed 'war'
        via shared cluster even without direct term overlap."""
        consumed = [make_item(metadata={"genres": ["War"]}, rating=5)]
        context = _build_context(consumed=consumed)
        candidate = make_item(
            status=ConsumptionStatus.UNREAD,
            metadata={"genres": ["Space Warfare"]},
        )
        scorer = TagOverlapScorer()
        score = scorer.score(candidate, context)
        # No direct overlap, but cluster match should give > 0.0
        assert score > 0.0


class TestScoringContextClusters:
    """Tests for consumed_clusters in ScoringContext."""

    def test_consumed_clusters_populated(self) -> None:
        """ScoringContext should populate consumed_clusters from genres."""
        consumed = [
            make_item(rating=5, metadata={"genre": "Science Fiction"}),
            make_item(rating=5, metadata={"genre": "Fantasy"}),
        ]
        context = _build_context(consumed=consumed)
        assert "science_fiction" in context.consumed_clusters
        assert "fantasy" in context.consumed_clusters


# ---------------------------------------------------------------------------
# SeriesOrderScorer tests
# ---------------------------------------------------------------------------


class TestSeriesOrderScorer:
    def test_next_in_sequence_high_rating(self) -> None:
        """Next in series with high rating (5) should score 1.0."""
        consumed = [
            make_item(title="Mistborn (Mistborn, #1)", rating=5),
        ]
        context = _build_context(consumed=consumed)
        candidate = make_item(
            title="Mistborn (Mistborn, #2)", status=ConsumptionStatus.UNREAD
        )
        scorer = SeriesOrderScorer()
        assert scorer.score(candidate, context) == 1.0

    def test_next_in_sequence_very_low_rating(self) -> None:
        """Next in series with very low rating (1) should score ~0.6."""
        consumed = [
            make_item(title="Mistborn (Mistborn, #1)", rating=1),
        ]
        context = _build_context(consumed=consumed)
        candidate = make_item(
            title="Mistborn (Mistborn, #2)", status=ConsumptionStatus.UNREAD
        )
        scorer = SeriesOrderScorer()
        score = scorer.score(candidate, context)
        assert 0.55 <= score <= 0.65  # Should be around 0.6

    def test_next_in_sequence_no_rating(self) -> None:
        """Next in series with no rating should score ~0.85 (default)."""
        consumed = [
            make_item(title="Mistborn (Mistborn, #1)", rating=None),
        ]
        context = _build_context(consumed=consumed)
        candidate = make_item(
            title="Mistborn (Mistborn, #2)", status=ConsumptionStatus.UNREAD
        )
        scorer = SeriesOrderScorer()
        score = scorer.score(candidate, context)
        assert score == 0.85

    def test_next_in_sequence_average_of_multiple_books(self) -> None:
        """Rating boost should use average of all consumed books in series."""
        consumed = [
            make_item(title="Mistborn (Mistborn, #1)", rating=5),
            make_item(title="Mistborn (Mistborn, #2)", rating=3),
        ]
        context = _build_context(consumed=consumed)
        candidate = make_item(
            title="Mistborn (Mistborn, #3)", status=ConsumptionStatus.UNREAD
        )
        scorer = SeriesOrderScorer()
        score = scorer.score(candidate, context)
        # Average rating is 4.0, so should score 1.0
        assert score == 1.0

    def test_first_in_unstarted_series(self) -> None:
        context = _build_context(consumed=[])
        candidate = make_item(title="Dune (Dune, #1)", status=ConsumptionStatus.UNREAD)
        scorer = SeriesOrderScorer()
        assert scorer.score(candidate, context) == 0.8

    def test_too_far_ahead(self) -> None:
        consumed = [make_item(title="Mistborn (Mistborn, #1)", rating=5)]
        context = _build_context(consumed=consumed)
        candidate = make_item(
            title="Mistborn (Mistborn, #5)", status=ConsumptionStatus.UNREAD
        )
        scorer = SeriesOrderScorer()
        assert scorer.score(candidate, context) == 0.3

    def test_candidate_at_max_consumed_scores_low(self) -> None:
        """Candidate at the same position as max consumed should score 0.2.

        When the user has consumed item #3, a candidate that is also #3
        is already consumed (or a duplicate) and should be deprioritized.
        """
        consumed = [
            make_item(title="Mistborn (Mistborn, #1)", rating=5),
            make_item(title="Mistborn (Mistborn, #2)", rating=4),
            make_item(title="Mistborn (Mistborn, #3)", rating=4),
        ]
        context = _build_context(consumed=consumed)
        candidate = make_item(
            title="Mistborn (Mistborn, #3)", status=ConsumptionStatus.UNREAD
        )
        scorer = SeriesOrderScorer()
        assert scorer.score(candidate, context) == 0.2

    def test_non_series_neutral(self) -> None:
        context = _build_context(consumed=[])
        candidate = make_item(title="Standalone Novel", status=ConsumptionStatus.UNREAD)
        scorer = SeriesOrderScorer()
        assert scorer.score(candidate, context) == 0.5


class TestSeriesOrderFractionalPositions:
    """Regression: a half-numbered novella scored as already consumed.

    Symptom: with The Expanse #1 and #2 read, "Gods of Risk (The Expanse, #2.5)"
    scored 0.2, below the 0.3 given to an entry that is too far ahead, so the
    novella series filtering had just unblocked ranked under unrelated books.

    Root cause: SeriesOrderScorer recognised succession as
    ``item_number == max_consumed + 1``, which no fractional position satisfies,
    so #2.5 fell through to the already-consumed branch.

    Fix: the scorer asks ``is_next_after_consumed`` in src/utils/series.py which
    entry comes next, the same fractional ordering ``should_recommend_item``
    already applied.
    """

    @staticmethod
    def _consumed() -> list[ContentItem]:
        return [
            make_item(title="Leviathan Wakes (The Expanse, #1)", rating=5),
            make_item(title="Caliban's War (The Expanse, #2)", rating=5),
        ]

    def test_next_up_half_numbered_entry_scores_as_next_regression(self) -> None:
        context = _build_context(consumed=self._consumed())
        novella = make_item(
            title="Gods of Risk (The Expanse, #2.5)", status=ConsumptionStatus.UNREAD
        )
        score = SeriesOrderScorer().score(novella, context)
        assert score == 1.0  # 5-star series average
        assert score > 0.3  # beats the "too far ahead" bucket

    def test_scorer_agrees_with_should_recommend_item_regression(self) -> None:
        consumed = self._consumed()
        novella = make_item(
            title="Gods of Risk (The Expanse, #2.5)", status=ConsumptionStatus.UNREAD
        )
        book_three = make_item(
            title="Abaddon's Gate (The Expanse, #3)", status=ConsumptionStatus.UNREAD
        )
        unconsumed = [novella, book_three]
        context = _build_context(consumed=consumed, unconsumed=unconsumed)
        scorer = SeriesOrderScorer()

        for candidate in unconsumed:
            recommended = should_recommend_item(
                candidate, context.series_tracking, unconsumed_items=unconsumed
            )
            assert (scorer.score(candidate, context) > 0.3) is recommended

        assert scorer.score(novella, context) == 1.0
        assert scorer.score(book_three, context) == 0.3


class TestSeriesOrderFractionalBoundaries:
    """Boundaries of the fractional next-in-sequence rule.

    Each case pins the scorer against ``should_recommend_item`` so the two
    cannot drift apart again, and covers the positions the happy path misses:
    a consumed novella, a novella still blocked by the book before it, two
    novellas competing for the same slot, another series' entries, and a
    non-ASCII series name.
    """

    def test_consumed_novella_advances_to_the_next_whole_number(self) -> None:
        consumed = [
            make_item(title="Leviathan Wakes (The Expanse, #1)", rating=5),
            make_item(title="Caliban's War (The Expanse, #2)", rating=5),
            make_item(title="Gods of Risk (The Expanse, #2.5)", rating=5),
        ]
        book_three = make_item(
            title="Abaddon's Gate (The Expanse, #3)", status=ConsumptionStatus.UNREAD
        )
        unconsumed = [book_three]
        context = _build_context(consumed=consumed, unconsumed=unconsumed)

        assert should_recommend_item(
            book_three, context.series_tracking, unconsumed_items=unconsumed
        )
        assert SeriesOrderScorer().score(book_three, context) == 1.0

    def test_half_numbered_entry_waits_for_the_book_before_it(self) -> None:
        consumed = [make_item(title="Leviathan Wakes (The Expanse, #1)", rating=5)]
        book_two = make_item(
            title="Caliban's War (The Expanse, #2)", status=ConsumptionStatus.UNREAD
        )
        novella = make_item(
            title="Gods of Risk (The Expanse, #2.5)", status=ConsumptionStatus.UNREAD
        )
        unconsumed = [book_two, novella]
        context = _build_context(consumed=consumed, unconsumed=unconsumed)
        scorer = SeriesOrderScorer()

        assert should_recommend_item(
            book_two, context.series_tracking, unconsumed_items=unconsumed
        )
        assert not should_recommend_item(
            novella, context.series_tracking, unconsumed_items=unconsumed
        )
        assert scorer.score(book_two, context) == 1.0
        assert scorer.score(novella, context) == 0.3

    def test_earlier_of_two_novellas_takes_the_next_slot(self) -> None:
        consumed = [
            make_item(title="Leviathan Wakes (The Expanse, #1)", rating=5),
            make_item(title="Caliban's War (The Expanse, #2)", rating=5),
        ]
        first_novella = make_item(
            title="The Butcher of Anderson Station (The Expanse, #2.1)",
            status=ConsumptionStatus.UNREAD,
        )
        later_novella = make_item(
            title="Gods of Risk (The Expanse, #2.5)", status=ConsumptionStatus.UNREAD
        )
        unconsumed = [later_novella, first_novella]
        context = _build_context(consumed=consumed, unconsumed=unconsumed)
        scorer = SeriesOrderScorer()

        for candidate in unconsumed:
            recommended = should_recommend_item(
                candidate, context.series_tracking, unconsumed_items=unconsumed
            )
            assert (scorer.score(candidate, context) > 0.3) is recommended
        assert scorer.score(first_novella, context) == 1.0
        assert scorer.score(later_novella, context) == 0.3


class TestSeriesOrderNextInSequenceEdges:
    """Paths into the next-in-sequence branch the fractional cases do not reach.

    Season-level TV candidates, a series with no ratings, a position carried in
    metadata rather than the title, and a series long enough that the whole tail
    of it is unconsumed.
    """

    def test_tv_seasons_advance_to_the_next_unwatched_season(self) -> None:
        consumed = [
            make_item(
                title="The Expanse (The Expanse, Season 1)",
                content_type=ContentType.TV_SHOW,
                rating=5,
            ),
            make_item(
                title="The Expanse (The Expanse, Season 2)",
                content_type=ContentType.TV_SHOW,
                rating=5,
            ),
        ]
        season_three = make_item(
            title="The Expanse (The Expanse, Season 3)",
            content_type=ContentType.TV_SHOW,
            status=ConsumptionStatus.UNREAD,
        )
        season_five = make_item(
            title="The Expanse (The Expanse, Season 5)",
            content_type=ContentType.TV_SHOW,
            status=ConsumptionStatus.UNREAD,
        )
        unconsumed = [season_three, season_five]
        context = _build_context(
            consumed=consumed,
            unconsumed=unconsumed,
            content_type=ContentType.TV_SHOW,
        )
        scorer = SeriesOrderScorer()

        for candidate in unconsumed:
            recommended = should_recommend_item(
                candidate, context.series_tracking, unconsumed_items=unconsumed
            )
            assert (scorer.score(candidate, context) > 0.3) is recommended
        assert scorer.score(season_three, context) == 1.0
        assert scorer.score(season_five, context) == 0.3

    def test_fractional_position_from_metadata_scores_as_next(self) -> None:
        consumed = [
            make_item(title="Leviathan Wakes (The Expanse, #1)", rating=5),
            make_item(title="Caliban's War (The Expanse, #2)", rating=5),
        ]
        novella = make_item(
            title="Gods of Risk",
            status=ConsumptionStatus.UNREAD,
            metadata={"series_name": "The Expanse", "series_position": "2.5"},
        )
        book_three = make_item(
            title="Abaddon's Gate (The Expanse, #3)", status=ConsumptionStatus.UNREAD
        )
        context = _build_context(consumed=consumed, unconsumed=[novella, book_three])
        scorer = SeriesOrderScorer()

        assert scorer.score(novella, context) == 1.0
        assert scorer.score(book_three, context) == 0.3


# ---------------------------------------------------------------------------
# RatingPatternScorer tests
# ---------------------------------------------------------------------------


class TestRatingPatternScorer:
    def test_high_average_in_genre(self) -> None:
        consumed = [
            make_item(rating=5, metadata={"genre": "Fantasy"}),
            make_item(rating=5, metadata={"genre": "Fantasy"}),
        ]
        context = _build_context(consumed=consumed)
        candidate = make_item(
            status=ConsumptionStatus.UNREAD, metadata={"genre": "Fantasy"}
        )
        scorer = RatingPatternScorer()
        score = scorer.score(candidate, context)
        # average=5 => (5-1)/4 = 1.0
        assert score == 1.0

    def test_no_matching_genre_neutral(self) -> None:
        consumed = [make_item(rating=5, metadata={"genre": "Fantasy"})]
        context = _build_context(consumed=consumed)
        candidate = make_item(
            status=ConsumptionStatus.UNREAD, metadata={"genre": "Romance"}
        )
        scorer = RatingPatternScorer()
        assert scorer.score(candidate, context) == 0.5


# ---------------------------------------------------------------------------
# build_scorers_with_overrides tests
# ---------------------------------------------------------------------------


class TestScorerClone:
    """Tests for the Scorer.clone() method."""

    def test_clone_custom_preference_preserves_args(self) -> None:
        """Cloning a CustomPreferenceScorer preserves genre_boosts and genre_penalties."""
        scorer = CustomPreferenceScorer(
            genre_boosts={"fantasy": 1.0},
            genre_penalties={"horror": 0.8},
            weight=2.0,
        )
        cloned = scorer.clone(weight=3.0)
        assert isinstance(cloned, CustomPreferenceScorer)
        assert cloned.weight == 3.0
        assert cloned.genre_boosts == {"fantasy": 1.0}
        assert cloned.genre_penalties == {"horror": 0.8}

    def test_clone_does_not_share_dicts(self) -> None:
        """Cloned CustomPreferenceScorer has independent copies of dicts."""
        scorer = CustomPreferenceScorer(
            genre_boosts={"fantasy": 1.0},
            weight=2.0,
        )
        cloned = scorer.clone(weight=3.0)
        cloned.genre_boosts["sci-fi"] = 0.5
        assert "sci-fi" not in scorer.genre_boosts


class TestBuildScorersWithOverrides:
    def test_partial_override(self) -> None:
        """Only specified scorers have their weight changed."""
        base = [
            GenreMatchScorer(weight=2.0),
            CreatorMatchScorer(weight=1.5),
            TagOverlapScorer(weight=1.0),
        ]
        overrides = {"genre_match": 5.0}
        result = build_scorers_with_overrides(base, overrides)
        assert result[0].weight == 5.0
        assert isinstance(result[0], GenreMatchScorer)
        assert result[1].weight == 1.5  # unchanged
        assert result[2].weight == 1.0  # unchanged

    def test_does_not_mutate_originals(self) -> None:
        """Original scorer list and instances are not mutated."""
        base = [GenreMatchScorer(weight=2.0)]
        build_scorers_with_overrides(base, {"genre_match": 9.0})
        assert base[0].weight == 2.0


# ---------------------------------------------------------------------------
# CustomPreferenceScorer tests
# ---------------------------------------------------------------------------


class TestCustomPreferenceScorer:
    """Tests for the CustomPreferenceScorer."""

    def test_genre_boost_scores_high(self) -> None:
        """Items matching a boosted genre should score above 0.5."""
        candidate = make_item(
            metadata={"genre": "horror"}, status=ConsumptionStatus.UNREAD
        )
        context = _build_context(consumed=[])
        scorer = CustomPreferenceScorer(genre_boosts={"horror": 1.0})
        score = scorer.score(candidate, context)
        assert score == 1.0

    def test_genre_penalty_scores_low(self) -> None:
        """Items matching a penalized genre should score below 0.5."""
        candidate = make_item(
            metadata={"genre": "romance"}, status=ConsumptionStatus.UNREAD
        )
        context = _build_context(consumed=[])
        scorer = CustomPreferenceScorer(genre_penalties={"romance": 1.0})
        score = scorer.score(candidate, context)
        assert score == 0.0

    def test_partial_boost(self) -> None:
        """Partial boost factor maps proportionally."""
        candidate = make_item(
            metadata={"genre": "mystery"}, status=ConsumptionStatus.UNREAD
        )
        context = _build_context(consumed=[])
        scorer = CustomPreferenceScorer(genre_boosts={"mystery": 0.5})
        score = scorer.score(candidate, context)
        assert score == 0.75  # 0.5 + (0.5 * 0.5)

    def test_no_matching_rules_returns_neutral(self) -> None:
        """Items not matching any rule should return 0.5."""
        candidate = make_item(
            metadata={"genre": "drama"}, status=ConsumptionStatus.UNREAD
        )
        context = _build_context(consumed=[])
        scorer = CustomPreferenceScorer(
            genre_boosts={"comedy": 1.0}, genre_penalties={"horror": 1.0}
        )
        score = scorer.score(candidate, context)
        assert score == 0.5

    def test_penalty_takes_precedence_over_boost(self) -> None:
        """When a genre has both boost and penalty, penalty wins."""
        candidate = make_item(
            metadata={"genres": ["horror", "comedy"]}, status=ConsumptionStatus.UNREAD
        )
        context = _build_context(consumed=[])
        # horror is penalized, comedy is boosted
        scorer = CustomPreferenceScorer(
            genre_boosts={"comedy": 1.0}, genre_penalties={"horror": 1.0}
        )
        score = scorer.score(candidate, context)
        # Penalty should be checked first
        assert score == 0.0


# ---------------------------------------------------------------------------
# ContentLengthScorer tests
# ---------------------------------------------------------------------------


class TestContentLengthScorer:
    """Tests for the ContentLengthScorer."""

    def test_no_preferences_returns_neutral(self) -> None:
        """No content_length_preferences in context returns 0.5 (neutral)."""
        candidate = make_item(
            content_type=ContentType.BOOK,
            metadata={"pages": 800},
            status=ConsumptionStatus.UNREAD,
        )
        context = _build_context(consumed=[])
        scorer = ContentLengthScorer()
        assert scorer.score(candidate, context) == 0.5

    def test_exact_match_returns_1(self) -> None:
        """Short book with short preference returns 1.0."""
        candidate = make_item(
            content_type=ContentType.BOOK,
            metadata={"pages": 200},
            status=ConsumptionStatus.UNREAD,
        )
        context = _build_context(consumed=[])
        context.content_length_preferences = {"book": "short"}
        scorer = ContentLengthScorer()
        assert scorer.score(candidate, context) == 1.0

    def test_opposite_returns_04(self) -> None:
        """Long book with short preference returns 0.4."""
        candidate = make_item(
            content_type=ContentType.BOOK,
            metadata={"pages": 800},
            status=ConsumptionStatus.UNREAD,
        )
        context = _build_context(consumed=[])
        context.content_length_preferences = {"book": "short"}
        scorer = ContentLengthScorer()
        assert scorer.score(candidate, context) == 0.4

    def test_video_game_with_only_own_hours_is_benefit_of_the_doubt(self) -> None:
        """300 logged hours are the player's, not the game's, so no penalty lands.

        The scorer is the path the engine actually calls, so this pins the
        product behaviour rather than the helper: unenriched games take 0.8,
        not the 0.4 an opposite-end classification would cost them.
        """
        candidate = make_item(
            content_type=ContentType.VIDEO_GAME,
            metadata={"playtime_minutes": 18000, "playtime_hours": 300.0},
            status=ConsumptionStatus.UNREAD,
        )
        context = _build_context(consumed=[], content_type=ContentType.VIDEO_GAME)
        context.content_length_preferences = {"video_game": "short"}
        scorer = ContentLengthScorer()
        assert scorer.score(candidate, context) == 0.8

    def test_video_game_long_average_still_penalised(self) -> None:
        """Dropping own hours does not disarm the scorer: RAWG's average bites."""
        candidate = make_item(
            content_type=ContentType.VIDEO_GAME,
            metadata={"average_playtime_hours": 90, "playtime_hours": 0.5},
            status=ConsumptionStatus.UNREAD,
        )
        context = _build_context(consumed=[], content_type=ContentType.VIDEO_GAME)
        context.content_length_preferences = {"video_game": "short"}
        scorer = ContentLengthScorer()
        assert scorer.score(candidate, context) == 0.4


# ---------------------------------------------------------------------------
# ContinuationScorer tests
# ---------------------------------------------------------------------------


class TestContinuationScorer:
    """Tests for the ContinuationScorer.

    Items with CURRENTLY_CONSUMING status score 1.0; all others score 0.0.
    Default weight: 2.0.
    """

    def test_currently_consuming_scores_1(self) -> None:
        """Items the user is actively consuming should score 1.0."""
        candidate = make_item(
            title="Breaking Bad (Season 3)",
            content_type=ContentType.TV_SHOW,
            status=ConsumptionStatus.CURRENTLY_CONSUMING,
        )
        context = _build_context(consumed=[])
        scorer = ContinuationScorer()
        assert scorer.score(candidate, context) == 1.0

    def test_unread_scores_0(self) -> None:
        """Unread items should score 0.0."""
        candidate = make_item(
            title="The Wire (Season 1)",
            content_type=ContentType.TV_SHOW,
            status=ConsumptionStatus.UNREAD,
        )
        context = _build_context(consumed=[])
        scorer = ContinuationScorer()
        assert scorer.score(candidate, context) == 0.0


# ---------------------------------------------------------------------------
# SeriesAffinityScorer tests
# ---------------------------------------------------------------------------


class TestSeriesAffinityScorer:
    """Tests for the SeriesAffinityScorer.

    Items in a franchise the user has rated well (avg >= 4.0) score 1.0.
    Items in a franchise with lower ratings score 0.5 (neutral).
    Items not in a series or in a series with no consumed entries score 0.5.
    Default weight: 1.0.
    """

    def test_well_rated_series_scores_1(self) -> None:
        """Item in a series where user averaged 4+ should score 1.0."""
        consumed = [
            make_item(
                title="Final Fantasy I",
                content_type=ContentType.VIDEO_GAME,
                metadata={"series": "Final Fantasy", "series_number": 1},
                rating=5,
            ),
            make_item(
                title="Final Fantasy V",
                content_type=ContentType.VIDEO_GAME,
                metadata={"series": "Final Fantasy", "series_number": 5},
                rating=4,
            ),
        ]
        candidate = make_item(
            title="Final Fantasy VII",
            content_type=ContentType.VIDEO_GAME,
            metadata={"series": "Final Fantasy", "series_number": 7},
            status=ConsumptionStatus.UNREAD,
        )
        context = _build_context(consumed=consumed)
        scorer = SeriesAffinityScorer()
        assert scorer.score(candidate, context) == 1.0

    def test_poorly_rated_series_scores_neutral(self) -> None:
        """Item in a series where user averaged < 4.0 should score 0.5."""
        consumed = [
            make_item(
                title="Final Fantasy I",
                content_type=ContentType.VIDEO_GAME,
                metadata={"series": "Final Fantasy", "series_number": 1},
                rating=2,
            ),
            make_item(
                title="Final Fantasy V",
                content_type=ContentType.VIDEO_GAME,
                metadata={"series": "Final Fantasy", "series_number": 5},
                rating=3,
            ),
        ]
        candidate = make_item(
            title="Final Fantasy VII",
            content_type=ContentType.VIDEO_GAME,
            metadata={"series": "Final Fantasy", "series_number": 7},
            status=ConsumptionStatus.UNREAD,
        )
        context = _build_context(consumed=consumed)
        scorer = SeriesAffinityScorer()
        assert scorer.score(candidate, context) == 0.5

    def test_not_in_series_scores_neutral(self) -> None:
        """Item not in any series should score 0.5."""
        candidate = make_item(
            title="Standalone Game",
            content_type=ContentType.VIDEO_GAME,
            status=ConsumptionStatus.UNREAD,
        )
        context = _build_context(consumed=[])
        scorer = SeriesAffinityScorer()
        assert scorer.score(candidate, context) == 0.5

    def test_exactly_4_average_scores_1(self) -> None:
        """Boundary: average rating of exactly 4.0 should score 1.0."""
        consumed = [
            make_item(
                title="Dune (Dune, #1)",
                metadata={"series": "Dune", "series_number": 1},
                rating=4,
            ),
        ]
        candidate = make_item(
            title="Dune (Dune, #2)",
            metadata={"series": "Dune", "series_number": 2},
            status=ConsumptionStatus.UNREAD,
        )
        context = _build_context(consumed=consumed)
        scorer = SeriesAffinityScorer()
        assert scorer.score(candidate, context) == 1.0

    def test_unrated_consumed_entries_excluded_from_average(self) -> None:
        """Unrated consumed entries should not drag down the series average."""
        consumed = [
            make_item(
                title="Final Fantasy I",
                content_type=ContentType.VIDEO_GAME,
                metadata={"series": "Final Fantasy", "series_number": 1},
                rating=5,
            ),
            make_item(
                title="Final Fantasy II",
                content_type=ContentType.VIDEO_GAME,
                metadata={"series": "Final Fantasy", "series_number": 2},
                rating=None,
            ),
        ]
        candidate = make_item(
            title="Final Fantasy VII",
            content_type=ContentType.VIDEO_GAME,
            metadata={"series": "Final Fantasy", "series_number": 7},
            status=ConsumptionStatus.UNREAD,
        )
        context = _build_context(consumed=consumed)
        scorer = SeriesAffinityScorer()
        # Only the rated entry (5) counts; average is 5.0 >= 4.0 -> 1.0
        assert scorer.score(candidate, context) == 1.0


# ---------------------------------------------------------------------------
# AdaptationScorer tests
# ---------------------------------------------------------------------------


class TestAdaptationScorer:
    """Tests for the AdaptationScorer.

    The engine pre-computes each candidate's adaptations into the context.
    The best rating among them maps onto the 1-5 scale, and a candidate that
    adapts nothing scores 0.0.  Default weight: 1.5.
    """

    @staticmethod
    def _context_where(
        candidate: ContentItem, adapts: list[ContentItem]
    ) -> ScoringContext:
        """A context in which *candidate* adapts *adapts* and nothing else."""
        context = _build_context(consumed=[])
        context.adaptations = {candidate_key(candidate): adapts} if adapts else {}
        return context

    def test_five_star_adaptation_scores_1(self) -> None:
        """A source the user rated 5 gives the candidate the full score."""
        source = make_item(item_id="book", title="Dune", rating=5)
        candidate = make_item(
            item_id="film", title="Dune", content_type=ContentType.MOVIE
        )

        scorer = AdaptationScorer()

        assert scorer.score(candidate, self._context_where(candidate, [source])) == 1.0

    def test_four_star_adaptation_scores_below_a_five_star_one(self) -> None:
        """The rating carries through rather than flattening to one bonus."""
        source = make_item(item_id="book", title="Dune", rating=4)
        candidate = make_item(
            item_id="film", title="Dune", content_type=ContentType.MOVIE
        )

        scorer = AdaptationScorer()

        assert scorer.score(candidate, self._context_where(candidate, [source])) == 0.75

    def test_best_rated_source_wins(self) -> None:
        """With several sources, the best-rated one sets the score."""
        sources = [
            make_item(item_id="book", title="Dune", rating=4),
            make_item(item_id="game", title="Dune", rating=5),
        ]
        candidate = make_item(
            item_id="film", title="Dune", content_type=ContentType.MOVIE
        )

        scorer = AdaptationScorer()

        assert scorer.score(candidate, self._context_where(candidate, sources)) == 1.0

    def test_candidate_adapting_nothing_scores_0(self) -> None:
        """No entry in the map means no boost."""
        candidate = make_item(
            item_id="film", title="Solaris", content_type=ContentType.MOVIE
        )

        scorer = AdaptationScorer()

        assert scorer.score(candidate, self._context_where(candidate, [])) == 0.0

    def test_unrated_source_scores_0(self) -> None:
        """An adaptation of something unrated says nothing about taste."""
        source = make_item(item_id="book", title="Dune", rating=None)
        candidate = make_item(
            item_id="film", title="Dune", content_type=ContentType.MOVIE
        )

        scorer = AdaptationScorer()

        assert scorer.score(candidate, self._context_where(candidate, [source])) == 0.0

    def test_another_candidates_adaptations_do_not_leak(self) -> None:
        """A candidate reads only its own entry, never a sibling's."""
        source = make_item(item_id="book", title="Dune", rating=5)
        adapter = make_item(
            item_id="film", title="Dune", content_type=ContentType.MOVIE
        )
        other = make_item(
            item_id="other", title="Solaris", content_type=ContentType.MOVIE
        )

        scorer = AdaptationScorer()

        assert scorer.score(other, self._context_where(adapter, [source])) == 0.0
