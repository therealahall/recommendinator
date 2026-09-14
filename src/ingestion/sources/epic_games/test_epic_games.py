import logging
import traceback
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pytest
import requests
from click.testing import CliRunner
from legendary.api.egs import EPCAPI
from legendary.models.exceptions import InvalidCredentialsError

from src.ingestion.plugin_base import OAuthError, SourceError
from src.ingestion.sources.epic_games.epic_games import (
    EpicGamesAPIError,
    EpicGamesPlugin,
    authenticate,
    exchange_code_for_tokens,
    extract_code_from_input,
    extract_metadata_fields,
    is_base_game,
)
from src.models.content import ConsumptionStatus, ContentType
from src.storage.manager import StorageManager
from tests.cli.conftest import _invoke_with_mocks

EPIC_LOGGER = "src.ingestion.sources.epic_games.epic_games"


class TestExtractCodeFromInput:
    def test_extracts_code_from_json(self) -> None:
        json_input = '{"authorizationCode": "a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6"}'

        result = extract_code_from_input(json_input)

        assert result == "a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6"

    def test_raises_error_for_json_without_code(self) -> None:
        with pytest.raises(OAuthError) as exc_info:
            extract_code_from_input('{"someOtherField": "value"}')

        assert "authorizationCode" in str(exc_info.value)

    def test_raises_error_for_short_input(self) -> None:
        with pytest.raises(OAuthError) as exc_info:
            extract_code_from_input("short")

        assert "too short" in str(exc_info.value)


class TestExchangeCodeForTokens:
    @patch("src.ingestion.sources.epic_games.epic_games.EPCAPI")
    def test_successful_exchange(self, mock_epcapi_cls: MagicMock) -> None:
        mock_api = MagicMock()
        mock_api.start_session.return_value = {
            "access_token": "access123",
            "refresh_token": "refresh456",
            "expires_in": 28800,
        }
        mock_epcapi_cls.return_value = mock_api

        result = exchange_code_for_tokens("test_code")

        assert result["refresh_token"] == "refresh456"
        assert result["access_token"] == "access123"
        mock_api.start_session.assert_called_once_with(authorization_code="test_code")


class TestEpicAuthTracebackRegression:
    @patch("src.ingestion.sources.epic_games.epic_games.EPCAPI")
    def test_transport_failure_logs_the_class_not_a_traceback(
        self, mock_epcapi_cls: MagicMock, caplog: pytest.LogCaptureFixture
    ) -> None:
        mock_api = MagicMock()
        mock_api.start_session.side_effect = ConnectionError("Connection refused")
        mock_epcapi_cls.return_value = mock_api

        with caplog.at_level(logging.ERROR, logger=EPIC_LOGGER):
            with pytest.raises(OAuthError, match="Failed to connect"):
                exchange_code_for_tokens("epic-auth-code-3f8a1c04d2")

        records = [record for record in caplog.records if record.name == EPIC_LOGGER]
        assert [record.getMessage() for record in records] == [
            "Epic token exchange request failed: ConnectionError"
        ]
        assert not any(record.exc_info for record in records)

    @patch("src.ingestion.sources.epic_games.epic_games.EPCAPI")
    def test_invalid_credentials_logs_the_class_not_a_traceback(
        self, mock_epcapi_cls: MagicMock, caplog: pytest.LogCaptureFixture
    ) -> None:
        mock_api = MagicMock()
        mock_api.start_session.side_effect = InvalidCredentialsError(
            "errors.com.epicgames.account.oauth.authorization_code_not_found"
        )
        mock_epcapi_cls.return_value = mock_api

        with caplog.at_level(logging.ERROR, logger=EPIC_LOGGER):
            with pytest.raises(OAuthError, match="Token exchange failed"):
                exchange_code_for_tokens("epic-auth-code-9b2e75f110")

        records = [record for record in caplog.records if record.name == EPIC_LOGGER]
        assert [record.getMessage() for record in records] == [
            "Epic token exchange failed (InvalidCredentialsError)"
        ]
        assert not any(record.exc_info for record in records)

    @patch("requests.sessions.Session.post")
    def test_cli_connect_logs_no_code_with_its_traceback(
        self,
        mock_post: MagicMock,
        caplog: pytest.LogCaptureFixture,
        tmp_path: Path,
    ) -> None:
        code = "epic-auth-code-4d70c9b8e1"
        response = MagicMock(spec=requests.Response)
        response.status_code = 400
        response.json.return_value = {
            "errorCode": "errors.com.epicgames.account.oauth.authorization_code_not_found",
            "errorMessage": f"Sorry, the authorization code {code} was not found",
        }
        mock_post.return_value = response
        storage = StorageManager(sqlite_path=tmp_path / "auth.db")
        storage.sources.upsert(1, "epic_games", "epic_games", {}, enabled=True)

        with caplog.at_level(logging.ERROR):
            result = _invoke_with_mocks(
                CliRunner(),
                ["auth", "connect", "--source", "epic_games", "--no-browser"],
                storage,
                input_text=f"{code}\n",
            )

        assert mock_post.call_args.kwargs["data"]["code"] == code
        assert code not in mock_post.call_args.args[0]
        chained = [record for record in caplog.records if record.exc_info]
        assert chained, "the CLI no longer logs a traceback, so this proves nothing"
        rendered = "".join(
            "".join(traceback.format_exception(*record.exc_info))
            for record in chained
            if record.exc_info
        )
        assert "InvalidCredentialsError" in rendered
        assert "OAuthError: Token exchange failed" in rendered
        assert code not in rendered
        assert code not in caplog.text
        assert result.exit_code != 0


def _make_library_record(
    namespace: str = "epic",
    app_name: str = "MyGame",
    catalog_item_id: str = "abc123",
    sandbox_type: str = "PUBLIC",
) -> dict:
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


class TestAuthenticate:
    @patch("src.ingestion.sources.epic_games.epic_games.EPCAPI")
    def test_authenticate_invalid_credentials(self, mock_epcapi_class: Mock) -> None:
        from legendary.models.exceptions import InvalidCredentialsError

        mock_api = Mock(spec=EPCAPI)
        mock_api.start_session.side_effect = InvalidCredentialsError(
            "errors.com.epicgames.oauth.invalid_token"
        )
        mock_epcapi_class.return_value = mock_api

        with pytest.raises(EpicGamesAPIError, match="invalid or expired"):
            authenticate("bad_token")


class TestIsBaseGame:
    def test_mod_category(self) -> None:
        metadata = _make_game_metadata(categories=[{"path": "mods"}])
        assert is_base_game(metadata) is False


class TestExtractMetadataFields:
    def test_minimal_metadata(self) -> None:
        game_metadata = {"id": "min123", "title": "Minimal Game"}
        library_record = _make_library_record()

        result = extract_metadata_fields(game_metadata, library_record)

        assert result["epic_catalog_item_id"] == "min123"
        assert "developer" not in result
        assert "description" not in result
        assert "categories" not in result
        assert "release_date" not in result


class TestEpicGamesPluginValidation:
    def test_validate_missing_refresh_token(self) -> None:
        plugin = EpicGamesPlugin()
        errors = plugin.validate_config({})

        assert len(errors) == 1
        assert "'refresh_token' is required" in errors[0]

    def test_validate_missing_token_passes_when_in_db(self) -> None:
        plugin = EpicGamesPlugin()
        mock_storage = Mock()
        mock_storage.credentials.get_for_source.return_value = {
            "refresh_token": "db_stored_token"
        }

        errors = plugin.validate_config(
            {"_source_id": "my_epic"},
            storage=mock_storage,
            user_id=1,
        )
        assert errors == []
        mock_storage.credentials.get_for_source.assert_called_once_with(1, "my_epic")


class TestEpicGamesPluginTransformConfig:
    def test_strips_whitespace(self) -> None:
        result = EpicGamesPlugin.transform_config({"refresh_token": "  my_token  "})
        assert result["refresh_token"] == "my_token"


class TestEpicGamesPluginFetch:
    @patch("src.ingestion.sources.epic_games.epic_games.get_game_metadata")
    @patch("src.ingestion.sources.epic_games.epic_games.get_library_items")
    @patch("src.ingestion.sources.epic_games.epic_games.authenticate")
    def test_fetch_base_games(
        self,
        mock_authenticate: Mock,
        mock_get_library: Mock,
        mock_get_metadata: Mock,
    ) -> None:
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

    @patch("src.ingestion.sources.epic_games.epic_games.authenticate")
    def test_error_wrapping(self, mock_authenticate: Mock) -> None:
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
    def test_broken_app_name_1_skipped(
        self,
        mock_authenticate: Mock,
        mock_get_library: Mock,
        mock_get_metadata: Mock,
    ) -> None:
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
    def test_metadata_none_skipped(
        self,
        mock_authenticate: Mock,
        mock_get_library: Mock,
        mock_get_metadata: Mock,
    ) -> None:
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
