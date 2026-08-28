"""Unit tests for the genre-fatigue variety penalty (issue #74)."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from src.models.content import ConsumptionStatus, ContentItem, ContentType
from src.models.user_preferences import UserPreferenceConfig
from src.recommendations.variety import (
    VARIETY_LADDER_STEPS,
    VARIETY_SERIES_CONTINUATION_FACTOR,
    VARIETY_TOP_PENALTY,
    _completion_recency,
    build_variety_ladder,
    top_penalty_for_preference,
    variety_penalty_for,
)
from src.storage.sqlite_db import SQLiteDB


def _completed(
    title: str,
    genres: list[str],
    *,
    completed_on: date | None = None,
    db_id: int | None = None,
    status: ConsumptionStatus = ConsumptionStatus.COMPLETED,
) -> ContentItem:
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
    return ContentItem(
        id=title.lower().replace(" ", "_"),
        title=title,
        content_type=ContentType.BOOK,
        status=ConsumptionStatus.UNREAD,
        metadata={"genres": genres},
    )


def _ongoing_show(
    title: str, genres: list[str], season_dates: dict[str, str]
) -> ContentItem:
    return ContentItem(
        id=title,
        title=title,
        content_type=ContentType.TV_SHOW,
        status=ConsumptionStatus.CURRENTLY_CONSUMING,
        metadata={
            "genres": genres,
            "seasons_watched": [1],
            "seasons_watched_dates": season_dates,
        },
    )


class TestTopPenaltyForPreference:
    def test_strength_scales_linearly(self) -> None:
        max_strength = UserPreferenceConfig.MAX_VARIETY_PENALTY
        assert top_penalty_for_preference(max_strength / 2) == pytest.approx(
            top_penalty_for_preference(max_strength) / 2
        )

    def test_out_of_range_strength_clamps_to_the_fraction_domain(self) -> None:
        """The engine multiplies a candidate's score by ``1 - penalty``, so a fraction
        above ``1.0`` would emit negative scores."""
        assert top_penalty_for_preference(50.0) == pytest.approx(VARIETY_TOP_PENALTY)
        assert top_penalty_for_preference(-5.0) == pytest.approx(0.0)


class TestBuildVarietyLadder:
    def test_stepped_percentages_descend_by_recency(self) -> None:
        items = [
            _completed("Bio", ["Biography"], completed_on=date(2026, 1, 5)),
            _completed("Crime", ["Mystery"], completed_on=date(2026, 1, 4)),
            _completed("Space", ["Science Fiction"], completed_on=date(2026, 1, 3)),
            _completed("Dragons", ["Fantasy"], completed_on=date(2026, 1, 2)),
            _completed("West", ["Western"], completed_on=date(2026, 1, 1)),
        ]
        ladder = build_variety_ladder(items)

        # Newest first: biography strongest, western weakest.
        rungs = [
            "nonfiction_documentary",
            "crime_thriller",
            "science_fiction",
            "fantasy",
            "western",
        ]
        for rung, cluster in enumerate(rungs):
            assert ladder[cluster] == pytest.approx(
                VARIETY_TOP_PENALTY
                * (VARIETY_LADDER_STEPS - rung)
                / VARIETY_LADDER_STEPS
            )

    def test_ladder_capped_at_step_count(self) -> None:
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
        assert set(ladder) == {
            "fantasy",
            "science_fiction",
            "crime_thriller",
            "nonfiction_documentary",
            "western",
        }
        assert "horror_dark" not in ladder

    def test_duplicate_clusters_collapse_to_one_rung(self) -> None:
        items = [
            _completed("Fantasy A", ["Fantasy"], completed_on=date(2026, 1, 3)),
            _completed("Fantasy B", ["Fantasy"], completed_on=date(2026, 1, 2)),
            _completed("Sci", ["Science Fiction"], completed_on=date(2026, 1, 1)),
        ]
        ladder = build_variety_ladder(items)
        assert len(ladder) == 2
        assert ladder["fantasy"] == pytest.approx(VARIETY_TOP_PENALTY)
        # Sci-fi is the second *distinct* cluster -> rung 1, not rung 2.
        assert ladder["science_fiction"] == pytest.approx(
            VARIETY_TOP_PENALTY * (VARIETY_LADDER_STEPS - 1) / VARIETY_LADDER_STEPS
        )

    def test_unread_items_excluded(self) -> None:
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

    def test_undated_items_sort_after_dated_items(self) -> None:
        items = [
            _completed("Undated", ["Fantasy"], completed_on=None, db_id=1),
            _completed("Dated", ["Science Fiction"], completed_on=date(2026, 1, 1)),
        ]
        ladder = build_variety_ladder(items)
        # Dated sci-fi is freshest -> top penalty; undated fantasy is rung 1.
        assert ladder["science_fiction"] == pytest.approx(VARIETY_TOP_PENALTY)
        assert ladder["fantasy"] == pytest.approx(
            VARIETY_TOP_PENALTY * (VARIETY_LADDER_STEPS - 1) / VARIETY_LADDER_STEPS
        )


class TestVarietyPenaltyFor:
    def test_matching_cluster_returns_its_penalty(self) -> None:
        ladder = {"fantasy": 0.8}
        assert variety_penalty_for(
            _candidate("Dragon", ["Fantasy"]), ladder
        ) == pytest.approx(0.8)

    def test_candidate_without_genres_returns_zero(self) -> None:
        ladder = {"fantasy": 0.8}
        assert variety_penalty_for(_candidate("Unknown", []), ladder) == 0.0

    def test_strongest_matching_cluster_wins(self) -> None:
        ladder = {"fantasy": 0.32, "science_fiction": 0.8}
        # Candidate is both fantasy and sci-fi; sci-fi is fresher -> 0.8.
        penalty = variety_penalty_for(
            _candidate("Crossover", ["Fantasy", "Science Fiction"]), ladder
        )
        assert penalty == pytest.approx(0.8)


class TestVarietySeriesContinuationRegression:
    def test_series_continuation_softens_penalty_regression(self) -> None:
        """Bug reported: after reading Expanse book #1, the legit next book #2
        (Caliban's War) sank to rank 123 under a 48% variety penalty while unreadable
        novellas floated to the top."""
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


class TestOngoingTvShowFinishedSeasons:
    """A finished season of an ongoing TV show counts as a completion event."""

    def test_finished_season_of_ongoing_show_enters_ladder(self) -> None:
        show = _ongoing_show("Wheel", ["Fantasy"], {"1": "2026-05-01T00:00:00+00:00"})
        ladder = build_variety_ladder([show], top_penalty=1.0)
        assert ladder.get("fantasy") == pytest.approx(1.0)

    def test_ongoing_show_without_seasons_does_not_enter_ladder(self) -> None:
        show = ContentItem(
            id="x",
            title="X",
            content_type=ContentType.TV_SHOW,
            status=ConsumptionStatus.CURRENTLY_CONSUMING,
            metadata={"genres": ["Fantasy"], "seasons_watched": []},
        )
        assert build_variety_ladder([show], top_penalty=1.0) == {}


def _completed_show(
    title: str,
    genres: list[str],
    *,
    completed_on: date | None,
    season_dates: dict[str, str] | None = None,
    db_id: int | None = None,
) -> ContentItem:
    metadata: dict[str, object] = {"genres": genres}
    if season_dates is not None:
        metadata["seasons_watched_dates"] = season_dates
    return ContentItem(
        id=title.lower().replace(" ", "_"),
        db_id=db_id,
        title=title,
        content_type=ContentType.TV_SHOW,
        status=ConsumptionStatus.COMPLETED,
        date_completed=completed_on,
        metadata=metadata,
    )


class TestCompletionRecency:
    def test_completed_tv_show_without_date_uses_latest_season_date(self) -> None:
        show = _completed_show(
            "DuckTales",
            ["Animation"],
            completed_on=None,
            season_dates={
                "3": "2026-06-01T00:00:00+00:00",
                "4": "2026-07-17T00:00:00+00:00",
            },
        )
        assert _completion_recency(show) == date(2026, 7, 17)

    def test_completed_tv_show_with_date_ignores_season_fallback(self) -> None:
        show = _completed_show(
            "DuckTales",
            ["Animation"],
            completed_on=date(2026, 1, 1),
            season_dates={"4": "2026-07-17T00:00:00+00:00"},
        )
        assert _completion_recency(show) == date(2026, 1, 1)


class TestCompletedTvShowSeasonDateFallbackRegression:
    def test_completed_animated_show_season_date_penalizes_animation(self) -> None:
        """Bug reported: DuckTales was COMPLETED and rated 4, its genres include
        Animation, but its ``date_completed`` column was NULL and its finish date
        lived only in ``metadata['seasons_watched_dates']``."""
        ducktales = _completed_show(
            "DuckTales",
            ["Animation"],
            completed_on=None,
            season_dates={"4": "2026-07-17T00:00:00+00:00"},
            db_id=1,
        )
        # Five dated, non-animation completions. Their completion dates all sit
        # a little before DuckTales' latest season date (2026-07-17), so once
        # DuckTales is treated as dated it is the freshest event and claims the
        # top rung.
        other_dated = [
            _completed_show(
                "Crime Show", ["Crime"], completed_on=date(2026, 7, 12), db_id=2
            ),
            _completed_show(
                "Space Show",
                ["Science Fiction"],
                completed_on=date(2026, 7, 13),
                db_id=3,
            ),
            _completed_show(
                "Dragon Show", ["Fantasy"], completed_on=date(2026, 7, 14), db_id=4
            ),
            _completed_show(
                "West Show", ["Western"], completed_on=date(2026, 7, 15), db_id=5
            ),
            _completed_show(
                "War Show", ["War"], completed_on=date(2026, 7, 16), db_id=6
            ),
        ]

        ladder = build_variety_ladder([ducktales, *other_dated])

        # DuckTales' cluster now lands on the ladder: dating it by its season
        # timestamp makes it the freshest completion instead of an undated event
        # that sorts below the five dated shows and off the five-rung ladder.
        assert "animation_family" in ladder

        bobs_burgers = _candidate("Bob's Burgers", ["Animation", "Comedy"])
        penalty = variety_penalty_for(bobs_burgers, ladder)
        # Before the fix this penalty was 0.0 (animation_family never on ladder).
        assert penalty > 0.0


class TestVarietyLadderUsesInAppCompletions:
    """Bug reported: finishing a book in the app and marking it complete left
    ``date_completed`` NULL, so the ladder — which sorts undated events last —
    ranked a years-old imported completion above it and demoted the wrong genre."""

    def test_in_app_completion_outranks_older_import_regression(
        self, tmp_path: Path
    ) -> None:
        storage = SQLiteDB(tmp_path / "variety.db")
        storage.save_content_item(
            ContentItem(
                id="import-1",
                title="Mistborn",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.COMPLETED,
                date_completed=date(2020, 1, 1),
                metadata={"genres": ["Fantasy"]},
            )
        )
        scifi_id = storage.save_content_item(
            ContentItem(
                id="in-app-1",
                title="Project Hail Mary",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.UNREAD,
                metadata={"genres": ["Science Fiction"]},
            )
        )

        storage.update_item_from_ui(db_id=scifi_id, status="completed")

        ladder = build_variety_ladder(storage.get_content_items())

        assert ladder["science_fiction"] == pytest.approx(VARIETY_TOP_PENALTY)
        assert ladder["fantasy"] < ladder["science_fiction"]


class TestUndatedCompletionsStillClaimRungs:
    def test_library_of_only_undated_completions_fills_the_ladder(self) -> None:
        undated = [
            _completed("Mistborn", ["Fantasy"], db_id=1),
            _completed("Dune", ["Science Fiction"], db_id=2),
            _completed("Gone Girl", ["Thriller"], db_id=3),
            _completed("Wolf Hall", ["History"], db_id=4),
            _completed("Bossypants", ["Comedy"], db_id=5),
        ]

        ladder = build_variety_ladder(undated)

        assert len(ladder) == VARIETY_LADDER_STEPS
        assert max(ladder.values()) == pytest.approx(VARIETY_TOP_PENALTY)

    def test_undated_completion_ranks_below_every_dated_one(self) -> None:
        ladder = build_variety_ladder(
            [
                _completed("Mistborn", ["Fantasy"], db_id=99),
                _completed("Dune", ["Science Fiction"], completed_on=date(2019, 1, 1)),
            ]
        )

        assert ladder["science_fiction"] > ladder["fantasy"]
