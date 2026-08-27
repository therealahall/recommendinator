"""Tests for Trakt API integration."""

from collections.abc import Callable
from datetime import date
from typing import Any
from unittest.mock import Mock, patch

import pytest
import requests

from src.ingestion.plugin_base import ProgressCallback, SourceError
from src.ingestion.sources.trakt.trakt import (
    TraktAPIError,
    TraktPlugin,
    fetch_list,
    fetch_show_season_totals,
    refresh_access_token,
)
from src.models.content import ConsumptionStatus, ContentItem, ContentType
from src.utils.series import expand_tv_shows_to_seasons
from tests.factories import make_storage_mock


def _movie(trakt_id: int, title: str, year: int = 2020) -> dict[str, Any]:
    """Build a minimal Trakt movie object."""
    return {
        "title": title,
        "year": year,
        "ids": {"trakt": trakt_id, "slug": f"{title.lower()}-{year}"},
    }


def _show(
    trakt_id: int, title: str, aired_episodes: int, year: int = 2015
) -> dict[str, Any]:
    """Build a minimal Trakt show object with extended info."""
    return {
        "title": title,
        "year": year,
        "ids": {"trakt": trakt_id, "slug": f"{title.lower()}-{year}"},
        "aired_episodes": aired_episodes,
        "genres": ["drama"],
    }


class TestRefreshAccessToken:
    """Tests for Trakt OAuth token refresh."""

    @patch("src.ingestion.sources.trakt.trakt.requests.post")
    def test_refresh_preserves_old_token_when_omitted(self, mock_post: Mock) -> None:
        """Test old refresh token is preserved when response omits it."""
        mock_response = Mock(spec=requests.Response)
        mock_response.json.return_value = {"access_token": "new_access"}
        mock_post.return_value = mock_response

        result = refresh_access_token("original_refresh", "cid", "secret")

        assert result["access_token"] == "new_access"
        assert result["refresh_token"] == "original_refresh"


class TestFetchList:
    """Tests for the paginated list fetcher."""

    @patch("src.ingestion.sources.trakt.trakt.requests.get")
    def test_pagination(self, mock_get: Mock) -> None:
        """Test that multiple pages are followed via the page-count header."""
        page1 = Mock(spec=requests.Response)
        page1.headers = {"X-Pagination-Page-Count": "2"}
        page1.json.return_value = [{"a": 1}]
        page2 = Mock(spec=requests.Response)
        page2.headers = {"X-Pagination-Page-Count": "2"}
        page2.json.return_value = [{"a": 2}]
        mock_get.side_effect = [page1, page2]

        result = fetch_list("/sync/watchlist/movies", "access", "cid")

        assert result == [{"a": 1}, {"a": 2}]
        assert mock_get.call_count == 2


class TestWatchedDateTimezone:
    """Tests for dating a watch by the viewer's calendar day, not UTC's."""

    def test_late_evening_local_watch_keeps_local_date_regression(
        self, host_timezone: Callable[[str], None]
    ) -> None:
        """Regression test: an evening watch was stored as the next day.

        Bug reported: a movie finished at 21:00 on 2026-03-14 in
        America/Los_Angeles is recorded by Trakt as the instant
        ``2026-03-15T04:00:00.000Z``, and the library reported a
        ``date_completed`` of 2026-03-15 — a day the user had not lived yet.
        Root cause: the plugin narrowed the parsed instant with ``.date()``,
        which yields the UTC calendar day rather than the viewer's.
        Fix: the instant is converted to the host's zone before narrowing, via
        ``local_date_from_iso_timestamp``.
        """
        host_timezone("America/Los_Angeles")
        payloads = _all_lists(
            watched_movies=[
                {
                    "last_watched_at": "2026-03-15T04:00:00.000Z",
                    "movie": _movie(1, "Inception"),
                }
            ]
        )
        items = _run_fetch(payloads, _config())

        assert items[0].date_completed == date(2026, 3, 14)


class TestTraktNormalizeRating:
    """Tests for the 10-point to 5-point rating normalization."""

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            (None, None),
            (0, None),
            (1, 1),
            (7, 4),
            (10, 5),
        ],
    )
    def test_normalize(self, raw: int | None, expected: int | None) -> None:
        """Test 1-10 ratings map to 1-5, never 0."""
        assert TraktPlugin().normalize_rating(raw) == expected


class TestTraktPluginValidation:
    """Tests for TraktPlugin config validation."""

    def test_validate_missing_client_id(self) -> None:
        """Test validation fails with actionable setup guidance when client_id missing."""
        errors = TraktPlugin().validate_config(
            {"client_secret": "secret", "refresh_token": "token"}
        )
        assert any("https://trakt.tv/oauth/applications" in error for error in errors)

    def test_validate_missing_secret_passes_when_in_db(self) -> None:
        """Test missing sensitive fields are satisfied from the credential DB."""
        plugin = TraktPlugin()
        mock_storage = make_storage_mock()
        mock_storage.credentials.get_for_source.return_value = {
            "client_secret": "db_secret",
            "refresh_token": "db_token",
        }

        errors = plugin.validate_config(
            {"_source_id": "my_trakt", "client_id": "cid"},
            storage=mock_storage,
            user_id=1,
        )
        assert errors == []
        mock_storage.credentials.get_for_source.assert_called_with(1, "my_trakt")


class TestTraktPluginFetch:
    """Tests for TraktPlugin.fetch()."""

    def test_fetch_watched_movie(self) -> None:
        """Test a watched movie becomes a COMPLETED movie item."""
        payloads = _all_lists(
            watched_movies=[
                {
                    "last_watched_at": "2021-05-01T10:00:00.000Z",
                    "movie": _movie(1, "Inception"),
                }
            ]
        )
        items = _run_fetch(payloads, _config())

        assert len(items) == 1
        item = items[0]
        assert item.title == "Inception"
        assert item.content_type == ContentType.MOVIE
        assert item.status == ConsumptionStatus.COMPLETED
        assert item.id == "trakt:1"
        assert item.date_completed == date(2021, 5, 1)
        assert item.metadata["trakt_id"] == 1
        assert item.metadata["slug"] == "inception-2020"
        assert item.metadata["year"] == 2020

    def test_fetch_fully_watched_show_completed(self) -> None:
        """Test a fully-watched show is COMPLETED with correct season fields."""
        payloads = _all_lists(
            watched_shows=[
                {
                    "last_watched_at": "2022-01-01T00:00:00.000Z",
                    "show": _show(10, "Severance", aired_episodes=9),
                    "seasons": [
                        {
                            "number": 1,
                            "episodes": [{"number": n} for n in range(1, 10)],
                        }
                    ],
                }
            ]
        )
        items = _run_fetch(payloads, _config())

        assert len(items) == 1
        item = items[0]
        assert item.content_type == ContentType.TV_SHOW
        assert item.status == ConsumptionStatus.COMPLETED
        assert item.date_completed == date(2022, 1, 1)
        assert item.metadata["seasons_watched"] == [1]
        assert item.metadata["total_seasons"] == 1

    def test_fetch_populates_seasons_watched_dates(self) -> None:
        """Test seasons_watched_dates is keyed by season number, max episode date.

        Closes the gap that ``_season_watched_dates`` is only unit-tested in
        isolation: every other fetch fixture uses episodes with no
        ``last_watched_at``, so the map wired into ``_add_watched_shows`` is
        never exercised end-to-end.
        """
        payloads = _all_lists(
            watched_shows=[
                {
                    "last_watched_at": "2022-06-01T00:00:00.000Z",
                    "show": _show(20, "The Expanse", aired_episodes=20),
                    "seasons": [
                        {
                            "number": 1,
                            "episodes": [
                                {
                                    "number": n,
                                    "last_watched_at": f"2022-01-0{n}T00:00:00.000Z",
                                }
                                for n in range(1, 10)
                            ]
                            + [
                                {
                                    "number": 10,
                                    "last_watched_at": "2022-01-10T00:00:00.000Z",
                                }
                            ],
                        },
                        {
                            "number": 3,
                            "episodes": [
                                {
                                    "number": n,
                                    "last_watched_at": f"2022-05-0{n}T00:00:00.000Z",
                                }
                                for n in range(1, 6)
                            ],
                        },
                    ],
                }
            ]
        )
        items = _run_fetch(
            payloads,
            _config(),
            season_totals={20: {1: 10, 2: 13, 3: 5, 4: 10, 5: 10}},
        )

        item = items[0]
        assert item.metadata["seasons_watched"] == [1, 3]
        assert item.metadata["seasons_watched_dates"] == {
            "1": "2022-01-10T00:00:00+00:00",
            "3": "2022-05-05T00:00:00+00:00",
        }

    def test_fetch_ratings_normalized(self) -> None:
        """Test ratings attach to matching items normalized from 1-10 to 1-5."""
        payloads = _all_lists(
            watched_movies=[
                {
                    "last_watched_at": "2021-05-01T10:00:00.000Z",
                    "movie": _movie(1, "Inception"),
                }
            ],
            ratings_movies=[{"rating": 9, "movie": _movie(1, "Inception")}],
        )
        items = _run_fetch(payloads, _config())

        assert len(items) == 1
        assert items[0].rating == 5

    def test_fetch_rated_but_not_watched_movie_imported(self) -> None:
        """Test a movie that is rated but not watched is still imported."""
        payloads = _all_lists(
            ratings_movies=[{"rating": 6, "movie": _movie(2, "Tenet")}],
        )
        items = _run_fetch(payloads, _config())

        assert len(items) == 1
        assert items[0].title == "Tenet"
        assert items[0].rating == 3
        assert items[0].content_type == ContentType.MOVIE

    def test_fetch_watchlist_unread(self) -> None:
        """Test watchlist items become UNREAD when not already watched."""
        payloads = _all_lists(
            watchlist_movies=[{"movie": _movie(3, "Dune")}],
            watchlist_shows=[{"show": _show(30, "Andor", aired_episodes=12)}],
        )
        items = _run_fetch(payloads, _config())

        by_title = {item.title: item for item in items}
        assert by_title["Dune"].status == ConsumptionStatus.UNREAD
        assert by_title["Dune"].content_type == ContentType.MOVIE
        assert by_title["Andor"].status == ConsumptionStatus.UNREAD
        assert by_title["Andor"].content_type == ContentType.TV_SHOW

    def test_fetch_dedup_across_lists(self) -> None:
        """Test an item in watched, ratings, and watchlist merges into one."""
        payloads = _all_lists(
            watched_movies=[
                {
                    "last_watched_at": "2021-05-01T10:00:00.000Z",
                    "movie": _movie(1, "Inception"),
                }
            ],
            ratings_movies=[{"rating": 10, "movie": _movie(1, "Inception")}],
            watchlist_movies=[{"movie": _movie(1, "Inception")}],
        )
        items = _run_fetch(payloads, _config())

        assert len(items) == 1
        item = items[0]
        # Watched status wins over watchlist
        assert item.status == ConsumptionStatus.COMPLETED
        assert item.rating == 5

    def test_fetch_include_watchlist_false_skips_watchlist(self) -> None:
        """Test include_watchlist=False does not fetch watchlist endpoints."""
        seen_endpoints: list[str] = []

        def fake_fetch_list(
            endpoint: str, *args: object, **kwargs: object
        ) -> list[dict[str, Any]]:
            seen_endpoints.append(endpoint)
            return []

        with (
            patch(
                "src.ingestion.sources.trakt.trakt.refresh_access_token",
                return_value={"access_token": "access", "refresh_token": "token"},
            ),
            patch(
                "src.ingestion.sources.trakt.trakt.fetch_list",
                side_effect=fake_fetch_list,
            ),
        ):
            list(TraktPlugin().fetch(_config(include_watchlist=False)))

        assert "/sync/watchlist/movies" not in seen_endpoints
        assert "/sync/watchlist/shows" not in seen_endpoints
        assert "/sync/watched/movies" in seen_endpoints

    def test_fetch_rotated_token_triggers_callback(self) -> None:
        """Test a rotated refresh token is persisted via the callback."""
        callback = Mock()
        with (
            patch(
                "src.ingestion.sources.trakt.trakt.refresh_access_token",
                return_value={
                    "access_token": "access",
                    "refresh_token": "rotated_token",
                },
            ),
            patch(
                "src.ingestion.sources.trakt.trakt.fetch_list",
                return_value=[],
            ),
        ):
            list(TraktPlugin().fetch(_config(_on_credential_rotated=callback)))

        callback.assert_called_once_with("refresh_token", "rotated_token")

    def test_fetch_api_error_raises_source_error(self) -> None:
        """Test Trakt API errors are wrapped in SourceError."""
        with patch(
            "src.ingestion.sources.trakt.trakt.refresh_access_token",
            side_effect=TraktAPIError("Token expired"),
        ):
            with pytest.raises(SourceError) as exc_info:
                list(TraktPlugin().fetch(_config()))

        assert exc_info.value.plugin_name == "trakt"
        assert "Token expired" in exc_info.value.message


def _run_fetch(
    payloads: dict[str, list[dict[str, Any]]],
    config: dict[str, Any],
    season_totals: dict[int, dict[int, int]] | None = None,
    progress_callback: ProgressCallback | None = None,
) -> list[ContentItem]:
    """Run TraktPlugin.fetch with refresh_access_token and fetch_list stubbed.

    Single source of truth for the fetch stubbing used by every fetch test.
    ``season_totals`` maps a show's trakt id to the ``season_number ->
    episode_count`` map the ``/shows/{id}/seasons?extended=full`` call should
    return for in-progress shows; its length is the show's real-season count.
    ``progress_callback`` is forwarded to fetch so progress tests can observe it.
    """

    def fake_fetch_list(
        endpoint: str, *args: object, **kwargs: object
    ) -> list[dict[str, Any]]:
        return payloads[endpoint]

    def fake_season_totals(
        trakt_id: int, *args: object, **kwargs: object
    ) -> dict[int, int]:
        return (season_totals or {}).get(trakt_id, {})

    with (
        patch(
            "src.ingestion.sources.trakt.trakt.refresh_access_token",
            return_value={"access_token": "access", "refresh_token": "token"},
        ),
        patch(
            "src.ingestion.sources.trakt.trakt.fetch_list",
            side_effect=fake_fetch_list,
        ),
        patch(
            "src.ingestion.sources.trakt.trakt.fetch_show_season_totals",
            side_effect=fake_season_totals,
        ),
    ):
        return list(TraktPlugin().fetch(config, progress_callback=progress_callback))


def _all_lists(
    watched_movies: list[dict[str, Any]] | None = None,
    watched_shows: list[dict[str, Any]] | None = None,
    ratings_movies: list[dict[str, Any]] | None = None,
    ratings_shows: list[dict[str, Any]] | None = None,
    watchlist_movies: list[dict[str, Any]] | None = None,
    watchlist_shows: list[dict[str, Any]] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Build the endpoint->payload map consumed by the fetch_list stub."""
    return {
        "/sync/watched/movies": watched_movies or [],
        "/sync/watched/shows": watched_shows or [],
        "/sync/ratings/movies": ratings_movies or [],
        "/sync/ratings/shows": ratings_shows or [],
        "/sync/watchlist/movies": watchlist_movies or [],
        "/sync/watchlist/shows": watchlist_shows or [],
    }


def _config(**overrides: object) -> dict[str, Any]:
    """Build a valid Trakt fetch config."""
    config: dict[str, Any] = {
        "client_id": "cid",
        "client_secret": "secret",
        "refresh_token": "token",
        "include_watchlist": True,
    }
    config.update(overrides)
    return config


class TestTraktSeasonExpansionHandoff:
    """AC4: a Trakt-produced show feeds expand_tv_shows_to_seasons correctly."""

    def test_partial_show_surfaces_unwatched_later_seasons(self) -> None:
        """A partially-watched show surfaces its unwatched LATER seasons.

        Acceptance evidence for the PM's gap: watched S1 + S2 of a 5-season
        show. The sync endpoint only reports the watched seasons, but the extra
        /shows/{id}/seasons call reports the true total (5), so the expansion
        helper the recommendation pipeline uses surfaces S3, S4, and S5 as
        candidate items — the seasons the user has not yet watched.
        """
        payloads = _all_lists(
            watched_shows=[
                {
                    "last_watched_at": "2022-06-01T00:00:00.000Z",
                    "show": _show(20, "The Expanse", aired_episodes=60),
                    "seasons": [
                        {
                            "number": 1,
                            "episodes": [{"number": n} for n in range(1, 11)],
                        },
                        {
                            "number": 2,
                            "episodes": [{"number": n} for n in range(1, 14)],
                        },
                    ],
                }
            ]
        )
        items = _run_fetch(
            payloads,
            _config(),
            season_totals={20: {1: 10, 2: 13, 3: 13, 4: 13, 5: 13}},
        )
        assert len(items) == 1
        show = items[0]
        assert show.status == ConsumptionStatus.CURRENTLY_CONSUMING
        assert show.metadata["seasons_watched"] == [1, 2]
        assert show.metadata["total_seasons"] == 5

        expanded = expand_tv_shows_to_seasons(items)
        season_titles = sorted(item.title for item in expanded)
        assert season_titles == [
            "The Expanse (Season 3)",
            "The Expanse (Season 4)",
            "The Expanse (Season 5)",
        ]
        seasons = sorted(item.metadata["season"] for item in expanded)
        assert seasons == [3, 4, 5]
        assert all(item.parent_id == "trakt:20" for item in expanded)

    def test_partially_watched_first_season_still_surfaces_regression(self) -> None:
        """Regression test: the Solitary bug end to end — a partial S1 is NOT
        hidden by season expansion.

        Bug reported: "Solitary" (4 seasons), user on episode 7 of 8 of season
        1, season 1 wrongly marked watched and hidden from recommendations.

        Root cause: ``_show_season_progress`` marked any season with watched
        episodes as complete, so the in-progress S1 landed in
        ``seasons_watched`` and ``expand_tv_shows_to_seasons`` skipped it.

        Fix: with the per-season-total comparison, S1 (7/8) is not in
        ``seasons_watched``, so the expansion surfaces it (and the later
        seasons) as candidates.

        This is the integration-layer counterpart to the unit-level
        ``test_partially_watched_season_not_marked_watched_regression`` in
        ``TestTraktPartialSeasonRegression``; both reproduce the same 7/8
        scenario at different layers.
        """
        payloads = _all_lists(
            watched_shows=[
                {
                    "last_watched_at": "2022-06-01T00:00:00.000Z",
                    "show": _show(50, "Solitary", aired_episodes=34),
                    "seasons": [
                        {
                            "number": 1,
                            "episodes": [{"number": n} for n in range(1, 8)],
                        }
                    ],
                }
            ]
        )
        items = _run_fetch(
            payloads,
            _config(),
            season_totals={50: {1: 8, 2: 8, 3: 8, 4: 10}},
        )
        show = items[0]
        assert show.status == ConsumptionStatus.CURRENTLY_CONSUMING
        assert show.metadata["seasons_watched"] == []
        assert show.metadata["total_seasons"] == 4

        expanded = expand_tv_shows_to_seasons(items)
        season_titles = sorted(item.title for item in expanded)
        # The partial S1 is surfaced, not skipped, alongside the unwatched rest.
        assert season_titles == [
            "Solitary (Season 1)",
            "Solitary (Season 2)",
            "Solitary (Season 3)",
            "Solitary (Season 4)",
        ]


class TestTraktSpecialsOnly:
    """Edge: a show with only specials (season 0) watched."""

    def test_specials_only_not_completed(self) -> None:
        """Watching only season 0 must not mark a show COMPLETED.

        No real seasons are watched, so it stays in progress and the real-season
        count comes from the /shows/{id}/seasons call (13 here) rather than the
        watched-seasons high-water mark.
        """
        payloads = _all_lists(
            watched_shows=[
                {
                    "last_watched_at": "2022-06-01T00:00:00.000Z",
                    "show": _show(21, "Doctor Who", aired_episodes=12),
                    "seasons": [{"number": 0, "episodes": [{"number": 1}]}],
                }
            ]
        )
        items = _run_fetch(
            payloads,
            _config(),
            season_totals={21: dict.fromkeys(range(1, 14), 8)},
        )
        assert len(items) == 1
        item = items[0]
        assert item.status == ConsumptionStatus.CURRENTLY_CONSUMING
        assert item.metadata["seasons_watched"] == []
        assert item.metadata["total_seasons"] == 13
        assert item.date_completed is None


class TestTraktEmptyLibrary:
    """Edge: empty library yields nothing and does not crash."""

    def test_empty_library_yields_nothing(self) -> None:
        """No watched/rated/watchlisted items -> zero ContentItems, no error."""
        items = _run_fetch(_all_lists(), _config())
        assert items == []


class TestTraktContentTypeDedup:
    """Edge: a movie and a show with the same title and trakt id stay distinct."""

    def test_same_title_movie_and_show_not_merged(self) -> None:
        """Dedup key includes content type: same id across types -> two items."""
        payloads = _all_lists(
            watched_movies=[
                {
                    "last_watched_at": "2021-05-01T10:00:00.000Z",
                    "movie": _movie(42, "Fargo"),
                }
            ],
            watched_shows=[
                {
                    "last_watched_at": "2021-05-01T10:00:00.000Z",
                    "show": _show(42, "Fargo", aired_episodes=10),
                    "seasons": [
                        {
                            "number": 1,
                            "episodes": [{"number": n} for n in range(1, 11)],
                        }
                    ],
                }
            ],
        )
        items = _run_fetch(payloads, _config())
        assert len(items) == 2
        by_type = {item.content_type for item in items}
        assert by_type == {ContentType.MOVIE, ContentType.TV_SHOW}


class TestTraktRatedWatchlistedItem:
    """AC7 flagged case: a rated-but-unwatched item also on the watchlist."""

    def test_rated_and_watchlisted_movie_stays_unread_with_rating(self) -> None:
        """A movie that is rated (not watched) and watchlisted stays UNREAD + rated.

        The plugin applies ratings before the watchlist; the watchlist pass sees
        the key already present and leaves it alone. The documented, intended
        result is a single UNREAD item carrying the normalized rating.
        """
        payloads = _all_lists(
            ratings_movies=[{"rating": 8, "movie": _movie(7, "Tenet")}],
            watchlist_movies=[{"movie": _movie(7, "Tenet")}],
        )
        items = _run_fetch(payloads, _config())
        assert len(items) == 1
        item = items[0]
        assert item.title == "Tenet"
        assert item.status == ConsumptionStatus.UNREAD
        assert item.rating == 4


class TestTraktPartialSeasonRegression:
    """Regression: a partially-watched season must not be marked watched.

    Reported symptom: a season the user has not finished (e.g. "Solitary" S1,
    7 of 8 episodes watched) was hidden from recommendations as already watched.

    Root cause: ``_show_season_progress`` used a bare truthy check on the
    season's ``episodes`` array. The /sync/watched/shows endpoint lists ONLY
    watched episodes, so any partially-watched season has a non-empty array and
    was wrongly marked fully watched.

    Fix: fetch each season's true episode total via
    /shows/{id}/seasons?extended=full and mark a season watched only when the
    watched-episode count reaches that total (and the total is known and > 0).
    """

    def test_partially_watched_season_not_marked_watched_regression(self) -> None:
        """Regression test: a partially-watched season is not marked watched.

        Bug reported: "Solitary" S1, 7 of 8 episodes watched, was hidden from
        recommendations as already watched.

        Root cause: ``_show_season_progress`` treated any season with a
        non-empty ``episodes`` array as fully watched, but the sync endpoint
        lists only watched episodes, so a partial season also has a non-empty
        array.

        Fix: mark a season watched only when its watched-episode count reaches
        the true episode total from ``fetch_show_season_totals``.

        The same 7/8 scenario is exercised end to end through
        ``expand_tv_shows_to_seasons`` by
        ``test_partially_watched_first_season_still_surfaces_regression``.
        """
        payloads = _all_lists(
            watched_shows=[
                {
                    "last_watched_at": "2022-06-01T00:00:00.000Z",
                    "show": _show(50, "Solitary", aired_episodes=18),
                    "seasons": [
                        {
                            "number": 1,
                            "episodes": [{"number": n} for n in range(1, 8)],
                        }
                    ],
                }
            ]
        )
        items = _run_fetch(payloads, _config(), season_totals={50: {1: 8, 2: 10}})

        item = items[0]
        assert item.status == ConsumptionStatus.CURRENTLY_CONSUMING
        assert item.metadata["seasons_watched"] == []
        assert item.metadata["total_seasons"] == 2

    def test_fully_watched_season_still_marked_watched_regression(self) -> None:
        """Regression test: the fix must not over-correct a complete season.

        Bug reported: same partial-season fix as above.

        Root cause / fix: comparing watched-episode count against the true
        season total must still count a season as watched when the user has
        actually seen every episode.

        Guard: a season watched to its full episode total stays in
        ``seasons_watched`` so a finished season is not wrongly re-surfaced.
        """
        payloads = _all_lists(
            watched_shows=[
                {
                    "last_watched_at": "2022-06-01T00:00:00.000Z",
                    "show": _show(51, "Halt and Catch Fire", aired_episodes=100),
                    "seasons": [
                        {
                            "number": 1,
                            "episodes": [{"number": n} for n in range(1, 11)],
                        }
                    ],
                }
            ]
        )
        items = _run_fetch(payloads, _config(), season_totals={51: {1: 10, 2: 5}})

        item = items[0]
        assert item.status == ConsumptionStatus.CURRENTLY_CONSUMING
        assert item.metadata["seasons_watched"] == [1]

    def test_mixed_seasons_only_complete_one_listed_regression(self) -> None:
        """Regression test: with one complete and one partial season, only the
        complete season is listed as watched.

        Bug reported: same partial-season fix as above.

        Root cause: the old truthy ``episodes`` check marked both seasons
        watched.

        Fix: per-season total comparison lists only the season watched to its
        full episode total, leaving the partial one in progress.
        """
        payloads = _all_lists(
            watched_shows=[
                {
                    "last_watched_at": "2022-06-01T00:00:00.000Z",
                    "show": _show(52, "Westworld", aired_episodes=100),
                    "seasons": [
                        {
                            "number": 1,
                            "episodes": [{"number": n} for n in range(1, 11)],
                        },
                        {
                            "number": 2,
                            "episodes": [{"number": n} for n in range(1, 4)],
                        },
                    ],
                }
            ]
        )
        items = _run_fetch(payloads, _config(), season_totals={52: {1: 10, 2: 8}})

        assert items[0].metadata["seasons_watched"] == [1]

    def test_unknown_season_total_not_marked_watched_regression(self) -> None:
        """Regression test: a season absent from the totals map stays in-progress.

        Bug reported: partial seasons hidden as watched (see the class
        docstring); this case guards the missing-total branch specifically.

        Root cause: when the ``/shows/{id}/seasons`` response omits a season
        the user has watched episodes for, its total is unknown, and any
        default other than "in progress" risks re-hiding an unfinished season.

        Fix: ``_show_season_progress`` uses ``season_totals.get(number, 0)`` and
        the ``season_total > 0`` guard, so an unknown total leaves the season in
        progress rather than marking it watched.
        """
        payloads = _all_lists(
            watched_shows=[
                {
                    "last_watched_at": "2022-06-01T00:00:00.000Z",
                    "show": _show(53, "Carnivale", aired_episodes=100),
                    "seasons": [
                        {
                            "number": 1,
                            "episodes": [{"number": n} for n in range(1, 6)],
                        }
                    ],
                }
            ]
        )
        items = _run_fetch(payloads, _config(), season_totals={53: {2: 8, 3: 8}})

        assert items[0].metadata["seasons_watched"] == []

    def test_rewatched_episodes_do_not_inflate_partial_season_regression(self) -> None:
        """Regression test: re-watches must not mark a partial season watched.

        Bug reported: partial seasons hidden as watched (see the class
        docstring); this case guards against a plays-based recount.

        Root cause: the /sync/watched/shows endpoint reports one entry per
        *distinct* watched episode carrying a ``plays`` counter, never one entry
        per play; summing plays would inflate a partial season past its total.

        Fix: ``_show_season_progress`` counts distinct watched episodes, so 7
        distinct episodes of an 8-episode season — each watched multiple times —
        is still 7 watched and the season stays in progress.
        """
        payloads = _all_lists(
            watched_shows=[
                {
                    "last_watched_at": "2022-06-01T00:00:00.000Z",
                    "show": _show(54, "Twin Peaks", aired_episodes=18),
                    "seasons": [
                        {
                            "number": 1,
                            "episodes": [
                                {"number": n, "plays": 3} for n in range(1, 8)
                            ],
                        }
                    ],
                }
            ]
        )
        items = _run_fetch(payloads, _config(), season_totals={54: {1: 8, 2: 10}})

        item = items[0]
        assert item.status == ConsumptionStatus.CURRENTLY_CONSUMING
        assert item.metadata["seasons_watched"] == []

    def test_episode_count_zero_leaves_season_in_progress_regression(self) -> None:
        """Regression test: a zero episode_count leaves the season in-progress.

        Bug reported: partial seasons hidden as watched (see the class
        docstring); this case guards the zero-total branch specifically.

        Root cause: ``fetch_show_season_totals`` maps an absent or 0
        ``episode_count`` to 0, and treating 0 as "complete" would hide a season
        of unknown size.

        Fix: the ``season_total > 0`` guard refuses to mark a season watched
        when its total is 0, so it is re-surfaced rather than wrongly hidden.
        """
        payloads = _all_lists(
            watched_shows=[
                {
                    "last_watched_at": "2022-06-01T00:00:00.000Z",
                    "show": _show(55, "Deadwood", aired_episodes=36),
                    "seasons": [
                        {
                            "number": 1,
                            "episodes": [{"number": n} for n in range(1, 13)],
                        }
                    ],
                }
            ]
        )
        items = _run_fetch(payloads, _config(), season_totals={55: {1: 0, 2: 12}})

        item = items[0]
        assert item.status == ConsumptionStatus.CURRENTLY_CONSUMING
        assert item.metadata["seasons_watched"] == []
        assert item.metadata["total_seasons"] == 2


class TestFetchShowSeasonTotals:
    """Tests for the /shows/{id}/seasons per-season episode-total helper."""

    @patch("src.ingestion.sources.trakt.trakt.requests.get")
    def test_maps_episode_counts_excluding_specials(self, mock_get: Mock) -> None:
        """Season 0 (specials) is excluded; real seasons map to episode_count."""
        mock_response = Mock(spec=requests.Response)
        mock_response.json.return_value = [
            {"number": 0, "episode_count": 5},
            {"number": 1, "episode_count": 10},
            {"number": 2, "episode_count": 13},
            {"number": 3, "episode_count": 8},
        ]
        mock_get.return_value = mock_response

        assert fetch_show_season_totals(20, "access", "cid") == {1: 10, 2: 13, 3: 8}
