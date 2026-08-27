"""Tests for GOG OAuth authentication."""

import logging
import traceback
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import requests
from click.testing import CliRunner

from src.auth.gog import (
    GogAuthError,
    exchange_code_for_tokens,
    extract_code_from_input,
    has_gog_token,
    is_gog_enabled,
    save_gog_token,
)
from src.ingestion.sources.gog import GOG_CLIENT_SECRET
from src.storage.manager import StorageManager
from tests.cli.conftest import _invoke_with_mocks
from tests.factories import make_storage_mock

GOG_LOGGER = "src.auth.gog"

# Token storage is shared by the three providers, so its failures log there.
OAUTH_LOGGER = "src.auth.oauth_sources"


class TestExtractCodeFromInput:
    """Tests for extract_code_from_input function."""

    def test_extracts_code_from_url(self) -> None:
        """Test extracting code from a redirect URL."""
        url = (
            "https://embed.gog.com/on_login_success?origin=client"
            "&code=oF8OSgZVMFb7a8Y3Dolrz4YPqDUnG7TCTsekYKcWnFNcmWWCJH7XJS3RN9d9NB0s"
        )

        result = extract_code_from_input(url)

        assert (
            result == "oF8OSgZVMFb7a8Y3Dolrz4YPqDUnG7TCTsekYKcWnFNcmWWCJH7XJS3RN9d9NB0s"
        )

    def test_raises_error_for_short_input(self) -> None:
        """Test that short input raises error."""
        with pytest.raises(GogAuthError) as exc_info:
            extract_code_from_input("short")

        assert "too short" in str(exc_info.value)


class TestExchangeCodeForTokens:
    """Tests for exchange_code_for_tokens function."""

    @patch("src.auth.gog.requests.get")
    def test_successful_exchange(self, mock_get: MagicMock) -> None:
        """Test successful token exchange."""
        mock_response = MagicMock(spec=requests.Response)
        mock_response.ok = True
        mock_response.json.return_value = {
            "access_token": "access123",
            "refresh_token": "refresh456",
            "expires_in": 3600,
        }
        mock_get.return_value = mock_response

        result = exchange_code_for_tokens("test_code")

        assert result["refresh_token"] == "refresh456"
        assert result["access_token"] == "access123"

    @patch("src.auth.gog.requests.get")
    def test_exchange_failure(
        self, mock_get: MagicMock, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Test token exchange failure."""
        mock_response = MagicMock(spec=requests.Response)
        mock_response.ok = False
        mock_response.status_code = 400
        mock_response.text = "Invalid code"
        mock_response.json.return_value = {"error_description": "Invalid code"}
        mock_get.return_value = mock_response

        with caplog.at_level(logging.ERROR, logger=GOG_LOGGER):
            with pytest.raises(GogAuthError, match="Token exchange failed"):
                exchange_code_for_tokens("bad_code")

        assert "GOG token exchange failed with status 400" in caplog.text


class TestSaveGogToken:
    """Tests for save_gog_token — DB persistence replaces config file writes."""

    @pytest.fixture()
    def storage(self, tmp_path: Path) -> StorageManager:
        """Create a StorageManager with a temp DB."""
        return StorageManager(sqlite_path=tmp_path / "test.db")

    def test_saves_token_to_db(self, storage: StorageManager) -> None:
        """Token is saved to encrypted DB storage."""
        save_gog_token(storage, "new_refresh_token")

        result = storage.credentials.get(1, "gog", "refresh_token")
        assert result == "new_refresh_token"

    def test_db_failure_logs_the_class_not_a_traceback(
        self, storage: StorageManager, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Regression: this sink logged a traceback while its Epic twin did not.

        The frames name absolute source paths and say nothing the class name
        does not, so the two save functions render a failure the same way.
        """
        with caplog.at_level(logging.ERROR, logger=OAUTH_LOGGER):
            with patch.object(
                storage.credentials, "save", side_effect=OSError("disk full")
            ):
                with pytest.raises(GogAuthError):
                    save_gog_token(storage, "some_token")

        records = [record for record in caplog.records if record.name == OAUTH_LOGGER]
        assert [record.getMessage() for record in records] == [
            "Failed to save GOG token to database: OSError"
        ]
        assert not any(record.exc_info for record in records)


def _gog_source(**fields: object) -> dict[str, Any]:
    """A config whose ``inputs.gog`` entry runs the GOG plugin."""
    return {"inputs": {"gog": {"plugin": "gog", "enabled": True, **fields}}}


class TestIsGogEnabled:
    """Tests for is_gog_enabled function."""

    def test_returns_true_when_enabled(self) -> None:
        """Test returns True when GOG is enabled."""
        assert is_gog_enabled(_gog_source()) is True

    def test_returns_false_for_a_source_running_another_plugin(self) -> None:
        """The id becomes a credential key, so the plugin behind it decides."""
        config = {"inputs": {"gog": {"plugin": "trakt", "enabled": True}}}

        assert is_gog_enabled(config) is False


class TestHasGogToken:
    """Tests for has_gog_token function."""

    @pytest.fixture()
    def storage(self, tmp_path: Path) -> StorageManager:
        """Create a StorageManager with a temp DB."""
        return StorageManager(sqlite_path=tmp_path / "test.db")

    def test_returns_true_when_token_in_db(self, storage: StorageManager) -> None:
        """DB token detected even when config has no token."""
        storage.credentials.save(1, "gog", "refresh_token", "db_token")

        assert has_gog_token(_gog_source(refresh_token=""), storage=storage) is True

    def test_a_config_only_token_is_not_reported_connected(
        self, storage: StorageManager
    ) -> None:
        """Disconnect deletes the stored row, and cannot reach config.yaml."""
        assert (
            has_gog_token(_gog_source(refresh_token="some_token"), storage=storage)
            is False
        )

    def test_another_plugins_source_is_never_reported_connected(
        self, storage: StorageManager
    ) -> None:
        """A Trakt source's token is not GOG's, however the id is spelled."""
        config = {"inputs": {"gog": {"plugin": "trakt", "enabled": True}}}
        storage.credentials.save(1, "gog", "refresh_token", "trakt_token")

        assert has_gog_token(config, storage=storage) is False


class TestGogAuthCredentialChainRegression:
    """Regression: the authorization code reached the log via ``__cause__``.

    A scrubbed message still left ``raise ... from error``, whose cause renders
    the token URL under ``exc_info=True`` at ``src/cli/commands/_auth.py``. Fix:
    ``from None``, so the CLI's traceback carries only the composed message.
    """

    @patch("src.auth.gog.requests.get")
    def test_connect_failure_traceback_omits_the_code_and_secret(
        self, mock_get: MagicMock, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Nothing a caller can render off the raised error names the code."""
        code = "gog-auth-code-7d1b3e"
        mock_get.side_effect = requests.ConnectionError(
            "HTTPSConnectionPool(host='auth.gog.com', port=443): Max retries "
            f"exceeded with url: /token?client_secret={GOG_CLIENT_SECRET}&code={code}"
        )

        with caplog.at_level(logging.ERROR, logger=GOG_LOGGER):
            with pytest.raises(GogAuthError) as raised:
                exchange_code_for_tokens(code)

        rendered = "".join(traceback.format_exception(raised.value))
        assert code not in rendered
        assert GOG_CLIENT_SECRET not in rendered
        assert code not in caplog.text
        assert GOG_CLIENT_SECRET not in caplog.text
        assert "GOG token exchange request failed: ConnectionError" in caplog.text

    @patch("src.cli.commands._auth.is_gog_enabled", return_value=True)
    @patch("src.auth.gog.requests.get")
    def test_cli_connect_logs_no_code_with_its_traceback(
        self,
        mock_get: MagicMock,
        _mock_enabled: MagicMock,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """The one caller docs/SECURITY.md names logs the whole chain verbatim."""
        code = "gog-auth-code-9c4e1a70b2"
        mock_get.side_effect = requests.ConnectionError(
            "HTTPSConnectionPool(host='auth.gog.com', port=443): Max retries "
            f"exceeded with url: /token?client_secret={GOG_CLIENT_SECRET}&code={code}"
        )

        with caplog.at_level(logging.ERROR):
            result = _invoke_with_mocks(
                CliRunner(),
                ["auth", "connect", "--source", "gog", "--no-browser"],
                make_storage_mock(),
                input_text=f"{code}\n",
            )

        chained = [record for record in caplog.records if record.exc_info]
        assert chained, "the CLI no longer logs a traceback, so this proves nothing"
        rendered = "".join(
            "".join(traceback.format_exception(*record.exc_info))
            for record in chained
            if record.exc_info
        )
        mock_get.assert_called_once()
        assert "GogAuthError: Failed to connect to GOG servers" in rendered
        assert code not in rendered
        assert GOG_CLIENT_SECRET not in rendered
        assert code not in caplog.text
        assert result.exit_code != 0
