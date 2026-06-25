"""Tests for Trakt OAuth device-code authentication."""

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import requests

from src.storage.manager import StorageManager
from src.web.trakt_auth import (
    DevicePollStatus,
    TraktAuthError,
    is_trakt_connected,
    poll_device_token,
    resolve_trakt_client_credentials,
    save_trakt_token,
    start_device_auth_flow,
)


def _response(status_code: int, json_body: dict[str, Any] | None = None) -> MagicMock:
    """Build a mock requests.Response with a status code and JSON body."""
    response = MagicMock(spec=requests.Response)
    response.status_code = status_code
    response.json.return_value = json_body or {}
    if status_code >= 400:
        response.raise_for_status.side_effect = requests.HTTPError(
            f"status {status_code}"
        )
    return response


class TestStartDeviceAuthFlow:
    """Tests for start_device_auth_flow."""

    @patch("src.web.trakt_auth.requests.post")
    def test_returns_device_and_user_code(self, mock_post: MagicMock) -> None:
        """A successful request returns the device/user code fields."""
        mock_post.return_value = _response(
            200,
            {
                "device_code": "dev123",
                "user_code": "ABCD1234",
                "verification_url": "https://trakt.tv/activate",
                "expires_in": 600,
                "interval": 5,
            },
        )

        result = start_device_auth_flow("client_id_value")

        assert result["device_code"] == "dev123"
        assert result["user_code"] == "ABCD1234"
        assert result["verification_url"] == "https://trakt.tv/activate"
        assert result["expires_in"] == 600
        assert result["interval"] == 5
        # client_id is sent in the body, never returned to callers.
        _, kwargs = mock_post.call_args
        assert kwargs["json"] == {"client_id": "client_id_value"}

    @pytest.mark.parametrize(
        "missing_field",
        ["device_code", "user_code", "verification_url"],
    )
    @patch("src.web.trakt_auth.requests.post")
    def test_incomplete_response_raises(
        self, mock_post: MagicMock, missing_field: str
    ) -> None:
        """A response missing any required field raises TraktAuthError.

        Every required field must be validated, not just device_code — a
        response missing user_code or verification_url is equally unusable.
        """
        body = {
            "device_code": "dev123",
            "user_code": "ABCD1234",
            "verification_url": "https://trakt.tv/activate",
        }
        del body[missing_field]
        mock_post.return_value = _response(200, body)

        with pytest.raises(TraktAuthError, match="incomplete"):
            start_device_auth_flow("client_id_value")

    @patch("src.web.trakt_auth.requests.post")
    def test_non_http_verification_url_rejected(self, mock_post: MagicMock) -> None:
        """A ``javascript:`` verification URL is rejected before being returned.

        The URL is bound to a Vue ``:href``; Vue does not sanitize
        ``javascript:`` URIs, so a hostile/compromised response must not reach
        the client.
        """
        mock_post.return_value = _response(
            200,
            {
                "device_code": "dev123",
                "user_code": "ABCD1234",
                "verification_url": "javascript:alert(1)",
            },
        )

        with pytest.raises(TraktAuthError, match="invalid verification URL"):
            start_device_auth_flow("client_id_value")

    @patch("src.web.trakt_auth.requests.post")
    def test_network_failure_raises(self, mock_post: MagicMock) -> None:
        """A network error raises TraktAuthError."""
        mock_post.side_effect = requests.RequestException("boom")

        with pytest.raises(TraktAuthError, match="Failed to start"):
            start_device_auth_flow("client_id_value")


class TestPollDeviceToken:
    """Tests for poll_device_token covering each documented status code."""

    @patch("src.web.trakt_auth.requests.post")
    def test_success_returns_refresh_token(self, mock_post: MagicMock) -> None:
        """A 200 response yields SUCCESS and the refresh token."""
        mock_post.return_value = _response(
            200,
            {"access_token": "access", "refresh_token": "refresh-xyz"},
        )

        result = poll_device_token("dev123", "cid", "secret")

        assert result.status is DevicePollStatus.SUCCESS
        assert result.refresh_token == "refresh-xyz"
        _, kwargs = mock_post.call_args
        assert kwargs["json"] == {
            "code": "dev123",
            "client_id": "cid",
            "client_secret": "secret",
        }

    @patch("src.web.trakt_auth.requests.post")
    def test_success_missing_token_raises(self, mock_post: MagicMock) -> None:
        """A 200 response without a refresh token raises."""
        mock_post.return_value = _response(200, {"access_token": "access"})

        with pytest.raises(TraktAuthError, match="missing refresh_token"):
            poll_device_token("dev123", "cid", "secret")

    @patch("src.web.trakt_auth.requests.post")
    def test_pending(self, mock_post: MagicMock) -> None:
        """A 400 response means the user has not approved yet."""
        mock_post.return_value = _response(400)

        result = poll_device_token("dev123", "cid", "secret")

        assert result.status is DevicePollStatus.PENDING
        assert result.refresh_token is None

    @patch("src.web.trakt_auth.requests.post")
    def test_slow_down(self, mock_post: MagicMock) -> None:
        """A 429 response means back off and keep polling."""
        mock_post.return_value = _response(429)

        result = poll_device_token("dev123", "cid", "secret")

        assert result.status is DevicePollStatus.SLOW_DOWN

    @patch("src.web.trakt_auth.requests.post")
    def test_expired(self, mock_post: MagicMock) -> None:
        """A 410 response means the device code expired."""
        mock_post.return_value = _response(410)

        result = poll_device_token("dev123", "cid", "secret")

        assert result.status is DevicePollStatus.EXPIRED

    @patch("src.web.trakt_auth.requests.post")
    def test_denied(self, mock_post: MagicMock) -> None:
        """A 418 response means the user denied the request."""
        mock_post.return_value = _response(418)

        result = poll_device_token("dev123", "cid", "secret")

        assert result.status is DevicePollStatus.DENIED

    @patch("src.web.trakt_auth.requests.post")
    def test_invalid_device_code_raises(self, mock_post: MagicMock) -> None:
        """A 404 response raises TraktAuthError."""
        mock_post.return_value = _response(404)

        with pytest.raises(TraktAuthError, match="invalid or unknown"):
            poll_device_token("dev123", "cid", "secret")

    @patch("src.web.trakt_auth.requests.post")
    def test_already_used_raises(self, mock_post: MagicMock) -> None:
        """A 409 response raises TraktAuthError."""
        mock_post.return_value = _response(409)

        with pytest.raises(TraktAuthError, match="already been used"):
            poll_device_token("dev123", "cid", "secret")

    @patch("src.web.trakt_auth.requests.post")
    def test_network_failure_raises(self, mock_post: MagicMock) -> None:
        """A network error raises TraktAuthError."""
        mock_post.side_effect = requests.RequestException("boom")

        with pytest.raises(TraktAuthError, match="Failed to reach Trakt"):
            poll_device_token("dev123", "cid", "secret")


class TestSaveTraktToken:
    """Tests for save_trakt_token persistence."""

    @pytest.fixture()
    def storage(self, tmp_path: Path) -> StorageManager:
        """Create a StorageManager with a temp DB."""
        return StorageManager(sqlite_path=tmp_path / "test.db")

    def test_saves_token_to_db(self, storage: StorageManager) -> None:
        """Token is saved to encrypted DB storage under source_id 'trakt'."""
        save_trakt_token(storage, "refresh-token")

        assert storage.get_credential(1, "trakt", "refresh_token") == "refresh-token"

    def test_custom_user_id(self, storage: StorageManager) -> None:
        """Token can be saved for a specific user."""
        with storage.connection() as conn:
            conn.execute("INSERT INTO users (id, username) VALUES (2, 'user2')")
            conn.commit()

        save_trakt_token(storage, "user2-token", user_id=2)

        assert storage.get_credential(2, "trakt", "refresh_token") == "user2-token"
        assert storage.get_credential(1, "trakt", "refresh_token") is None

    def test_db_failure_raises(self, storage: StorageManager) -> None:
        """DB write failure raises TraktAuthError, not the underlying error."""
        with patch.object(storage, "save_credential", side_effect=OSError("disk full")):
            with pytest.raises(TraktAuthError, match="Failed to save Trakt token"):
                save_trakt_token(storage, "token")


class TestResolveTraktClientCredentials:
    """Tests for resolve_trakt_client_credentials."""

    def test_resolves_from_resolved_inputs(self) -> None:
        """client_id and client_secret come from the resolved Trakt config."""
        storage = MagicMock(spec=StorageManager)
        resolved = MagicMock()
        resolved.source_id = "trakt"
        resolved.config = {"client_id": "cid", "client_secret": "secret"}
        with patch("src.web.trakt_auth.resolve_inputs", return_value=[resolved]):
            client_id, client_secret = resolve_trakt_client_credentials({}, storage)

        assert client_id == "cid"
        assert client_secret == "secret"

    def test_missing_source_raises(self) -> None:
        """When no Trakt source is resolved, an error is raised."""
        storage = MagicMock(spec=StorageManager)
        with patch("src.web.trakt_auth.resolve_inputs", return_value=[]):
            with pytest.raises(TraktAuthError, match="not configured"):
                resolve_trakt_client_credentials({}, storage)

    def test_missing_secret_raises(self) -> None:
        """When the secret is absent, an actionable error is raised."""
        storage = MagicMock(spec=StorageManager)
        resolved = MagicMock()
        resolved.source_id = "trakt"
        resolved.config = {"client_id": "cid", "client_secret": ""}
        with patch("src.web.trakt_auth.resolve_inputs", return_value=[resolved]):
            with pytest.raises(TraktAuthError, match="client id and secret"):
                resolve_trakt_client_credentials({}, storage)


class TestIsTraktConnected:
    """Tests for is_trakt_connected."""

    @pytest.fixture()
    def storage(self, tmp_path: Path) -> StorageManager:
        """Create a StorageManager with a temp DB."""
        return StorageManager(sqlite_path=tmp_path / "test.db")

    def test_true_when_token_present(self, storage: StorageManager) -> None:
        """Returns True when a refresh token is stored."""
        storage.save_credential(1, "trakt", "refresh_token", "token")

        assert is_trakt_connected(storage) is True

    def test_false_when_absent(self, storage: StorageManager) -> None:
        """Returns False when no token is stored."""
        assert is_trakt_connected(storage) is False

    def test_false_without_storage(self) -> None:
        """Returns False when storage is unavailable."""
        assert is_trakt_connected(None) is False
