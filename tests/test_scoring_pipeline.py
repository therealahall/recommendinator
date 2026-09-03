import pytest

from src.models.content import ConsumptionStatus, ContentItem, ContentType
from src.recommendations.identity import candidate_key
from src.recommendations.preferences import PreferenceAnalyzer
from src.recommendations.scorers import (
    DEFAULT_SCORERS,
    AdaptationScorer,
    GenreMatchScorer,
    Scorer,
    ScoringContext,
    TagOverlapScorer,
)
from src.recommendations.scoring_pipeline import ScoringPipeline
from src.utils.series import build_series_tracking
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


class TestScoringPipeline:
    def test_results_sorted_descending(self) -> None:
        consumed = [
            make_item(
                rating=5,
                metadata={"genre": "Fantasy"},
                status=ConsumptionStatus.COMPLETED,
            )
        ]
        context = _build_context(consumed=consumed)

        good_match = make_item(title="Good", metadata={"genre": "Fantasy"})
        poor_match = make_item(title="Poor", metadata={"genre": "Horror"})

        pipeline = ScoringPipeline(DEFAULT_SCORERS)
        result = pipeline.score_candidates_with_breakdown(
            [poor_match, good_match], context
        )

        assert result[0].item.title == "Good"
        assert result[1].item.title == "Poor"
        assert result[0].aggregate_score >= result[1].aggregate_score

    def test_score_clamped_to_unit_interval(self) -> None:
        class AlwaysMaxScorer(Scorer):
            def score(self, candidate: ContentItem, context: ScoringContext) -> float:
                return 1.0

        class AlwaysMinScorer(Scorer):
            def score(self, candidate: ContentItem, context: ScoringContext) -> float:
                return 0.0

        context = _build_context()
        candidate = make_item()

        pipeline = ScoringPipeline([AlwaysMaxScorer(weight=10.0)])
        result = pipeline.score_candidates_with_breakdown([candidate], context)
        assert result[0].aggregate_score == 1.0

        pipeline = ScoringPipeline([AlwaysMinScorer(weight=10.0)])
        result = pipeline.score_candidates_with_breakdown([candidate], context)
        assert result[0].aggregate_score == 0.0

    def test_a_scorer_that_cannot_fire_is_left_out_of_the_divisor(self) -> None:
        class StrongScorer(Scorer):
            def score(self, candidate: ContentItem, context: ScoringContext) -> float:
                return 0.8

        class InapplicableScorer(Scorer):
            def applies(self, candidate: ContentItem, context: ScoringContext) -> bool:
                return False

            def score(self, candidate: ContentItem, context: ScoringContext) -> float:
                raise AssertionError("a scorer that cannot fire must not be asked")

        context = _build_context()
        pipeline = ScoringPipeline(
            [StrongScorer(weight=1.0), InapplicableScorer(weight=3.0)]
        )

        result = pipeline.score_candidates_with_breakdown([make_item()], context)

        # Averaged in as a zero it would be 0.2.
        assert result[0].aggregate_score == pytest.approx(0.8)

    def test_zero_total_weight(self) -> None:
        context = _build_context()
        candidate = make_item()
        pipeline = ScoringPipeline(
            [GenreMatchScorer(weight=0.0), TagOverlapScorer(weight=0.0)]
        )
        result = pipeline.score_candidates_with_breakdown([candidate], context)
        assert result[0].aggregate_score == 0.0


class TestAdaptationCannotDemoteRegression:
    def test_an_adaptation_of_a_well_rated_work_never_ranks_below_no_adaptation(
        self,
    ) -> None:
        class WellLikedScorer(Scorer):
            def score(self, candidate: ContentItem, context: ScoringContext) -> float:
                return 0.9

        context = _build_context()
        candidate = make_item(title="Dune", content_type=ContentType.MOVIE)
        pipeline = ScoringPipeline([WellLikedScorer(weight=2.0), AdaptationScorer()])

        alone = pipeline.score_candidates_with_breakdown([candidate], context)[0]
        context.adaptations = {
            candidate_key(candidate): [
                make_item(item_id="source", title="Dune", rating=4)
            ]
        }
        adapting = pipeline.score_candidates_with_breakdown([candidate], context)[0]

        assert "adaptation" not in alone.score_breakdown
        assert "adaptation" in adapting.score_breakdown
        assert adapting.aggregate_score >= alone.aggregate_score


class TestTiebreakerRegression:
    """Bug reported: Recommendations appeared in alphabetical order when scores were
    similar, because Python's stable sort preserved the original order (which was
    alphabetical from the database query)."""

    def test_first_in_series_prioritized_over_alphabetical_order_regression(
        self,
    ) -> None:
        consumed = [
            make_item(
                rating=5,
                metadata={"genre": "Adventure"},
                status=ConsumptionStatus.COMPLETED,
            )
        ]
        context = _build_context(consumed=consumed)

        # "An Amazing Sequel" sorts before "The Zebra Adventure" alphabetically
        # (after article stripping: "Amazing Sequel" < "Zebra Adventure")
        book_2 = make_item(
            title="An Amazing Sequel (Test Series #2)",
            metadata={"genre": "Adventure"},
            item_id="2",
        )
        book_1 = make_item(
            title="The Zebra Adventure (Test Series #1)",
            metadata={"genre": "Adventure"},
            item_id="1",
        )

        pipeline = ScoringPipeline(DEFAULT_SCORERS)
        results = pipeline.score_candidates_with_breakdown([book_2, book_1], context)

        assert (
            "Zebra Adventure" in results[0].item.title
        ), "First-in-series should be prioritized over alphabetical order"
        assert "Amazing Sequel" in results[1].item.title

    def test_tiebreaker_consistent_ordering(self) -> None:
        """The tiebreaker uses a hash of the title, so ordering should be
        deterministic (not random) but also not purely alphabetical."""
        consumed = [
            make_item(
                rating=5,
                metadata={"genre": "Fiction"},
                status=ConsumptionStatus.COMPLETED,
            )
        ]
        context = _build_context(consumed=consumed)

        items = [
            make_item(
                title=f"Book {chr(65 + i)}",
                metadata={"genre": "Fiction"},
                item_id=str(i),
            )
            for i in range(5)
        ]

        pipeline = ScoringPipeline(DEFAULT_SCORERS)

        first_run = pipeline.score_candidates_with_breakdown(items, context)
        first_order = [r.item.title for r in first_run]

        for _ in range(3):
            subsequent_run = pipeline.score_candidates_with_breakdown(items, context)
            subsequent_order = [r.item.title for r in subsequent_run]
            assert (
                first_order == subsequent_order
            ), "Tiebreaker should produce consistent ordering"
