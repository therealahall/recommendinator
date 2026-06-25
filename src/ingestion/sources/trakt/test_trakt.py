"""Tests for Trakt API integration."""

from datetime import date
from typing import Any
from unittest.mock import Mock, patch

import pytest
import requests

from src.ingestion.plugin_base import ProgressCallback, SourceError, SourcePlugin
from src.ingestion.registry import PluginRegistry
from src.ingestion.sources.trakt.trakt import (
    TraktAPIError,
    TraktPlugin,
    _parse_completed_date,
    fetch_list,
    fetch_show_season_count,
    refresh_access_token,
)
from src.models.content import ConsumptionStatus, ContentItem, ContentType
from src.storage.manager import StorageManager
from src.utils.series import expand_tv_shows_to_seasons


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
    def test_refresh_success(self, mock_post: Mock) -> None:
        """Test successful token refresh returns access and refresh tokens."""
        mock_response = Mock(spec=requests.Response)
        mock_response.json.return_value = {
            "access_token": "new_access",
            "refresh_token": "new_refresh",
        }
        mock_post.return_value = mock_response

        result = refresh_access_token("old_refresh", "my_client_id", "my_secret")

        assert result["access_token"] == "new_access"
        assert result["refresh_token"] == "new_refresh"
        mock_post.assert_called_once()
        call_args = mock_post.call_args
        assert "oauth/token" in call_args[0][0]
        assert call_args[1]["json"]["grant_type"] == "refresh_token"
        assert call_args[1]["json"]["refresh_token"] == "old_refresh"
        assert call_args[1]["json"]["client_id"] == "my_client_id"
        assert call_args[1]["json"]["client_secret"] == "my_secret"

    @patch("src.ingestion.sources.trakt.trakt.requests.post")
    def test_refresh_preserves_old_token_when_omitted(self, mock_post: Mock) -> None:
        """Test old refresh token is preserved when response omits it."""
        mock_response = Mock(spec=requests.Response)
        mock_response.json.return_value = {"access_token": "new_access"}
        mock_post.return_value = mock_response

        result = refresh_access_token("original_refresh", "cid", "secret")

        assert result["access_token"] == "new_access"
        assert result["refresh_token"] == "original_refresh"

    @patch("src.ingestion.sources.trakt.trakt.requests.post")
    def test_refresh_missing_access_token(self, mock_post: Mock) -> None:
        """Test refresh raises when response has no access_token."""
        mock_response = Mock(spec=requests.Response)
        mock_response.json.return_value = {"refresh_token": "x"}
        mock_post.return_value = mock_response

        with pytest.raises(TraktAPIError, match="missing access_token"):
            refresh_access_token("token", "cid", "secret")

    @patch("src.ingestion.sources.trakt.trakt.requests.post")
    def test_refresh_network_error(self, mock_post: Mock) -> None:
        """Test refresh wraps network errors in TraktAPIError."""
        mock_post.side_effect = requests.RequestException("401")

        with pytest.raises(TraktAPIError, match="Failed to refresh access token"):
            refresh_access_token("token", "cid", "secret")


class TestFetchList:
    """Tests for the paginated list fetcher."""

    @patch("src.ingestion.sources.trakt.trakt.requests.get")
    def test_single_page(self, mock_get: Mock) -> None:
        """Test fetching a single unpaginated page."""
        mock_response = Mock(spec=requests.Response)
        mock_response.headers = {}
        mock_response.json.return_value = [{"a": 1}, {"a": 2}]
        mock_get.return_value = mock_response

        result = fetch_list("/sync/watched/movies", "access", "cid")

        assert result == [{"a": 1}, {"a": 2}]
        call_args = mock_get.call_args
        assert call_args[1]["headers"]["trakt-api-version"] == "2"
        assert call_args[1]["headers"]["trakt-api-key"] == "cid"
        assert call_args[1]["headers"]["Authorization"] == "Bearer access"

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

    @patch("src.ingestion.sources.trakt.trakt.requests.get")
    def test_api_error(self, mock_get: Mock) -> None:
        """Test API errors are wrapped in TraktAPIError."""
        mock_get.side_effect = requests.RequestException("500")

        with pytest.raises(TraktAPIError, match="Failed to fetch"):
            fetch_list("/sync/watched/shows", "access", "cid")

    @patch("src.ingestion.sources.trakt.trakt.requests.get")
    def test_malformed_pagination_header_stops_cleanly(self, mock_get: Mock) -> None:
        """A non-numeric page-count header stops after one page without crashing."""
        mock_response = Mock(spec=requests.Response)
        mock_response.headers = {"X-Pagination-Page-Count": "abc"}
        mock_response.json.return_value = [{"a": 1}]
        mock_get.return_value = mock_response

        result = fetch_list("/sync/watched/movies", "access", "cid")

        assert result == [{"a": 1}]
        assert mock_get.call_count == 1

    @patch("src.ingestion.sources.trakt.trakt.requests.get")
    def test_extended_param_forwarded(self, mock_get: Mock) -> None:
        """The extended argument is sent as the ``extended`` query parameter."""
        mock_response = Mock(spec=requests.Response)
        mock_response.headers = {}
        mock_response.json.return_value = []
        mock_get.return_value = mock_response

        fetch_list("/sync/watched/shows", "access", "cid", extended="full")

        call_args = mock_get.call_args
        assert call_args[1]["params"]["extended"] == "full"


class TestParseCompletedDate:
    """Tests for the Trakt ISO 8601 timestamp parser."""

    def test_iso_with_z_suffix(self) -> None:
        """A UTC timestamp ending in Z parses to the right date."""
        assert _parse_completed_date("2021-05-01T10:00:00.000Z") == date(2021, 5, 1)

    def test_iso_with_numeric_offset(self) -> None:
        """A timestamp with a numeric UTC offset parses to the right date."""
        assert _parse_completed_date("2021-05-01T10:00:00+02:00") == date(2021, 5, 1)

    def test_none_returns_none(self) -> None:
        """None input yields None."""
        assert _parse_completed_date(None) is None

    def test_empty_string_returns_none(self) -> None:
        """An empty string yields None."""
        assert _parse_completed_date("") is None

    def test_malformed_string_returns_none(self) -> None:
        """A non-ISO string yields None instead of raising."""
        assert _parse_completed_date("not-a-date") is None


class TestTraktPluginProperties:
    """Tests for TraktPlugin metadata properties."""

    def test_is_source_plugin(self) -> None:
        """Test TraktPlugin is a SourcePlugin subclass."""
        assert isinstance(TraktPlugin(), SourcePlugin)

    def test_name(self) -> None:
        """Test plugin name identifier."""
        assert TraktPlugin().name == "trakt"

    def test_display_name(self) -> None:
        """Test human-readable display name."""
        assert TraktPlugin().display_name == "Trakt"

    def test_content_types(self) -> None:
        """Test plugin provides TV shows and movies."""
        assert TraktPlugin().content_types == [
            ContentType.TV_SHOW,
            ContentType.MOVIE,
        ]

    def test_requires_api_key(self) -> None:
        """Test plugin requires credentials."""
        assert TraktPlugin().requires_api_key is True

    def test_requires_network(self) -> None:
        """Test plugin requires network."""
        assert TraktPlugin().requires_network is True

    def test_config_schema(self) -> None:
        """Test config schema exposes the expected fields and flags."""
        schema = TraktPlugin().get_config_schema()
        by_name = {f.name: f for f in schema}

        assert set(by_name) == {
            "client_id",
            "client_secret",
            "refresh_token",
            "include_watchlist",
        }
        assert by_name["client_id"].required is True
        assert by_name["client_id"].sensitive is False
        assert by_name["client_secret"].required is True
        assert by_name["client_secret"].sensitive is True
        assert by_name["refresh_token"].required is True
        assert by_name["refresh_token"].sensitive is True
        assert by_name["include_watchlist"].required is False
        assert by_name["include_watchlist"].default is True

    def test_get_info(self) -> None:
        """Test plugin info includes all metadata."""
        info = TraktPlugin().get_info()
        assert info.name == "trakt"
        assert info.display_name == "Trakt"
        assert info.content_types == [ContentType.TV_SHOW, ContentType.MOVIE]
        assert info.requires_api_key is True


class TestTraktNormalizeRating:
    """Tests for the 10-point to 5-point rating normalization."""

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            (None, None),
            (0, None),
            (1, 1),
            (2, 1),
            (3, 2),
            (4, 2),
            (5, 3),
            (6, 3),
            (7, 4),
            (8, 4),
            (9, 5),
            (10, 5),
        ],
    )
    def test_normalize(self, raw: int | None, expected: int | None) -> None:
        """Test 1-10 ratings map to 1-5, never 0."""
        assert TraktPlugin().normalize_rating(raw) == expected


class TestTraktPluginValidation:
    """Tests for TraktPlugin config validation."""

    def _valid_config(self) -> dict[str, Any]:
        return {
            "client_id": "cid",
            "client_secret": "secret",
            "refresh_token": "token",
        }

    def test_validate_valid_config(self) -> None:
        """Test validation passes with all required fields present."""
        assert TraktPlugin().validate_config(self._valid_config()) == []

    def test_validate_missing_client_id(self) -> None:
        """Test validation fails with actionable setup guidance when client_id missing."""
        config = self._valid_config()
        del config["client_id"]
        errors = TraktPlugin().validate_config(config)
        assert any("https://trakt.tv/oauth/applications" in error for error in errors)

    def test_validate_missing_client_secret(self) -> None:
        """Test validation fails when client_secret missing."""
        config = self._valid_config()
        del config["client_secret"]
        errors = TraktPlugin().validate_config(config)
        assert any("client_secret" in error for error in errors)

    def test_validate_missing_refresh_token(self) -> None:
        """Test validation fails when refresh_token missing."""
        config = self._valid_config()
        del config["refresh_token"]
        errors = TraktPlugin().validate_config(config)
        assert any("refresh_token" in error for error in errors)

    def test_validate_missing_secret_passes_when_in_db(self) -> None:
        """Test missing sensitive fields are satisfied from the credential DB."""
        plugin = TraktPlugin()
        mock_storage = Mock(spec=StorageManager)
        mock_storage.get_credentials_for_source.return_value = {
            "client_secret": "db_secret",
            "refresh_token": "db_token",
        }

        errors = plugin.validate_config(
            {"_source_id": "my_trakt", "client_id": "cid"},
            storage=mock_storage,
            user_id=1,
        )
        assert errors == []
        mock_storage.get_credentials_for_source.assert_called_with(1, "my_trakt")

    def test_validate_missing_secret_fails_when_not_in_db(self) -> None:
        """Test validation still fails when sensitive fields absent from DB too."""
        plugin = TraktPlugin()
        mock_storage = Mock(spec=StorageManager)
        mock_storage.get_credentials_for_source.return_value = {}

        errors = plugin.validate_config(
            {"_source_id": "my_trakt", "client_id": "cid"},
            storage=mock_storage,
            user_id=1,
        )
        assert any("client_secret" in error for error in errors)
        assert any("refresh_token" in error for error in errors)


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
        assert item.source == "trakt"
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

    def test_fetch_partially_watched_show_currently_consuming(self) -> None:
        """Test a partially-watched show is CURRENTLY_CONSUMING with right seasons.

        The sync endpoint only returns watched seasons (S1, S3); total_seasons
        comes from the extra /shows/{id}/seasons call, which reports the true
        real-season count (5) so the unwatched later seasons can be recommended.
        """
        payloads = _all_lists(
            watched_shows=[
                {
                    "last_watched_at": "2022-06-01T00:00:00.000Z",
                    "show": _show(20, "The Expanse", aired_episodes=20),
                    "seasons": [
                        {
                            "number": 1,
                            "episodes": [{"number": n} for n in range(1, 11)],
                        },
                        {
                            "number": 3,
                            "episodes": [{"number": n} for n in range(1, 6)],
                        },
                    ],
                }
            ]
        )
        items = _run_fetch(payloads, _config(), season_counts={20: 5})

        item = items[0]
        assert item.status == ConsumptionStatus.CURRENTLY_CONSUMING
        assert item.date_completed is None
        assert item.metadata["seasons_watched"] == [1, 3]
        assert item.metadata["total_seasons"] == 5

    def test_fetch_show_excludes_specials_season_zero(self) -> None:
        """Test season 0 (specials) is excluded from seasons_watched."""
        payloads = _all_lists(
            watched_shows=[
                {
                    "last_watched_at": "2022-06-01T00:00:00.000Z",
                    "show": _show(21, "Doctor Who", aired_episodes=12),
                    "seasons": [
                        {"number": 0, "episodes": [{"number": 1}]},
                        {
                            "number": 1,
                            "episodes": [{"number": n} for n in range(1, 13)],
                        },
                    ],
                }
            ]
        )
        items = _run_fetch(payloads, _config())

        item = items[0]
        assert item.metadata["seasons_watched"] == [1]
        assert item.metadata["total_seasons"] == 1

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

    def test_fetch_same_token_no_callback(self) -> None:
        """Test no callback when the refresh token is unchanged."""
        callback = Mock()
        with (
            patch(
                "src.ingestion.sources.trakt.trakt.refresh_access_token",
                return_value={"access_token": "access", "refresh_token": "token"},
            ),
            patch(
                "src.ingestion.sources.trakt.trakt.fetch_list",
                return_value=[],
            ),
        ):
            list(TraktPlugin().fetch(_config(_on_credential_rotated=callback)))

        callback.assert_not_called()

    def test_fetch_progress_callback(self) -> None:
        """Test the progress callback reports monotonic counts and a final total.

        Each call passes (count, total, message). The phase calls (total=None)
        report a non-decreasing accumulated item count; the per-item yield calls
        (total set) report a 1..N index that is monotonically non-decreasing and
        whose final call carries the count, the matching total, and the title.
        At least one intermediate phase message must be passed.
        """
        payloads = _all_lists(
            watched_movies=[
                {
                    "last_watched_at": "2021-05-01T10:00:00.000Z",
                    "movie": _movie(1, "Inception"),
                },
                {
                    "last_watched_at": "2021-06-01T10:00:00.000Z",
                    "movie": _movie(2, "Tenet"),
                },
            ]
        )
        calls: list[tuple[int, int | None, str]] = []

        def callback(count: int, total: int | None, message: str) -> None:
            calls.append((count, total, message))

        _run_fetch(payloads, _config(), progress_callback=callback)

        phase_counts = [count for count, total, _ in calls if total is None]
        assert phase_counts == sorted(phase_counts)

        yield_calls = [(count, total, msg) for count, total, msg in calls if total]
        yield_counts = [count for count, _, _ in yield_calls]
        assert yield_counts == sorted(yield_counts)

        final_count, final_total, final_message = yield_calls[-1]
        assert final_total == len(yield_calls)
        assert final_count == final_total
        assert final_message == "Tenet"

        messages = [message for _, _, message in calls]
        assert "Fetching watched shows..." in messages

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

    def test_fetch_uses_source_identifier(self) -> None:
        """Test item.source uses the configured _source_id."""
        payloads = _all_lists(
            watched_movies=[
                {
                    "last_watched_at": "2021-05-01T10:00:00.000Z",
                    "movie": _movie(1, "Inception"),
                }
            ]
        )
        items = _run_fetch(payloads, _config(_source_id="my_trakt"))

        assert items[0].source == "my_trakt"


def _run_fetch(
    payloads: dict[str, list[dict[str, Any]]],
    config: dict[str, Any],
    season_counts: dict[int, int] | None = None,
    progress_callback: ProgressCallback | None = None,
) -> list[ContentItem]:
    """Run TraktPlugin.fetch with refresh_access_token and fetch_list stubbed.

    Single source of truth for the fetch stubbing used by every fetch test.
    ``season_counts`` maps a show's trakt id to the real-season count the
    ``/shows/{id}/seasons`` call should return for in-progress shows.
    ``progress_callback`` is forwarded to fetch so progress tests can observe it.
    """

    def fake_fetch_list(
        endpoint: str, *args: object, **kwargs: object
    ) -> list[dict[str, Any]]:
        return payloads[endpoint]

    def fake_season_count(trakt_id: int, *args: object, **kwargs: object) -> int:
        return (season_counts or {}).get(trakt_id, 0)

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
            "src.ingestion.sources.trakt.trakt.fetch_show_season_count",
            side_effect=fake_season_count,
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


class TestTraktAutoDiscovery:
    """AC1: the Trakt plugin is auto-discovered by the real registry."""

    def test_plugin_discovered_in_registry(self) -> None:
        """Trakt appears in the live registry from directory discovery alone."""
        registry = PluginRegistry()
        registry.discover_plugins()

        assert "trakt" in registry.list_plugin_names()
        plugin = registry.get_plugin("trakt")
        assert plugin is not None
        assert plugin.display_name == "Trakt"

    def test_discovered_plugin_exposes_schema_fields(self) -> None:
        """The discovered plugin carries the documented schema + sensitivity flags."""
        registry = PluginRegistry()
        registry.discover_plugins()
        plugin = registry.get_plugin("trakt")
        assert plugin is not None

        by_name = {f.name: f for f in plugin.get_config_schema()}
        assert set(by_name) == {
            "client_id",
            "client_secret",
            "refresh_token",
            "include_watchlist",
        }
        # client_id is not secret; client_secret and refresh_token are.
        assert by_name["client_id"].sensitive is False
        assert by_name["client_secret"].sensitive is True
        assert by_name["refresh_token"].sensitive is True
        assert by_name["include_watchlist"].required is False
        assert by_name["include_watchlist"].default is True

    def test_discovered_via_content_type_filter(self) -> None:
        """Trakt is returned when filtering the registry by movie/TV content type."""
        registry = PluginRegistry()
        registry.discover_plugins()

        movie_names = {
            p.name for p in registry.get_plugins_by_content_type(ContentType.MOVIE)
        }
        tv_names = {
            p.name for p in registry.get_plugins_by_content_type(ContentType.TV_SHOW)
        }

        assert "trakt" in movie_names
        assert "trakt" in tv_names


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
        items = _run_fetch(payloads, _config(), season_counts={20: 5})
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

    def test_season_gap_surfaces_correct_unwatched_set(self) -> None:
        """A gap in watched seasons still yields the right unwatched set.

        Watched S1 + S3 of a 5-season show. The expansion must surface S2, S4,
        and S5 — the skipped middle season as well as the unwatched later ones.
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
                        {"number": 3, "episodes": [{"number": n} for n in range(1, 6)]},
                    ],
                }
            ]
        )
        items = _run_fetch(payloads, _config(), season_counts={20: 5})
        show = items[0]
        assert show.metadata["seasons_watched"] == [1, 3]
        assert show.metadata["total_seasons"] == 5

        expanded = expand_tv_shows_to_seasons(items)
        season_titles = sorted(item.title for item in expanded)
        assert season_titles == [
            "The Expanse (Season 2)",
            "The Expanse (Season 4)",
            "The Expanse (Season 5)",
        ]

    def test_fully_watched_single_season_expands_to_nothing(self) -> None:
        """A fully-watched 1-season show leaves no unwatched seasons to recommend."""
        payloads = _all_lists(
            watched_shows=[
                {
                    "last_watched_at": "2022-01-01T00:00:00.000Z",
                    "show": _show(10, "Severance", aired_episodes=9),
                    "seasons": [
                        {"number": 1, "episodes": [{"number": n} for n in range(1, 10)]}
                    ],
                }
            ]
        )
        items = _run_fetch(payloads, _config())
        expanded = expand_tv_shows_to_seasons(items)
        assert expanded == []


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
        items = _run_fetch(payloads, _config(), season_counts={21: 13})
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

    def test_empty_library_watchlist_disabled(self) -> None:
        """Empty library with watchlist disabled still yields nothing cleanly."""
        items = _run_fetch(_all_lists(), _config(include_watchlist=False))
        assert items == []


class TestTraktUnratedItems:
    """Edge: unrated entries normalize to rating None, never 0."""

    def test_unrated_watched_movie_has_none_rating(self) -> None:
        """A watched movie with no rating entry keeps rating=None."""
        payloads = _all_lists(
            watched_movies=[
                {
                    "last_watched_at": "2021-05-01T10:00:00.000Z",
                    "movie": _movie(1, "Inception"),
                }
            ]
        )
        items = _run_fetch(payloads, _config())
        assert items[0].rating is None

    def test_rating_entry_of_zero_yields_none(self) -> None:
        """A ratings entry with rating 0 (Trakt 'unrated') becomes None, not 0."""
        payloads = _all_lists(
            watched_movies=[
                {
                    "last_watched_at": "2021-05-01T10:00:00.000Z",
                    "movie": _movie(1, "Inception"),
                }
            ],
            ratings_movies=[{"rating": 0, "movie": _movie(1, "Inception")}],
        )
        items = _run_fetch(payloads, _config())
        assert items[0].rating is None


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

    def test_rated_show_ratings_normalized_endtoend(self) -> None:
        """Show ratings attach to the watched show, normalized 1-10 -> 1-5."""
        payloads = _all_lists(
            watched_shows=[
                {
                    "last_watched_at": "2022-01-01T00:00:00.000Z",
                    "show": _show(11, "Severance", aired_episodes=9),
                    "seasons": [
                        {
                            "number": 1,
                            "episodes": [{"number": n} for n in range(1, 10)],
                        }
                    ],
                }
            ],
            ratings_shows=[
                {"rating": 7, "show": _show(11, "Severance", aired_episodes=9)}
            ],
        )
        items = _run_fetch(payloads, _config())
        assert len(items) == 1
        assert items[0].rating == 4
        assert items[0].status == ConsumptionStatus.COMPLETED


class TestFetchShowSeasonCount:
    """Tests for the /shows/{id}/seasons real-season-count helper."""

    @patch("src.ingestion.sources.trakt.trakt.requests.get")
    def test_counts_real_seasons_excluding_specials(self, mock_get: Mock) -> None:
        """Season 0 (specials) is excluded from the real-season count."""
        mock_response = Mock(spec=requests.Response)
        mock_response.json.return_value = [
            {"number": 0},
            {"number": 1},
            {"number": 2},
            {"number": 3},
        ]
        mock_get.return_value = mock_response

        assert fetch_show_season_count(20, "access", "cid") == 3

        call_args = mock_get.call_args
        assert "/shows/20/seasons" in call_args[0][0]
        assert call_args[1]["headers"]["trakt-api-version"] == "2"
        assert call_args[1]["headers"]["trakt-api-key"] == "cid"
        assert call_args[1]["headers"]["Authorization"] == "Bearer access"

    @patch("src.ingestion.sources.trakt.trakt.requests.get")
    def test_ignores_malformed_season_numbers(self, mock_get: Mock) -> None:
        """Seasons with a missing or non-integer number are not counted."""
        mock_response = Mock(spec=requests.Response)
        mock_response.json.return_value = [
            {"number": 1},
            {"number": None},
            {"foo": "bar"},
            {"number": 2},
        ]
        mock_get.return_value = mock_response

        assert fetch_show_season_count(20, "access", "cid") == 2

    @patch("src.ingestion.sources.trakt.trakt.requests.get")
    def test_api_error_raises(self, mock_get: Mock) -> None:
        """A network error from the seasons call is wrapped in TraktAPIError."""
        mock_get.side_effect = requests.RequestException("500")

        with pytest.raises(TraktAPIError, match="Failed to fetch /shows/20/seasons"):
            fetch_show_season_count(20, "access", "cid")


class TestSeasonCountCallScope:
    """The extra season-count call fires only for in-progress shows."""

    def _run_fetch_tracking_calls(
        self, payloads: dict[str, list[dict[str, Any]]], config: dict[str, Any]
    ) -> list[int]:
        """Run fetch and return the trakt ids passed to fetch_show_season_count."""
        called_ids: list[int] = []

        def fake_fetch_list(
            endpoint: str, *args: object, **kwargs: object
        ) -> list[dict[str, Any]]:
            return payloads[endpoint]

        def fake_season_count(trakt_id: int, *args: object, **kwargs: object) -> int:
            called_ids.append(trakt_id)
            return 5

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
                "src.ingestion.sources.trakt.trakt.fetch_show_season_count",
                side_effect=fake_season_count,
            ),
        ):
            list(TraktPlugin().fetch(config))
        return called_ids

    def test_not_called_for_fully_watched_show(self) -> None:
        """A COMPLETED (fully-watched) show makes no extra season-count call."""
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
        assert self._run_fetch_tracking_calls(payloads, _config()) == []

    def test_not_called_for_watchlist_only_show(self) -> None:
        """A watchlist-only (UNREAD) show makes no extra season-count call."""
        payloads = _all_lists(
            watchlist_shows=[{"show": _show(30, "Andor", aired_episodes=12)}],
        )
        assert self._run_fetch_tracking_calls(payloads, _config()) == []

    def test_called_only_for_in_progress_show(self) -> None:
        """Among a mix of shows, only the in-progress one triggers the call."""
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
                },
                {
                    "last_watched_at": "2022-06-01T00:00:00.000Z",
                    "show": _show(20, "The Expanse", aired_episodes=60),
                    "seasons": [
                        {
                            "number": 1,
                            "episodes": [{"number": n} for n in range(1, 11)],
                        }
                    ],
                },
            ],
            watchlist_shows=[{"show": _show(30, "Andor", aired_episodes=12)}],
        )
        assert self._run_fetch_tracking_calls(payloads, _config()) == [20]

    def test_season_count_error_raises_source_error(self) -> None:
        """A failure in the season-count call surfaces as SourceError."""
        payloads = _all_lists(
            watched_shows=[
                {
                    "last_watched_at": "2022-06-01T00:00:00.000Z",
                    "show": _show(20, "The Expanse", aired_episodes=60),
                    "seasons": [
                        {
                            "number": 1,
                            "episodes": [{"number": n} for n in range(1, 11)],
                        }
                    ],
                }
            ]
        )

        def fake_fetch_list(
            endpoint: str, *args: object, **kwargs: object
        ) -> list[dict[str, Any]]:
            return payloads[endpoint]

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
                "src.ingestion.sources.trakt.trakt.fetch_show_season_count",
                side_effect=TraktAPIError("seasons fetch failed"),
            ),
        ):
            with pytest.raises(SourceError) as exc_info:
                list(TraktPlugin().fetch(_config()))

        assert exc_info.value.plugin_name == "trakt"
        assert "seasons fetch failed" in exc_info.value.message
