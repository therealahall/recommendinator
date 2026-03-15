"""Tests for GOG OAuth authentication."""

import logging
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests

from src.storage.manager import StorageManager
from src.web.gog_auth import (
    GogAuthError,
    exchange_code_for_tokens,
    extract_code_from_input,
    get_gog_auth_url,
    has_gog_token,
    is_gog_enabled,
    save_gog_token,
)


class TestGetGogAuthUrl:
    """Tests for get_gog_auth_url function."""

    def test_returns_valid_url(self) -> None:
        """Test that auth URL is properly formatted."""
        url = get_gog_auth_url()

        assert url.startswith("https://auth.gog.com/auth?")
        assert "client_id=46899977096215655" in url
        assert "response_type=code" in url


class TestExtractCodeFromInput:
    """Tests for extract_code_from_input function."""

    def test_extracts_code_from_raw_input(self) -> None:
        """Test extracting a raw authorization code."""
        code = "oF8OSgZVMFb7a8Y3Dolrz4YPqDUnG7TCTsekYKcWnFNcmWWCJH7XJS3RN9d9NB0s"

        result = extract_code_from_input(code)

        assert result == code

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

    def test_raises_error_for_url_without_code(self) -> None:
        """Test that URL without code parameter raises error."""
        url = "https://embed.gog.com/on_login_success?origin=client"

        with pytest.raises(GogAuthError) as exc_info:
            extract_code_from_input(url)

        assert "code" in str(exc_info.value)

    def test_raises_error_for_short_input(self) -> None:
        """Test that short input raises error."""
        with pytest.raises(GogAuthError) as exc_info:
            extract_code_from_input("short")

        assert "too short" in str(exc_info.value)

    def test_strips_whitespace(self) -> None:
        """Test that whitespace is stripped."""
        code = "  oF8OSgZVMFb7a8Y3Dolrz4YPqDUnG7TCTsekYKcWnFNcmWWCJH7XJS3RN9d9NB0s  "

        result = extract_code_from_input(code)

        assert not result.startswith(" ")
        assert not result.endswith(" ")


class TestExchangeCodeForTokens:
    """Tests for exchange_code_for_tokens function."""

    @patch("src.web.gog_auth.requests.get")
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

    @patch("src.web.gog_auth.requests.get")
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

        with caplog.at_level(logging.ERROR, logger="src.web.gog_auth"):
            with pytest.raises(GogAuthError, match="Token exchange failed"):
                exchange_code_for_tokens("bad_code")

        assert "GOG token exchange failed with status 400" in caplog.text

    @patch("src.web.gog_auth.requests.get")
    def test_missing_refresh_token(self, mock_get: MagicMock) -> None:
        """Test response missing refresh_token."""
        mock_response = MagicMock(spec=requests.Response)
        mock_response.ok = True
        mock_response.json.return_value = {"access_token": "access123"}
        mock_get.return_value = mock_response

        with pytest.raises(GogAuthError) as exc_info:
            exchange_code_for_tokens("test_code")

        assert "refresh_token" in str(exc_info.value)

    @patch("src.web.gog_auth.requests.get")
    def test_network_failure_raises_gog_auth_error(self, mock_get: MagicMock) -> None:
        """Network error during token exchange raises GogAuthError."""
        mock_get.side_effect = requests.RequestException("Connection timed out")

        with pytest.raises(GogAuthError, match="Failed to connect to GOG servers"):
            exchange_code_for_tokens("test_code")


class TestSaveGogToken:
    """Tests for save_gog_token — DB persistence replaces config file writes."""

    @pytest.fixture()
    def storage(self, tmp_path: Path) -> StorageManager:
        """Create a StorageManager with a temp DB."""
        return StorageManager(sqlite_path=tmp_path / "test.db")

    def test_saves_token_to_db(self, storage: StorageManager) -> None:
        """Token is saved to encrypted DB storage."""
        save_gog_token(storage, "new_refresh_token")

        result = storage.get_credential(1, "gog", "refresh_token")
        assert result == "new_refresh_token"

    def test_overwrites_existing_token(self, storage: StorageManager) -> None:
        """Saving a new token overwrites the old one."""
        save_gog_token(storage, "old_token")
        save_gog_token(storage, "new_token")

        assert storage.get_credential(1, "gog", "refresh_token") == "new_token"

    def test_custom_user_id(self, storage: StorageManager) -> None:
        """Token can be saved for a specific user."""
        # Create user 2
        with storage.connection() as conn:
            conn.execute("INSERT INTO users (id, username) VALUES (2, 'user2')")
            conn.commit()

        save_gog_token(storage, "user2_token", user_id=2)

        assert storage.get_credential(2, "gog", "refresh_token") == "user2_token"
        assert storage.get_credential(1, "gog", "refresh_token") is None

    def test_db_failure_raises_gog_auth_error(self, storage: StorageManager) -> None:
        """DB write failure raises GogAuthError, not the underlying exception."""
        with patch.object(storage, "save_credential", side_effect=OSError("disk full")):
            with pytest.raises(GogAuthError, match="Failed to save GOG token"):
                save_gog_token(storage, "some_token")


class TestIsGogEnabled:
    """Tests for is_gog_enabled function."""

    def test_returns_true_when_enabled(self) -> None:
        """Test returns True when GOG is enabled."""
        config = {"inputs": {"gog": {"enabled": True}}}

        assert is_gog_enabled(config) is True

    def test_returns_false_when_disabled(self) -> None:
        """Test returns False when GOG is disabled."""
        config = {"inputs": {"gog": {"enabled": False}}}

        assert is_gog_enabled(config) is False

    def test_returns_false_when_missing(self) -> None:
        """Test returns False when GOG config is missing."""
        config = {"inputs": {}}

        assert is_gog_enabled(config) is False

    def test_returns_false_when_inputs_missing(self) -> None:
        """Test returns False when inputs section is missing."""
        config = {}

        assert is_gog_enabled(config) is False


class TestHasGogToken:
    """Tests for has_gog_token function."""

    @pytest.fixture()
    def storage(self, tmp_path: Path) -> StorageManager:
        """Create a StorageManager with a temp DB."""
        return StorageManager(sqlite_path=tmp_path / "test.db")

    def test_returns_true_when_token_in_config(self) -> None:
        """Config-only token is detected (backwards compat)."""
        config = {"inputs": {"gog": {"refresh_token": "some_token"}}}

        assert has_gog_token(config) is True

    def test_returns_false_when_token_empty(self) -> None:
        """Test returns False when refresh token is empty."""
        config = {"inputs": {"gog": {"refresh_token": ""}}}

        assert has_gog_token(config) is False

    def test_returns_false_when_token_missing(self) -> None:
        """Test returns False when refresh token is missing."""
        config = {"inputs": {"gog": {}}}

        assert has_gog_token(config) is False

    def test_returns_false_when_whitespace_only(self) -> None:
        """Test returns False when token is whitespace only."""
        config = {"inputs": {"gog": {"refresh_token": "   "}}}

        assert has_gog_token(config) is False

    def test_returns_true_when_token_in_db(self, storage: StorageManager) -> None:
        """DB token detected even when config has no token."""
        config = {"inputs": {"gog": {"refresh_token": ""}}}
        storage.save_credential(1, "gog", "refresh_token", "db_token")

        assert has_gog_token(config, storage=storage) is True

    def test_config_fallback_when_no_storage(self) -> None:
        """Without storage, only config is checked."""
        config = {"inputs": {"gog": {"refresh_token": "config_token"}}}

        assert has_gog_token(config, storage=None) is True
