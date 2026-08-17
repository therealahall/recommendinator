"""Tests for Trakt OAuth device-code authentication."""

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import requests

from src.auth.trakt import (
    DevicePollStatus,
    TraktAuthError,
    has_trakt_token,
    poll_device_token,
    resolve_trakt_client_credentials,
    save_trakt_token,
    start_device_auth_flow,
)
from src.storage.manager import StorageManager


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

    @patch("src.auth.trakt.requests.post")
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

    @patch("src.auth.trakt.requests.post")
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


class TestPollDeviceToken:
    """Tests for poll_device_token covering each documented status code."""

    @patch("src.auth.trakt.requests.post")
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

    @patch("src.auth.trakt.requests.post")
    def test_pending(self, mock_post: MagicMock) -> None:
        """A 400 response means the user has not approved yet."""
        mock_post.return_value = _response(400)

        result = poll_device_token("dev123", "cid", "secret")

        assert result.status is DevicePollStatus.PENDING
        assert result.refresh_token is None

    @patch("src.auth.trakt.requests.post")
    def test_expired(self, mock_post: MagicMock) -> None:
        """A 410 response means the device code expired."""
        mock_post.return_value = _response(410)

        result = poll_device_token("dev123", "cid", "secret")

        assert result.status is DevicePollStatus.EXPIRED


class TestSaveTraktToken:
    """Tests for save_trakt_token persistence."""

    @pytest.fixture()
    def storage(self, tmp_path: Path) -> StorageManager:
        """Create a StorageManager with a temp DB."""
        return StorageManager(sqlite_path=tmp_path / "test.db")

    def test_saves_token_to_db(self, storage: StorageManager) -> None:
        """Token is saved to encrypted DB storage under source_id 'trakt'."""
        save_trakt_token(storage, "refresh-token")

        assert storage.credentials.get(1, "trakt", "refresh_token") == "refresh-token"


class TestResolveTraktClientCredentials:
    """Tests for resolve_trakt_client_credentials."""

    @pytest.fixture()
    def storage(self, tmp_path: Path) -> StorageManager:
        """Create a StorageManager with a temp DB."""
        return StorageManager(sqlite_path=tmp_path / "test.db")

    @staticmethod
    def _trakt_source(source_id: str = "trakt", **fields: str) -> dict[str, Any]:
        return {"inputs": {source_id: {"plugin": "trakt", "enabled": True, **fields}}}

    def test_resolves_from_resolved_inputs(self, storage: StorageManager) -> None:
        """client_id and client_secret come from the resolved Trakt config."""
        config = self._trakt_source(client_id="cid", client_secret="secret")

        assert resolve_trakt_client_credentials(config, storage) == ("cid", "secret")

    def test_a_source_running_another_plugin_is_refused(
        self, storage: StorageManager
    ) -> None:
        """The device flow's token is stored under the id it is asked about.

        The refusal is "not configured" rather than "client id and secret":
        unbound, this source resolves and only its missing fields complain.
        """
        config = {
            "inputs": {
                "my_games": {
                    "plugin": "gog",
                    "enabled": True,
                    "client_id": "cid",
                    "client_secret": "secret",
                }
            }
        }

        with pytest.raises(TraktAuthError, match="not configured"):
            resolve_trakt_client_credentials(config, storage, source_id="my_games")


class TestHasTraktToken:
    """Tests for has_trakt_token."""

    @pytest.fixture()
    def storage(self, tmp_path: Path) -> StorageManager:
        """Create a StorageManager with a temp DB."""
        return StorageManager(sqlite_path=tmp_path / "test.db")

    @staticmethod
    def _trakt_source(plugin: str = "trakt") -> dict[str, Any]:
        return {"inputs": {"trakt": {"plugin": plugin, "enabled": True}}}

    def test_true_when_token_present(self, storage: StorageManager) -> None:
        """Returns True when a refresh token is stored."""
        storage.credentials.save(1, "trakt", "refresh_token", "token")

        assert has_trakt_token(self._trakt_source(), storage) is True

    def test_another_plugins_source_is_never_reported_connected(
        self, storage: StorageManager
    ) -> None:
        """Regression: this reader took the id on trust, unlike its two twins.

        A GOG source called ``trakt`` answered True, and the CLI asked it with
        no ownership gate in front.
        """
        storage.credentials.save(1, "trakt", "refresh_token", "gog-token")

        assert has_trakt_token(self._trakt_source(plugin="gog"), storage) is False
