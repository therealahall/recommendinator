"""Tests for GOG OAuth authentication."""

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.web.gog_auth import (
    GogAuthError,
    exchange_code_for_tokens,
    extract_code_from_input,
    get_gog_auth_url,
    has_gog_token,
    is_gog_enabled,
    update_config_with_token,
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
        mock_response = MagicMock()
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
    def test_exchange_failure(self, mock_get: MagicMock) -> None:
        """Test token exchange failure."""
        mock_response = MagicMock()
        mock_response.ok = False
        mock_response.status_code = 400
        mock_response.text = "Invalid code"
        mock_response.json.return_value = {"error_description": "Invalid code"}
        mock_get.return_value = mock_response

        with pytest.raises(GogAuthError, match="Token exchange failed"):
            exchange_code_for_tokens("bad_code")

    @patch("src.web.gog_auth.requests.get")
    def test_missing_refresh_token(self, mock_get: MagicMock) -> None:
        """Test response missing refresh_token."""
        mock_response = MagicMock()
        mock_response.ok = True
        mock_response.json.return_value = {"access_token": "access123"}
        mock_get.return_value = mock_response

        with pytest.raises(GogAuthError) as exc_info:
            exchange_code_for_tokens("test_code")

        assert "refresh_token" in str(exc_info.value)


class TestUpdateConfigWithToken:
    """Tests for update_config_with_token function."""

    def test_updates_existing_config(self) -> None:
        """Test updating an existing config file with GOG section."""
        config_content = """inputs:
  gog:
    refresh_token: ""
    enabled: true
"""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False
        ) as temp_file:
            temp_file.write(config_content)
            temp_path = Path(temp_file.name)

        try:
            update_config_with_token(temp_path, "new_refresh_token_123")

            updated_content = temp_path.read_text()
            assert "new_refresh_token_123" in updated_content
        finally:
            temp_path.unlink()

    def test_raises_error_for_missing_file(self) -> None:
        """GogAuthError propagates with original message when config file is absent.

        The exists check is outside the try block, so the error propagates
        directly without being caught by the broad except Exception clause.
        The message must not contain filesystem paths (security).
        """
        with pytest.raises(GogAuthError, match="Config file not found") as exc_info:
            update_config_with_token(Path("/nonexistent/config.yaml"), "token")

        error_message = str(exc_info.value)
        # Security: filesystem path must not leak in error message
        assert "/nonexistent" not in error_message
        assert "config.yaml" not in error_message

    def test_non_gog_error_is_wrapped(self) -> None:
        """Test that non-GogAuthError exceptions are wrapped with generic message."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False
        ) as temp_file:
            temp_file.write("inputs:\n  gog:\n    refresh_token: old\n")
            temp_path = Path(temp_file.name)

        try:
            with patch.object(
                Path, "read_text", side_effect=PermissionError("Permission denied")
            ):
                with pytest.raises(GogAuthError, match="Failed to update config file"):
                    update_config_with_token(temp_path, "new_token")
        finally:
            temp_path.unlink()


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

    def test_returns_true_when_token_present(self) -> None:
        """Test returns True when refresh token is present."""
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
