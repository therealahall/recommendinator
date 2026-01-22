"""Tests for Steam API integration."""

import pytest
from unittest.mock import Mock, patch, MagicMock
from datetime import date

from src.ingestion.sources.steam import (
    get_steam_id_from_vanity_url,
    get_owned_games,
    get_game_details,
    parse_steam_games,
    SteamAPIError,
)
from src.models.content import ContentType, ConsumptionStatus


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
        # Create response for batch of 20
        mock_response.json.return_value = {
            str(i): {"success": True, "data": {"name": f"Game {i}"}} for i in range(20)
        }
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response

        app_ids = list(range(25))  # 25 games should trigger 2 batches
        details = get_game_details(app_ids)

        # Should have made 2 calls (20 + 5)
        assert mock_get.call_count == 2


class TestParseSteamGames:
    """Tests for parsing Steam games into ContentItems."""

    @patch("src.ingestion.sources.steam.get_game_details")
    @patch("src.ingestion.sources.steam.get_owned_games")
    def test_parse_steam_games_basic(self, mock_get_games, mock_get_details):
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

        items = list(parse_steam_games("test_key", steam_id="76561198000000000"))

        assert len(items) == 1
        item = items[0]
        assert item.title == "Test Game"
        assert item.content_type == ContentType.VIDEO_GAME
        assert item.id == "12345"
        assert item.author is None
        assert item.status == ConsumptionStatus.COMPLETED
        assert item.rating == 2  # 2 hours = rating 2
        assert item.metadata["playtime_hours"] == 2.0
        assert item.metadata["playtime_minutes"] == 120

    @patch("src.ingestion.sources.steam.get_game_details")
    @patch("src.ingestion.sources.steam.get_owned_games")
    def test_parse_steam_games_status_mapping(self, mock_get_games, mock_get_details):
        """Test status mapping based on playtime."""
        mock_get_details.return_value = {}

        # Test unread (0 minutes)
        mock_get_games.return_value = [
            {"appid": 1, "name": "Unread Game", "playtime_forever": 0}
        ]
        items = list(parse_steam_games("test_key", steam_id="76561198000000000"))
        assert items[0].status == ConsumptionStatus.UNREAD
        assert items[0].rating is None

        # Test currently consuming (< 1 hour)
        mock_get_games.return_value = [
            {"appid": 2, "name": "Playing Game", "playtime_forever": 30}
        ]
        items = list(parse_steam_games("test_key", steam_id="76561198000000000"))
        assert items[0].status == ConsumptionStatus.CURRENTLY_CONSUMING
        assert items[0].rating is None  # < 1 hour = no rating

        # Test completed (1+ hours)
        mock_get_games.return_value = [
            {"appid": 3, "name": "Completed Game", "playtime_forever": 60}
        ]
        items = list(parse_steam_games("test_key", steam_id="76561198000000000"))
        assert items[0].status == ConsumptionStatus.COMPLETED

    @patch("src.ingestion.sources.steam.get_game_details")
    @patch("src.ingestion.sources.steam.get_owned_games")
    def test_parse_steam_games_rating_estimation(
        self, mock_get_games, mock_get_details
    ):
        """Test rating estimation based on playtime."""
        mock_get_details.return_value = {}

        test_cases = [
            (1, None),  # < 1 hour = no rating
            (60, 2),  # 1 hour = rating 2
            (300, 3),  # 5 hours = rating 3
            (600, 4),  # 10 hours = rating 4
            (1200, 5),  # 20 hours = rating 5
        ]

        for playtime_minutes, expected_rating in test_cases:
            mock_get_games.return_value = [
                {
                    "appid": 12345,
                    "name": "Test Game",
                    "playtime_forever": playtime_minutes,
                }
            ]
            items = list(parse_steam_games("test_key", steam_id="76561198000000000"))
            assert (
                items[0].rating == expected_rating
            ), f"Expected rating {expected_rating} for {playtime_minutes} minutes"

    @patch("src.ingestion.sources.steam.get_game_details")
    @patch("src.ingestion.sources.steam.get_owned_games")
    def test_parse_steam_games_min_playtime_filter(
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
            parse_steam_games(
                "test_key", steam_id="76561198000000000", min_playtime_minutes=50
            )
        )

        assert len(items) == 2  # Only games 2 and 3
        assert all(item.metadata["playtime_minutes"] >= 50 for item in items)

    @patch("src.ingestion.sources.steam.get_game_details")
    @patch("src.ingestion.sources.steam.get_owned_games")
    def test_parse_steam_games_metadata(self, mock_get_games, mock_get_details):
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

        items = list(parse_steam_games("test_key", steam_id="76561198000000000"))

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
    def test_parse_steam_games_no_name_skipped(self, mock_get_games, mock_get_details):
        """Test that games without names are skipped."""
        mock_get_games.return_value = [
            {"appid": 12345, "name": "", "playtime_forever": 120},
            {"appid": 67890, "name": "Valid Game", "playtime_forever": 60},
        ]
        mock_get_details.return_value = {
            12345: {},  # No name in details either
            67890: {"name": "Valid Game"},
        }

        items = list(parse_steam_games("test_key", steam_id="76561198000000000"))

        assert len(items) == 1
        assert items[0].title == "Valid Game"

    @patch("src.ingestion.sources.steam.get_steam_id_from_vanity_url")
    @patch("src.ingestion.sources.steam.get_game_details")
    @patch("src.ingestion.sources.steam.get_owned_games")
    def test_parse_steam_games_vanity_url(
        self, mock_get_games, mock_get_details, mock_resolve_vanity
    ):
        """Test parsing with vanity URL instead of Steam ID."""
        mock_resolve_vanity.return_value = "76561198000000000"
        mock_get_games.return_value = [
            {"appid": 12345, "name": "Test Game", "playtime_forever": 60}
        ]
        mock_get_details.return_value = {12345: {"name": "Test Game"}}

        items = list(parse_steam_games("test_key", vanity_url="testuser"))

        assert len(items) == 1
        mock_resolve_vanity.assert_called_once_with("test_key", "testuser")

    @patch("src.ingestion.sources.steam.get_steam_id_from_vanity_url")
    def test_parse_steam_games_vanity_url_failure(self, mock_resolve_vanity):
        """Test parsing when vanity URL resolution fails."""
        mock_resolve_vanity.return_value = None

        with pytest.raises(SteamAPIError, match="Could not resolve Steam ID"):
            list(parse_steam_games("test_key", vanity_url="nonexistent"))

    def test_parse_steam_games_no_id_or_vanity(self):
        """Test parsing without Steam ID or vanity URL."""
        with pytest.raises(
            ValueError, match="Either steam_id or vanity_url must be provided"
        ):
            list(parse_steam_games("test_key"))

    @patch("src.ingestion.sources.steam.get_game_details")
    @patch("src.ingestion.sources.steam.get_owned_games")
    def test_parse_steam_games_empty_library(self, mock_get_games, mock_get_details):
        """Test parsing with empty game library."""
        mock_get_games.return_value = []

        items = list(parse_steam_games("test_key", steam_id="76561198000000000"))

        assert len(items) == 0
        # Should not call get_game_details for empty library
        mock_get_details.assert_not_called()
