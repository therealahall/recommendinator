from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import requests

from src.auth.trakt import (
    DevicePollStatus,
    TraktAuthError,
    poll_device_token,
    resolve_trakt_client_credentials,
    save_trakt_token,
    start_device_auth_flow,
)
from src.storage.manager import StorageManager


def _response(status_code: int, json_body: dict[str, Any] | None = None) -> MagicMock:
    response = MagicMock(spec=requests.Response)
    response.status_code = status_code
    response.json.return_value = json_body or {}
    if status_code >= 400:
        response.raise_for_status.side_effect = requests.HTTPError(
            f"status {status_code}"
        )
    return response


class TestStartDeviceAuthFlow:
    @patch("src.auth.trakt.requests.post")
    def test_returns_device_and_user_code(self, mock_post: MagicMock) -> None:
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
        _, kwargs = mock_post.call_args
        assert kwargs["json"] == {"client_id": "client_id_value"}

    @patch("src.auth.trakt.requests.post")
    def test_non_http_verification_url_rejected(self, mock_post: MagicMock) -> None:
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
    @patch("src.auth.trakt.requests.post")
    def test_success_returns_refresh_token(self, mock_post: MagicMock) -> None:
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
        mock_post.return_value = _response(400)

        result = poll_device_token("dev123", "cid", "secret")

        assert result.status is DevicePollStatus.PENDING
        assert result.refresh_token is None

    @patch("src.auth.trakt.requests.post")
    def test_expired(self, mock_post: MagicMock) -> None:
        mock_post.return_value = _response(410)

        result = poll_device_token("dev123", "cid", "secret")

        assert result.status is DevicePollStatus.EXPIRED


class TestSaveTraktToken:
    @pytest.fixture()
    def storage(self, tmp_path: Path) -> StorageManager:
        return StorageManager(sqlite_path=tmp_path / "test.db")

    def test_saves_token_to_db(self, storage: StorageManager) -> None:
        save_trakt_token(storage, "refresh-token")

        assert storage.credentials.get(1, "trakt", "refresh_token") == "refresh-token"


class TestResolveTraktClientCredentials:
    @pytest.fixture()
    def storage(self, tmp_path: Path) -> StorageManager:
        return StorageManager(sqlite_path=tmp_path / "test.db")

    def test_resolves_from_the_source_row_and_its_credentials(
        self, storage: StorageManager
    ) -> None:
        storage.sources.upsert(1, "trakt", "trakt", {"client_id": "cid"}, enabled=True)
        storage.credentials.save(1, "trakt", "client_secret", "secret")

        assert resolve_trakt_client_credentials(storage) == ("cid", "secret")

    def test_a_source_running_another_plugin_is_refused(
        self, storage: StorageManager
    ) -> None:
        storage.sources.upsert(1, "my_games", "gog", {"client_id": "cid"}, enabled=True)
        storage.credentials.save(1, "my_games", "client_secret", "secret")

        with pytest.raises(TraktAuthError, match="not configured"):
            resolve_trakt_client_credentials(storage, source_id="my_games")
