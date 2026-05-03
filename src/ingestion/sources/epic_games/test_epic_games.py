"""Tests for Epic Games Store API integration."""

from unittest.mock import Mock, patch

import pytest
from legendary.api.egs import EPCAPI

from src.ingestion.plugin_base import SourceError, SourcePlugin
from src.ingestion.sources.epic_games.epic_games import (
    EpicGamesAPIError,
    EpicGamesPlugin,
    authenticate,
    extract_metadata_fields,
    get_game_metadata,
    get_library_items,
    is_base_game,
)
from src.models.content import ConsumptionStatus, ContentType

# ---------------------------------------------------------------------------
# Sample data fixtures
# ---------------------------------------------------------------------------


def _make_library_record(
    namespace: str = "epic",
    app_name: str = "MyGame",
    catalog_item_id: str = "abc123",
    sandbox_type: str = "PUBLIC",
) -> dict:
    """Create a minimal library record dict."""
    return {
        "namespace": namespace,
        "appName": app_name,
        "catalogItemId": catalog_item_id,
        "sandboxType": sandbox_type,
    }


def _make_game_metadata(
    catalog_item_id: str = "abc123",
    title: str = "My Game",
    developer: str = "Dev Studio",
    description: str = "A fun game.",
    categories: list | None = None,
    main_game_item: dict | None = None,
    release_info: list | None = None,
) -> dict:
    """Create a minimal game metadata dict."""
    metadata: dict = {
        "id": catalog_item_id,
        "title": title,
        "developer": developer,
        "description": description,
    }
    if categories is not None:
        metadata["categories"] = categories
    else:
        metadata["categories"] = [{"path": "games"}]
    if main_game_item is not None:
        metadata["mainGameItem"] = main_game_item
    if release_info is not None:
        metadata["releaseInfo"] = release_info
    return metadata


# ===========================================================================
# authenticate()
# ===========================================================================


class TestAuthenticate:
    """Tests for Epic Games OAuth authentication."""

    @patch("src.ingestion.sources.epic_games.epic_games.EPCAPI")
    def test_authenticate_success(self, mock_epcapi_class: Mock) -> None:
        """Test successful authentication returns an EPCAPI instance."""
        mock_api = Mock(spec=EPCAPI)
        mock_epcapi_class.return_value = mock_api

        result = authenticate("valid_refresh_token")

        assert result is mock_api
        mock_api.start_session.assert_called_once_with(
            refresh_token="valid_refresh_token"
        )

    @patch("src.ingestion.sources.epic_games.epic_games.EPCAPI")
    def test_authenticate_invalid_credentials(self, mock_epcapi_class: Mock) -> None:
        """Test that InvalidCredentialsError is wrapped in EpicGamesAPIError."""
        from legendary.models.exceptions import InvalidCredentialsError

        mock_api = Mock(spec=EPCAPI)
        mock_api.start_session.side_effect = InvalidCredentialsError(
            "errors.com.epicgames.oauth.invalid_token"
        )
        mock_epcapi_class.return_value = mock_api

        with pytest.raises(EpicGamesAPIError, match="invalid or expired"):
            authenticate("bad_token")

    @patch("src.ingestion.sources.epic_games.epic_games.EPCAPI")
    def test_authenticate_generic_error(self, mock_epcapi_class: Mock) -> None:
        """Test that generic exceptions are wrapped in EpicGamesAPIError."""
        mock_api = Mock(spec=EPCAPI)
        mock_api.start_session.side_effect = ConnectionError("Network down")
        mock_epcapi_class.return_value = mock_api

        with pytest.raises(EpicGamesAPIError, match="Failed to authenticate"):
            authenticate("some_token")


# ===========================================================================
# get_library_items()
# ===========================================================================


class TestGetLibraryItems:
    """Tests for fetching library items."""

    def test_success(self) -> None:
        """Test successful library fetch."""
        mock_api = Mock(spec=EPCAPI)
        mock_api.get_library_items.return_value = [
            _make_library_record(app_name="GameA"),
            _make_library_record(app_name="GameB"),
        ]

        result = get_library_items(mock_api)

        assert len(result) == 2
        assert result[0]["appName"] == "GameA"
        assert result[1]["appName"] == "GameB"
        mock_api.get_library_items.assert_called_once_with(include_metadata=True)

    def test_empty_library(self) -> None:
        """Test fetching when the library is empty."""
        mock_api = Mock(spec=EPCAPI)
        mock_api.get_library_items.return_value = []

        result = get_library_items(mock_api)

        assert result == []

    def test_api_error(self) -> None:
        """Test handling API error during fetch."""
        mock_api = Mock(spec=EPCAPI)
        mock_api.get_library_items.side_effect = Exception("Server error")

        with pytest.raises(EpicGamesAPIError, match="Failed to fetch library items"):
            get_library_items(mock_api)


# ===========================================================================
# get_game_metadata()
# ===========================================================================


class TestGetGameMetadata:
    """Tests for fetching individual game metadata."""

    def test_success(self) -> None:
        """Test successful metadata fetch."""
        mock_api = Mock(spec=EPCAPI)
        mock_api.get_game_info.return_value = _make_game_metadata(title="The Witcher 3")

        result = get_game_metadata(mock_api, "epic", "abc123")

        assert result is not None
        assert result["title"] == "The Witcher 3"
        mock_api.get_game_info.assert_called_once_with("epic", "abc123")

    def test_not_found_returns_none(self) -> None:
        """Test that None is returned when game is not found."""
        mock_api = Mock(spec=EPCAPI)
        mock_api.get_game_info.return_value = None

        result = get_game_metadata(mock_api, "epic", "missing123")

        assert result is None

    def test_api_error(self) -> None:
        """Test handling of API errors."""
        mock_api = Mock(spec=EPCAPI)
        mock_api.get_game_info.side_effect = Exception("Server error")

        with pytest.raises(EpicGamesAPIError, match="Failed to fetch metadata"):
            get_game_metadata(mock_api, "epic", "abc123")


# ===========================================================================
# is_base_game()
# ===========================================================================


class TestIsBaseGame:
    """Tests for the is_base_game() classifier."""

    def test_base_game(self) -> None:
        """Test that a regular game is identified as a base game."""
        metadata = _make_game_metadata(categories=[{"path": "games"}])
        assert is_base_game(metadata) is True

    def test_dlc_has_main_game_item(self) -> None:
        """Test that DLC (has mainGameItem) is not a base game."""
        metadata = _make_game_metadata(
            main_game_item={"id": "parent123", "title": "Parent Game"}
        )
        assert is_base_game(metadata) is False

    def test_mod_category(self) -> None:
        """Test that items with 'mods' category are not base games."""
        metadata = _make_game_metadata(categories=[{"path": "mods"}])
        assert is_base_game(metadata) is False

    def test_empty_categories(self) -> None:
        """Test that empty categories list is treated as base game."""
        metadata = _make_game_metadata(categories=[])
        assert is_base_game(metadata) is True

    def test_no_categories_key(self) -> None:
        """Test that missing categories key is treated as base game."""
        metadata = {"id": "abc", "title": "Game"}
        assert is_base_game(metadata) is True


# ===========================================================================
# extract_metadata_fields()
# ===========================================================================


class TestExtractMetadataFields:
    """Tests for metadata extraction."""

    def test_full_metadata(self) -> None:
        """Test extraction with all fields present."""
        game_metadata = _make_game_metadata(
            catalog_item_id="cat123",
            developer="Dev Studio",
            description="A fun game.",
            categories=[{"path": "games"}, {"path": "multiplayer"}],
            release_info=[{"dateAdded": "2023-06-15T00:00:00.000Z"}],
        )
        library_record = _make_library_record(
            namespace="epic_ns",
            app_name="MyGame",
            catalog_item_id="cat123",
        )

        result = extract_metadata_fields(game_metadata, library_record)

        assert result["epic_namespace"] == "epic_ns"
        assert result["epic_catalog_item_id"] == "cat123"
        assert result["epic_app_name"] == "MyGame"
        assert result["developer"] == "Dev Studio"
        assert result["description"] == "A fun game."
        assert result["categories"] == ["games", "multiplayer"]
        assert result["release_date"] == "2023-06-15T00:00:00.000Z"

    def test_minimal_metadata(self) -> None:
        """Test extraction with minimal fields."""
        game_metadata = {"id": "min123", "title": "Minimal Game"}
        library_record = _make_library_record()

        result = extract_metadata_fields(game_metadata, library_record)

        assert result["epic_catalog_item_id"] == "min123"
        assert "developer" not in result
        assert "description" not in result
        assert "categories" not in result
        assert "release_date" not in result

    def test_missing_fields_graceful(self) -> None:
        """Test that missing optional fields don't cause errors."""
        game_metadata = {
            "id": "x",
            "title": "X",
            "categories": [],
            "releaseInfo": [],
        }
        library_record = _make_library_record()

        result = extract_metadata_fields(game_metadata, library_record)

        assert "developer" not in result
        assert "description" not in result
        assert "release_date" not in result


# ===========================================================================
# EpicGamesPlugin — properties
# ===========================================================================


class TestEpicGamesPluginProperties:
    """Tests for EpicGamesPlugin metadata properties."""

    def test_is_source_plugin(self) -> None:
        """Test that EpicGamesPlugin is a SourcePlugin subclass."""
        plugin = EpicGamesPlugin()
        assert isinstance(plugin, SourcePlugin)

    def test_name(self) -> None:
        """Test plugin name identifier."""
        plugin = EpicGamesPlugin()
        assert plugin.name == "epic_games"

    def test_display_name(self) -> None:
        """Test human-readable display name."""
        plugin = EpicGamesPlugin()
        assert plugin.display_name == "Epic Games Store"

    def test_content_types(self) -> None:
        """Test that plugin provides video games."""
        plugin = EpicGamesPlugin()
        assert plugin.content_types == [ContentType.VIDEO_GAME]

    def test_requires_api_key(self) -> None:
        """Test that plugin requires credentials (refresh token)."""
        plugin = EpicGamesPlugin()
        assert plugin.requires_api_key is True

    def test_requires_network(self) -> None:
        """Test that plugin requires network access."""
        plugin = EpicGamesPlugin()
        assert plugin.requires_network is True

    def test_config_schema(self) -> None:
        """Test configuration schema fields."""
        plugin = EpicGamesPlugin()
        schema = plugin.get_config_schema()

        field_names = [field.name for field in schema]
        assert "refresh_token" in field_names

        token_field = next(field for field in schema if field.name == "refresh_token")
        assert token_field.required is True
        assert token_field.sensitive is True

    def test_get_source_identifier(self) -> None:
        """Test source identifier matches plugin name."""
        plugin = EpicGamesPlugin()
        assert plugin.get_source_identifier() == "epic_games"

    def test_get_info(self) -> None:
        """Test plugin info includes all metadata."""
        plugin = EpicGamesPlugin()
        info = plugin.get_info()

        assert info.name == "epic_games"
        assert info.display_name == "Epic Games Store"
        assert info.content_types == [ContentType.VIDEO_GAME]
        assert info.requires_api_key is True
        assert info.requires_network is True


# ===========================================================================
# EpicGamesPlugin — validation
# ===========================================================================


class TestEpicGamesPluginValidation:
    """Tests for EpicGamesPlugin config validation."""

    def test_validate_valid_config(self) -> None:
        """Test validation passes with valid config."""
        plugin = EpicGamesPlugin()
        errors = plugin.validate_config({"refresh_token": "valid_token_here"})
        assert errors == []

    def test_validate_missing_refresh_token(self) -> None:
        """Test validation fails when refresh_token is missing."""
        plugin = EpicGamesPlugin()
        errors = plugin.validate_config({})

        assert len(errors) == 1
        assert "'refresh_token' is required" in errors[0]

    def test_validate_empty_refresh_token(self) -> None:
        """Test validation fails when refresh_token is empty."""
        plugin = EpicGamesPlugin()
        errors = plugin.validate_config({"refresh_token": ""})

        assert len(errors) == 1
        assert "'refresh_token' is required" in errors[0]

    def test_validate_whitespace_refresh_token(self) -> None:
        """Test validation fails when refresh_token is whitespace only."""
        plugin = EpicGamesPlugin()
        errors = plugin.validate_config({"refresh_token": "   "})

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
        plugin = EpicGamesPlugin()
        mock_storage = Mock()
        mock_storage.get_credentials_for_source.return_value = {
            "refresh_token": "db_stored_token"
        }

        # Config has no refresh_token, but DB does — should pass
        errors = plugin.validate_config(
            {"_source_id": "my_epic"},
            storage=mock_storage,
            user_id=1,
        )
        assert errors == []
        mock_storage.get_credentials_for_source.assert_called_once_with(1, "my_epic")

    def test_validate_missing_token_fails_when_not_in_db(self) -> None:
        """Validation still fails when token is absent from both config and DB."""
        plugin = EpicGamesPlugin()
        mock_storage = Mock()
        mock_storage.get_credentials_for_source.return_value = {}

        errors = plugin.validate_config(
            {"_source_id": "my_epic"},
            storage=mock_storage,
            user_id=1,
        )
        assert len(errors) == 1
        assert "'refresh_token' is required" in errors[0]

    def test_validate_missing_token_fails_without_storage(self) -> None:
        """Validation fails when no storage is provided and token is missing."""
        plugin = EpicGamesPlugin()
        errors = plugin.validate_config({})
        assert len(errors) == 1
        assert "'refresh_token' is required" in errors[0]


# ===========================================================================
# EpicGamesPlugin — transform_config
# ===========================================================================


class TestEpicGamesPluginTransformConfig:
    """Tests for EpicGamesPlugin.transform_config."""

    def test_strips_whitespace(self) -> None:
        """Test that whitespace is stripped from refresh_token."""
        result = EpicGamesPlugin.transform_config({"refresh_token": "  my_token  "})
        assert result["refresh_token"] == "my_token"

    def test_defaults_empty_token(self) -> None:
        """Test that missing refresh_token defaults to empty string."""
        result = EpicGamesPlugin.transform_config({})
        assert result["refresh_token"] == ""


# ===========================================================================
# EpicGamesPlugin — fetch()
# ===========================================================================


class TestEpicGamesPluginFetch:
    """Tests for EpicGamesPlugin.fetch()."""

    @patch("src.ingestion.sources.epic_games.epic_games.get_game_metadata")
    @patch("src.ingestion.sources.epic_games.epic_games.get_library_items")
    @patch("src.ingestion.sources.epic_games.epic_games.authenticate")
    def test_fetch_base_games(
        self,
        mock_authenticate: Mock,
        mock_get_library: Mock,
        mock_get_metadata: Mock,
    ) -> None:
        """Test fetching base games through the plugin interface."""
        mock_api = Mock(spec=EPCAPI)
        mock_authenticate.return_value = mock_api
        mock_get_library.return_value = [
            _make_library_record(
                namespace="epic",
                app_name="GameOne",
                catalog_item_id="cat1",
            ),
        ]
        mock_get_metadata.return_value = _make_game_metadata(
            catalog_item_id="cat1",
            title="Game One",
            developer="Studio A",
        )

        plugin = EpicGamesPlugin()
        items = list(plugin.fetch({"refresh_token": "my_token"}))

        assert len(items) == 1
        assert items[0].title == "Game One"
        assert items[0].content_type == ContentType.VIDEO_GAME
        assert items[0].id == "cat1"
        assert items[0].source == "epic_games"
        assert items[0].status == ConsumptionStatus.UNREAD
        assert items[0].rating is None
        assert items[0].author is None

    @patch("src.ingestion.sources.epic_games.epic_games.get_game_metadata")
    @patch("src.ingestion.sources.epic_games.epic_games.get_library_items")
    @patch("src.ingestion.sources.epic_games.epic_games.authenticate")
    def test_dlc_filtered_out(
        self,
        mock_authenticate: Mock,
        mock_get_library: Mock,
        mock_get_metadata: Mock,
    ) -> None:
        """Test that DLC items are filtered out."""
        mock_api = Mock(spec=EPCAPI)
        mock_authenticate.return_value = mock_api
        mock_get_library.return_value = [
            _make_library_record(app_name="BaseGame", catalog_item_id="base1"),
            _make_library_record(app_name="DLCPack", catalog_item_id="dlc1"),
        ]
        mock_get_metadata.side_effect = [
            _make_game_metadata(catalog_item_id="base1", title="Base Game"),
            _make_game_metadata(
                catalog_item_id="dlc1",
                title="DLC Pack",
                main_game_item={"id": "base1", "title": "Base Game"},
            ),
        ]

        plugin = EpicGamesPlugin()
        items = list(plugin.fetch({"refresh_token": "token"}))

        assert len(items) == 1
        assert items[0].title == "Base Game"

    @patch("src.ingestion.sources.epic_games.epic_games.get_game_metadata")
    @patch("src.ingestion.sources.epic_games.epic_games.get_library_items")
    @patch("src.ingestion.sources.epic_games.epic_games.authenticate")
    def test_metadata_fields_populated(
        self,
        mock_authenticate: Mock,
        mock_get_library: Mock,
        mock_get_metadata: Mock,
    ) -> None:
        """Test that metadata fields are populated correctly."""
        mock_api = Mock(spec=EPCAPI)
        mock_authenticate.return_value = mock_api
        mock_get_library.return_value = [
            _make_library_record(
                namespace="epic_ns",
                app_name="TestGame",
                catalog_item_id="test1",
            ),
        ]
        mock_get_metadata.return_value = _make_game_metadata(
            catalog_item_id="test1",
            title="Test Game",
            developer="Test Dev",
            description="A test game.",
            categories=[{"path": "games"}],
            release_info=[{"dateAdded": "2024-01-15T00:00:00.000Z"}],
        )

        plugin = EpicGamesPlugin()
        items = list(plugin.fetch({"refresh_token": "token"}))

        metadata = items[0].metadata
        assert metadata["epic_namespace"] == "epic_ns"
        assert metadata["epic_catalog_item_id"] == "test1"
        assert metadata["epic_app_name"] == "TestGame"
        assert metadata["developer"] == "Test Dev"
        assert metadata["description"] == "A test game."
        assert metadata["categories"] == ["games"]
        assert metadata["release_date"] == "2024-01-15T00:00:00.000Z"

    @patch("src.ingestion.sources.epic_games.epic_games.get_game_metadata")
    @patch("src.ingestion.sources.epic_games.epic_games.get_library_items")
    @patch("src.ingestion.sources.epic_games.epic_games.authenticate")
    def test_titleless_items_skipped(
        self,
        mock_authenticate: Mock,
        mock_get_library: Mock,
        mock_get_metadata: Mock,
    ) -> None:
        """Test that games without titles are skipped."""
        mock_api = Mock(spec=EPCAPI)
        mock_authenticate.return_value = mock_api
        mock_get_library.return_value = [
            _make_library_record(app_name="NoTitle", catalog_item_id="nt1"),
            _make_library_record(app_name="HasTitle", catalog_item_id="ht1"),
        ]
        mock_get_metadata.side_effect = [
            _make_game_metadata(catalog_item_id="nt1", title=""),
            _make_game_metadata(catalog_item_id="ht1", title="Has Title"),
        ]

        plugin = EpicGamesPlugin()
        items = list(plugin.fetch({"refresh_token": "token"}))

        assert len(items) == 1
        assert items[0].title == "Has Title"

    @patch("src.ingestion.sources.epic_games.epic_games.get_game_metadata")
    @patch("src.ingestion.sources.epic_games.epic_games.get_library_items")
    @patch("src.ingestion.sources.epic_games.epic_games.authenticate")
    def test_private_sandbox_skipped(
        self,
        mock_authenticate: Mock,
        mock_get_library: Mock,
        mock_get_metadata: Mock,
    ) -> None:
        """Test that PRIVATE sandbox items are skipped."""
        mock_api = Mock(spec=EPCAPI)
        mock_authenticate.return_value = mock_api
        mock_get_library.return_value = [
            _make_library_record(
                app_name="PrivateGame",
                catalog_item_id="priv1",
                sandbox_type="PRIVATE",
            ),
            _make_library_record(
                app_name="PublicGame",
                catalog_item_id="pub1",
                sandbox_type="PUBLIC",
            ),
        ]
        mock_get_metadata.return_value = _make_game_metadata(
            catalog_item_id="pub1", title="Public Game"
        )

        plugin = EpicGamesPlugin()
        items = list(plugin.fetch({"refresh_token": "token"}))

        assert len(items) == 1
        assert items[0].title == "Public Game"
        # get_game_metadata should only have been called once (for the public item)
        mock_get_metadata.assert_called_once()

    @patch("src.ingestion.sources.epic_games.epic_games.get_game_metadata")
    @patch("src.ingestion.sources.epic_games.epic_games.get_library_items")
    @patch("src.ingestion.sources.epic_games.epic_games.authenticate")
    def test_ue_namespace_skipped(
        self,
        mock_authenticate: Mock,
        mock_get_library: Mock,
        mock_get_metadata: Mock,
    ) -> None:
        """Test that Unreal Engine marketplace items are skipped."""
        mock_api = Mock(spec=EPCAPI)
        mock_authenticate.return_value = mock_api
        mock_get_library.return_value = [
            _make_library_record(
                namespace="ue",
                app_name="UEAsset",
                catalog_item_id="ue1",
            ),
            _make_library_record(
                namespace="epic",
                app_name="RealGame",
                catalog_item_id="game1",
            ),
        ]
        mock_get_metadata.return_value = _make_game_metadata(
            catalog_item_id="game1", title="Real Game"
        )

        plugin = EpicGamesPlugin()
        items = list(plugin.fetch({"refresh_token": "token"}))

        assert len(items) == 1
        assert items[0].title == "Real Game"

    @patch("src.ingestion.sources.epic_games.epic_games.get_game_metadata")
    @patch("src.ingestion.sources.epic_games.epic_games.get_library_items")
    @patch("src.ingestion.sources.epic_games.epic_games.authenticate")
    def test_progress_callback(
        self,
        mock_authenticate: Mock,
        mock_get_library: Mock,
        mock_get_metadata: Mock,
    ) -> None:
        """Test that progress callback is invoked during fetch."""
        mock_api = Mock(spec=EPCAPI)
        mock_authenticate.return_value = mock_api
        mock_get_library.return_value = [
            _make_library_record(app_name="G1", catalog_item_id="c1"),
            _make_library_record(app_name="G2", catalog_item_id="c2"),
        ]
        mock_get_metadata.side_effect = [
            _make_game_metadata(catalog_item_id="c1", title="Game 1"),
            _make_game_metadata(catalog_item_id="c2", title="Game 2"),
        ]

        plugin = EpicGamesPlugin()
        callback = Mock()
        items = list(
            plugin.fetch({"refresh_token": "token"}, progress_callback=callback)
        )

        assert len(items) == 2
        assert callback.call_count > 0

    @patch("src.ingestion.sources.epic_games.epic_games.authenticate")
    def test_error_wrapping(self, mock_authenticate: Mock) -> None:
        """Test that EpicGamesAPIError is wrapped in SourceError."""
        mock_authenticate.side_effect = EpicGamesAPIError("Token expired")

        plugin = EpicGamesPlugin()
        with pytest.raises(SourceError, match="Token expired") as exc_info:
            list(plugin.fetch({"refresh_token": "bad_token"}))

        assert exc_info.value.plugin_name == "epic_games"

    @patch("src.ingestion.sources.epic_games.epic_games.get_game_metadata")
    @patch("src.ingestion.sources.epic_games.epic_games.get_library_items")
    @patch("src.ingestion.sources.epic_games.epic_games.authenticate")
    def test_metadata_fetch_failure_graceful_skip(
        self,
        mock_authenticate: Mock,
        mock_get_library: Mock,
        mock_get_metadata: Mock,
    ) -> None:
        """Test that individual metadata failures are logged and skipped."""
        mock_api = Mock(spec=EPCAPI)
        mock_authenticate.return_value = mock_api
        mock_get_library.return_value = [
            _make_library_record(app_name="Fails", catalog_item_id="fail1"),
            _make_library_record(app_name="Works", catalog_item_id="ok1"),
        ]
        mock_get_metadata.side_effect = [
            EpicGamesAPIError("Server error"),
            _make_game_metadata(catalog_item_id="ok1", title="Working Game"),
        ]

        plugin = EpicGamesPlugin()
        items = list(plugin.fetch({"refresh_token": "token"}))

        assert len(items) == 1
        assert items[0].title == "Working Game"

    @patch("src.ingestion.sources.epic_games.epic_games.get_game_metadata")
    @patch("src.ingestion.sources.epic_games.epic_games.get_library_items")
    @patch("src.ingestion.sources.epic_games.epic_games.authenticate")
    def test_all_games_are_unread(
        self,
        mock_authenticate: Mock,
        mock_get_library: Mock,
        mock_get_metadata: Mock,
    ) -> None:
        """Test that all games have UNREAD status (Epic has no playtime data)."""
        mock_api = Mock(spec=EPCAPI)
        mock_authenticate.return_value = mock_api
        mock_get_library.return_value = [
            _make_library_record(app_name=f"G{index}", catalog_item_id=f"c{index}")
            for index in range(3)
        ]
        mock_get_metadata.side_effect = [
            _make_game_metadata(catalog_item_id=f"c{index}", title=f"Game {index}")
            for index in range(3)
        ]

        plugin = EpicGamesPlugin()
        items = list(plugin.fetch({"refresh_token": "token"}))

        for item in items:
            assert item.status == ConsumptionStatus.UNREAD
            assert item.rating is None

    @patch("src.ingestion.sources.epic_games.epic_games.get_game_metadata")
    @patch("src.ingestion.sources.epic_games.epic_games.get_library_items")
    @patch("src.ingestion.sources.epic_games.epic_games.authenticate")
    def test_broken_app_name_1_skipped(
        self,
        mock_authenticate: Mock,
        mock_get_library: Mock,
        mock_get_metadata: Mock,
    ) -> None:
        """Test that the broken placeholder appName '1' is skipped."""
        mock_api = Mock(spec=EPCAPI)
        mock_authenticate.return_value = mock_api
        mock_get_library.return_value = [
            _make_library_record(
                app_name="1",
                catalog_item_id="broken1",
            ),
            _make_library_record(
                app_name="RealGame",
                catalog_item_id="real1",
            ),
        ]
        mock_get_metadata.return_value = _make_game_metadata(
            catalog_item_id="real1", title="Real Game"
        )

        plugin = EpicGamesPlugin()
        items = list(plugin.fetch({"refresh_token": "token"}))

        assert len(items) == 1
        assert items[0].title == "Real Game"

    @patch("src.ingestion.sources.epic_games.epic_games.get_game_metadata")
    @patch("src.ingestion.sources.epic_games.epic_games.get_library_items")
    @patch("src.ingestion.sources.epic_games.epic_games.authenticate")
    def test_rotated_refresh_token_triggers_callback(
        self,
        mock_authenticate: Mock,
        mock_get_library: Mock,
        mock_get_metadata: Mock,
    ) -> None:
        """Regression test: rotated Epic refresh tokens must be persisted.

        Bug: When Epic Games returns a new refresh_token during session start
        (token rotation), the updated token was discarded. If the old token
        expired before the next sync, the user had to re-authenticate.

        Root cause: authenticate() returned the EPCAPI instance but the new
        refresh_token from start_session() was never extracted or saved.

        Fix: After authentication, extract the refresh_token from the session
        data (api.user dict) and call the _on_credential_rotated callback
        (injected by execute_sync) when it differs from the original.
        """
        mock_api = Mock(spec=EPCAPI)
        mock_api.user = {"refresh_token": "rotated_epic_token"}
        mock_authenticate.return_value = mock_api
        mock_get_library.return_value = [
            _make_library_record(app_name="G1", catalog_item_id="c1"),
        ]
        mock_get_metadata.return_value = _make_game_metadata(
            catalog_item_id="c1", title="Game 1"
        )

        credential_callback = Mock()
        plugin = EpicGamesPlugin()
        list(
            plugin.fetch(
                {
                    "refresh_token": "old_epic_token",
                    "_on_credential_rotated": credential_callback,
                }
            )
        )

        credential_callback.assert_called_once_with(
            "refresh_token", "rotated_epic_token"
        )

    @patch("src.ingestion.sources.epic_games.epic_games.get_game_metadata")
    @patch("src.ingestion.sources.epic_games.epic_games.get_library_items")
    @patch("src.ingestion.sources.epic_games.epic_games.authenticate")
    def test_same_refresh_token_does_not_trigger_callback(
        self,
        mock_authenticate: Mock,
        mock_get_library: Mock,
        mock_get_metadata: Mock,
    ) -> None:
        """No callback when the Epic refresh token hasn't changed."""
        mock_api = Mock(spec=EPCAPI)
        mock_api.user = {"refresh_token": "same_token"}
        mock_authenticate.return_value = mock_api
        mock_get_library.return_value = [
            _make_library_record(app_name="G1", catalog_item_id="c1"),
        ]
        mock_get_metadata.return_value = _make_game_metadata(
            catalog_item_id="c1", title="Game 1"
        )

        credential_callback = Mock()
        plugin = EpicGamesPlugin()
        items = list(
            plugin.fetch(
                {
                    "refresh_token": "same_token",
                    "_on_credential_rotated": credential_callback,
                }
            )
        )

        assert len(items) == 1
        credential_callback.assert_not_called()

    @patch("src.ingestion.sources.epic_games.epic_games.get_game_metadata")
    @patch("src.ingestion.sources.epic_games.epic_games.get_library_items")
    @patch("src.ingestion.sources.epic_games.epic_games.authenticate")
    def test_rotated_token_without_callback_does_not_raise(
        self,
        mock_authenticate: Mock,
        mock_get_library: Mock,
        mock_get_metadata: Mock,
    ) -> None:
        """Token rotation with no callback injected completes without error."""
        mock_api = Mock(spec=EPCAPI)
        mock_api.user = {"refresh_token": "rotated_token"}
        mock_authenticate.return_value = mock_api
        mock_get_library.return_value = [
            _make_library_record(app_name="G1", catalog_item_id="c1"),
        ]
        mock_get_metadata.return_value = _make_game_metadata(
            catalog_item_id="c1", title="Game 1"
        )

        plugin = EpicGamesPlugin()
        # No _on_credential_rotated in config
        items = list(plugin.fetch({"refresh_token": "old_token"}))
        assert len(items) == 1

    @patch("src.ingestion.sources.epic_games.epic_games.get_game_metadata")
    @patch("src.ingestion.sources.epic_games.epic_games.get_library_items")
    @patch("src.ingestion.sources.epic_games.epic_games.authenticate")
    def test_empty_user_dict_does_not_trigger_callback(
        self,
        mock_authenticate: Mock,
        mock_get_library: Mock,
        mock_get_metadata: Mock,
    ) -> None:
        """No callback when api.user has no refresh_token key."""
        mock_api = Mock(spec=EPCAPI)
        mock_api.user = {}  # No refresh_token key
        mock_authenticate.return_value = mock_api
        mock_get_library.return_value = [
            _make_library_record(app_name="G1", catalog_item_id="c1"),
        ]
        mock_get_metadata.return_value = _make_game_metadata(
            catalog_item_id="c1", title="Game 1"
        )

        credential_callback = Mock()
        plugin = EpicGamesPlugin()
        items = list(
            plugin.fetch(
                {
                    "refresh_token": "old_token",
                    "_on_credential_rotated": credential_callback,
                }
            )
        )

        assert len(items) == 1
        credential_callback.assert_not_called()

    @patch("src.ingestion.sources.epic_games.epic_games.get_game_metadata")
    @patch("src.ingestion.sources.epic_games.epic_games.get_library_items")
    @patch("src.ingestion.sources.epic_games.epic_games.authenticate")
    def test_none_user_dict_does_not_trigger_callback(
        self,
        mock_authenticate: Mock,
        mock_get_library: Mock,
        mock_get_metadata: Mock,
    ) -> None:
        """No callback when api.user is None (isinstance guard exercised)."""
        mock_api = Mock(spec=EPCAPI)
        mock_api.user = None
        mock_authenticate.return_value = mock_api
        mock_get_library.return_value = [
            _make_library_record(app_name="G1", catalog_item_id="c1"),
        ]
        mock_get_metadata.return_value = _make_game_metadata(
            catalog_item_id="c1", title="Game 1"
        )

        credential_callback = Mock()
        plugin = EpicGamesPlugin()
        items = list(
            plugin.fetch(
                {
                    "refresh_token": "old_token",
                    "_on_credential_rotated": credential_callback,
                }
            )
        )

        assert len(items) == 1
        credential_callback.assert_not_called()

    @patch("src.ingestion.sources.epic_games.epic_games.get_game_metadata")
    @patch("src.ingestion.sources.epic_games.epic_games.get_library_items")
    @patch("src.ingestion.sources.epic_games.epic_games.authenticate")
    def test_metadata_none_skipped(
        self,
        mock_authenticate: Mock,
        mock_get_library: Mock,
        mock_get_metadata: Mock,
    ) -> None:
        """Test that items where get_game_metadata returns None are skipped."""
        mock_api = Mock(spec=EPCAPI)
        mock_authenticate.return_value = mock_api
        mock_get_library.return_value = [
            _make_library_record(app_name="NoMeta", catalog_item_id="nm1"),
            _make_library_record(app_name="HasMeta", catalog_item_id="hm1"),
        ]
        mock_get_metadata.side_effect = [
            None,
            _make_game_metadata(catalog_item_id="hm1", title="Has Metadata"),
        ]

        plugin = EpicGamesPlugin()
        items = list(plugin.fetch({"refresh_token": "token"}))

        assert len(items) == 1
        assert items[0].title == "Has Metadata"
