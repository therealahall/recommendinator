"""Tests for GOG.com API integration."""

import logging
import traceback
from unittest.mock import Mock, patch

import pytest
import requests

from src.ingestion.plugin_base import SourceError
from src.ingestion.sources.gog.gog import (
    GOG_AUTH_URL,
    GOG_CLIENT_ID,
    GOG_CLIENT_SECRET,
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

    @patch("src.ingestion.sources.gog.gog.requests.get")
    def test_refresh_preserves_old_refresh_token_when_not_returned(
        self, mock_get: Mock
    ) -> None:
        """Test that old refresh token is kept when response omits it."""
        mock_response = Mock(spec=requests.Response)
        mock_response.json.return_value = {
            "access_token": "new_access",
        }
        mock_get.return_value = mock_response

        result = refresh_access_token("original_refresh")

        assert result["access_token"] == "new_access"
        assert result["refresh_token"] == "original_refresh"


class TestGetOwnedGames:
    """Tests for fetching owned games from GOG."""

    @patch("src.ingestion.sources.gog.gog.requests.get")
    def test_multiple_pages(self, mock_get: Mock) -> None:
        """Test paginating through multiple pages of owned games."""
        page1_response = Mock(spec=requests.Response)
        page1_response.json.return_value = {
            "totalPages": 2,
            "products": [{"id": 1, "title": "Game 1"}],
        }

        page2_response = Mock(spec=requests.Response)
        page2_response.json.return_value = {
            "totalPages": 2,
            "products": [{"id": 2, "title": "Game 2"}],
        }

        mock_get.side_effect = [page1_response, page2_response]

        result = get_owned_games("test_token", rate_limit_seconds=0)

        assert len(result) == 2
        assert result[0]["id"] == 1
        assert result[1]["id"] == 2
        assert mock_get.call_count == 2


class TestGetWishlistProductIds:
    """Tests for fetching wishlist product IDs."""

    @patch("src.ingestion.sources.gog.gog.requests.get")
    def test_success(self, mock_get: Mock) -> None:
        """Test successful wishlist fetch."""
        mock_response = Mock(spec=requests.Response)
        mock_response.json.return_value = {
            "wishlist": {"12345": True, "67890": True, "11111": True}
        }
        mock_get.return_value = mock_response

        result = get_wishlist_product_ids("test_token")

        assert len(result) == 3
        assert set(result) == {12345, 67890, 11111}


class TestGetProductDetails:
    """Tests for fetching individual product details."""

    @patch("src.ingestion.sources.gog.gog.requests.get")
    def test_not_found_returns_none(self, mock_get: Mock) -> None:
        """Test that 404 returns None instead of raising."""
        mock_response = Mock(spec=requests.Response)
        mock_response.status_code = 404
        mock_get.return_value = mock_response

        result = get_product_details(99999)

        assert result is None


class TestGetMultipleProductDetails:
    """Tests for batch product detail fetching."""

    @patch("src.ingestion.sources.gog.gog.get_product_details")
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


class TestGogPluginValidation:
    """Tests for GogPlugin config validation."""

    def test_validate_missing_refresh_token(self) -> None:
        """Test validation fails when refresh_token is missing."""
        plugin = GogPlugin()
        errors = plugin.validate_config({})

        assert len(errors) == 1
        assert "'refresh_token' is required" in errors[0]

    def test_validate_missing_token_passes_when_in_db(self) -> None:
        """Regression: CLI sync fails when refresh_token is in DB but not config.

        Bug: validate_config() checked for refresh_token in the config dict
        without considering that the token might be stored in the encrypted
        credential database (used by the web UI's OAuth flow). This caused
        CLI sync to fail with "'refresh_token' is required" even though
        resolve_inputs() would inject the DB credential before fetch().

        Root cause: validate_config() had no awareness of DB-stored
        credentials, so it rejected configs where sensitive fields were
        absent from the YAML but present in the credential store.

        Fix: validate_config() now accepts optional storage and user_id
        parameters. When a required sensitive field is missing from config
        but a credential exists in DB for that source, validation passes.
        """
        plugin = GogPlugin()
        mock_storage = Mock()
        mock_storage.credentials.get_for_source.return_value = {
            "refresh_token": "db_stored_token"
        }

        # Config has no refresh_token, but DB does — should pass
        errors = plugin.validate_config(
            {"_source_id": "my_gog"},
            storage=mock_storage,
            user_id=1,
        )
        assert errors == []
        mock_storage.credentials.get_for_source.assert_called_once_with(1, "my_gog")


class TestGogPluginTransformConfig:
    """Tests for GogPlugin.transform_config."""

    def test_strips_whitespace(self) -> None:
        """Test that whitespace is stripped from refresh_token."""
        result = GogPlugin.transform_config({"refresh_token": "  my_token  "})
        assert result["refresh_token"] == "my_token"


class TestGogPluginFetch:
    """Tests for GogPlugin.fetch()."""

    @patch("src.ingestion.sources.gog.gog.get_wishlist_product_ids")
    @patch("src.ingestion.sources.gog.gog.get_owned_games")
    @patch("src.ingestion.sources.gog.gog.refresh_access_token")
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

    @patch("src.ingestion.sources.gog.gog.get_multiple_product_details")
    @patch("src.ingestion.sources.gog.gog.get_wishlist_product_ids")
    @patch("src.ingestion.sources.gog.gog.get_owned_games")
    @patch("src.ingestion.sources.gog.gog.refresh_access_token")
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

    @patch("src.ingestion.sources.gog.gog.get_wishlist_product_ids")
    @patch("src.ingestion.sources.gog.gog.get_owned_games")
    @patch("src.ingestion.sources.gog.gog.refresh_access_token")
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

    @patch("src.ingestion.sources.gog.gog.get_wishlist_product_ids")
    @patch("src.ingestion.sources.gog.gog.get_owned_games")
    @patch("src.ingestion.sources.gog.gog.refresh_access_token")
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

    @patch("src.ingestion.sources.gog.gog.get_owned_games")
    @patch("src.ingestion.sources.gog.gog.refresh_access_token")
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

    @patch("src.ingestion.sources.gog.gog.get_owned_games")
    @patch("src.ingestion.sources.gog.gog.refresh_access_token")
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
        assert metadata["platforms"] == ["Windows", "Linux"]

    @patch("src.ingestion.sources.gog.gog.get_owned_games")
    @patch("src.ingestion.sources.gog.gog.refresh_access_token")
    def test_platforms_omitted_when_no_platform_is_supported_regression(
        self,
        mock_refresh: Mock,
        mock_owned: Mock,
    ) -> None:
        """``worksOn`` with nothing set writes no platforms at all.

        Bug reported: GOG wrote ``platforms`` as a per-platform flag dict
        while every other source — and the ``platforms`` detail column —
        uses a list of names, so a GOG game exported a Python repr into the
        CSV ``platform`` cell and re-imported that literal string.

        Root cause: the flag dict was stored verbatim. It was also always
        truthy, so a game GOG reports as running nowhere still claimed a
        platform value.

        Fix: keep only the platforms reported as supported, as a list of
        their names, and write nothing when none are.
        """
        mock_refresh.return_value = {
            "access_token": "access",
            "refresh_token": "refresh",
        }
        mock_owned.return_value = [
            {
                "id": 43,
                "title": "Unsupported Everywhere",
                "worksOn": {"Windows": False, "Mac": False, "Linux": False},
            },
        ]

        plugin = GogPlugin()
        items = list(
            plugin.fetch({"refresh_token": "token", "include_wishlist": False})
        )

        assert "platforms" not in items[0].metadata

    @patch("src.ingestion.sources.gog.gog.get_multiple_product_details")
    @patch("src.ingestion.sources.gog.gog.get_wishlist_product_ids")
    @patch("src.ingestion.sources.gog.gog.get_owned_games")
    @patch("src.ingestion.sources.gog.gog.refresh_access_token")
    def test_company_shapes_all_reduce_to_names(
        self,
        mock_refresh: Mock,
        mock_owned: Mock,
        mock_wishlist: Mock,
        mock_details: Mock,
    ) -> None:
        """Every shape GOG names a company in leaves the plugin as text.

        ``developer`` and ``publisher`` are text columns, which refuse a
        mapping outright, so a product describing its companies as objects
        rather than bare names has to be reduced here or its whole row fails
        the sync. Both shapes appear in the fixtures this file already had, so
        a nameless object and a lone object standing in for the list are
        covered beside them.
        """
        mock_refresh.return_value = {
            "access_token": "access",
            "refresh_token": "refresh",
        }
        mock_owned.return_value = []
        mock_wishlist.return_value = [200]
        mock_details.return_value = {
            200: {
                "id": 200,
                "title": "Wishlisted Game",
                "developers": ["Bare Name", {"name": "Object Name"}, {"slug": "no"}],
                "publishers": {"name": "Lone Object"},
            }
        }

        plugin = GogPlugin()
        items = list(plugin.fetch({"refresh_token": "token"}))

        assert items[0].metadata["developers"] == ["Bare Name", "Object Name"]
        assert items[0].metadata["publishers"] == ["Lone Object"]

    @patch("src.ingestion.sources.gog.gog.refresh_access_token")
    def test_api_error_raises_source_error(self, mock_refresh: Mock) -> None:
        """Test that GOG API errors are wrapped in SourceError."""
        mock_refresh.side_effect = GogAPIError("Token expired")

        plugin = GogPlugin()
        with pytest.raises(SourceError) as exc_info:
            list(plugin.fetch({"refresh_token": "bad_token"}))

        assert exc_info.value.plugin_name == "gog"
        assert "Token expired" in exc_info.value.message

    @patch("src.ingestion.sources.gog.gog.get_owned_games")
    @patch("src.ingestion.sources.gog.gog.refresh_access_token")
    def test_rotated_refresh_token_triggers_callback(
        self,
        mock_refresh: Mock,
        mock_owned: Mock,
    ) -> None:
        """Regression test: rotated GOG refresh tokens must be persisted.

        Bug: When GOG returns a new refresh_token during sync (token rotation),
        the updated token was discarded. If the old token expired before the
        next sync, the user had to re-authenticate.

        Root cause: _fetch_gog_games only used the access_token from the
        refresh response and ignored the new refresh_token.

        Fix: When the refresh_token changes, call the _on_credential_rotated
        callback (injected by execute_sync) so the new token is saved to the
        credential database.
        """
        mock_refresh.return_value = {
            "access_token": "new_access",
            "refresh_token": "rotated_refresh_token",
        }
        mock_owned.return_value = [{"id": 1, "title": "Game"}]

        credential_callback = Mock()
        plugin = GogPlugin()
        list(
            plugin.fetch(
                {
                    "refresh_token": "old_refresh_token",
                    "include_wishlist": False,
                    "_on_credential_rotated": credential_callback,
                }
            )
        )

        credential_callback.assert_called_once_with(
            "refresh_token", "rotated_refresh_token"
        )


_EXPIRED_TOKEN = "gog-refresh-token-4f2c9a"


def _expired_token_response() -> Mock:
    """A GOG token-endpoint 401 whose text is the credential-bearing URL."""
    response = Mock(spec=requests.Response)
    response.status_code = 401
    response.raise_for_status = Mock(
        side_effect=requests.HTTPError(
            "401 Client Error: Unauthorized for url: "
            f"{GOG_AUTH_URL}?client_id={GOG_CLIENT_ID}"
            f"&client_secret={GOG_CLIENT_SECRET}"
            f"&grant_type=refresh_token&refresh_token={_EXPIRED_TOKEN}",
            response=response,
        )
    )
    return response


class TestGogRefreshTokenChainRegression:
    """Regression: the refresh token reached the log via ``__cause__``.

    A scrubbed message still left ``raise ... from error``, whose cause renders
    the token URL under a caller's ``exc_info=True``. Fix: ``from None`` here,
    chain kept where the cause is clean.
    """

    @patch("src.ingestion.sources.gog.gog.requests.get")
    def test_refresh_traceback_omits_the_refresh_token(
        self, mock_get: Mock, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Nothing a caller can render off the raised error names the token."""
        mock_get.return_value = _expired_token_response()

        with caplog.at_level(logging.ERROR, logger="src.ingestion.sources.gog.gog"):
            with pytest.raises(GogAPIError) as raised:
                refresh_access_token(_EXPIRED_TOKEN)

        rendered = "".join(traceback.format_exception(raised.value))
        assert _EXPIRED_TOKEN not in rendered
        assert GOG_CLIENT_SECRET not in rendered
        assert str(raised.value) == "Failed to refresh access token: HTTP 401"
        assert _EXPIRED_TOKEN not in caplog.text
        assert "Error refreshing GOG access token: HTTP 401" in caplog.text

    @patch("src.ingestion.sources.gog.gog.requests.get")
    def test_fetch_traceback_omits_the_refresh_token(self, mock_get: Mock) -> None:
        """The ``SourceError`` sync_manager logs carries a clean chain."""
        mock_get.return_value = _expired_token_response()

        with pytest.raises(SourceError) as raised:
            list(GogPlugin().fetch({"refresh_token": _EXPIRED_TOKEN}))

        rendered = "".join(traceback.format_exception(raised.value))
        assert _EXPIRED_TOKEN not in rendered
        assert GOG_CLIENT_SECRET not in rendered
        assert str(raised.value) == "gog: Failed to refresh access token: HTTP 401"
