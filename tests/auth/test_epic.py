import logging
import traceback
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import requests
from click.testing import CliRunner
from legendary.models.exceptions import InvalidCredentialsError

from src.auth.epic import (
    EpicAuthError,
    exchange_code_for_tokens,
    extract_code_from_input,
    has_epic_token,
    save_epic_token,
)
from src.storage.manager import StorageManager
from tests.cli.conftest import _invoke_with_mocks
from tests.factories import make_storage_mock

EPIC_LOGGER = "src.auth.epic"

OAUTH_LOGGER = "src.auth.oauth_sources"


class TestExtractCodeFromInput:
    def test_extracts_code_from_json(self) -> None:
        json_input = '{"authorizationCode": "a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6"}'

        result = extract_code_from_input(json_input)

        assert result == "a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6"

    def test_raises_error_for_json_without_code(self) -> None:
        json_input = '{"someOtherField": "value"}'

        with pytest.raises(EpicAuthError) as exc_info:
            extract_code_from_input(json_input)

        assert "authorizationCode" in str(exc_info.value)

    def test_raises_error_for_short_input(self) -> None:
        with pytest.raises(EpicAuthError) as exc_info:
            extract_code_from_input("short")

        assert "too short" in str(exc_info.value)


class TestExchangeCodeForTokens:
    @patch("src.auth.epic.EPCAPI")
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


class TestSaveEpicToken:
    @pytest.fixture()
    def storage(self, tmp_path: Path) -> StorageManager:
        return StorageManager(sqlite_path=tmp_path / "test.db")

    def test_saves_token_to_db(self, storage: StorageManager) -> None:
        save_epic_token(storage, "new_refresh_token")

        result = storage.credentials.get(1, "epic_games", "refresh_token")
        assert result == "new_refresh_token"

    def test_db_failure_logs_the_class_not_a_traceback(
        self, storage: StorageManager, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.ERROR, logger=OAUTH_LOGGER):
            with patch.object(
                storage.credentials, "save", side_effect=OSError("disk full")
            ):
                with pytest.raises(EpicAuthError):
                    save_epic_token(storage, "some_token")

        records = [record for record in caplog.records if record.name == OAUTH_LOGGER]
        assert [record.getMessage() for record in records] == [
            "Failed to save Epic Games token to database: OSError"
        ]
        assert not any(record.exc_info for record in records)


def _epic_source(**fields: object) -> dict[str, Any]:
    return {
        "inputs": {"epic_games": {"plugin": "epic_games", "enabled": True, **fields}}
    }


class TestHasEpicToken:
    @pytest.fixture()
    def storage(self, tmp_path: Path) -> StorageManager:
        return StorageManager(sqlite_path=tmp_path / "test.db")

    def test_returns_true_when_token_in_db(self, storage: StorageManager) -> None:
        storage.credentials.save(1, "epic_games", "refresh_token", "db_token")

        assert has_epic_token(_epic_source(refresh_token=""), storage=storage) is True


class TestEpicAuthTracebackRegression:
    @patch("src.auth.epic.EPCAPI")
    def test_transport_failure_logs_the_class_not_a_traceback(
        self, mock_epcapi_cls: MagicMock, caplog: pytest.LogCaptureFixture
    ) -> None:
        mock_api = MagicMock()
        mock_api.start_session.side_effect = ConnectionError("Connection refused")
        mock_epcapi_cls.return_value = mock_api

        with caplog.at_level(logging.ERROR, logger=EPIC_LOGGER):
            with pytest.raises(EpicAuthError, match="Failed to connect"):
                exchange_code_for_tokens("epic-auth-code-3f8a1c04d2")

        records = [record for record in caplog.records if record.name == EPIC_LOGGER]
        assert [record.getMessage() for record in records] == [
            "Epic token exchange request failed: ConnectionError"
        ]
        assert not any(record.exc_info for record in records)

    @patch("src.auth.epic.EPCAPI")
    def test_invalid_credentials_logs_the_class_not_a_traceback(
        self, mock_epcapi_cls: MagicMock, caplog: pytest.LogCaptureFixture
    ) -> None:
        mock_api = MagicMock()
        mock_api.start_session.side_effect = InvalidCredentialsError(
            "errors.com.epicgames.account.oauth.authorization_code_not_found"
        )
        mock_epcapi_cls.return_value = mock_api

        with caplog.at_level(logging.ERROR, logger=EPIC_LOGGER):
            with pytest.raises(EpicAuthError, match="Token exchange failed"):
                exchange_code_for_tokens("epic-auth-code-9b2e75f110")

        records = [record for record in caplog.records if record.name == EPIC_LOGGER]
        assert [record.getMessage() for record in records] == [
            "Epic token exchange failed (InvalidCredentialsError)"
        ]
        assert not any(record.exc_info for record in records)


class TestEpicAuthCredentialChain:
    @patch("src.cli.commands._auth.is_epic_enabled", return_value=True)
    @patch("requests.sessions.Session.post")
    def test_cli_connect_logs_no_code_with_its_traceback(
        self,
        mock_post: MagicMock,
        _mock_enabled: MagicMock,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        code = "epic-auth-code-4d70c9b8e1"
        response = MagicMock(spec=requests.Response)
        response.status_code = 400
        response.json.return_value = {
            "errorCode": "errors.com.epicgames.account.oauth.authorization_code_not_found",
            "errorMessage": f"Sorry, the authorization code {code} was not found",
        }
        mock_post.return_value = response

        with caplog.at_level(logging.ERROR):
            result = _invoke_with_mocks(
                CliRunner(),
                ["auth", "connect", "--source", "epic", "--no-browser"],
                make_storage_mock(),
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
        assert "EpicAuthError: Token exchange failed" in rendered
        assert code not in rendered
        assert code not in caplog.text
        assert result.exit_code != 0
