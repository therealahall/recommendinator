import pytest

from src.models.content import ConsumptionStatus, ContentItem, ContentType
from src.recommendations.genre_clusters import get_clusters_for_terms
from src.recommendations.genre_normalizer import ALLOWED_TERMS
from src.recommendations.identity import candidate_key
from src.recommendations.preferences import PreferenceAnalyzer
from src.recommendations.scorers import (
    AdaptationScorer,
    ContentLengthScorer,
    ContinuationScorer,
    CreatorMatchScorer,
    GenreMatchScorer,
    ScoringContext,
    TagOverlapScorer,
    build_scorers_with_overrides,
    extract_creators,
    extract_genres,
)
from src.utils.series import SeriesOrder, build_series_tracking
from tests.factories import make_item


def _build_context(
    consumed: list[ContentItem] | None = None,
    unconsumed: list[ContentItem] | None = None,
    content_type: ContentType = ContentType.BOOK,
    in_progress: list[ContentItem] | None = None,
) -> ScoringContext:
    consumed = consumed or []
    unconsumed = unconsumed or []
    in_progress = in_progress or []
    analyzer = PreferenceAnalyzer(min_rating=4)
    preferences = analyzer.analyze(consumed)
    series_order = SeriesOrder([*consumed, *unconsumed, *in_progress])
    series_tracking = build_series_tracking(consumed, series_order)
    return ScoringContext(
        preferences=preferences,
        consumed_items=consumed,
        series_tracking=series_tracking,
        content_type=content_type,
        series_order=series_order,
        in_progress_positions=build_series_tracking(in_progress, series_order),
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
        assert extract_creators(item) == {"brandon sanderson"}

    def test_director_metadata(self) -> None:
        item = make_item(metadata={"director": "Christopher Nolan"})
        assert extract_creators(item) == {"christopher nolan"}

    def test_a_reordered_creator_list_is_the_same_creators(self) -> None:
        listed = make_item(author="Matt Wolpert, Ronald D. Moore, Ben Nedivi")
        reordered = make_item(author="Ben Nedivi,Matt Wolpert ,  Ronald D. Moore,")
        assert extract_creators(listed) == extract_creators(reordered)
        assert extract_creators(listed) == {
            "matt wolpert",
            "ronald d. moore",
            "ben nedivi",
        }

    def test_a_suffix_stays_on_the_name_it_follows(self) -> None:
        item = make_item(
            author="CAPCOM Co., Ltd., Sammy Davis, Jr., Atlus Co.,LTD, Ubisoft, S.A., "
            "Mojang, AB, Halfbrick, Pty. Ltd."
        )
        assert extract_creators(item) == {
            "capcom co., ltd.",
            "sammy davis, jr.",
            "atlus co., ltd",
            "ubisoft, s.a.",
            "mojang, ab",
            "halfbrick, pty. ltd.",
        }


def _genre_score(ratings: dict[str, list[int]], genre: str, *shared: str) -> float:
    # A book library scoring a film: genre taste carries across content types.
    consumed = [
        make_item(rating=rating, genres=", ".join([name, *shared]))
        for name, scale in ratings.items()
        for rating in scale
    ]
    candidate = make_item(
        content_type=ContentType.MOVIE, genres=", ".join([genre, *shared])
    )
    return GenreMatchScorer().score(candidate, _build_context(consumed=consumed))


class TestGenreMatchScorer:
    @pytest.mark.parametrize("shared", [(), ("Drama",)])
    # The second is a generous rater, whose 3 stars is a dislike and 4 average.
    @pytest.mark.parametrize(("romance", "comedy"), [(2, [3, 4]), (3, [4, 4])])
    def test_loved_average_and_disliked_genres_separate_even_under_a_shared_term(
        self, romance: int, comedy: list[int], shared: tuple[str, ...]
    ) -> None:
        library = {"Horror": [5] * 5, "Romance": [romance] * 5, "Comedy": comedy}
        assert _genre_score(library, "Horror", *shared) >= 0.75
        assert _genre_score(library, "Romance", *shared) <= 0.25
        assert 0.45 <= _genre_score(library, "Comedy", *shared) <= 0.55

    def test_a_genre_rated_once_claims_less_than_one_rated_five_times(self) -> None:
        once = _genre_score({"Western": [5], "Romance": [1]}, "Western")
        five_times = _genre_score({"Western": [5] * 5, "Romance": [1] * 5}, "Western")
        assert 0.5 < once < five_times

    def test_a_library_silent_on_the_genre_scores_it_neutral(self) -> None:
        assert _genre_score({}, "Fantasy") == 0.5
        assert _genre_score({"Fantasy": [5, 2, 4]}, "Fantasy") == 0.5
        assert _genre_score({"Fantasy": [4, 4], "Horror": [4]}, "Fantasy") == 0.5

    def test_a_disliked_term_offsets_a_loved_one_and_an_unrated_one_counts_for_nothing(
        self,
    ) -> None:
        consumed = [
            make_item(rating=5, metadata={"genre": "Crime"}),
            make_item(rating=1, metadata={"genre": "Western"}),
        ]
        context = _build_context(consumed=consumed)
        scorer = GenreMatchScorer()

        def score(*genres: str) -> float:
            candidate = make_item(
                status=ConsumptionStatus.UNREAD, metadata={"genres": list(genres)}
            )
            return scorer.score(candidate, context)

        assert score("Drama", "Crime") == score("Crime") > 0.5
        assert score("Drama", "Crime", "Western") == 0.5

    def test_a_shared_genre_cluster_without_a_shared_tag_scores_neutral(self) -> None:
        """Catches genre_match starting to read genre clusters, which only
        tag_overlap does."""
        library = {"Cyberpunk": [5] * 3, "Romance": [1] * 3}
        consumed = [make_item(genres="Cyberpunk", rating=5)]
        space_opera = make_item(status=ConsumptionStatus.UNREAD, genres="Space Opera")

        assert _genre_score(library, "Cyberpunk") > 0.5
        assert _genre_score(library, "Space Opera") == 0.5
        assert TagOverlapScorer().score(space_opera, _build_context(consumed)) > 0


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

    def test_a_cowritten_show_matches_as_well_as_a_solo_one(self) -> None:
        context = _build_context(
            consumed=[make_item(author="Taylor Sheridan", rating=5)]
        )
        solo, cowritten = (
            make_item(author=author, status=ConsumptionStatus.UNREAD)
            for author in ("Taylor Sheridan", "Taylor Sheridan, Hugh Dillon")
        )
        scorer = CreatorMatchScorer()
        assert scorer.score(cowritten, context) == scorer.score(solo, context) == 1.0

    def test_one_creator_of_an_unrated_cowritten_item_counts_as_consumed(
        self,
    ) -> None:
        context = _build_context(
            consumed=[make_item(author="Matt Wolpert, Ben Nedivi")]
        )
        candidate = make_item(author="Ben Nedivi", status=ConsumptionStatus.UNREAD)
        assert CreatorMatchScorer().score(candidate, context) == 0.7

    def test_a_creator_loved_once_and_hated_once_scores_neutral(self) -> None:
        """A zero net read as unrated, so the wash scored the 0.7 mild positive."""
        context = _build_context(
            consumed=[
                make_item(author="Ben Nedivi", rating=5),
                make_item(author="Ben Nedivi", rating=1),
            ]
        )
        candidate = make_item(author="Ben Nedivi", status=ConsumptionStatus.UNREAD)
        assert CreatorMatchScorer().score(candidate, context) == 0.5

    def test_a_creator_rated_only_one_star_scores_below_a_stranger(self) -> None:
        """Catches a 1-star creator no longer dragging their next work down."""
        context = _build_context(consumed=[make_item(author="Ilse Varden", rating=1)])
        scorer = CreatorMatchScorer()
        disliked, stranger = (
            make_item(author=author, status=ConsumptionStatus.UNREAD)
            for author in ("Ilse Varden", "Oren Pell")
        )
        assert scorer.score(disliked, context) < scorer.score(stranger, context) == 0.5

    def test_a_mixed_record_scores_between_a_hated_and_a_loved_creator(self) -> None:
        """Catches a favourite's many 5s sinking 1, 1, 5, 5, 5 below a lone 1."""
        consumed = [
            *(make_item(author="Tamsin Rook", rating=5) for _ in range(10)),
            *(make_item(author="Ilse Varden", rating=r) for r in (1, 1, 5, 5, 5)),
            *(make_item(author="Mara Quill", rating=5) for _ in range(3)),
            make_item(author="Oren Pell", rating=1),
            make_item(author="Wren Hale", rating=5),
        ]
        context = _build_context(consumed=consumed)
        hated, mixed, loved, loved_once = (
            CreatorMatchScorer().score(
                make_item(author=author, status=ConsumptionStatus.UNREAD), context
            )
            for author in ("Oren Pell", "Ilse Varden", "Mara Quill", "Wren Hale")
        )
        assert hated < 0.5 < mixed < loved
        assert mixed == loved_once == pytest.approx(0.55)


class TestTagOverlapScorer:
    def test_no_overlap(self) -> None:
        consumed = [make_item(metadata={"genre": "Fantasy"}, rating=5)]
        context = _build_context(consumed=consumed)
        candidate = make_item(
            status=ConsumptionStatus.UNREAD, metadata={"genre": "Comedy"}
        )
        scorer = TagOverlapScorer()
        assert scorer.score(candidate, context) == 0.0

    @staticmethod
    def _score(consumed: list[ContentItem], *genres: str) -> float:
        candidate = make_item(status=ConsumptionStatus.UNREAD, genres=", ".join(genres))
        return TagOverlapScorer().score(candidate, _build_context(consumed=consumed))

    def test_the_score_is_the_best_share_of_a_consumed_items_terms_not_the_candidates(
        self,
    ) -> None:
        consumed = [make_item(genres="Battle, War, Drama", rating=5)]
        reached = get_clusters_for_terms(["battle", "war", "drama"])
        unrelated = sorted(
            term
            for term in ALLOWED_TERMS - {"battle", "war", "drama"}
            if not get_clusters_for_terms([term]) & reached
        )[:55]
        assert (
            self._score(consumed, "Battle") == self._score(consumed, "War") == 1.5 / 5
        )
        assert self._score(consumed, "Battle", "War") == 2 / 5
        assert self._score(consumed, "Battle", "War", "Drama") == 3 / 5
        assert self._score(consumed, "Battle", *unrelated) == 1.5 / 5
        consumed.append(make_item(genres="War", rating=5))
        assert self._score(consumed, "War") == 1 / 3

    def test_a_one_tag_item_never_gives_full_marks_and_a_richer_one_reaches_higher(
        self,
    ) -> None:
        magic = [make_item(genres="Magic", rating=5)]
        rich = [make_item(genres="Magic, Wizards, Sorcery", rating=5)]
        assert self._score(magic, "Magic") == 1 / 3
        assert self._score(rich, "Magic", "Wizards", "Sorcery") == 3 / 5

    def test_a_term_counts_once_however_many_clusters_it_shares(self) -> None:
        consumed = [make_item(genres="Space Warfare, Science Fiction", rating=5)]
        assert self._score(consumed, "Space Warfare") == 1.5 / 4
        assert self._score(consumed, "War") == 0.5 / 4
        assert self._score(consumed, "Space Opera") == self._score(
            consumed, "Cyberpunk"
        )

    def test_a_related_term_scores_above_nothing_and_below_the_exact_term(
        self,
    ) -> None:
        for consumed, exact, related in [
            ("War", "War", "Battle"),
            ("Science Fiction", "Science Fiction", "Cyberpunk"),
            ("Fighting", "Fighting", "Beat 'em up"),
        ]:
            library = [make_item(genres=consumed, rating=5)]
            assert self._score(library, exact) == 1 / 3
            assert 0 < self._score(library, related) < 1 / 3

    def test_a_one_star_match_scores_half_a_five_star_one(self) -> None:
        def score(rating: int) -> float:
            return self._score([make_item(genres="War", rating=rating)], "War")

        assert score(1) == score(5) / 2 == 1 / 6

    @pytest.mark.parametrize("ratings", [(5, 1), (1, 5)])
    def test_identically_tagged_items_keep_the_higher_ratings_lift(
        self, ratings: tuple[int, int]
    ) -> None:
        # Both orders: last-wins fails (5, 1), first-wins fails (1, 5).
        consumed = [make_item(genres="War", rating=rating) for rating in ratings]
        assert self._score(consumed, "War") == 1 / 3

    def test_a_larger_library_leaves_each_candidates_score_where_it_was(self) -> None:
        """Catches a return to scoring against the union of the library's tags."""
        own_terms = {"battle", "war", "drama", "survival"}
        closest = make_item(genres="Battle, War, Drama, Survival", rating=5)
        reached = get_clusters_for_terms(sorted(own_terms))
        unrelated = sorted(
            term
            for term in ALLOWED_TERMS - own_terms
            if not get_clusters_for_terms([term]) & reached
        )[:60]
        library = [closest] + [
            make_item(genres=", ".join(unrelated[index : index + 3]), rating=5)
            for index in range(0, 60, 3)
        ]
        overlaps = [("Battle",), ("Battle", "War"), ("Battle", "War", "Drama")]

        alone = [self._score([closest], *genres) for genres in overlaps]
        among_many = [self._score(library, *genres) for genres in overlaps]

        assert among_many == alone == [1.5 / 6, 2 / 6, 3 / 6]


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


class TestContentLengthScorer:
    def test_only_a_type_with_a_length_preference_carries_the_row(self) -> None:
        """Unset, every item took a constant 0.5, and "any" a constant 1.0."""
        movie = make_item(content_type=ContentType.MOVIE, metadata={"runtime": 200})
        book = make_item(content_type=ContentType.BOOK, metadata={"pages": 800})
        context = _build_context(consumed=[])
        scorer = ContentLengthScorer()

        assert not scorer.applies(movie, context)
        context.content_length_preferences = {"movie": "short", "book": "any"}
        assert scorer.applies(movie, context)
        assert not scorer.applies(book, context)

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


def _season(title: str, season: int, status: ConsumptionStatus) -> ContentItem:
    return make_item(
        title=f"{title} (Season {season})",
        content_type=ContentType.TV_SHOW,
        status=status,
        metadata={"series_name": title, "season": season},
    )


class TestContinuationScorer:
    def test_the_season_after_unrated_watched_ones_scores_neutral(self) -> None:
        candidate = _season("Breaking Bad", 3, ConsumptionStatus.UNREAD)
        context = _build_context(
            consumed=[
                _season("Breaking Bad", season, ConsumptionStatus.COMPLETED)
                for season in (1, 2)
            ],
            unconsumed=[candidate],
            content_type=ContentType.TV_SHOW,
        )
        scorer = ContinuationScorer()
        assert scorer.applies(candidate, context) is True
        assert scorer.score(candidate, context) == 0.5

    def test_the_rating_averages_only_the_rated_entries_of_that_series(self) -> None:
        consumed = [
            make_item(title="Mistborn (Mistborn, #1)", rating=5),
            make_item(title="Mistborn (Mistborn, #2)", rating=3),
            make_item(title="Mistborn (Mistborn, #3)", rating=None),
            make_item(title="Elantris (Elantris, #1)", rating=1),
        ]
        candidate = make_item(
            title="Mistborn (Mistborn, #4)", status=ConsumptionStatus.UNREAD
        )
        context = _build_context(consumed=consumed, unconsumed=[candidate])
        assert ContinuationScorer().score(candidate, context) == 0.75

    def test_a_rating_pools_under_the_stated_name_not_a_title_fragment(self) -> None:
        consumed = [
            make_item(
                title="Left 4 Dead",
                content_type=ContentType.VIDEO_GAME,
                metadata={"series_name": "Left 4 Dead", "release_year": 2008},
                rating=5,
            ),
        ]
        candidate = make_item(
            title="Left 4 Dead 2",
            content_type=ContentType.VIDEO_GAME,
            metadata={"series_name": "Left 4 Dead", "release_year": 2009},
            status=ConsumptionStatus.UNREAD,
        )
        context = _build_context(
            consumed=consumed,
            unconsumed=[candidate],
            content_type=ContentType.VIDEO_GAME,
        )

        assert context.series_ratings == {"Left 4 Dead": [5]}
        assert ContinuationScorer().score(candidate, context) == 1.0

    def test_a_same_named_book_series_never_scales_the_shows_seasons(self) -> None:
        books = make_item(
            title="Leviathan Wakes", metadata={"series_name": "The Expanse"}, rating=5
        )
        candidate = _season("The Expanse", 2, ConsumptionStatus.UNREAD)
        context = _build_context(
            consumed=[books, _season("The Expanse", 1, ConsumptionStatus.COMPLETED)],
            unconsumed=[candidate],
            content_type=ContentType.TV_SHOW,
        )
        assert ContinuationScorer().score(candidate, context) == 0.5

    def test_a_later_season_is_silent_while_one_is_in_flight(self) -> None:
        candidate = _season("Breaking Bad", 3, ConsumptionStatus.UNREAD)
        context = _build_context(
            consumed=[_season("Breaking Bad", 1, ConsumptionStatus.COMPLETED)],
            unconsumed=[candidate],
            in_progress=[
                _season("Breaking Bad", 2, ConsumptionStatus.CURRENTLY_CONSUMING)
            ],
            content_type=ContentType.TV_SHOW,
        )
        assert ContinuationScorer().applies(candidate, context) is False

    def test_an_earlier_season_than_the_one_watched_does_not_apply(self) -> None:
        candidate = _season("Breaking Bad", 1, ConsumptionStatus.UNREAD)
        context = _build_context(
            consumed=[_season("Breaking Bad", 2, ConsumptionStatus.COMPLETED)],
            unconsumed=[candidate],
            content_type=ContentType.TV_SHOW,
        )
        assert ContinuationScorer().applies(candidate, context) is False

    def test_a_show_never_started_is_untouched(self) -> None:
        candidate = _season("The Wire", 2, ConsumptionStatus.UNREAD)
        context = _build_context(
            consumed=[_season("Breaking Bad", 1, ConsumptionStatus.COMPLETED)],
            unconsumed=[candidate],
            content_type=ContentType.TV_SHOW,
        )
        assert ContinuationScorer().applies(candidate, context) is False

    def test_a_standalone_candidate_does_not_apply(self) -> None:
        candidate = make_item(title="Piranesi", status=ConsumptionStatus.UNREAD)
        context = _build_context(unconsumed=[candidate])
        assert ContinuationScorer().applies(candidate, context) is False


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
