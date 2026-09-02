import logging
from unittest.mock import Mock, patch

import pytest
import requests

from src.ingestion.plugin_base import SourceError
from src.ingestion.sources.steam.steam import (
    SteamAPIError,
    SteamPlugin,
    _fetch_steam_games,
    get_owned_games,
    get_steam_id_from_vanity_url,
)
from src.models.content import ConsumptionStatus, ContentType


class TestGetSteamIdFromVanityUrl:
    @patch("src.ingestion.sources.steam.steam.requests.get")
    def test_resolve_vanity_url_not_found(self, mock_get):
        mock_response = Mock(spec=requests.Response)
        mock_response.json.return_value = {"response": {"success": 42}}
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response

        steam_id = get_steam_id_from_vanity_url("test_key", "nonexistent")

        assert steam_id is None


class TestParseSteamGames:
    @patch("src.ingestion.sources.steam.steam.get_owned_games")
    def test__fetch_steam_games_basic(self, mock_get_games):
        mock_get_games.return_value = [
            {
                "appid": 12345,
                "name": "Test Game",
                "playtime_forever": 120,
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
        assert item.status == ConsumptionStatus.UNREAD
        assert item.rating is None
        assert item.metadata["playtime_hours"] == 2.0
        assert item.metadata["playtime_minutes"] == 120

    @patch("src.ingestion.sources.steam.steam.requests.get")
    def test_cover_art_is_derived_from_the_app_id_and_costs_no_request(
        self, mock_get: Mock
    ) -> None:
        mock_response = Mock(spec=requests.Response)
        mock_response.json.return_value = {
            "response": {
                "games": [{"appid": 620, "name": "Portal 2", "playtime_forever": 60}]
            }
        }
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response

        items = list(_fetch_steam_games("test_key", steam_id="76561198000000000"))

        assert items[0].cover_url == (
            "https://shared.fastly.steamstatic.com/store_item_assets/steam/apps/"
            "620/library_600x900_2x.jpg"
        )
        assert mock_get.call_count == 1

    @patch("src.ingestion.sources.steam.steam.get_owned_games")
    def test__fetch_steam_games_min_playtime_filter(self, mock_get_games):
        mock_get_games.return_value = [
            {"appid": 1, "name": "Game 1", "playtime_forever": 10},
            {"appid": 2, "name": "Game 2", "playtime_forever": 100},
            {"appid": 3, "name": "Game 3", "playtime_forever": 200},
        ]

        items = list(
            _fetch_steam_games(
                "test_key", steam_id="76561198000000000", min_playtime_minutes=50
            )
        )

        assert len(items) == 2
        assert all(item.metadata["playtime_minutes"] >= 50 for item in items)

    @patch("src.ingestion.sources.steam.steam.get_owned_games")
    def test__fetch_steam_games_metadata(self, mock_get_games):
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

    @patch("src.ingestion.sources.steam.steam.get_owned_games")
    def test__fetch_steam_games_no_name_skipped(self, mock_get_games):
        mock_get_games.return_value = [
            {"appid": 12345, "name": "", "playtime_forever": 120},
            {"appid": 67890, "name": "Valid Game", "playtime_forever": 60},
        ]

        items = list(_fetch_steam_games("test_key", steam_id="76561198000000000"))

        assert len(items) == 1
        assert items[0].title == "Valid Game"

    @patch("src.ingestion.sources.steam.steam.get_steam_id_from_vanity_url")
    @patch("src.ingestion.sources.steam.steam.get_owned_games")
    def test__fetch_steam_games_vanity_url(self, mock_get_games, mock_resolve_vanity):
        mock_resolve_vanity.return_value = "76561198000000000"
        mock_get_games.return_value = [
            {"appid": 12345, "name": "Test Game", "playtime_forever": 60}
        ]

        items = list(_fetch_steam_games("test_key", vanity_url="testuser"))

        assert len(items) == 1
        mock_resolve_vanity.assert_called_once_with("test_key", "testuser")

    @patch("src.ingestion.sources.steam.steam.get_steam_id_from_vanity_url")
    def test__fetch_steam_games_vanity_url_failure(self, mock_resolve_vanity):
        mock_resolve_vanity.return_value = None

        with pytest.raises(SteamAPIError, match="Could not resolve Steam ID"):
            list(_fetch_steam_games("test_key", vanity_url="nonexistent"))


class TestSteamPluginValidation:
    def test_validate_missing_api_key(self) -> None:
        plugin = SteamPlugin()
        errors = plugin.validate_config({"steam_id": "76561198000000000"})

        assert len(errors) == 1
        assert "'api_key' is required" in errors[0]

    def test_validate_missing_id_and_vanity(self) -> None:
        plugin = SteamPlugin()
        errors = plugin.validate_config({"api_key": "test_key"})

        assert len(errors) == 1
        assert "steam_id" in errors[0] or "vanity_url" in errors[0]


class TestSteamStatusInferenceRegression:
    """Reported in: https://github.com/therealahall/recommendinator/issues/42"""

    @pytest.mark.parametrize("playtime_minutes", [0, 1, 30, 6000])
    @patch("src.ingestion.sources.steam.steam.get_owned_games")
    def test_fetch_steam_games_status_always_unread_regression(
        self,
        mock_get_games: Mock,
        playtime_minutes: int,
    ) -> None:
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
    """Reported in: https://github.com/therealahall/recommendinator/issues/2"""

    def test_transform_config_none_steam_id_regression(self) -> None:
        result = SteamPlugin.transform_config(
            {"api_key": "test_key", "steam_id": None, "vanity_url": "testuser"}
        )
        assert result["steam_id"] is None
        assert result["vanity_url"] == "testuser"
        assert result["api_key"] == "test_key"

    @patch("src.ingestion.sources.steam.steam.get_steam_id_from_vanity_url")
    @patch("src.ingestion.sources.steam.steam.get_owned_games")
    def test_fetch_pipeline_none_values_regression(
        self,
        mock_get_games: Mock,
        mock_resolve_vanity: Mock,
    ) -> None:
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
    @patch("src.ingestion.sources.steam.steam.get_owned_games")
    def test_fetch_through_plugin(self, mock_get_games: Mock) -> None:
        mock_get_games.return_value = [
            {"appid": 12345, "name": "Test Game", "playtime_forever": 120}
        ]

        plugin = SteamPlugin()
        items = list(
            plugin.fetch({"api_key": "test_key", "steam_id": "76561198000000000"})
        )

        assert len(items) == 1
        assert items[0].title == "Test Game"


class TestSteamTwoPassRegression:
    """Reported in: https://github.com/therealahall/recommendinator/issues/34"""

    @patch("src.ingestion.sources.steam.steam.requests.get")
    def test_fetch_calls_only_owned_games_endpoint(self, mock_get: Mock) -> None:
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

    @patch("src.ingestion.sources.steam.steam.get_owned_games")
    def test_fetch_skips_games_with_missing_appid(self, mock_get_games: Mock) -> None:
        mock_get_games.return_value = [
            {"name": "No appid", "playtime_forever": 100},
            {"appid": None, "name": "Null appid", "playtime_forever": 100},
            {"appid": 42, "name": "Valid", "playtime_forever": 100},
        ]

        items = list(_fetch_steam_games("test_key", steam_id="76561198000000000"))

        assert len(items) == 1
        assert items[0].id == "42"

    def test_fetch_missing_id_and_vanity_raises_source_error(self) -> None:
        plugin = SteamPlugin()
        with pytest.raises(SourceError, match="steam_id or vanity_url") as exc_info:
            list(
                plugin.fetch({"api_key": "test_key", "steam_id": "", "vanity_url": ""})
            )

        assert exc_info.value.plugin_name == "steam"


class TestSteamApiKeyScrubbingRegression:
    @patch("src.ingestion.sources.steam.steam.requests.get")
    def test_owned_games_http_error_does_not_leak_api_key(self, mock_get: Mock) -> None:
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

    @patch("src.ingestion.sources.steam.steam.requests.get")
    def test_transport_error_surfaces_only_exception_type(self, mock_get: Mock) -> None:
        api_key = "SECRET_STEAM_KEY_789"
        mock_get.side_effect = requests.ConnectionError(
            f"Failed to connect; key={api_key} was in URL"
        )

        with pytest.raises(SteamAPIError) as exc_info:
            get_owned_games(api_key, "76561198000000000")

        message = str(exc_info.value)
        assert api_key not in message
        assert "ConnectionError" in message


STEAM_LOGGER = "src.ingestion.sources.steam.steam"


def _connection_error_quoting(api_key: str, path: str) -> requests.ConnectionError:
    """The fault ``requests`` raises with the whole request URL in its words."""
    return requests.ConnectionError(
        "HTTPSConnectionPool(host='api.steampowered.com', port=443): Max retries "
        f"exceeded with url: {path}?key={api_key}"
    )


class TestSteamCredentialChainRegression:
    @patch("src.ingestion.sources.steam.steam.requests.get")
    def test_a_caller_logging_with_exc_info_cannot_reach_the_key(
        self, mock_get: Mock, caplog: pytest.LogCaptureFixture
    ) -> None:
        api_key = "steam-web-api-key-d47e"
        mock_get.side_effect = _connection_error_quoting(
            api_key, "/IPlayerService/GetOwnedGames/v0001/"
        )
        plugin = SteamPlugin()

        with pytest.raises(SourceError) as raised:
            list(plugin.fetch({"api_key": api_key, "steam_id": "76561198000000000"}))

        with caplog.at_level(logging.ERROR, logger="caller"):
            logging.getLogger("caller").error("Sync failed", exc_info=raised.value)

        assert (
            "Traceback" in caplog.text
        ), "nothing was rendered, so this proves nothing"
        assert api_key not in caplog.text


class TestSteamLogInjectionRegression:
    @patch("src.ingestion.sources.steam.steam.requests.get")
    def test_a_newline_in_the_steam_id_cannot_forge_a_log_entry(
        self, mock_get: Mock, caplog: pytest.LogCaptureFixture
    ) -> None:
        response = Mock(spec=requests.Response)
        response.json.return_value = {"response": {"games": []}}
        response.raise_for_status = Mock()
        mock_get.return_value = response

        with caplog.at_level(logging.INFO, logger=STEAM_LOGGER):
            list(
                _fetch_steam_games(
                    "key", steam_id="76561198000000000\nFound 9999 games"
                )
            )

        assert [
            record.getMessage()
            for record in caplog.records
            if record.name == STEAM_LOGGER
        ] == [
            "Fetching owned games from Steam API for Steam ID: "
            "76561198000000000\\nFound 9999 games",
            "Found 0 games in Steam library",
            "No games found in Steam library",
        ]
