"""Tests for the ScoringPipeline."""

from src.models.content import ConsumptionStatus, ContentItem, ContentType
from src.recommendations.preferences import PreferenceAnalyzer
from src.recommendations.scorers import (
    DEFAULT_SCORERS,
    GenreMatchScorer,
    Scorer,
    ScoringContext,
    TagOverlapScorer,
)
from src.recommendations.scoring_pipeline import ScoringPipeline
from src.utils.series import build_series_tracking


def _make_item(
    title: str = "Item",
    metadata: dict | None = None,
    rating: int | None = None,
    author: str | None = None,
    status: ConsumptionStatus = ConsumptionStatus.UNREAD,
    content_type: ContentType = ContentType.BOOK,
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


class TestScoringPipeline:
    def test_results_sorted_descending(self) -> None:
        """Higher-scoring candidates should appear first."""
        consumed = [
            _make_item(
                rating=5,
                metadata={"genre": "Fantasy"},
                status=ConsumptionStatus.COMPLETED,
            )
        ]
        context = _build_context(consumed=consumed)

        good_match = _make_item(title="Good", metadata={"genre": "Fantasy"})
        poor_match = _make_item(title="Poor", metadata={"genre": "Horror"})

        pipeline = ScoringPipeline(DEFAULT_SCORERS)
        result = pipeline.score_candidates([poor_match, good_match], context)

        assert result[0][0].title == "Good"
        assert result[1][0].title == "Poor"
        assert result[0][1] >= result[1][1]

    def test_empty_candidates(self) -> None:
        """Empty candidate list should return empty results."""
        context = _build_context()
        pipeline = ScoringPipeline(DEFAULT_SCORERS)
        assert pipeline.score_candidates([], context) == []

    def test_weight_normalization(self) -> None:
        """Aggregate score should be in [0, 1] regardless of weights."""
        consumed = [
            _make_item(
                rating=5,
                metadata={"genre": "Fantasy"},
                status=ConsumptionStatus.COMPLETED,
            )
        ]
        context = _build_context(consumed=consumed)
        candidate = _make_item(metadata={"genre": "Fantasy"})

        pipeline = ScoringPipeline(DEFAULT_SCORERS)
        result = pipeline.score_candidates([candidate], context)
        score = result[0][1]
        assert 0.0 <= score <= 1.0

    def test_score_clamped_to_unit_interval(self) -> None:
        """Even with extreme inputs, scores should remain in [0, 1]."""

        class AlwaysMaxScorer(Scorer):
            def score(self, candidate: ContentItem, context: ScoringContext) -> float:
                return 1.0

        class AlwaysMinScorer(Scorer):
            def score(self, candidate: ContentItem, context: ScoringContext) -> float:
                return 0.0

        context = _build_context()
        candidate = _make_item()

        # All max
        pipeline = ScoringPipeline([AlwaysMaxScorer(weight=10.0)])
        result = pipeline.score_candidates([candidate], context)
        assert result[0][1] == 1.0

        # All min
        pipeline = ScoringPipeline([AlwaysMinScorer(weight=10.0)])
        result = pipeline.score_candidates([candidate], context)
        assert result[0][1] == 0.0

    def test_zero_total_weight(self) -> None:
        """If all scorers have weight 0, scores should be 0.0."""
        context = _build_context()
        candidate = _make_item()
        pipeline = ScoringPipeline(
            [GenreMatchScorer(weight=0.0), TagOverlapScorer(weight=0.0)]
        )
        result = pipeline.score_candidates([candidate], context)
        assert result[0][1] == 0.0
