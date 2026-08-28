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
    return {
        "title": title,
        "year": year,
        "ids": {"trakt": trakt_id, "slug": f"{title.lower()}-{year}"},
    }


def _show(
    trakt_id: int, title: str, aired_episodes: int, year: int = 2015
) -> dict[str, Any]:
    return {
        "title": title,
        "year": year,
        "ids": {"trakt": trakt_id, "slug": f"{title.lower()}-{year}"},
        "aired_episodes": aired_episodes,
        "genres": ["drama"],
    }


class TestRefreshAccessToken:
    @patch("src.ingestion.sources.trakt.trakt.requests.post")
    def test_refresh_preserves_old_token_when_omitted(self, mock_post: Mock) -> None:
        mock_response = Mock(spec=requests.Response)
        mock_response.json.return_value = {"access_token": "new_access"}
        mock_post.return_value = mock_response

        result = refresh_access_token("original_refresh", "cid", "secret")

        assert result["access_token"] == "new_access"
        assert result["refresh_token"] == "original_refresh"


class TestFetchList:
    @patch("src.ingestion.sources.trakt.trakt.requests.get")
    def test_pagination(self, mock_get: Mock) -> None:
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
    def test_late_evening_local_watch_keeps_local_date_regression(
        self, host_timezone: Callable[[str], None]
    ) -> None:
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
        assert TraktPlugin().normalize_rating(raw) == expected


class TestTraktPluginValidation:
    def test_validate_missing_client_id(self) -> None:
        errors = TraktPlugin().validate_config(
            {"client_secret": "secret", "refresh_token": "token"}
        )
        assert any("https://trakt.tv/oauth/applications" in error for error in errors)

    def test_validate_missing_secret_passes_when_in_db(self) -> None:
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
    def test_fetch_watched_movie(self) -> None:
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
        payloads = _all_lists(
            ratings_movies=[{"rating": 6, "movie": _movie(2, "Tenet")}],
        )
        items = _run_fetch(payloads, _config())

        assert len(items) == 1
        assert items[0].title == "Tenet"
        assert items[0].rating == 3
        assert items[0].content_type == ContentType.MOVIE

    def test_fetch_watchlist_unread(self) -> None:
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
        assert item.status == ConsumptionStatus.COMPLETED
        assert item.rating == 5

    def test_fetch_include_watchlist_false_skips_watchlist(self) -> None:
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
    return {
        "/sync/watched/movies": watched_movies or [],
        "/sync/watched/shows": watched_shows or [],
        "/sync/ratings/movies": ratings_movies or [],
        "/sync/ratings/shows": ratings_shows or [],
        "/sync/watchlist/movies": watchlist_movies or [],
        "/sync/watchlist/shows": watchlist_shows or [],
    }


def _config(**overrides: object) -> dict[str, Any]:
    config: dict[str, Any] = {
        "client_id": "cid",
        "client_secret": "secret",
        "refresh_token": "token",
        "include_watchlist": True,
    }
    config.update(overrides)
    return config


class TestTraktSeasonExpansionHandoff:
    def test_partial_show_surfaces_unwatched_later_seasons(self) -> None:
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
        assert season_titles == [
            "Solitary (Season 1)",
            "Solitary (Season 2)",
            "Solitary (Season 3)",
            "Solitary (Season 4)",
        ]


class TestTraktSpecialsOnly:
    def test_specials_only_not_completed(self) -> None:
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
    def test_empty_library_yields_nothing(self) -> None:
        items = _run_fetch(_all_lists(), _config())
        assert items == []


class TestTraktContentTypeDedup:
    def test_same_title_movie_and_show_not_merged(self) -> None:
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
    def test_rated_and_watchlisted_movie_stays_unread_with_rating(self) -> None:
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
    def test_partially_watched_season_not_marked_watched_regression(self) -> None:
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
    @patch("src.ingestion.sources.trakt.trakt.requests.get")
    def test_maps_episode_counts_excluding_specials(self, mock_get: Mock) -> None:
        mock_response = Mock(spec=requests.Response)
        mock_response.json.return_value = [
            {"number": 0, "episode_count": 5},
            {"number": 1, "episode_count": 10},
            {"number": 2, "episode_count": 13},
            {"number": 3, "episode_count": 8},
        ]
        mock_get.return_value = mock_response

        assert fetch_show_season_totals(20, "access", "cid") == {1: 10, 2: 13, 3: 8}
