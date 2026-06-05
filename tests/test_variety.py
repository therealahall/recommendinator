"""Unit tests for the genre-fatigue variety penalty (issue #74).

These tests document the stepped penalty ladder built from recently completed
content and the penalty looked up for a candidate. They cover completion-date
ordering, distinct-cluster stepping, the exact stepped percentages, and the
COMPLETED-only filter.
"""

from __future__ import annotations

from datetime import date

import pytest

from src.models.content import ConsumptionStatus, ContentItem, ContentType
from src.recommendations.variety import (
    VARIETY_LADDER_STEPS,
    VARIETY_SERIES_CONTINUATION_FACTOR,
    VARIETY_TOP_PENALTY,
    build_variety_ladder,
    variety_penalty_for,
)


def _completed(
    title: str,
    genres: list[str],
    *,
    completed_on: date | None = None,
    db_id: int | None = None,
    status: ConsumptionStatus = ConsumptionStatus.COMPLETED,
) -> ContentItem:
    """Build a completed book item carrying *genres* in metadata."""
    return ContentItem(
        id=title.lower().replace(" ", "_"),
        db_id=db_id,
        title=title,
        content_type=ContentType.BOOK,
        status=status,
        date_completed=completed_on,
        metadata={"genres": genres},
    )


def _candidate(title: str, genres: list[str]) -> ContentItem:
    """Build an unread candidate book item carrying *genres* in metadata."""
    return ContentItem(
        id=title.lower().replace(" ", "_"),
        title=title,
        content_type=ContentType.BOOK,
        status=ConsumptionStatus.UNREAD,
        metadata={"genres": genres},
    )


class TestBuildVarietyLadder:
    """Tests for :func:`build_variety_ladder`."""

    def test_empty_when_no_completed_items(self) -> None:
        assert build_variety_ladder([]) == {}

    def test_single_cluster_gets_top_penalty(self) -> None:
        ladder = build_variety_ladder(
            [_completed("Mistborn", ["Fantasy"], completed_on=date(2026, 1, 1))]
        )
        assert ladder == {"fantasy": pytest.approx(VARIETY_TOP_PENALTY)}

    def test_stepped_percentages_descend_by_recency(self) -> None:
        """The documented ladder: 80 / 64 / 48 / 32 / 16 percent."""
        items = [
            _completed("Bio", ["Biography"], completed_on=date(2026, 1, 5)),
            _completed("Crime", ["Mystery"], completed_on=date(2026, 1, 4)),
            _completed("Space", ["Science Fiction"], completed_on=date(2026, 1, 3)),
            _completed("Dragons", ["Fantasy"], completed_on=date(2026, 1, 2)),
            _completed("West", ["Western"], completed_on=date(2026, 1, 1)),
        ]
        ladder = build_variety_ladder(items)

        # Newest first: biography strongest, western weakest.
        assert ladder["nonfiction_documentary"] == pytest.approx(0.80)
        assert ladder["crime_thriller"] == pytest.approx(0.64)
        assert ladder["science_fiction"] == pytest.approx(0.48)
        assert ladder["fantasy"] == pytest.approx(0.32)
        assert ladder["western"] == pytest.approx(0.16)

    def test_ladder_capped_at_step_count(self) -> None:
        """A sixth distinct cluster is beyond the ladder and is not recorded."""
        items = [
            _completed("F", ["Fantasy"], completed_on=date(2026, 1, 6)),
            _completed("S", ["Science Fiction"], completed_on=date(2026, 1, 5)),
            _completed("M", ["Mystery"], completed_on=date(2026, 1, 4)),
            _completed("B", ["Biography"], completed_on=date(2026, 1, 3)),
            _completed("W", ["Western"], completed_on=date(2026, 1, 2)),
            _completed("H", ["Horror"], completed_on=date(2026, 1, 1)),
        ]
        ladder = build_variety_ladder(items)
        assert len(ladder) == VARIETY_LADDER_STEPS
        # The five freshest clusters are recorded; the sixth is dropped.
        assert set(ladder) == {
            "fantasy",
            "science_fiction",
            "crime_thriller",
            "nonfiction_documentary",
            "western",
        }
        assert "horror_dark" not in ladder

    def test_duplicate_clusters_collapse_to_one_rung(self) -> None:
        """Finishing two fantasy books does not consume two rungs."""
        items = [
            _completed("Fantasy A", ["Fantasy"], completed_on=date(2026, 1, 3)),
            _completed("Fantasy B", ["Fantasy"], completed_on=date(2026, 1, 2)),
            _completed("Sci", ["Science Fiction"], completed_on=date(2026, 1, 1)),
        ]
        ladder = build_variety_ladder(items)
        assert len(ladder) == 2
        assert ladder["fantasy"] == pytest.approx(VARIETY_TOP_PENALTY)
        # Sci-fi is the second *distinct* cluster -> rung 1, not rung 2.
        assert ladder["science_fiction"] == pytest.approx(0.64)

    def test_db_id_breaks_ties_for_same_completion_date(self) -> None:
        """With equal dates, the higher db_id is treated as the most recent."""
        items = [
            _completed("Older", ["Fantasy"], completed_on=date(2026, 1, 1), db_id=1),
            _completed(
                "Newer", ["Science Fiction"], completed_on=date(2026, 1, 1), db_id=2
            ),
        ]
        ladder = build_variety_ladder(items)
        assert ladder["science_fiction"] == pytest.approx(VARIETY_TOP_PENALTY)
        assert ladder["fantasy"] == pytest.approx(0.64)

    def test_unread_items_excluded(self) -> None:
        """Only COMPLETED items contribute to the ladder."""
        items = [
            _completed(
                "Wishlist",
                ["Fantasy"],
                completed_on=date(2026, 1, 2),
                status=ConsumptionStatus.UNREAD,
            ),
            _completed("Done", ["Science Fiction"], completed_on=date(2026, 1, 1)),
        ]
        ladder = build_variety_ladder(items)
        assert "fantasy" not in ladder
        assert ladder["science_fiction"] == pytest.approx(VARIETY_TOP_PENALTY)

    def test_currently_consuming_items_excluded(self) -> None:
        """In-progress items do not represent a finished genre."""
        items = [
            _completed(
                "Reading",
                ["Fantasy"],
                completed_on=date(2026, 1, 2),
                status=ConsumptionStatus.CURRENTLY_CONSUMING,
            ),
            _completed("Done", ["Science Fiction"], completed_on=date(2026, 1, 1)),
        ]
        ladder = build_variety_ladder(items)
        assert "fantasy" not in ladder
        assert ladder["science_fiction"] == pytest.approx(VARIETY_TOP_PENALTY)

    def test_undated_items_sort_after_dated_items(self) -> None:
        """Items without a completion date rank below dated ones."""
        items = [
            _completed("Undated", ["Fantasy"], completed_on=None, db_id=1),
            _completed("Dated", ["Science Fiction"], completed_on=date(2026, 1, 1)),
        ]
        ladder = build_variety_ladder(items)
        # Dated sci-fi is freshest -> top penalty; undated fantasy is rung 1.
        assert ladder["science_fiction"] == pytest.approx(VARIETY_TOP_PENALTY)
        assert ladder["fantasy"] == pytest.approx(0.64)

    def test_custom_steps_and_top_penalty(self) -> None:
        items = [
            _completed("A", ["Fantasy"], completed_on=date(2026, 1, 2)),
            _completed("B", ["Science Fiction"], completed_on=date(2026, 1, 1)),
        ]
        ladder = build_variety_ladder(items, steps=2, top_penalty=1.0)
        assert ladder["fantasy"] == pytest.approx(1.0)
        assert ladder["science_fiction"] == pytest.approx(0.5)

    def test_zero_steps_disables_ladder(self) -> None:
        items = [_completed("A", ["Fantasy"], completed_on=date(2026, 1, 1))]
        assert build_variety_ladder(items, steps=0) == {}


class TestVarietyPenaltyFor:
    """Tests for :func:`variety_penalty_for`."""

    def test_no_ladder_no_penalty(self) -> None:
        assert variety_penalty_for(_candidate("X", ["Fantasy"]), {}) == 0.0

    def test_matching_cluster_returns_its_penalty(self) -> None:
        ladder = {"fantasy": 0.8}
        assert variety_penalty_for(
            _candidate("Dragon", ["Fantasy"]), ladder
        ) == pytest.approx(0.8)

    def test_unmatched_candidate_returns_zero(self) -> None:
        ladder = {"fantasy": 0.8}
        assert variety_penalty_for(_candidate("Crime", ["Mystery"]), ladder) == 0.0

    def test_candidate_without_genres_returns_zero(self) -> None:
        """An un-enriched candidate (no genre metadata) is never penalised."""
        ladder = {"fantasy": 0.8}
        assert variety_penalty_for(_candidate("Unknown", []), ladder) == 0.0

    def test_strongest_matching_cluster_wins(self) -> None:
        """A multi-genre candidate is judged by its freshest matching genre."""
        ladder = {"fantasy": 0.32, "science_fiction": 0.8}
        # Candidate is both fantasy and sci-fi; sci-fi is fresher -> 0.8.
        penalty = variety_penalty_for(
            _candidate("Crossover", ["Fantasy", "Science Fiction"]), ladder
        )
        assert penalty == pytest.approx(0.8)

    def test_series_continuation_no_match_stays_zero(self) -> None:
        """Continuation softening of a non-matching candidate is still zero.

        The softening is multiplicative, so a candidate that matches no ladder
        cluster stays at ``0.0`` even when flagged as a series continuation.
        """
        ladder = {"fantasy": 0.8}
        candidate = _candidate("Sci Sequel", ["Science Fiction"])
        assert (
            variety_penalty_for(candidate, ladder, is_series_continuation=True) == 0.0
        )


class TestVarietySeriesContinuationRegression:
    """Regression tests for the variety penalty burying the next series book."""

    def test_series_continuation_softens_penalty_regression(self) -> None:
        """Regression test: the next book in a started series isn't buried.

        Bug reported: after reading Expanse book #1, the legit next book #2
        (Caliban's War) sank to rank 123 under a 48% variety penalty while
        unreadable novellas floated to the top.
        Root cause: the variety-after-completion penalty hit the next-in-series
        book at full strength because it shares the just-completed sci-fi
        cluster — finishing book #1 was treated as finishing the genre.
        Fix: soften (halve) — not remove — the variety penalty for an item that
        continues a series the user is actively progressing through, so genre
        fatigue still nudges but no longer buries the next book.
        """
        ladder = {"science_fiction": VARIETY_TOP_PENALTY}
        candidate = _candidate("Caliban's War", ["Science Fiction"])
        full = variety_penalty_for(candidate, ladder)
        softened = variety_penalty_for(candidate, ladder, is_series_continuation=True)
        assert full == pytest.approx(VARIETY_TOP_PENALTY)
        assert softened == pytest.approx(
            VARIETY_TOP_PENALTY * VARIETY_SERIES_CONTINUATION_FACTOR
        )
        # Softening only lowers; it never raises the penalty.
        assert softened < full
