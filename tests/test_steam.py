"""Tests for Steam API integration."""

from unittest.mock import Mock, patch

import pytest

from src.ingestion.plugin_base import SourceError, SourcePlugin
from src.ingestion.sources.steam import (
    SteamAPIError,
    SteamPlugin,
    _fetch_steam_games,
    get_game_details,
    get_owned_games,
    get_steam_id_from_vanity_url,
)
from src.models.content import ConsumptionStatus, ContentType


class TestGetSteamIdFromVanityUrl:
    """Tests for Steam vanity URL resolution."""

    @patch("src.ingestion.sources.steam.requests.get")
    def test_resolve_vanity_url_success(self, mock_get):
        """Test successful vanity URL resolution."""
        mock_response = Mock()
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
        mock_response = Mock()
        mock_response.json.return_value = {"response": {"success": 42}}
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response

        steam_id = get_steam_id_from_vanity_url("test_key", "nonexistent")

        assert steam_id is None

    @patch("src.ingestion.sources.steam.requests.get")
    def test_resolve_vanity_url_api_error(self, mock_get):
        """Test vanity URL resolution with API error."""
        import requests

        mock_get.side_effect = requests.RequestException("Connection error")

        with pytest.raises(SteamAPIError, match="Failed to resolve Steam ID"):
            get_steam_id_from_vanity_url("test_key", "testuser")


class TestGetOwnedGames:
    """Tests for fetching owned games."""

    @patch("src.ingestion.sources.steam.requests.get")
    def test_get_owned_games_success(self, mock_get):
        """Test successful game fetch."""
        mock_response = Mock()
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
        mock_response = Mock()
        mock_response.json.return_value = {"response": {"game_count": 0, "games": []}}
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response

        games = get_owned_games("test_key", "76561198000000000")

        assert len(games) == 0

    @patch("src.ingestion.sources.steam.requests.get")
    def test_get_owned_games_api_error(self, mock_get):
        """Test game fetch with API error."""
        import requests

        mock_get.side_effect = requests.RequestException("API error")

        with pytest.raises(SteamAPIError, match="Failed to fetch Steam games"):
            get_owned_games("test_key", "76561198000000000")


class TestGetGameDetails:
    """Tests for fetching game details."""

    @patch("src.ingestion.sources.steam.requests.get")
    def test_get_game_details_success(self, mock_get):
        """Test successful game details fetch."""
        mock_response = Mock()
        mock_response.json.return_value = {
            "12345": {
                "success": True,
                "data": {
                    "name": "Test Game",
                    "short_description": "A test game",
                    "developers": ["Developer 1"],
                    "publishers": ["Publisher 1"],
                    "genres": [{"description": "Action"}],
                    "release_date": {"date": "Jan 1, 2020"},
                    "metacritic": {"score": 85},
                },
            }
        }
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response

        details = get_game_details([12345])

        assert 12345 in details
        assert details[12345]["name"] == "Test Game"
        assert details[12345]["short_description"] == "A test game"

    @patch("src.ingestion.sources.steam.requests.get")
    def test_get_game_details_batch(self, mock_get):
        """Test game details fetch with multiple games."""
        mock_response = Mock()
        mock_response.json.return_value = {
            "12345": {"success": True, "data": {"name": "Game 1"}},
            "67890": {"success": True, "data": {"name": "Game 2"}},
        }
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response

        details = get_game_details([12345, 67890])

        assert len(details) == 2
        assert 12345 in details
        assert 67890 in details

    @patch("src.ingestion.sources.steam.requests.get")
    def test_get_game_details_large_batch(self, mock_get):
        """Test game details fetch with large batch (should split)."""
        mock_response = Mock()
        # Create response for single game (batch_size = 1)
        mock_response.json.return_value = {
            "0": {"success": True, "data": {"name": "Game 0"}}
        }
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response

        app_ids = list(range(5))  # 5 games should trigger 5 calls
        get_game_details(app_ids)

        # Should have made 5 calls (batch_size = 1)
        assert mock_get.call_count == 5


class TestParseSteamGames:
    """Tests for parsing Steam games into ContentItems."""

    @patch("src.ingestion.sources.steam.get_game_details")
    @patch("src.ingestion.sources.steam.get_owned_games")
    def test__fetch_steam_games_basic(self, mock_get_games, mock_get_details):
        """Test basic game parsing."""
        mock_get_games.return_value = [
            {
                "appid": 12345,
                "name": "Test Game",
                "playtime_forever": 120,  # 2 hours
                "playtime_2weeks": 30,
            }
        ]
        mock_get_details.return_value = {
            12345: {
                "name": "Test Game",
                "short_description": "A great game",
                "developers": ["Developer 1"],
                "genres": [{"description": "Action"}],
            }
        }

        items = list(_fetch_steam_games("test_key", steam_id="76561198000000000"))

        assert len(items) == 1
        item = items[0]
        assert item.title == "Test Game"
        assert item.content_type == ContentType.VIDEO_GAME
        assert item.id == "12345"
        assert item.author is None
        # Games with playtime are marked as "currently consuming" since Steam
        # doesn't provide completion data and playtime is unreliable
        assert item.status == ConsumptionStatus.CURRENTLY_CONSUMING
        assert item.rating is None  # Ratings are user-provided, not inferred
        assert item.metadata["playtime_hours"] == 2.0
        assert item.metadata["playtime_minutes"] == 120

    @patch("src.ingestion.sources.steam.get_game_details")
    @patch("src.ingestion.sources.steam.get_owned_games")
    def test__fetch_steam_games_status_mapping(self, mock_get_games, mock_get_details):
        """Test status mapping based on playtime.

        Steam doesn't provide completion data, and playtime is unreliable for
        determining completion (a 5-hour indie game vs a 100-hour RPG). We only
        distinguish between "never played" (UNREAD) and "has been played"
        (CURRENTLY_CONSUMING). Users can manually mark games as completed.
        """
        mock_get_details.return_value = {}

        # Test unread (0 minutes playtime = never played)
        mock_get_games.return_value = [
            {"appid": 1, "name": "Unread Game", "playtime_forever": 0}
        ]
        items = list(_fetch_steam_games("test_key", steam_id="76561198000000000"))
        assert items[0].status == ConsumptionStatus.UNREAD
        assert items[0].rating is None

        # Test currently consuming (any playtime = has been played)
        mock_get_games.return_value = [
            {"appid": 2, "name": "Playing Game", "playtime_forever": 30}
        ]
        items = list(_fetch_steam_games("test_key", steam_id="76561198000000000"))
        assert items[0].status == ConsumptionStatus.CURRENTLY_CONSUMING
        assert items[0].rating is None

        # Test that even high playtime doesn't mean "completed"
        # (a 100-hour RPG vs a 5-hour indie game)
        mock_get_games.return_value = [
            {"appid": 3, "name": "Long Game", "playtime_forever": 6000}  # 100 hours
        ]
        items = list(_fetch_steam_games("test_key", steam_id="76561198000000000"))
        assert items[0].status == ConsumptionStatus.CURRENTLY_CONSUMING

    @patch("src.ingestion.sources.steam.get_game_details")
    @patch("src.ingestion.sources.steam.get_owned_games")
    def test__fetch_steam_games_rating_always_none(
        self, mock_get_games, mock_get_details
    ):
        """Test that ratings are always None (user-provided, not inferred from playtime)."""
        mock_get_details.return_value = {}

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

    @patch("src.ingestion.sources.steam.get_game_details")
    @patch("src.ingestion.sources.steam.get_owned_games")
    def test__fetch_steam_games_min_playtime_filter(
        self, mock_get_games, mock_get_details
    ):
        """Test minimum playtime filter."""
        mock_get_games.return_value = [
            {"appid": 1, "name": "Game 1", "playtime_forever": 10},
            {"appid": 2, "name": "Game 2", "playtime_forever": 100},
            {"appid": 3, "name": "Game 3", "playtime_forever": 200},
        ]
        mock_get_details.return_value = {
            1: {"name": "Game 1"},
            2: {"name": "Game 2"},
            3: {"name": "Game 3"},
        }

        # Filter games with < 50 minutes playtime
        items = list(
            _fetch_steam_games(
                "test_key", steam_id="76561198000000000", min_playtime_minutes=50
            )
        )

        assert len(items) == 2  # Only games 2 and 3
        assert all(item.metadata["playtime_minutes"] >= 50 for item in items)

    @patch("src.ingestion.sources.steam.get_game_details")
    @patch("src.ingestion.sources.steam.get_owned_games")
    def test__fetch_steam_games_metadata(self, mock_get_games, mock_get_details):
        """Test that game metadata is properly extracted."""
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
        mock_get_details.return_value = {
            12345: {
                "name": "Test Game",
                "short_description": "A great game",
                "developers": ["Dev 1", "Dev 2"],
                "publishers": ["Pub 1"],
                "genres": [{"description": "Action"}, {"description": "Adventure"}],
                "categories": [{"description": "Single-player"}],
                "release_date": {"date": "Jan 1, 2020"},
                "website": "https://example.com",
                "metacritic": {"score": 85},
            }
        }

        items = list(_fetch_steam_games("test_key", steam_id="76561198000000000"))

        assert len(items) == 1
        metadata = items[0].metadata
        assert metadata["steam_app_id"] == "12345"
        assert metadata["playtime_minutes"] == 120
        assert metadata["playtime_hours"] == 2.0
        assert metadata["playtime_2weeks"] == 30
        assert metadata["developers"] == ["Dev 1", "Dev 2"]
        assert metadata["publishers"] == ["Pub 1"]
        assert metadata["genres"] == ["Action", "Adventure"]
        assert metadata["metacritic_score"] == 85

    @patch("src.ingestion.sources.steam.get_game_details")
    @patch("src.ingestion.sources.steam.get_owned_games")
    def test__fetch_steam_games_no_name_skipped(self, mock_get_games, mock_get_details):
        """Test that games without names are skipped."""
        mock_get_games.return_value = [
            {"appid": 12345, "name": "", "playtime_forever": 120},
            {"appid": 67890, "name": "Valid Game", "playtime_forever": 60},
        ]
        mock_get_details.return_value = {
            12345: {},  # No name in details either
            67890: {"name": "Valid Game"},
        }

        items = list(_fetch_steam_games("test_key", steam_id="76561198000000000"))

        assert len(items) == 1
        assert items[0].title == "Valid Game"

    @patch("src.ingestion.sources.steam.get_steam_id_from_vanity_url")
    @patch("src.ingestion.sources.steam.get_game_details")
    @patch("src.ingestion.sources.steam.get_owned_games")
    def test__fetch_steam_games_vanity_url(
        self, mock_get_games, mock_get_details, mock_resolve_vanity
    ):
        """Test parsing with vanity URL instead of Steam ID."""
        mock_resolve_vanity.return_value = "76561198000000000"
        mock_get_games.return_value = [
            {"appid": 12345, "name": "Test Game", "playtime_forever": 60}
        ]
        mock_get_details.return_value = {12345: {"name": "Test Game"}}

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

    @patch("src.ingestion.sources.steam.get_game_details")
    @patch("src.ingestion.sources.steam.get_owned_games")
    def test__fetch_steam_games_empty_library(self, mock_get_games, mock_get_details):
        """Test parsing with empty game library."""
        mock_get_games.return_value = []

        items = list(_fetch_steam_games("test_key", steam_id="76561198000000000"))

        assert len(items) == 0
        # Should not call get_game_details for empty library
        mock_get_details.assert_not_called()


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


class TestSteamPluginFetch:
    """Tests for SteamPlugin.fetch()."""

    @patch("src.ingestion.sources.steam.get_game_details")
    @patch("src.ingestion.sources.steam.get_owned_games")
    def test_fetch_through_plugin(
        self, mock_get_games: Mock, mock_get_details: Mock
    ) -> None:
        """Test fetching games through the plugin interface."""
        mock_get_games.return_value = [
            {"appid": 12345, "name": "Test Game", "playtime_forever": 120}
        ]
        mock_get_details.return_value = {12345: {"name": "Test Game"}}

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
        with pytest.raises(SourceError) as exc_info:
            list(plugin.fetch({"api_key": "test_key", "steam_id": "76561198000000000"}))

        assert exc_info.value.plugin_name == "steam"
        assert "API failure" in exc_info.value.message
