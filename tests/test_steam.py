"""Tests for Steam API integration."""

from unittest.mock import Mock, patch

import pytest
import requests

import src.ingestion.sources.steam as steam_module
from src.ingestion.plugin_base import SourceError, SourcePlugin
from src.ingestion.sources.steam import (
    SteamAPIError,
    SteamPlugin,
    _fetch_steam_games,
    get_owned_games,
    get_steam_id_from_vanity_url,
)
from src.models.content import ConsumptionStatus, ContentType


class TestGetSteamIdFromVanityUrl:
    """Tests for Steam vanity URL resolution."""

    @patch("src.ingestion.sources.steam.requests.get")
    def test_resolve_vanity_url_success(self, mock_get):
        """Test successful vanity URL resolution."""
        mock_response = Mock(spec=requests.Response)
        mock_response.json.return_value = {
            "response": {"success": 1, "steamid": "76561198000000000"}
        }
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response

        steam_id = get_steam_id_from_vanity_url("test_key", "testuser")

        assert steam_id == "76561198000000000"
        mock_get.assert_called_once()
        call_args = mock_get.call_args
        assert "ISteamUser/ResolveVanityURL" in call_args[0][0]
        assert call_args[1]["params"]["key"] == "test_key"
        assert call_args[1]["params"]["vanityurl"] == "testuser"

    @patch("src.ingestion.sources.steam.requests.get")
    def test_resolve_vanity_url_not_found(self, mock_get):
        """Test vanity URL resolution when not found."""
        mock_response = Mock(spec=requests.Response)
        mock_response.json.return_value = {"response": {"success": 42}}
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response

        steam_id = get_steam_id_from_vanity_url("test_key", "nonexistent")

        assert steam_id is None

    @patch("src.ingestion.sources.steam.requests.get")
    def test_resolve_vanity_url_api_error(self, mock_get):
        """Test vanity URL resolution with API error."""
        mock_get.side_effect = requests.RequestException("Connection error")

        with pytest.raises(SteamAPIError, match="Failed to resolve Steam ID"):
            get_steam_id_from_vanity_url("test_key", "testuser")


class TestGetOwnedGames:
    """Tests for fetching owned games."""

    @patch("src.ingestion.sources.steam.requests.get")
    def test_get_owned_games_success(self, mock_get):
        """Test successful game fetch."""
        mock_response = Mock(spec=requests.Response)
        mock_response.json.return_value = {
            "response": {
                "game_count": 2,
                "games": [
                    {
                        "appid": 12345,
                        "name": "Test Game 1",
                        "playtime_forever": 120,
                        "playtime_2weeks": 30,
                    },
                    {
                        "appid": 67890,
                        "name": "Test Game 2",
                        "playtime_forever": 0,
                    },
                ],
            }
        }
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response

        games = get_owned_games("test_key", "76561198000000000")

        assert len(games) == 2
        assert games[0]["appid"] == 12345
        assert games[0]["name"] == "Test Game 1"
        assert games[0]["playtime_forever"] == 120
        mock_get.assert_called_once()
        call_args = mock_get.call_args
        assert "IPlayerService/GetOwnedGames" in call_args[0][0]
        assert call_args[1]["params"]["steamid"] == "76561198000000000"

    @patch("src.ingestion.sources.steam.requests.get")
    def test_get_owned_games_empty(self, mock_get):
        """Test game fetch with empty library."""
        mock_response = Mock(spec=requests.Response)
        mock_response.json.return_value = {"response": {"game_count": 0, "games": []}}
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response

        games = get_owned_games("test_key", "76561198000000000")

        assert len(games) == 0

    @patch("src.ingestion.sources.steam.requests.get")
    def test_get_owned_games_api_error(self, mock_get):
        """Test game fetch with API error."""
        mock_get.side_effect = requests.RequestException("API error")

        with pytest.raises(SteamAPIError, match="Failed to fetch Steam games"):
            get_owned_games("test_key", "76561198000000000")


class TestParseSteamGames:
    """Tests for parsing Steam games into ContentItems."""

    @patch("src.ingestion.sources.steam.get_owned_games")
    def test__fetch_steam_games_basic(self, mock_get_games):
        """Test basic game parsing."""
        mock_get_games.return_value = [
            {
                "appid": 12345,
                "name": "Test Game",
                "playtime_forever": 120,  # 2 hours
                "playtime_2weeks": 30,
            }
        ]

        items = list(_fetch_steam_games("test_key", steam_id="76561198000000000"))

        assert len(items) == 1
        item = items[0]
        assert item.title == "Test Game"
        assert item.content_type == ContentType.VIDEO_GAME
        assert item.id == "12345"
        assert item.author is None
        # Steam imports always default to UNREAD; user marks progress in the UI.
        assert item.status == ConsumptionStatus.UNREAD
        assert item.rating is None  # Ratings are user-provided, not inferred
        assert item.metadata["playtime_hours"] == 2.0
        assert item.metadata["playtime_minutes"] == 120

    @patch("src.ingestion.sources.steam.get_owned_games")
    def test__fetch_steam_games_rating_always_none(self, mock_get_games):
        """Test that ratings are always None (user-provided, not inferred from playtime)."""
        for playtime_minutes in [0, 1, 60, 300, 600, 1200]:
            mock_get_games.return_value = [
                {
                    "appid": 12345,
                    "name": "Test Game",
                    "playtime_forever": playtime_minutes,
                }
            ]
            items = list(_fetch_steam_games("test_key", steam_id="76561198000000000"))
            assert (
                items[0].rating is None
            ), f"Expected None rating for {playtime_minutes} minutes, got {items[0].rating}"

    @patch("src.ingestion.sources.steam.get_owned_games")
    def test__fetch_steam_games_min_playtime_filter(self, mock_get_games):
        """Test minimum playtime filter."""
        mock_get_games.return_value = [
            {"appid": 1, "name": "Game 1", "playtime_forever": 10},
            {"appid": 2, "name": "Game 2", "playtime_forever": 100},
            {"appid": 3, "name": "Game 3", "playtime_forever": 200},
        ]

        # Filter games with < 50 minutes playtime
        items = list(
            _fetch_steam_games(
                "test_key", steam_id="76561198000000000", min_playtime_minutes=50
            )
        )

        assert len(items) == 2  # Only games 2 and 3
        assert all(item.metadata["playtime_minutes"] >= 50 for item in items)

    @patch("src.ingestion.sources.steam.get_owned_games")
    def test__fetch_steam_games_metadata(self, mock_get_games):
        """Playtime fields from GetOwnedGames flow into metadata."""
        mock_get_games.return_value = [
            {
                "appid": 12345,
                "name": "Test Game",
                "playtime_forever": 120,
                "playtime_2weeks": 30,
                "playtime_windows_forever": 100,
                "playtime_mac_forever": 20,
                "playtime_linux_forever": 0,
            }
        ]

        items = list(_fetch_steam_games("test_key", steam_id="76561198000000000"))

        assert len(items) == 1
        metadata = items[0].metadata
        assert metadata["steam_app_id"] == "12345"
        assert metadata["playtime_minutes"] == 120
        assert metadata["playtime_hours"] == 2.0
        assert metadata["playtime_2weeks"] == 30
        assert metadata["playtime_windows_forever"] == 100
        assert metadata["playtime_mac_forever"] == 20
        assert metadata["playtime_linux_forever"] == 0

    @patch("src.ingestion.sources.steam.get_owned_games")
    def test__fetch_steam_games_no_name_skipped(self, mock_get_games):
        """Test that games without names are skipped."""
        mock_get_games.return_value = [
            {"appid": 12345, "name": "", "playtime_forever": 120},
            {"appid": 67890, "name": "Valid Game", "playtime_forever": 60},
        ]

        items = list(_fetch_steam_games("test_key", steam_id="76561198000000000"))

        assert len(items) == 1
        assert items[0].title == "Valid Game"

    @patch("src.ingestion.sources.steam.get_steam_id_from_vanity_url")
    @patch("src.ingestion.sources.steam.get_owned_games")
    def test__fetch_steam_games_vanity_url(self, mock_get_games, mock_resolve_vanity):
        """Test parsing with vanity URL instead of Steam ID."""
        mock_resolve_vanity.return_value = "76561198000000000"
        mock_get_games.return_value = [
            {"appid": 12345, "name": "Test Game", "playtime_forever": 60}
        ]

        items = list(_fetch_steam_games("test_key", vanity_url="testuser"))

        assert len(items) == 1
        mock_resolve_vanity.assert_called_once_with("test_key", "testuser")

    @patch("src.ingestion.sources.steam.get_steam_id_from_vanity_url")
    def test__fetch_steam_games_vanity_url_failure(self, mock_resolve_vanity):
        """Test parsing when vanity URL resolution fails."""
        mock_resolve_vanity.return_value = None

        with pytest.raises(SteamAPIError, match="Could not resolve Steam ID"):
            list(_fetch_steam_games("test_key", vanity_url="nonexistent"))

    def test__fetch_steam_games_no_id_or_vanity(self):
        """Test parsing without Steam ID or vanity URL."""
        with pytest.raises(
            ValueError, match="Either steam_id or vanity_url must be provided"
        ):
            list(_fetch_steam_games("test_key"))

    @patch("src.ingestion.sources.steam.get_owned_games")
    def test__fetch_steam_games_empty_library(self, mock_get_games):
        """Test parsing with empty game library."""
        mock_get_games.return_value = []

        items = list(_fetch_steam_games("test_key", steam_id="76561198000000000"))

        assert len(items) == 0


class TestSteamPluginProperties:
    """Tests for SteamPlugin metadata properties."""

    def test_is_source_plugin(self) -> None:
        """Test that SteamPlugin is a SourcePlugin subclass."""
        plugin = SteamPlugin()
        assert isinstance(plugin, SourcePlugin)

    def test_name(self) -> None:
        """Test plugin name identifier."""
        plugin = SteamPlugin()
        assert plugin.name == "steam"

    def test_display_name(self) -> None:
        """Test human-readable display name."""
        plugin = SteamPlugin()
        assert plugin.display_name == "Steam"

    def test_content_types(self) -> None:
        """Test that plugin provides video games."""
        plugin = SteamPlugin()
        assert plugin.content_types == [ContentType.VIDEO_GAME]

    def test_requires_api_key(self) -> None:
        """Test that plugin requires an API key."""
        plugin = SteamPlugin()
        assert plugin.requires_api_key is True

    def test_requires_network(self) -> None:
        """Test that plugin requires network access."""
        plugin = SteamPlugin()
        assert plugin.requires_network is True

    def test_config_schema(self) -> None:
        """Test configuration schema fields."""
        plugin = SteamPlugin()
        schema = plugin.get_config_schema()

        field_names = [field.name for field in schema]
        assert "api_key" in field_names
        assert "steam_id" in field_names
        assert "vanity_url" in field_names
        assert "min_playtime_minutes" in field_names

        api_key_field = next(field for field in schema if field.name == "api_key")
        assert api_key_field.required is True
        assert api_key_field.sensitive is True

    def test_get_source_identifier(self) -> None:
        """Test source identifier matches plugin name."""
        plugin = SteamPlugin()
        assert plugin.get_source_identifier() == "steam"

    def test_get_info(self) -> None:
        """Test plugin info includes all metadata."""
        plugin = SteamPlugin()
        info = plugin.get_info()

        assert info.name == "steam"
        assert info.display_name == "Steam"
        assert info.content_types == [ContentType.VIDEO_GAME]
        assert info.requires_api_key is True
        assert info.requires_network is True


class TestSteamPluginValidation:
    """Tests for SteamPlugin config validation."""

    def test_validate_valid_config(self) -> None:
        """Test validation passes with valid config."""
        plugin = SteamPlugin()
        errors = plugin.validate_config(
            {"api_key": "test_key", "steam_id": "76561198000000000"}
        )
        assert errors == []

    def test_validate_valid_vanity_url(self) -> None:
        """Test validation passes with vanity URL instead of steam_id."""
        plugin = SteamPlugin()
        errors = plugin.validate_config(
            {"api_key": "test_key", "vanity_url": "testuser"}
        )
        assert errors == []

    def test_validate_missing_api_key(self) -> None:
        """Test validation fails when api_key is missing."""
        plugin = SteamPlugin()
        errors = plugin.validate_config({"steam_id": "76561198000000000"})

        assert len(errors) == 1
        assert "'api_key' is required" in errors[0]

    def test_validate_missing_id_and_vanity(self) -> None:
        """Test validation fails when both steam_id and vanity_url are missing."""
        plugin = SteamPlugin()
        errors = plugin.validate_config({"api_key": "test_key"})

        assert len(errors) == 1
        assert "steam_id" in errors[0] or "vanity_url" in errors[0]

    def test_validate_empty_api_key(self) -> None:
        """Test validation fails when api_key is empty."""
        plugin = SteamPlugin()
        errors = plugin.validate_config(
            {"api_key": "", "steam_id": "76561198000000000"}
        )

        assert any("api_key" in error for error in errors)

    def test_validate_all_missing(self) -> None:
        """Test validation reports all errors when everything is missing."""
        plugin = SteamPlugin()
        errors = plugin.validate_config({})

        assert len(errors) == 2


class TestSteamStatusInferenceRegression:
    """Regression tests for Steam status inference from playtime (issue #42).

    Bug: Steam ingestion inferred ConsumptionStatus.CURRENTLY_CONSUMING for any
    game with playtime_forever > 0, so every previously-played game appeared as
    "currently consuming" on import. This is inconsistent with all other
    ingestion sources (Goodreads, generic CSV, markdown), which only set
    CURRENTLY_CONSUMING when the user explicitly declares it.

    Root cause: _fetch_steam_games branched on playtime_minutes to choose
    between UNREAD and CURRENTLY_CONSUMING, but Steam exposes no explicit
    "currently playing" or "completed" signal — playtime alone is not a
    reliable indicator of either.

    Fix: Always assign ConsumptionStatus.UNREAD. Users mark progress in the UI.

    Reported in: https://github.com/therealahall/recommendinator/issues/42
    """

    @pytest.mark.parametrize("playtime_minutes", [0, 1, 30, 6000])
    @patch("src.ingestion.sources.steam.get_owned_games")
    def test_fetch_steam_games_status_always_unread_regression(
        self,
        mock_get_games: Mock,
        playtime_minutes: int,
    ) -> None:
        """Status is UNREAD regardless of playtime."""
        mock_get_games.return_value = [
            {
                "appid": 12345,
                "name": "Test Game",
                "playtime_forever": playtime_minutes,
            }
        ]

        items = list(_fetch_steam_games("test_key", steam_id="76561198000000000"))

        assert len(items) == 1
        assert items[0].status == ConsumptionStatus.UNREAD


class TestSteamNoneConfigValuesRegression:
    """Regression tests for None values in Steam config causing AttributeError.

    Bug: When YAML config has keys with no value (e.g., `steam_id:` with no value),
    PyYAML parses them as None. The pattern `config.get("steam_id", "").strip()`
    fails because .get() returns None (key exists with None value) rather than
    the default "". Calling .strip() on None raises AttributeError:
    'NoneType' object has no attribute 'strip'.

    Root cause: Using .get(key, "") instead of (config.get(key) or "").
    Fix: Use (value or "") pattern before .strip() in transform_config. The fetch()
    method delegates normalization to transform_config() to avoid duplication.

    Reported in: https://github.com/therealahall/recommendinator/issues/2
    """

    def test_transform_config_none_steam_id_regression(self) -> None:
        """transform_config handles None steam_id without raising."""
        result = SteamPlugin.transform_config(
            {"api_key": "test_key", "steam_id": None, "vanity_url": "testuser"}
        )
        assert result["steam_id"] is None
        assert result["vanity_url"] == "testuser"
        assert result["api_key"] == "test_key"

    def test_transform_config_none_vanity_url_regression(self) -> None:
        """transform_config handles None vanity_url without raising."""
        result = SteamPlugin.transform_config(
            {"api_key": "test_key", "steam_id": "123", "vanity_url": None}
        )
        assert result["steam_id"] == "123"
        assert result["vanity_url"] is None

    def test_transform_config_none_api_key_regression(self) -> None:
        """transform_config coerces None api_key to "" without raising.

        The resulting config is invalid and will be rejected by validate_config.
        This tests that the coercion itself does not crash.
        """
        result = SteamPlugin.transform_config({"api_key": None, "steam_id": "123"})
        assert result["api_key"] == ""
        # Confirm the transformed config correctly fails validation
        plugin = SteamPlugin()
        errors = plugin.validate_config(result)
        assert any("api_key" in error for error in errors)

    def test_transform_config_all_none_regression(self) -> None:
        """transform_config handles all None values without raising."""
        result = SteamPlugin.transform_config(
            {"api_key": None, "steam_id": None, "vanity_url": None}
        )
        assert result["api_key"] == ""
        assert result["steam_id"] is None
        assert result["vanity_url"] is None

    @patch("src.ingestion.sources.steam.get_steam_id_from_vanity_url")
    @patch("src.ingestion.sources.steam.get_owned_games")
    def test_fetch_pipeline_none_values_regression(
        self,
        mock_get_games: Mock,
        mock_resolve_vanity: Mock,
    ) -> None:
        """Full pipeline: None YAML values survive transform_config -> fetch.

        Simulates a YAML config with a blank steam_id field (parsed as None
        by PyYAML). The config is passed through transform_config then fetch,
        matching the real pipeline path.
        """
        mock_resolve_vanity.return_value = "76561198000000000"
        mock_get_games.return_value = [
            {"appid": 1, "name": "Game", "playtime_forever": 60}
        ]

        plugin = SteamPlugin()
        raw_config = {"api_key": "test_key", "steam_id": None, "vanity_url": "testuser"}
        transformed = SteamPlugin.transform_config(raw_config)
        items = list(plugin.fetch(transformed))

        assert len(items) == 1
        mock_resolve_vanity.assert_called_once_with("test_key", "testuser")


class TestSteamPluginFetch:
    """Tests for SteamPlugin.fetch()."""

    @patch("src.ingestion.sources.steam.get_owned_games")
    def test_fetch_through_plugin(self, mock_get_games: Mock) -> None:
        """Test fetching games through the plugin interface."""
        mock_get_games.return_value = [
            {"appid": 12345, "name": "Test Game", "playtime_forever": 120}
        ]

        plugin = SteamPlugin()
        items = list(
            plugin.fetch({"api_key": "test_key", "steam_id": "76561198000000000"})
        )

        assert len(items) == 1
        assert items[0].title == "Test Game"
        assert items[0].source == "steam"

    @patch("src.ingestion.sources.steam.get_owned_games")
    def test_fetch_api_error_raises_source_error(self, mock_get_games: Mock) -> None:
        """Test that Steam API errors are wrapped in SourceError."""
        mock_get_games.side_effect = SteamAPIError("API failure")

        plugin = SteamPlugin()
        with pytest.raises(SourceError, match="API failure") as exc_info:
            list(plugin.fetch({"api_key": "test_key", "steam_id": "76561198000000000"}))

        assert exc_info.value.plugin_name == "steam"


class TestSteamTwoPassRegression:
    """Regression tests for the slow Steam Store API metadata pass (issue #34).

    Bug: Steam sync ran a slow first pass calling the Steam Store appdetails
    endpoint once per game (rate-limited to ~3s per request) before yielding
    any items, then a fast second pass that emitted ContentItems with
    per-game progress. For libraries of a few hundred games the first pass
    took 15+ minutes and blocked all sync output.

    Root cause: ``_fetch_steam_games`` called ``get_game_details(app_ids)``
    inline to enrich each game with release_date/genres/publishers/etc.,
    duplicating metadata that the RAWG enrichment provider already supplies
    asynchronously after ingestion.

    Fix: Drop the inline Steam Store API pass entirely. Sync only calls
    ``GetOwnedGames`` (one request) and yields items immediately. Background
    enrichment via RAWG fills in the same metadata without blocking.

    Reported in: https://github.com/therealahall/recommendinator/issues/34
    """

    @patch("src.ingestion.sources.steam.requests.get")
    def test_fetch_calls_only_owned_games_endpoint(self, mock_get: Mock) -> None:
        """Sync hits GetOwnedGames once and never the Steam Store appdetails endpoint."""
        mock_response = Mock(spec=requests.Response)
        mock_response.json.return_value = {
            "response": {
                "games": [
                    {"appid": 1, "name": "Game 1", "playtime_forever": 60},
                    {"appid": 2, "name": "Game 2", "playtime_forever": 120},
                    {"appid": 3, "name": "Game 3", "playtime_forever": 180},
                ]
            }
        }
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response

        items = list(_fetch_steam_games("test_key", steam_id="76561198000000000"))

        assert len(items) == 3
        assert mock_get.call_count == 1
        called_url = mock_get.call_args_list[0][0][0]
        assert "IPlayerService/GetOwnedGames" in called_url
        assert "store.steampowered.com" not in called_url

    def test_get_game_details_no_longer_exported(self) -> None:
        """The old slow per-game appdetails helper is removed from the module."""
        assert not hasattr(steam_module, "get_game_details")

    @patch("src.ingestion.sources.steam.get_owned_games")
    def test_fetch_skips_games_with_missing_appid(self, mock_get_games: Mock) -> None:
        """Games whose appid is missing or None are silently skipped."""
        mock_get_games.return_value = [
            {"name": "No appid", "playtime_forever": 100},
            {"appid": None, "name": "Null appid", "playtime_forever": 100},
            {"appid": 42, "name": "Valid", "playtime_forever": 100},
        ]

        items = list(_fetch_steam_games("test_key", steam_id="76561198000000000"))

        assert len(items) == 1
        assert items[0].id == "42"

    def test_fetch_missing_id_and_vanity_raises_source_error(self) -> None:
        """SteamPlugin.fetch wraps the ValueError from missing id+vanity in SourceError."""
        plugin = SteamPlugin()
        with pytest.raises(SourceError, match="steam_id or vanity_url") as exc_info:
            list(
                plugin.fetch({"api_key": "test_key", "steam_id": "", "vanity_url": ""})
            )

        assert exc_info.value.plugin_name == "steam"

    @patch("src.ingestion.sources.steam.get_owned_games")
    def test_fetch_progress_callback_translates_phase(
        self, mock_get_games: Mock
    ) -> None:
        """Adapter translates 'owned_games' phase to a human-readable label
        and forwards per-item titles unchanged."""
        mock_get_games.return_value = [
            {"appid": 1, "name": "Alpha", "playtime_forever": 30},
            {"appid": 2, "name": "Beta", "playtime_forever": 60},
        ]
        callback = Mock()

        plugin = SteamPlugin()
        list(
            plugin.fetch(
                {"api_key": "test_key", "steam_id": "76561198000000000"},
                progress_callback=callback,
            )
        )

        phases = [call.args[2] for call in callback.call_args_list]
        assert "Fetching library..." in phases
        assert "Alpha" in phases
        assert "Beta" in phases


class TestSteamApiKeyScrubbingRegression:
    """Regression tests for Steam API key leaking via error messages.

    Bug: ``requests.HTTPError.__str__`` includes the full request URL, which
    for Steam Web API calls embeds ``?key=<api_key>`` in the query string. The
    plugin wrapped the exception verbatim as ``SteamAPIError(f'... {error}')``
    and again as ``SourceError(self.name, str(error))``. ``SourceError``
    propagates into ``SyncJob.error_message``, which the web API returns to
    the browser and writes to logs — exposing the user's Steam API key.

    Root cause: `f"... {error}"` interpolation called the default
    ``RequestException.__str__``, which contains the full URL (including the
    ``key`` query parameter) for HTTPErrors raised by ``raise_for_status()``.

    Fix: ``_scrub_request_error`` renders only ``HTTP <status>`` for HTTP
    errors and the bare exception class name for transport errors, before the
    string ever reaches ``SteamAPIError`` or any logger.
    """

    @patch("src.ingestion.sources.steam.requests.get")
    def test_vanity_url_http_error_does_not_leak_api_key(self, mock_get: Mock) -> None:
        """HTTPError on vanity resolution surfaces only the status code."""
        api_key = "SECRET_STEAM_KEY_123"
        url_with_key = (
            "https://api.steampowered.com/ISteamUser/ResolveVanityURL/v0001/"
            f"?key={api_key}&vanityurl=user"
        )
        response = Mock(spec=requests.Response)
        response.status_code = 401
        http_error = requests.HTTPError(
            f"401 Client Error: UNAUTHORIZED for url: {url_with_key}",
            response=response,
        )
        response.raise_for_status = Mock(side_effect=http_error)
        mock_get.return_value = response

        with pytest.raises(SteamAPIError) as exc_info:
            get_steam_id_from_vanity_url(api_key, "user")

        message = str(exc_info.value)
        assert api_key not in message
        assert "HTTP 401" in message

    @patch("src.ingestion.sources.steam.requests.get")
    def test_owned_games_http_error_does_not_leak_api_key(self, mock_get: Mock) -> None:
        """HTTPError on owned-games fetch surfaces only the status code."""
        api_key = "SECRET_STEAM_KEY_456"
        url_with_key = (
            "https://api.steampowered.com/IPlayerService/GetOwnedGames/v0001/"
            f"?key={api_key}&steamid=76561198000000000"
        )
        response = Mock(spec=requests.Response)
        response.status_code = 503
        http_error = requests.HTTPError(
            f"503 Server Error: Service Unavailable for url: {url_with_key}",
            response=response,
        )
        response.raise_for_status = Mock(side_effect=http_error)
        mock_get.return_value = response

        with pytest.raises(SteamAPIError) as exc_info:
            get_owned_games(api_key, "76561198000000000")

        message = str(exc_info.value)
        assert api_key not in message
        assert "HTTP 503" in message

    @patch("src.ingestion.sources.steam.requests.get")
    def test_transport_error_surfaces_only_exception_type(self, mock_get: Mock) -> None:
        """Connection errors surface only the exception class, not message text."""
        api_key = "SECRET_STEAM_KEY_789"
        mock_get.side_effect = requests.ConnectionError(
            f"Failed to connect; key={api_key} was in URL"
        )

        with pytest.raises(SteamAPIError) as exc_info:
            get_owned_games(api_key, "76561198000000000")

        message = str(exc_info.value)
        assert api_key not in message
        assert "ConnectionError" in message

    @patch("src.ingestion.sources.steam.get_owned_games")
    def test_source_error_propagates_scrubbed_message(
        self, mock_get_games: Mock
    ) -> None:
        """End-to-end: SourceError raised through plugin.fetch carries no API key."""
        api_key = "SECRET_STEAM_KEY_END2END"
        mock_get_games.side_effect = SteamAPIError(
            "Failed to fetch Steam games: HTTP 401"
        )

        plugin = SteamPlugin()
        with pytest.raises(SourceError) as exc_info:
            list(plugin.fetch({"api_key": api_key, "steam_id": "76561198000000000"}))

        assert api_key not in str(exc_info.value)
