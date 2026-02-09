"""Tests for GOG.com API integration."""

from unittest.mock import Mock, patch

import pytest
import requests

from src.ingestion.plugin_base import SourceError, SourcePlugin
from src.ingestion.sources.gog import (
    GogAPIError,
    GogPlugin,
    get_multiple_product_details,
    get_owned_games,
    get_product_details,
    get_wishlist_product_ids,
    refresh_access_token,
)
from src.models.content import ConsumptionStatus, ContentType


class TestRefreshAccessToken:
    """Tests for GOG OAuth token refresh."""

    @patch("src.ingestion.sources.gog.requests.get")
    def test_refresh_success(self, mock_get: Mock) -> None:
        """Test successful token refresh."""
        mock_response = Mock()
        mock_response.json.return_value = {
            "access_token": "new_access_token",
            "refresh_token": "new_refresh_token",
        }
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response

        result = refresh_access_token("old_refresh_token")

        assert result["access_token"] == "new_access_token"
        assert result["refresh_token"] == "new_refresh_token"
        mock_get.assert_called_once()
        call_args = mock_get.call_args
        assert "auth.gog.com/token" in call_args[0][0]
        assert call_args[1]["params"]["grant_type"] == "refresh_token"
        assert call_args[1]["params"]["refresh_token"] == "old_refresh_token"

    @patch("src.ingestion.sources.gog.requests.get")
    def test_refresh_expired_token(self, mock_get: Mock) -> None:
        """Test token refresh with expired/invalid token."""
        mock_get.side_effect = requests.RequestException("401 Unauthorized")

        with pytest.raises(GogAPIError, match="Failed to refresh access token"):
            refresh_access_token("expired_token")

    @patch("src.ingestion.sources.gog.requests.get")
    def test_refresh_network_error(self, mock_get: Mock) -> None:
        """Test token refresh with network error."""
        mock_get.side_effect = requests.RequestException("Connection refused")

        with pytest.raises(GogAPIError, match="Failed to refresh access token"):
            refresh_access_token("some_token")

    @patch("src.ingestion.sources.gog.requests.get")
    def test_refresh_missing_access_token(self, mock_get: Mock) -> None:
        """Test token refresh when response is missing access_token."""
        mock_response = Mock()
        mock_response.json.return_value = {"refresh_token": "new_refresh"}
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response

        with pytest.raises(GogAPIError, match="Response missing access_token"):
            refresh_access_token("some_token")

    @patch("src.ingestion.sources.gog.requests.get")
    def test_refresh_preserves_old_refresh_token_when_not_returned(
        self, mock_get: Mock
    ) -> None:
        """Test that old refresh token is kept when response omits it."""
        mock_response = Mock()
        mock_response.json.return_value = {
            "access_token": "new_access",
        }
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response

        result = refresh_access_token("original_refresh")

        assert result["access_token"] == "new_access"
        assert result["refresh_token"] == "original_refresh"


class TestGetOwnedGames:
    """Tests for fetching owned games from GOG."""

    @patch("src.ingestion.sources.gog.requests.get")
    def test_single_page(self, mock_get: Mock) -> None:
        """Test fetching owned games when all fit on one page."""
        mock_response = Mock()
        mock_response.json.return_value = {
            "totalPages": 1,
            "products": [
                {"id": 1234, "title": "Game One", "slug": "game-one"},
                {"id": 5678, "title": "Game Two", "slug": "game-two"},
            ],
        }
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response

        result = get_owned_games("test_access_token")

        assert len(result) == 2
        assert result[0]["id"] == 1234
        assert result[1]["title"] == "Game Two"
        mock_get.assert_called_once()
        call_args = mock_get.call_args
        assert call_args[1]["headers"]["Authorization"] == "Bearer test_access_token"

    @patch("src.ingestion.sources.gog.requests.get")
    def test_multiple_pages(self, mock_get: Mock) -> None:
        """Test paginating through multiple pages of owned games."""
        page1_response = Mock()
        page1_response.json.return_value = {
            "totalPages": 2,
            "products": [{"id": 1, "title": "Game 1"}],
        }
        page1_response.raise_for_status = Mock()

        page2_response = Mock()
        page2_response.json.return_value = {
            "totalPages": 2,
            "products": [{"id": 2, "title": "Game 2"}],
        }
        page2_response.raise_for_status = Mock()

        mock_get.side_effect = [page1_response, page2_response]

        result = get_owned_games("test_token", rate_limit_seconds=0)

        assert len(result) == 2
        assert result[0]["id"] == 1
        assert result[1]["id"] == 2
        assert mock_get.call_count == 2

    @patch("src.ingestion.sources.gog.requests.get")
    def test_empty_library(self, mock_get: Mock) -> None:
        """Test fetching when the library is empty."""
        mock_response = Mock()
        mock_response.json.return_value = {
            "totalPages": 1,
            "products": [],
        }
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response

        result = get_owned_games("test_token")

        assert result == []

    @patch("src.ingestion.sources.gog.requests.get")
    def test_api_error(self, mock_get: Mock) -> None:
        """Test handling API error during fetch."""
        mock_get.side_effect = requests.RequestException("Server error")

        with pytest.raises(GogAPIError, match="Failed to fetch owned games"):
            get_owned_games("test_token")

    @patch("src.ingestion.sources.gog.requests.get")
    def test_auth_header_sent(self, mock_get: Mock) -> None:
        """Test that Bearer auth header is sent correctly."""
        mock_response = Mock()
        mock_response.json.return_value = {"totalPages": 1, "products": []}
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response

        get_owned_games("my_token_123")

        call_args = mock_get.call_args
        assert call_args[1]["headers"]["Authorization"] == "Bearer my_token_123"


class TestGetWishlistProductIds:
    """Tests for fetching wishlist product IDs."""

    @patch("src.ingestion.sources.gog.requests.get")
    def test_success(self, mock_get: Mock) -> None:
        """Test successful wishlist fetch."""
        mock_response = Mock()
        mock_response.json.return_value = {
            "wishlist": {"12345": True, "67890": True, "11111": True}
        }
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response

        result = get_wishlist_product_ids("test_token")

        assert len(result) == 3
        assert set(result) == {12345, 67890, 11111}

    @patch("src.ingestion.sources.gog.requests.get")
    def test_empty_wishlist(self, mock_get: Mock) -> None:
        """Test fetching an empty wishlist."""
        mock_response = Mock()
        mock_response.json.return_value = {"wishlist": {}}
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response

        result = get_wishlist_product_ids("test_token")

        assert result == []

    @patch("src.ingestion.sources.gog.requests.get")
    def test_api_error(self, mock_get: Mock) -> None:
        """Test handling API error."""
        mock_get.side_effect = requests.RequestException("Forbidden")

        with pytest.raises(GogAPIError, match="Failed to fetch wishlist"):
            get_wishlist_product_ids("test_token")


class TestGetProductDetails:
    """Tests for fetching individual product details."""

    @patch("src.ingestion.sources.gog.requests.get")
    def test_success(self, mock_get: Mock) -> None:
        """Test successful product detail fetch."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "id": 12345,
            "title": "The Witcher 3",
            "slug": "the-witcher-3",
            "genres": [{"name": "RPG"}, {"name": "Adventure"}],
            "developers": ["CD Projekt Red"],
            "publishers": ["CD Projekt"],
        }
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response

        result = get_product_details(12345)

        assert result is not None
        assert result["title"] == "The Witcher 3"
        assert result["developers"] == ["CD Projekt Red"]

    @patch("src.ingestion.sources.gog.requests.get")
    def test_not_found_returns_none(self, mock_get: Mock) -> None:
        """Test that 404 returns None instead of raising."""
        mock_response = Mock()
        mock_response.status_code = 404
        mock_get.return_value = mock_response

        result = get_product_details(99999)

        assert result is None

    @patch("src.ingestion.sources.gog.requests.get")
    def test_api_error(self, mock_get: Mock) -> None:
        """Test handling of non-404 API errors."""
        mock_get.side_effect = requests.RequestException("Server error")

        with pytest.raises(GogAPIError, match="Failed to fetch product details"):
            get_product_details(12345)


class TestGetMultipleProductDetails:
    """Tests for batch product detail fetching."""

    @patch("src.ingestion.sources.gog.get_product_details")
    def test_fetches_all_products(self, mock_get_details: Mock) -> None:
        """Test that all products are fetched."""
        mock_get_details.side_effect = [
            {"id": 1, "title": "Game 1"},
            {"id": 2, "title": "Game 2"},
        ]

        result = get_multiple_product_details([1, 2], rate_limit_seconds=0)

        assert len(result) == 2
        assert result[1]["title"] == "Game 1"
        assert result[2]["title"] == "Game 2"

    @patch("src.ingestion.sources.gog.get_product_details")
    def test_skips_none_results(self, mock_get_details: Mock) -> None:
        """Test that None results (404s) are excluded."""
        mock_get_details.side_effect = [
            {"id": 1, "title": "Game 1"},
            None,  # 404
            {"id": 3, "title": "Game 3"},
        ]

        result = get_multiple_product_details([1, 2, 3], rate_limit_seconds=0)

        assert len(result) == 2
        assert 2 not in result

    @patch("src.ingestion.sources.gog.get_product_details")
    def test_progress_callback(self, mock_get_details: Mock) -> None:
        """Test that progress callback is called."""
        mock_get_details.return_value = {"id": 1, "title": "Game"}
        callback = Mock()

        get_multiple_product_details(
            [1, 2], rate_limit_seconds=0, progress_callback=callback
        )

        assert callback.call_count == 2
        callback.assert_any_call(1, 2)
        callback.assert_any_call(2, 2)


class TestGogPluginProperties:
    """Tests for GogPlugin metadata properties."""

    def test_is_source_plugin(self) -> None:
        """Test that GogPlugin is a SourcePlugin subclass."""
        plugin = GogPlugin()
        assert isinstance(plugin, SourcePlugin)

    def test_name(self) -> None:
        """Test plugin name identifier."""
        plugin = GogPlugin()
        assert plugin.name == "gog"

    def test_display_name(self) -> None:
        """Test human-readable display name."""
        plugin = GogPlugin()
        assert plugin.display_name == "GOG"

    def test_content_types(self) -> None:
        """Test that plugin provides video games."""
        plugin = GogPlugin()
        assert plugin.content_types == [ContentType.VIDEO_GAME]

    def test_requires_api_key(self) -> None:
        """Test that plugin requires credentials (refresh token)."""
        plugin = GogPlugin()
        assert plugin.requires_api_key is True

    def test_requires_network(self) -> None:
        """Test that plugin requires network access."""
        plugin = GogPlugin()
        assert plugin.requires_network is True

    def test_config_schema(self) -> None:
        """Test configuration schema fields."""
        plugin = GogPlugin()
        schema = plugin.get_config_schema()

        field_names = [field.name for field in schema]
        assert "refresh_token" in field_names
        assert "include_wishlist" in field_names
        assert "enrich_wishlist" in field_names

        token_field = next(field for field in schema if field.name == "refresh_token")
        assert token_field.required is True
        assert token_field.sensitive is True

    def test_get_source_identifier(self) -> None:
        """Test source identifier matches plugin name."""
        plugin = GogPlugin()
        assert plugin.get_source_identifier() == "gog"

    def test_get_info(self) -> None:
        """Test plugin info includes all metadata."""
        plugin = GogPlugin()
        info = plugin.get_info()

        assert info.name == "gog"
        assert info.display_name == "GOG"
        assert info.content_types == [ContentType.VIDEO_GAME]
        assert info.requires_api_key is True
        assert info.requires_network is True


class TestGogPluginValidation:
    """Tests for GogPlugin config validation."""

    def test_validate_valid_config(self) -> None:
        """Test validation passes with valid config."""
        plugin = GogPlugin()
        errors = plugin.validate_config({"refresh_token": "valid_token_here"})
        assert errors == []

    def test_validate_missing_refresh_token(self) -> None:
        """Test validation fails when refresh_token is missing."""
        plugin = GogPlugin()
        errors = plugin.validate_config({})

        assert len(errors) == 1
        assert "'refresh_token' is required" in errors[0]

    def test_validate_empty_refresh_token(self) -> None:
        """Test validation fails when refresh_token is empty."""
        plugin = GogPlugin()
        errors = plugin.validate_config({"refresh_token": ""})

        assert len(errors) == 1
        assert "'refresh_token' is required" in errors[0]

    def test_validate_whitespace_refresh_token(self) -> None:
        """Test validation fails when refresh_token is whitespace only."""
        plugin = GogPlugin()
        errors = plugin.validate_config({"refresh_token": "   "})

        assert len(errors) == 1
        assert "'refresh_token' is required" in errors[0]


class TestGogPluginTransformConfig:
    """Tests for GogPlugin.transform_config."""

    def test_strips_whitespace(self) -> None:
        """Test that whitespace is stripped from refresh_token."""
        result = GogPlugin.transform_config({"refresh_token": "  my_token  "})
        assert result["refresh_token"] == "my_token"

    def test_defaults_include_wishlist(self) -> None:
        """Test that include_wishlist defaults to True."""
        result = GogPlugin.transform_config({"refresh_token": "token"})
        assert result["include_wishlist"] is True

    def test_defaults_enrich_wishlist(self) -> None:
        """Test that enrich_wishlist defaults to True."""
        result = GogPlugin.transform_config({"refresh_token": "token"})
        assert result["enrich_wishlist"] is True

    def test_respects_explicit_false(self) -> None:
        """Test that explicit False values are preserved."""
        result = GogPlugin.transform_config(
            {
                "refresh_token": "token",
                "include_wishlist": False,
                "enrich_wishlist": False,
            }
        )
        assert result["include_wishlist"] is False
        assert result["enrich_wishlist"] is False


class TestGogPluginFetch:
    """Tests for GogPlugin.fetch()."""

    @patch("src.ingestion.sources.gog.get_wishlist_product_ids")
    @patch("src.ingestion.sources.gog.get_owned_games")
    @patch("src.ingestion.sources.gog.refresh_access_token")
    def test_fetch_owned_games(
        self,
        mock_refresh: Mock,
        mock_owned: Mock,
        mock_wishlist: Mock,
    ) -> None:
        """Test fetching owned games through the plugin interface."""
        mock_refresh.return_value = {
            "access_token": "access",
            "refresh_token": "refresh",
        }
        mock_owned.return_value = [
            {"id": 1234, "title": "The Witcher 3", "slug": "the-witcher-3"},
        ]
        mock_wishlist.return_value = []

        plugin = GogPlugin()
        items = list(
            plugin.fetch({"refresh_token": "my_token", "include_wishlist": True})
        )

        assert len(items) == 1
        assert items[0].title == "The Witcher 3"
        assert items[0].content_type == ContentType.VIDEO_GAME
        assert items[0].id == "1234"
        assert items[0].source == "gog"
        assert items[0].status == ConsumptionStatus.UNREAD
        assert items[0].rating is None
        assert items[0].author is None

    @patch("src.ingestion.sources.gog.get_multiple_product_details")
    @patch("src.ingestion.sources.gog.get_wishlist_product_ids")
    @patch("src.ingestion.sources.gog.get_owned_games")
    @patch("src.ingestion.sources.gog.refresh_access_token")
    def test_fetch_with_wishlist(
        self,
        mock_refresh: Mock,
        mock_owned: Mock,
        mock_wishlist: Mock,
        mock_details: Mock,
    ) -> None:
        """Test fetching both owned and wishlisted games."""
        mock_refresh.return_value = {
            "access_token": "access",
            "refresh_token": "refresh",
        }
        mock_owned.return_value = [
            {"id": 100, "title": "Owned Game"},
        ]
        mock_wishlist.return_value = [200]
        mock_details.return_value = {
            200: {
                "id": 200,
                "title": "Wishlisted Game",
                "slug": "wishlisted-game",
                "genres": [{"name": "RPG"}, {"name": "Adventure"}],
                "developers": ["Dev Studio"],
                "publishers": ["Publisher Co"],
                "description": {"full": "A great game about adventure."},
            }
        }

        plugin = GogPlugin()
        items = list(
            plugin.fetch(
                {
                    "refresh_token": "token",
                    "include_wishlist": True,
                    "enrich_wishlist": True,
                }
            )
        )

        assert len(items) == 2

        owned_item = items[0]
        assert owned_item.title == "Owned Game"
        assert owned_item.metadata["gog_owned"] is True
        assert owned_item.metadata["gog_wishlisted"] is False

        wishlist_item = items[1]
        assert wishlist_item.title == "Wishlisted Game"
        assert wishlist_item.metadata["gog_owned"] is False
        assert wishlist_item.metadata["gog_wishlisted"] is True
        assert wishlist_item.metadata["genres"] == ["RPG", "Adventure"]
        assert wishlist_item.metadata["developers"] == ["Dev Studio"]

    @patch("src.ingestion.sources.gog.get_wishlist_product_ids")
    @patch("src.ingestion.sources.gog.get_owned_games")
    @patch("src.ingestion.sources.gog.refresh_access_token")
    def test_fetch_wishlist_excluded(
        self,
        mock_refresh: Mock,
        mock_owned: Mock,
        mock_wishlist: Mock,
    ) -> None:
        """Test that wishlist is not fetched when include_wishlist is False."""
        mock_refresh.return_value = {
            "access_token": "access",
            "refresh_token": "refresh",
        }
        mock_owned.return_value = [
            {"id": 100, "title": "Owned Game"},
        ]

        plugin = GogPlugin()
        items = list(
            plugin.fetch({"refresh_token": "token", "include_wishlist": False})
        )

        assert len(items) == 1
        assert items[0].title == "Owned Game"
        mock_wishlist.assert_not_called()

    @patch("src.ingestion.sources.gog.get_wishlist_product_ids")
    @patch("src.ingestion.sources.gog.get_owned_games")
    @patch("src.ingestion.sources.gog.refresh_access_token")
    def test_deduplication_owned_and_wishlisted(
        self,
        mock_refresh: Mock,
        mock_owned: Mock,
        mock_wishlist: Mock,
    ) -> None:
        """Test that games both owned and wishlisted are only yielded once (owned)."""
        mock_refresh.return_value = {
            "access_token": "access",
            "refresh_token": "refresh",
        }
        mock_owned.return_value = [
            {"id": 100, "title": "Duplicate Game"},
        ]
        # Same game ID appears in wishlist
        mock_wishlist.return_value = [100, 200]

        plugin = GogPlugin()
        # Without enrichment, product 200 has no title → skipped
        items = list(
            plugin.fetch(
                {
                    "refresh_token": "token",
                    "include_wishlist": True,
                    "enrich_wishlist": False,
                }
            )
        )

        # Only the owned game should appear (wishlist ID 100 is filtered,
        # ID 200 is skipped because no title without enrichment)
        assert len(items) == 1
        assert items[0].metadata["gog_owned"] is True

    @patch("src.ingestion.sources.gog.get_owned_games")
    @patch("src.ingestion.sources.gog.refresh_access_token")
    def test_skip_titleless_games(
        self,
        mock_refresh: Mock,
        mock_owned: Mock,
    ) -> None:
        """Test that games without titles are skipped."""
        mock_refresh.return_value = {
            "access_token": "access",
            "refresh_token": "refresh",
        }
        mock_owned.return_value = [
            {"id": 1, "title": ""},
            {"id": 2, "title": None},
            {"id": 3, "title": "Valid Game"},
        ]

        plugin = GogPlugin()
        items = list(
            plugin.fetch({"refresh_token": "token", "include_wishlist": False})
        )

        assert len(items) == 1
        assert items[0].title == "Valid Game"

    @patch("src.ingestion.sources.gog.get_owned_games")
    @patch("src.ingestion.sources.gog.refresh_access_token")
    def test_metadata_fields(
        self,
        mock_refresh: Mock,
        mock_owned: Mock,
    ) -> None:
        """Test that metadata fields are populated correctly."""
        mock_refresh.return_value = {
            "access_token": "access",
            "refresh_token": "refresh",
        }
        mock_owned.return_value = [
            {
                "id": 42,
                "title": "Cyberpunk",
                "slug": "cyberpunk-2077",
                "category": "Game",
                "globalReleaseDate": "2020-12-10",
                "genres": ["RPG", "Action"],
                "tags": ["Open World", "Sci-Fi"],
                "dlcCount": 2,
                "worksOn": {"Windows": True, "Mac": False, "Linux": True},
            },
        ]

        plugin = GogPlugin()
        items = list(
            plugin.fetch({"refresh_token": "token", "include_wishlist": False})
        )

        metadata = items[0].metadata
        assert metadata["gog_product_id"] == "42"
        assert metadata["gog_owned"] is True
        assert metadata["gog_wishlisted"] is False
        assert metadata["slug"] == "cyberpunk-2077"
        assert metadata["url"] == "https://www.gog.com/game/cyberpunk-2077"
        assert metadata["category"] == "Game"
        assert metadata["release_date"] == "2020-12-10"
        assert metadata["genres"] == ["RPG", "Action"]
        assert metadata["tags"] == ["Open World", "Sci-Fi"]
        assert metadata["dlc_count"] == 2
        assert metadata["platforms"]["windows"] is True
        assert metadata["platforms"]["mac"] is False
        assert metadata["platforms"]["linux"] is True

    @patch("src.ingestion.sources.gog.get_owned_games")
    @patch("src.ingestion.sources.gog.refresh_access_token")
    def test_progress_callback(
        self,
        mock_refresh: Mock,
        mock_owned: Mock,
    ) -> None:
        """Test that progress callback is invoked during fetch."""
        mock_refresh.return_value = {
            "access_token": "access",
            "refresh_token": "refresh",
        }
        mock_owned.return_value = [
            {"id": 1, "title": "Game One"},
            {"id": 2, "title": "Game Two"},
        ]

        plugin = GogPlugin()
        callback = Mock()
        items = list(
            plugin.fetch(
                {"refresh_token": "token", "include_wishlist": False},
                progress_callback=callback,
            )
        )

        assert len(items) == 2
        assert callback.call_count > 0

    @patch("src.ingestion.sources.gog.refresh_access_token")
    def test_api_error_raises_source_error(self, mock_refresh: Mock) -> None:
        """Test that GOG API errors are wrapped in SourceError."""
        mock_refresh.side_effect = GogAPIError("Token expired")

        plugin = GogPlugin()
        with pytest.raises(SourceError) as exc_info:
            list(plugin.fetch({"refresh_token": "bad_token"}))

        assert exc_info.value.plugin_name == "gog"
        assert "Token expired" in exc_info.value.message

    @patch("src.ingestion.sources.gog.get_owned_games")
    @patch("src.ingestion.sources.gog.refresh_access_token")
    def test_all_owned_games_are_unread(
        self,
        mock_refresh: Mock,
        mock_owned: Mock,
    ) -> None:
        """Test that all owned games have UNREAD status (GOG has no playtime data)."""
        mock_refresh.return_value = {
            "access_token": "access",
            "refresh_token": "refresh",
        }
        mock_owned.return_value = [
            {"id": 1, "title": "Game 1"},
            {"id": 2, "title": "Game 2"},
            {"id": 3, "title": "Game 3"},
        ]

        plugin = GogPlugin()
        items = list(
            plugin.fetch({"refresh_token": "token", "include_wishlist": False})
        )

        for item in items:
            assert item.status == ConsumptionStatus.UNREAD
            assert item.rating is None
