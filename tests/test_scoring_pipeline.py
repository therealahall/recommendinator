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
from src.recommendations.scoring_pipeline import ScoredCandidate, ScoringPipeline
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
        """Higher-scoring candidates should appear first."""
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

    def test_empty_candidates(self) -> None:
        """Empty candidate list should return empty results."""
        context = _build_context()
        pipeline = ScoringPipeline(DEFAULT_SCORERS)
        assert pipeline.score_candidates_with_breakdown([], context) == []

    def test_weight_normalization(self) -> None:
        """Aggregate score should be in [0, 1] regardless of weights."""
        consumed = [
            make_item(
                rating=5,
                metadata={"genre": "Fantasy"},
                status=ConsumptionStatus.COMPLETED,
            )
        ]
        context = _build_context(consumed=consumed)
        candidate = make_item(metadata={"genre": "Fantasy"})

        pipeline = ScoringPipeline(DEFAULT_SCORERS)
        result = pipeline.score_candidates_with_breakdown([candidate], context)
        assert 0.0 <= result[0].aggregate_score <= 1.0

    def test_score_clamped_to_unit_interval(self) -> None:
        """Even with extreme inputs, scores should remain in [0, 1]."""

        class AlwaysMaxScorer(Scorer):
            def score(self, candidate: ContentItem, context: ScoringContext) -> float:
                return 1.0

        class AlwaysMinScorer(Scorer):
            def score(self, candidate: ContentItem, context: ScoringContext) -> float:
                return 0.0

        context = _build_context()
        candidate = make_item()

        # All max
        pipeline = ScoringPipeline([AlwaysMaxScorer(weight=10.0)])
        result = pipeline.score_candidates_with_breakdown([candidate], context)
        assert result[0].aggregate_score == 1.0

        # All min
        pipeline = ScoringPipeline([AlwaysMinScorer(weight=10.0)])
        result = pipeline.score_candidates_with_breakdown([candidate], context)
        assert result[0].aggregate_score == 0.0

    def test_zero_total_weight(self) -> None:
        """If all scorers have weight 0, scores should be 0.0."""
        context = _build_context()
        candidate = make_item()
        pipeline = ScoringPipeline(
            [GenreMatchScorer(weight=0.0), TagOverlapScorer(weight=0.0)]
        )
        result = pipeline.score_candidates_with_breakdown([candidate], context)
        assert result[0].aggregate_score == 0.0

    def test_breakdown_keys_present(self) -> None:
        """score_candidates_with_breakdown returns expected scorer keys."""
        consumed = [
            make_item(
                rating=5,
                metadata={"genre": "Fantasy"},
                status=ConsumptionStatus.COMPLETED,
            )
        ]
        context = _build_context(consumed=consumed)
        candidate = make_item(title="Test", metadata={"genre": "Fantasy"})

        pipeline = ScoringPipeline(DEFAULT_SCORERS)
        results = pipeline.score_candidates_with_breakdown([candidate], context)

        assert len(results) == 1
        scored = results[0]
        assert isinstance(scored, ScoredCandidate)
        assert "genre_match" in scored.score_breakdown
        assert "creator_match" in scored.score_breakdown
        assert "tag_overlap" in scored.score_breakdown
        assert "series_order" in scored.score_breakdown
        assert "rating_pattern" in scored.score_breakdown
        assert "continuation" in scored.score_breakdown
        assert "series_affinity" in scored.score_breakdown
        # All raw scores should be in [0, 1]
        for raw_score in scored.score_breakdown.values():
            assert 0.0 <= raw_score <= 1.0

    def test_breakdown_sorted_descending(self) -> None:
        """score_candidates_with_breakdown returns results sorted descending."""
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
        results = pipeline.score_candidates_with_breakdown(
            [poor_match, good_match], context
        )

        assert results[0].item.title == "Good"
        assert results[1].item.title == "Poor"
        assert results[0].aggregate_score >= results[1].aggregate_score


class TestTiebreakerRegression:
    """Regression tests for tiebreaker logic to prevent alphabetical ordering.

    Bug reported: Recommendations appeared in alphabetical order when scores
    were similar, because Python's stable sort preserved the original order
    (which was alphabetical from the database query).

    Fix: Added tiebreaker that prioritizes first-in-series items and uses
    a stable hash for pseudo-random ordering among equal scores.
    """

    def test_first_in_series_prioritized_over_alphabetical_order_regression(
        self,
    ) -> None:
        """Regression test: First-in-series items should rank higher than later items.

        Bug: When scores were tied, items sorted alphabetically. This meant
        "An Amazing Sequel #2" would appear before "The Zebra Adventure #1"
        even though #1 should be recommended first.

        Fix: Tiebreaker prioritizes first-in-series items.
        """
        # All items have same genre, so all scores will be similar
        consumed = [
            make_item(
                rating=5,
                metadata={"genre": "Adventure"},
                status=ConsumptionStatus.COMPLETED,
            )
        ]
        context = _build_context(consumed=consumed)

        # Create items that would sort differently alphabetically vs by series
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

        # Feed in alphabetical order (book_2 first)
        pipeline = ScoringPipeline(DEFAULT_SCORERS)
        results = pipeline.score_candidates_with_breakdown([book_2, book_1], context)

        # Book #1 should be first due to tiebreaker prioritizing first-in-series
        assert (
            "Zebra Adventure" in results[0].item.title
        ), "First-in-series should be prioritized over alphabetical order"
        assert "Amazing Sequel" in results[1].item.title

    def test_tiebreaker_consistent_ordering(self) -> None:
        """Tiebreaker should produce consistent results across multiple runs.

        The tiebreaker uses a hash of the title, so ordering should be
        deterministic (not random) but also not purely alphabetical.
        """
        consumed = [
            make_item(
                rating=5,
                metadata={"genre": "Fiction"},
                status=ConsumptionStatus.COMPLETED,
            )
        ]
        context = _build_context(consumed=consumed)

        # Create multiple items with same genre (similar scores)
        items = [
            make_item(
                title=f"Book {chr(65 + i)}",  # Book A, Book B, Book C, ...
                metadata={"genre": "Fiction"},
                item_id=str(i),
            )
            for i in range(5)
        ]

        pipeline = ScoringPipeline(DEFAULT_SCORERS)

        # Run multiple times and verify consistent ordering
        first_run = pipeline.score_candidates_with_breakdown(items, context)
        first_order = [r.item.title for r in first_run]

        for _ in range(3):
            subsequent_run = pipeline.score_candidates_with_breakdown(items, context)
            subsequent_order = [r.item.title for r in subsequent_run]
            assert (
                first_order == subsequent_order
            ), "Tiebreaker should produce consistent ordering"

    def test_tiebreaker_does_not_affect_different_scores(self) -> None:
        """Items with genuinely different scores should still sort by score."""
        consumed = [
            make_item(
                rating=5,
                metadata={"genre": "Fantasy"},
                status=ConsumptionStatus.COMPLETED,
            )
        ]
        context = _build_context(consumed=consumed)

        # Different genres = different scores
        fantasy_book = make_item(
            title="Zzz Last Alphabetically",
            metadata={"genre": "Fantasy"},  # Matches consumed genre
            item_id="1",
        )
        horror_book = make_item(
            title="Aaa First Alphabetically",
            metadata={"genre": "Horror"},  # Different genre
            item_id="2",
        )

        pipeline = ScoringPipeline(DEFAULT_SCORERS)
        results = pipeline.score_candidates_with_breakdown(
            [horror_book, fantasy_book], context
        )

        # Fantasy book should be first despite being last alphabetically
        assert "Zzz Last" in results[0].item.title
        assert results[0].aggregate_score > results[1].aggregate_score
