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


class TestExtractGenres:
    def test_both_genre_and_genres(self) -> None:
        item = make_item(metadata={"genre": "Sci-Fi", "genres": ["Action"]})
        result = extract_genres(item)
        assert "science fiction" in result
        assert "action" in result

    def test_tags_included_for_cross_content_matching(self) -> None:
        item = make_item(
            metadata={"genres": ["Fantasy"], "tags": ["epic", "adventure"]}
        )
        result = extract_genres(item)
        assert "fantasy" in result
        assert "epic" in result
        assert "adventure" in result

    def test_tags_list_as_string(self) -> None:
        item = make_item(metadata={"tags": "sci-fi, space opera"})
        result = extract_genres(item)
        assert "science fiction" in result
        assert "space opera" in result


class TestExtractCreator:
    def test_author_field(self) -> None:
        item = make_item(author="Brandon Sanderson")
        assert extract_creator(item) == "brandon sanderson"

    def test_director_metadata(self) -> None:
        item = make_item(metadata={"director": "Christopher Nolan"})
        assert extract_creator(item) == "christopher nolan"


class TestScoringContext:
    def test_ratings_by_genre(self) -> None:
        consumed = [
            make_item(rating=5, metadata={"genre": "Fantasy"}),
            make_item(rating=3, metadata={"genre": "Fantasy"}),
        ]
        context = _build_context(consumed=consumed)
        assert context.ratings_by_genre["fantasy"] == [5, 3]


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


class TestTagOverlapScorer:
    def test_no_overlap(self) -> None:
        consumed = [make_item(metadata={"genre": "Fantasy"}, rating=5)]
        context = _build_context(consumed=consumed)
        candidate = make_item(
            status=ConsumptionStatus.UNREAD, metadata={"genre": "Comedy"}
        )
        scorer = TagOverlapScorer()
        assert scorer.score(candidate, context) == 0.0

    def test_two_matches_scores_medium(self) -> None:
        consumed = [make_item(metadata={"genres": ["Fantasy", "Action"]}, rating=5)]
        context = _build_context(consumed=consumed)
        candidate = make_item(
            status=ConsumptionStatus.UNREAD,
            metadata={"genres": ["Action", "Fantasy", "Horror"]},
        )
        scorer = TagOverlapScorer()
        assert scorer.score(candidate, context) == 0.5

    def test_cluster_match_provides_semantic_floor(self) -> None:
        consumed = [make_item(metadata={"genres": ["War"]}, rating=5)]
        context = _build_context(consumed=consumed)
        candidate = make_item(
            status=ConsumptionStatus.UNREAD,
            metadata={"genres": ["Space Warfare"]},
        )
        scorer = TagOverlapScorer()
        score = scorer.score(candidate, context)
        assert score > 0.0


class TestScoringContextClusters:
    def test_consumed_clusters_populated(self) -> None:
        consumed = [
            make_item(rating=5, metadata={"genre": "Science Fiction"}),
            make_item(rating=5, metadata={"genre": "Fantasy"}),
        ]
        context = _build_context(consumed=consumed)
        assert "science_fiction" in context.consumed_clusters
        assert "fantasy" in context.consumed_clusters


class TestSeriesOrderScorer:
    def test_next_in_sequence_high_rating(self) -> None:
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
        consumed = [
            make_item(title="Mistborn (Mistborn, #1)", rating=1),
        ]
        context = _build_context(consumed=consumed)
        candidate = make_item(
            title="Mistborn (Mistborn, #2)", status=ConsumptionStatus.UNREAD
        )
        scorer = SeriesOrderScorer()
        score = scorer.score(candidate, context)
        assert 0.55 <= score <= 0.65

    def test_next_in_sequence_no_rating(self) -> None:
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
        """When the user has consumed item #3, a candidate that is also #3 is already
        consumed (or a duplicate) and should be deprioritized."""
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
    """Symptom: with The Expanse #1 and #2 read, "Gods of Risk (The Expanse, #2.5)"
    scored 0.2, below the 0.3 given to an entry that is too far ahead, so the novella
    series filtering had just unblocked ranked under unrelated books."""

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
        assert score == 1.0
        assert score > 0.3

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
    """Paths into the next-in-sequence branch the fractional cases do not reach."""

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
        assert score == 1.0

    def test_no_matching_genre_neutral(self) -> None:
        consumed = [make_item(rating=5, metadata={"genre": "Fantasy"})]
        context = _build_context(consumed=consumed)
        candidate = make_item(
            status=ConsumptionStatus.UNREAD, metadata={"genre": "Romance"}
        )
        scorer = RatingPatternScorer()
        assert scorer.score(candidate, context) == 0.5


class TestScorerClone:
    def test_clone_custom_preference_preserves_args(self) -> None:
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
        scorer = CustomPreferenceScorer(
            genre_boosts={"fantasy": 1.0},
            weight=2.0,
        )
        cloned = scorer.clone(weight=3.0)
        cloned.genre_boosts["sci-fi"] = 0.5
        assert "sci-fi" not in scorer.genre_boosts


class TestBuildScorersWithOverrides:
    def test_partial_override(self) -> None:
        base = [
            GenreMatchScorer(weight=2.0),
            CreatorMatchScorer(weight=1.5),
            TagOverlapScorer(weight=1.0),
        ]
        overrides = {"genre_match": 5.0}
        result = build_scorers_with_overrides(base, overrides)
        assert result[0].weight == 5.0
        assert isinstance(result[0], GenreMatchScorer)
        assert result[1].weight == 1.5
        assert result[2].weight == 1.0

    def test_does_not_mutate_originals(self) -> None:
        base = [GenreMatchScorer(weight=2.0)]
        build_scorers_with_overrides(base, {"genre_match": 9.0})
        assert base[0].weight == 2.0


class TestCustomPreferenceScorer:
    def test_genre_boost_scores_high(self) -> None:
        candidate = make_item(
            metadata={"genre": "horror"}, status=ConsumptionStatus.UNREAD
        )
        context = _build_context(consumed=[])
        scorer = CustomPreferenceScorer(genre_boosts={"horror": 1.0})
        score = scorer.score(candidate, context)
        assert score == 1.0

    def test_genre_penalty_scores_low(self) -> None:
        candidate = make_item(
            metadata={"genre": "romance"}, status=ConsumptionStatus.UNREAD
        )
        context = _build_context(consumed=[])
        scorer = CustomPreferenceScorer(genre_penalties={"romance": 1.0})
        score = scorer.score(candidate, context)
        assert score == 0.0

    def test_partial_boost(self) -> None:
        candidate = make_item(
            metadata={"genre": "mystery"}, status=ConsumptionStatus.UNREAD
        )
        context = _build_context(consumed=[])
        scorer = CustomPreferenceScorer(genre_boosts={"mystery": 0.5})
        score = scorer.score(candidate, context)
        assert score == 0.75

    def test_no_matching_rules_returns_neutral(self) -> None:
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
        candidate = make_item(
            metadata={"genres": ["horror", "comedy"]}, status=ConsumptionStatus.UNREAD
        )
        context = _build_context(consumed=[])
        scorer = CustomPreferenceScorer(
            genre_boosts={"comedy": 1.0}, genre_penalties={"horror": 1.0}
        )
        score = scorer.score(candidate, context)
        assert score == 0.0


class TestContentLengthScorer:
    def test_no_preferences_returns_neutral(self) -> None:
        candidate = make_item(
            content_type=ContentType.BOOK,
            metadata={"pages": 800},
            status=ConsumptionStatus.UNREAD,
        )
        context = _build_context(consumed=[])
        scorer = ContentLengthScorer()
        assert scorer.score(candidate, context) == 0.5

    def test_exact_match_returns_1(self) -> None:
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
        """The scorer is the path the engine actually calls, so this pins the product
        behaviour rather than the helper: unenriched games take 0.8, not the 0.4 an
        opposite-end classification would cost them."""
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


class TestContinuationScorer:
    def test_currently_consuming_scores_1(self) -> None:
        candidate = make_item(
            title="Breaking Bad (Season 3)",
            content_type=ContentType.TV_SHOW,
            status=ConsumptionStatus.CURRENTLY_CONSUMING,
        )
        context = _build_context(consumed=[])
        scorer = ContinuationScorer()
        assert scorer.score(candidate, context) == 1.0

    def test_an_unstarted_item_does_not_apply(self) -> None:
        candidate = make_item(
            title="The Wire (Season 1)",
            content_type=ContentType.TV_SHOW,
            status=ConsumptionStatus.UNREAD,
        )
        context = _build_context(consumed=[])
        scorer = ContinuationScorer()
        assert scorer.applies(candidate, context) is False


class TestSeriesAffinityScorer:
    def test_well_rated_series_scores_1(self) -> None:
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
        candidate = make_item(
            title="Standalone Game",
            content_type=ContentType.VIDEO_GAME,
            status=ConsumptionStatus.UNREAD,
        )
        context = _build_context(consumed=[])
        scorer = SeriesAffinityScorer()
        assert scorer.score(candidate, context) == 0.5

    def test_exactly_4_average_scores_1(self) -> None:
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
        assert scorer.score(candidate, context) == 1.0


class TestAdaptationScorer:
    """The engine pre-computes each candidate's adaptations into the context."""

    @staticmethod
    def _context_where(
        candidate: ContentItem, adapts: list[ContentItem]
    ) -> ScoringContext:
        context = _build_context(consumed=[])
        context.adaptations = {candidate_key(candidate): adapts} if adapts else {}
        return context

    def test_a_cited_adaptation_scores_the_top_of_the_scale(self) -> None:
        source = make_item(item_id="book", title="Dune", rating=5)
        candidate = make_item(
            item_id="film", title="Dune", content_type=ContentType.MOVIE
        )

        scorer = AdaptationScorer()

        assert scorer.score(candidate, self._context_where(candidate, [source])) == 1.0

    def test_a_candidate_adapting_nothing_does_not_apply(self) -> None:
        candidate = make_item(
            item_id="film", title="Solaris", content_type=ContentType.MOVIE
        )

        scorer = AdaptationScorer()

        assert scorer.applies(candidate, self._context_where(candidate, [])) is False

    def test_another_candidates_adaptations_do_not_leak(self) -> None:
        source = make_item(item_id="book", title="Dune", rating=5)
        adapter = make_item(
            item_id="film", title="Dune", content_type=ContentType.MOVIE
        )
        other = make_item(
            item_id="other", title="Solaris", content_type=ContentType.MOVIE
        )

        scorer = AdaptationScorer()

        assert scorer.applies(other, self._context_where(adapter, [source])) is False
