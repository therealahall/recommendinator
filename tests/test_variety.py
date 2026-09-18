"""Unit tests for the genre-fatigue variety penalty (issue #74)."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from src.models.content import ConsumptionStatus, ContentItem, ContentType
from src.models.user_preferences import UserPreferenceConfig
from src.recommendations.variety import (
    VARIETY_LADDER_STEPS,
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

        genres = ["Biography", "Mystery", "Science Fiction", "Fantasy", "Western"]
        for rung, genre in enumerate(genres):
            assert variety_penalty_for(
                _candidate(genre, [genre]), ladder
            ) == pytest.approx(
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
        assert variety_penalty_for(_candidate("H", ["Horror"]), ladder) == 0.0

    def test_duplicate_clusters_collapse_to_one_rung(self) -> None:
        items = [
            _completed("Fantasy A", ["Fantasy"], completed_on=date(2026, 1, 3)),
            _completed("Fantasy B", ["Fantasy"], completed_on=date(2026, 1, 2)),
            _completed("Sci", ["Science Fiction"], completed_on=date(2026, 1, 1)),
        ]
        ladder = build_variety_ladder(items)
        assert len(ladder) == 2
        assert variety_penalty_for(
            _candidate("F", ["Fantasy"]), ladder
        ) == pytest.approx(VARIETY_TOP_PENALTY)
        assert variety_penalty_for(
            _candidate("S", ["Science Fiction"]), ladder
        ) == pytest.approx(
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
        assert variety_penalty_for(_candidate("F", ["Fantasy"]), ladder) == 0.0
        assert variety_penalty_for(
            _candidate("S", ["Science Fiction"]), ladder
        ) == pytest.approx(VARIETY_TOP_PENALTY)

    def test_every_cluster_of_one_completion_shares_its_rung(self) -> None:
        """A finished book belonging to five clusters used to fill every rung by
        itself, so nothing finished before it reached the ladder at all."""
        omnibus_genres = ["Fantasy", "Science Fiction", "Mystery", "Horror", "Western"]
        items = [
            _completed("Omnibus", omnibus_genres, completed_on=date(2026, 1, 2)),
            _completed("Bio", ["Biography"], completed_on=date(2026, 1, 1)),
        ]
        ladder = build_variety_ladder(items)

        assert len(ladder) == 2
        assert variety_penalty_for(
            _candidate("Twin", omnibus_genres), ladder
        ) == pytest.approx(VARIETY_TOP_PENALTY)
        assert variety_penalty_for(
            _candidate("Memoir", ["Biography"]), ladder
        ) == pytest.approx(
            VARIETY_TOP_PENALTY * (VARIETY_LADDER_STEPS - 1) / VARIETY_LADDER_STEPS
        )

    def test_shared_cluster_keeps_the_fresher_completions_rung(self) -> None:
        items = [
            _completed(
                "New Fantasy", ["Fantasy", "Mystery"], completed_on=date(2026, 1, 2)
            ),
            _completed(
                "Old Fantasy", ["Fantasy", "Western"], completed_on=date(2026, 1, 1)
            ),
        ]
        ladder = build_variety_ladder(items)
        older_rung = (
            VARIETY_TOP_PENALTY * (VARIETY_LADDER_STEPS - 1) / VARIETY_LADDER_STEPS
        )

        shared = variety_penalty_for(_candidate("F", ["Fantasy"]), ladder)
        older_only = variety_penalty_for(_candidate("W", ["Western"]), ladder)

        assert len(ladder) == 2
        assert older_only == pytest.approx(older_rung / 2)
        assert shared > older_only

    def test_undated_items_sort_after_dated_items(self) -> None:
        items = [
            _completed("Undated", ["Fantasy"], completed_on=None, db_id=1),
            _completed("Dated", ["Science Fiction"], completed_on=date(2026, 1, 1)),
        ]
        ladder = build_variety_ladder(items)
        assert variety_penalty_for(
            _candidate("S", ["Science Fiction"]), ladder
        ) == pytest.approx(VARIETY_TOP_PENALTY)
        assert variety_penalty_for(
            _candidate("F", ["Fantasy"]), ladder
        ) == pytest.approx(
            VARIETY_TOP_PENALTY * (VARIETY_LADDER_STEPS - 1) / VARIETY_LADDER_STEPS
        )


class TestVarietyPenaltyFor:
    def test_matching_cluster_returns_its_penalty(self) -> None:
        ladder = [(frozenset({"fantasy"}), 0.8)]
        assert variety_penalty_for(
            _candidate("Dragon", ["Fantasy"]), ladder
        ) == pytest.approx(0.8)

    def test_candidate_without_genres_returns_zero(self) -> None:
        ladder = [(frozenset({"fantasy"}), 0.8)]
        assert variety_penalty_for(_candidate("Unknown", []), ladder) == 0.0

    def test_penalty_scales_with_how_much_of_a_rung_a_candidate_shares(self) -> None:
        """Every candidate touching one cluster of a densely tagged completion used
        to take that completion's whole penalty, which flattened most of a library."""
        ladder = build_variety_ladder(
            [_completed("Feral Gods", ["LitRPG", "Adventure", "Comedy"])],
            top_penalty=VARIETY_TOP_PENALTY,
        )

        def penalty(genres: list[str]) -> float:
            return variety_penalty_for(_candidate("Next", genres), ladder)

        assert penalty(["LitRPG", "Adventure", "Comedy"]) == pytest.approx(1.0)
        assert penalty(["LitRPG"]) == pytest.approx(0.5)
        assert penalty(["Epic Fantasy"]) == pytest.approx(0.25)
        assert penalty(["Thriller"]) == 0.0

    def test_close_match_on_an_older_rung_beats_a_brush_with_the_newest(self) -> None:
        ladder = [
            (
                frozenset({"fantasy", "science_fiction", "crime_thriller", "western"}),
                1.0,
            ),
            (frozenset({"horror_dark", "comedy_lighthearted"}), 0.8),
        ]
        penalty = variety_penalty_for(
            _candidate("Funny Horror", ["Horror", "Comedy", "Fantasy"]), ladder
        )
        assert penalty == pytest.approx(0.8)

    def test_clusters_outside_a_rung_do_not_soften_it(self) -> None:
        """The penalty used to fall as a candidate's own cluster count rose, so a
        sparsely imported book outranked a heavily enriched one on genre alone."""
        ladder = [(frozenset({"fantasy"}), 1.0)]

        focused = variety_penalty_for(_candidate("Dragons", ["Fantasy"]), ladder)
        broad = variety_penalty_for(
            _candidate("Dragons in Love", ["Fantasy", "Romance", "Adventure"]), ladder
        )

        assert focused == pytest.approx(1.0)
        assert broad == pytest.approx(focused)


class TestOngoingTvShowFinishedSeasons:
    """A finished season of an ongoing TV show counts as a completion event."""

    def test_finished_season_of_ongoing_show_enters_ladder(self) -> None:
        show = _ongoing_show("Wheel", ["Fantasy"], {"1": "2026-05-01T00:00:00+00:00"})
        ladder = build_variety_ladder([show], top_penalty=1.0)
        assert variety_penalty_for(
            _candidate("Dragon", ["Fantasy"]), ladder
        ) == pytest.approx(1.0)

    def test_ongoing_show_without_seasons_does_not_enter_ladder(self) -> None:
        show = ContentItem(
            id="x",
            title="X",
            content_type=ContentType.TV_SHOW,
            status=ConsumptionStatus.CURRENTLY_CONSUMING,
            metadata={"genres": ["Fantasy"], "seasons_watched": []},
        )
        assert build_variety_ladder([show], top_penalty=1.0) == []


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

        bobs_burgers = _candidate("Bob's Burgers", ["Animation", "Comedy"])
        penalty = variety_penalty_for(bobs_burgers, ladder)
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

        scifi = variety_penalty_for(_candidate("S", ["Science Fiction"]), ladder)
        assert scifi == pytest.approx(VARIETY_TOP_PENALTY)
        assert variety_penalty_for(_candidate("F", ["Fantasy"]), ladder) < scifi


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
        assert max(penalty for _, penalty in ladder) == pytest.approx(
            VARIETY_TOP_PENALTY
        )

    def test_undated_completion_ranks_below_every_dated_one(self) -> None:
        ladder = build_variety_ladder(
            [
                _completed("Mistborn", ["Fantasy"], db_id=99),
                _completed("Dune", ["Science Fiction"], completed_on=date(2019, 1, 1)),
            ]
        )

        assert variety_penalty_for(
            _candidate("S", ["Science Fiction"]), ladder
        ) > variety_penalty_for(_candidate("F", ["Fantasy"]), ladder)
