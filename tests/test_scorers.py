"""Tests for the individual scorers and ScoringContext."""

from src.models.content import ConsumptionStatus, ContentItem, ContentType
from src.recommendations.preferences import PreferenceAnalyzer
from src.recommendations.scorers import (
    CreatorMatchScorer,
    GenreMatchScorer,
    RatingPatternScorer,
    ScoringContext,
    SemanticSimilarityScorer,
    SeriesOrderScorer,
    TagOverlapScorer,
    _extract_creator,
    _extract_genres,
)
from src.utils.series import build_series_tracking

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_item(
    title: str = "Test Item",
    content_type: ContentType = ContentType.BOOK,
    status: ConsumptionStatus = ConsumptionStatus.COMPLETED,
    rating: int | None = None,
    author: str | None = None,
    metadata: dict | None = None,
    item_id: str | None = None,
) -> ContentItem:
    return ContentItem(
        id=item_id,
        title=title,
        content_type=content_type,
        status=status,
        rating=rating,
        author=author,
        metadata=metadata or {},
    )


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
    def test_single_genre(self) -> None:
        item = _make_item(metadata={"genre": "Fantasy"})
        assert _extract_genres(item) == ["fantasy"]

    def test_genres_list(self) -> None:
        item = _make_item(metadata={"genres": ["Action", "RPG"]})
        assert _extract_genres(item) == ["action", "rpg"]

    def test_both_genre_and_genres(self) -> None:
        item = _make_item(metadata={"genre": "Sci-Fi", "genres": ["Action"]})
        result = _extract_genres(item)
        assert "sci-fi" in result
        assert "action" in result

    def test_no_metadata(self) -> None:
        item = _make_item(metadata={})
        assert _extract_genres(item) == []


class TestExtractCreator:
    def test_author_field(self) -> None:
        item = _make_item(author="Brandon Sanderson")
        assert _extract_creator(item) == "brandon sanderson"

    def test_director_metadata(self) -> None:
        item = _make_item(metadata={"director": "Christopher Nolan"})
        assert _extract_creator(item) == "christopher nolan"

    def test_no_creator(self) -> None:
        item = _make_item()
        assert _extract_creator(item) is None


# ---------------------------------------------------------------------------
# ScoringContext tests
# ---------------------------------------------------------------------------


class TestScoringContext:
    def test_populates_consumed_genres(self) -> None:
        consumed = [
            _make_item(rating=5, metadata={"genre": "Fantasy"}),
            _make_item(rating=4, metadata={"genres": ["Sci-Fi", "Action"]}),
        ]
        context = _build_context(consumed=consumed)
        assert "fantasy" in context.consumed_genres
        assert "sci-fi" in context.consumed_genres
        assert "action" in context.consumed_genres

    def test_populates_consumed_creators(self) -> None:
        consumed = [
            _make_item(author="Author A", rating=5),
            _make_item(metadata={"director": "Director B"}, rating=3),
        ]
        context = _build_context(consumed=consumed)
        assert "author a" in context.consumed_creators
        assert "director b" in context.consumed_creators

    def test_ratings_by_genre(self) -> None:
        consumed = [
            _make_item(rating=5, metadata={"genre": "Fantasy"}),
            _make_item(rating=3, metadata={"genre": "Fantasy"}),
        ]
        context = _build_context(consumed=consumed)
        assert context.ratings_by_genre["fantasy"] == [5, 3]


# ---------------------------------------------------------------------------
# GenreMatchScorer tests
# ---------------------------------------------------------------------------


class TestGenreMatchScorer:
    def test_preferred_genre_scores_high(self) -> None:
        consumed = [_make_item(rating=5, metadata={"genre": "Fantasy"})]
        context = _build_context(consumed=consumed)
        candidate = _make_item(
            status=ConsumptionStatus.UNREAD, metadata={"genre": "Fantasy"}
        )
        scorer = GenreMatchScorer()
        score = scorer.score(candidate, context)
        assert score > 0.5

    def test_disliked_genre_scores_low(self) -> None:
        consumed = [_make_item(rating=1, metadata={"genre": "Romance"})]
        context = _build_context(consumed=consumed)
        candidate = _make_item(
            status=ConsumptionStatus.UNREAD, metadata={"genre": "Romance"}
        )
        scorer = GenreMatchScorer()
        score = scorer.score(candidate, context)
        assert score < 0.5

    def test_no_genre_returns_neutral(self) -> None:
        context = _build_context(consumed=[])
        candidate = _make_item(status=ConsumptionStatus.UNREAD)
        scorer = GenreMatchScorer()
        assert scorer.score(candidate, context) == 0.5


# ---------------------------------------------------------------------------
# CreatorMatchScorer tests
# ---------------------------------------------------------------------------


class TestCreatorMatchScorer:
    def test_preferred_author_scores_high(self) -> None:
        consumed = [_make_item(author="Brandon Sanderson", rating=5)]
        context = _build_context(consumed=consumed)
        candidate = _make_item(
            author="Brandon Sanderson", status=ConsumptionStatus.UNREAD
        )
        scorer = CreatorMatchScorer()
        score = scorer.score(candidate, context)
        assert score > 0.5

    def test_unknown_creator_neutral(self) -> None:
        context = _build_context(consumed=[])
        candidate = _make_item(author="Unknown Author", status=ConsumptionStatus.UNREAD)
        scorer = CreatorMatchScorer()
        assert scorer.score(candidate, context) == 0.5

    def test_no_creator_neutral(self) -> None:
        context = _build_context(consumed=[])
        candidate = _make_item(status=ConsumptionStatus.UNREAD)
        scorer = CreatorMatchScorer()
        assert scorer.score(candidate, context) == 0.5


# ---------------------------------------------------------------------------
# TagOverlapScorer tests
# ---------------------------------------------------------------------------


class TestTagOverlapScorer:
    def test_full_overlap(self) -> None:
        consumed = [_make_item(metadata={"genre": "Fantasy"}, rating=5)]
        context = _build_context(consumed=consumed)
        candidate = _make_item(
            status=ConsumptionStatus.UNREAD, metadata={"genre": "Fantasy"}
        )
        scorer = TagOverlapScorer()
        assert scorer.score(candidate, context) == 1.0

    def test_no_overlap(self) -> None:
        consumed = [_make_item(metadata={"genre": "Fantasy"}, rating=5)]
        context = _build_context(consumed=consumed)
        candidate = _make_item(
            status=ConsumptionStatus.UNREAD, metadata={"genre": "Horror"}
        )
        scorer = TagOverlapScorer()
        assert scorer.score(candidate, context) == 0.0

    def test_partial_overlap(self) -> None:
        consumed = [_make_item(metadata={"genres": ["Fantasy", "Action"]}, rating=5)]
        context = _build_context(consumed=consumed)
        candidate = _make_item(
            status=ConsumptionStatus.UNREAD, metadata={"genres": ["Action", "Horror"]}
        )
        scorer = TagOverlapScorer()
        score = scorer.score(candidate, context)
        # intersection = {action}, union = {fantasy, action, horror} => 1/3
        assert abs(score - 1 / 3) < 0.01

    def test_empty_genres_returns_zero(self) -> None:
        context = _build_context(consumed=[])
        candidate = _make_item(status=ConsumptionStatus.UNREAD, metadata={})
        scorer = TagOverlapScorer()
        assert scorer.score(candidate, context) == 0.0


# ---------------------------------------------------------------------------
# SeriesOrderScorer tests
# ---------------------------------------------------------------------------


class TestSeriesOrderScorer:
    def test_next_in_sequence(self) -> None:
        consumed = [
            _make_item(title="Mistborn (Mistborn, #1)", rating=5),
        ]
        context = _build_context(consumed=consumed)
        candidate = _make_item(
            title="Mistborn (Mistborn, #2)", status=ConsumptionStatus.UNREAD
        )
        scorer = SeriesOrderScorer()
        assert scorer.score(candidate, context) == 1.0

    def test_first_in_unstarted_series(self) -> None:
        context = _build_context(consumed=[])
        candidate = _make_item(title="Dune (Dune, #1)", status=ConsumptionStatus.UNREAD)
        scorer = SeriesOrderScorer()
        assert scorer.score(candidate, context) == 0.8

    def test_too_far_ahead(self) -> None:
        consumed = [_make_item(title="Mistborn (Mistborn, #1)", rating=5)]
        context = _build_context(consumed=consumed)
        candidate = _make_item(
            title="Mistborn (Mistborn, #5)", status=ConsumptionStatus.UNREAD
        )
        scorer = SeriesOrderScorer()
        assert scorer.score(candidate, context) == 0.3

    def test_non_series_neutral(self) -> None:
        context = _build_context(consumed=[])
        candidate = _make_item(
            title="Standalone Novel", status=ConsumptionStatus.UNREAD
        )
        scorer = SeriesOrderScorer()
        assert scorer.score(candidate, context) == 0.5


# ---------------------------------------------------------------------------
# RatingPatternScorer tests
# ---------------------------------------------------------------------------


class TestRatingPatternScorer:
    def test_high_average_in_genre(self) -> None:
        consumed = [
            _make_item(rating=5, metadata={"genre": "Fantasy"}),
            _make_item(rating=5, metadata={"genre": "Fantasy"}),
        ]
        context = _build_context(consumed=consumed)
        candidate = _make_item(
            status=ConsumptionStatus.UNREAD, metadata={"genre": "Fantasy"}
        )
        scorer = RatingPatternScorer()
        score = scorer.score(candidate, context)
        # average=5 => (5-1)/4 = 1.0
        assert score == 1.0

    def test_low_average_in_genre(self) -> None:
        consumed = [
            _make_item(rating=1, metadata={"genre": "Horror"}),
            _make_item(rating=1, metadata={"genre": "Horror"}),
        ]
        context = _build_context(consumed=consumed)
        candidate = _make_item(
            status=ConsumptionStatus.UNREAD, metadata={"genre": "Horror"}
        )
        scorer = RatingPatternScorer()
        score = scorer.score(candidate, context)
        # average=1 => (1-1)/4 = 0.0
        assert score == 0.0

    def test_no_matching_genre_neutral(self) -> None:
        consumed = [_make_item(rating=5, metadata={"genre": "Fantasy"})]
        context = _build_context(consumed=consumed)
        candidate = _make_item(
            status=ConsumptionStatus.UNREAD, metadata={"genre": "Romance"}
        )
        scorer = RatingPatternScorer()
        assert scorer.score(candidate, context) == 0.5

    def test_no_genre_info_neutral(self) -> None:
        context = _build_context(consumed=[])
        candidate = _make_item(status=ConsumptionStatus.UNREAD)
        scorer = RatingPatternScorer()
        assert scorer.score(candidate, context) == 0.5


# ---------------------------------------------------------------------------
# SemanticSimilarityScorer tests
# ---------------------------------------------------------------------------


class TestSemanticSimilarityScorer:
    def test_returns_precomputed_score(self) -> None:
        """Scorer returns the pre-computed similarity score for a candidate."""
        candidate = _make_item(item_id="item-1", status=ConsumptionStatus.UNREAD)
        context = _build_context(consumed=[])
        context.similarity_scores = {"item-1": 0.85}
        scorer = SemanticSimilarityScorer()
        assert scorer.score(candidate, context) == 0.85

    def test_returns_zero_when_candidate_not_in_scores(self) -> None:
        """Scorer returns 0.0 when candidate id is not in similarity_scores."""
        candidate = _make_item(item_id="item-2", status=ConsumptionStatus.UNREAD)
        context = _build_context(consumed=[])
        context.similarity_scores = {"item-1": 0.85}
        scorer = SemanticSimilarityScorer()
        assert scorer.score(candidate, context) == 0.0

    def test_returns_zero_when_similarity_scores_empty(self) -> None:
        """Scorer returns 0.0 when no similarity scores are available."""
        candidate = _make_item(item_id="item-1", status=ConsumptionStatus.UNREAD)
        context = _build_context(consumed=[])
        scorer = SemanticSimilarityScorer()
        assert scorer.score(candidate, context) == 0.0

    def test_handles_none_candidate_id(self) -> None:
        """Scorer handles candidates with None id via dict lookup."""
        candidate = _make_item(status=ConsumptionStatus.UNREAD)
        assert candidate.id is None
        context = _build_context(consumed=[])
        context.similarity_scores = {None: 0.7}
        scorer = SemanticSimilarityScorer()
        assert scorer.score(candidate, context) == 0.7

    def test_default_weight(self) -> None:
        """SemanticSimilarityScorer default weight is 1.5."""
        scorer = SemanticSimilarityScorer()
        assert scorer.weight == 1.5
