from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner
from fastapi.testclient import TestClient

from src.storage.manager import StorageManager
from src.storage.schema import _SCHEMA_VERSION
from src.web.auth import SESSION_COOKIE, UNAUTHORIZED_DETAIL
from src.web.healthcheck import HEALTHY_STATUS
from tests.cli.conftest import _invoke_with_mocks
from tests.factories import booted_web_app

_USERNAME = "owner"
_DISPLAY_NAME = "The Owner"
_PASSWORD = "correct horse battery"
_REPLACEMENT = "a replacement passphrase"

_RETIRED_KEY = "api_token"
_RETIRED_VALUE = "9f" * 32

_THE_LIBRARY = [("goodreads_csv", "The Dispossessed"), ("steam", "Disco Elysium")]

_SETUP_BODY = {
    "username": _USERNAME,
    "display_name": _DISPLAY_NAME,
    "password": _PASSWORD,
}


@pytest.fixture()
def config() -> dict[str, Any]:
    return {
        "storage": {"database_path": "data/test.db"},
        "web": {"host": "127.0.0.1", "port": 18473, _RETIRED_KEY: _RETIRED_VALUE},
    }


@pytest.fixture()
def upgrading_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "upgrading.db"
    StorageManager(sqlite_path=db_path)

    conn = sqlite3.connect(db_path)
    try:
        conn.executemany(
            """INSERT INTO content_items
                   (user_id, source, title, content_type, status)
               VALUES (1, ?, ?, 'book', 'completed')""",
            _THE_LIBRARY,
        )
        for column in ("password_hash", "password_salt", "password_updated_at"):
            conn.execute(f"ALTER TABLE users DROP COLUMN {column}")
        conn.execute("DROP TABLE sessions")
        conn.execute(f"PRAGMA user_version = {_SCHEMA_VERSION - 1}")
        conn.commit()
    finally:
        conn.close()
    return db_path


@pytest.fixture()
def upgraded(upgrading_db: Path) -> StorageManager:
    return StorageManager(sqlite_path=upgrading_db)


@pytest.fixture()
def client(upgraded: StorageManager, config: dict[str, Any]) -> Iterator[TestClient]:
    with booted_web_app(upgraded, config) as app:
        yield TestClient(app)


def _titles(response: Any) -> list[str]:
    return sorted(item["title"] for item in response.json())


class TestUpgradingAPopulatedInstall:
    def test_claiming_signs_the_claimant_in_and_keeps_the_library(
        self, client: TestClient, upgraded: StorageManager
    ) -> None:
        created = client.post("/api/auth/setup", json=_SETUP_BODY)

        stamped = upgraded.accounts.describe(1)
        assert created.status_code == 200
        assert stamped is not None
        assert created.json() == {
            "id": 1,
            "username": _USERNAME,
            "display_name": _DISPLAY_NAME,
            "password_updated_at": stamped["password_updated_at"],
        }
        library = client.get("/api/items", params={"user_id": 1, "limit": 50})
        assert library.status_code == 200
        assert _titles(library) == sorted(title for _, title in _THE_LIBRARY)
        assert [user["id"] for user in upgraded.get_all_users()] == [1]


class TestTheHealthCheckReadsTheGatesOwnRefusal:
    def test_an_anonymous_status_call_answers_what_the_probe_calls_healthy(
        self, client: TestClient
    ) -> None:
        response = client.get("/api/status")

        assert response.status_code == HEALTHY_STATUS
        assert response.json()["detail"] == UNAUTHORIZED_DETAIL


class TestTheSessionOutlivesTheBrowserThatOpenedIt:
    def test_a_restored_cookie_alone_still_authenticates(
        self, client: TestClient, upgraded: StorageManager, config: dict[str, Any]
    ) -> None:
        client.post("/api/auth/setup", json=_SETUP_BODY)
        restored = client.cookies[SESSION_COOKIE]

        with booted_web_app(upgraded, config) as restarted:
            reopened = TestClient(restarted, cookies={SESSION_COOKIE: restored})
            response = reopened.get("/api/auth/session")

        assert response.status_code == 200
        assert response.json()["user"]["username"] == _USERNAME


class TestTwoBrowsersSignedInAtOnce:
    @pytest.fixture()
    def both(self, client: TestClient) -> tuple[TestClient, TestClient]:
        client.post("/api/auth/setup", json=_SETUP_BODY)
        elsewhere = TestClient(client.app)
        signed_in = elsewhere.post(
            "/api/auth/login", json={"username": _USERNAME, "password": _PASSWORD}
        )
        assert signed_in.status_code == 200
        assert client.cookies[SESSION_COOKIE] != elsewhere.cookies[SESSION_COOKIE]
        return client, elsewhere

    def test_signing_one_out_leaves_the_other_signed_in(
        self, both: tuple[TestClient, TestClient]
    ) -> None:
        here, elsewhere = both

        assert elsewhere.post("/api/auth/logout").status_code == 204

        assert here.get("/api/users").status_code == 200
        assert elsewhere.get("/api/users").status_code == 401


class TestRenamingTheAccountFromSettings:
    @pytest.fixture()
    def signed_in(self, client: TestClient) -> TestClient:
        response = client.post("/api/auth/setup", json=_SETUP_BODY)
        assert response.status_code == 200
        return client

    def test_the_new_name_signs_in_and_the_old_one_stops(
        self, signed_in: TestClient
    ) -> None:
        renamed = signed_in.patch(
            "/api/users/1", json={"username": "keeper", "display_name": "Keeper"}
        )
        assert signed_in.post("/api/auth/logout").status_code == 204

        refused = signed_in.post(
            "/api/auth/login", json={"username": _USERNAME, "password": _PASSWORD}
        )
        accepted = signed_in.post(
            "/api/auth/login", json={"username": "keeper", "password": _PASSWORD}
        )

        assert renamed.status_code == 200
        assert refused.status_code == 401
        assert accepted.status_code == 200
        assert accepted.json()["display_name"] == "Keeper"


class TestPasswordAgeReachesTheAccountScreenRegression:
    @pytest.fixture()
    def signed_in(self, client: TestClient) -> TestClient:
        response = client.post("/api/auth/setup", json=_SETUP_BODY)
        assert response.status_code == 200
        return client

    def test_the_session_call_carries_the_password_age_regression(
        self, signed_in: TestClient
    ) -> None:
        user = signed_in.get("/api/auth/session").json()["user"]

        assert user["password_updated_at"] is not None

    def test_a_password_change_moves_what_that_screen_reads_regression(
        self, signed_in: TestClient
    ) -> None:
        before = signed_in.get("/api/auth/session").json()["user"]

        changed = signed_in.put(
            "/api/users/1/password",
            json={"current_password": _PASSWORD, "new_password": _REPLACEMENT},
        )
        after = signed_in.get("/api/auth/session").json()["user"]

        assert changed.status_code == 204
        assert after["password_updated_at"] >= before["password_updated_at"]


class TestTheBreakGlassResetAgainstARunningServer:
    @pytest.fixture()
    def signed_in(self, client: TestClient) -> TestClient:
        response = client.post("/api/auth/setup", json=_SETUP_BODY)
        assert response.status_code == 200
        return client

    @staticmethod
    def _reset(db_path: Path) -> Any:
        return _invoke_with_mocks(
            CliRunner(),
            ["account", "set-password"],
            StorageManager(sqlite_path=db_path),
            input_text=f"{_REPLACEMENT}\n{_REPLACEMENT}\n",
        )

    def test_the_reset_ends_the_live_browser_session(
        self, signed_in: TestClient, upgrading_db: Path
    ) -> None:
        result = self._reset(upgrading_db)

        assert result.exit_code == 0, result.output
        assert signed_in.get("/api/users").status_code == 401

    def test_the_new_password_signs_in_through_the_running_server(
        self, signed_in: TestClient, upgrading_db: Path
    ) -> None:
        self._reset(upgrading_db)

        refused = signed_in.post(
            "/api/auth/login", json={"username": _USERNAME, "password": _PASSWORD}
        )
        accepted = signed_in.post(
            "/api/auth/login", json={"username": _USERNAME, "password": _REPLACEMENT}
        )

        assert refused.status_code == 401
        assert accepted.status_code == 200
        assert (
            signed_in.get("/api/auth/session").json()["user"]["username"] == _USERNAME
        )


class TestMalformedSignInInput:
    def test_an_empty_password_reaches_the_check_and_is_refused(
        self, client: TestClient
    ) -> None:
        client.post("/api/auth/setup", json=_SETUP_BODY)
        client.cookies.clear()

        response = client.post(
            "/api/auth/login", json={"username": _USERNAME, "password": ""}
        )

        assert response.status_code == 401
        assert client.get("/api/users").status_code == 401
