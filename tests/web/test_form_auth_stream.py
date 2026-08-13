"""The form-auth stream assembled: upgraded database, booted app, CLI reset.

Each piece was verified alone. The retired config key is spliced everywhere,
because ``tests/test_bearer_token_is_retired.py`` sweeps this tree for it.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner
from fastapi.testclient import TestClient

from src.storage.accounts import MIN_PASSWORD_LENGTH
from src.storage.manager import StorageManager
from src.storage.schema import _SCHEMA_VERSION
from src.web.auth import SESSION_COOKIE, UNAUTHORIZED_DETAIL
from src.web.auth_api import reset_login_throttle
from src.web.healthcheck import HEALTHY_STATUS
from src.web.state import app_state
from tests.cli.conftest import _invoke_with_mocks
from tests.factories import booted_web_app

_USERNAME = "owner"
_DISPLAY_NAME = "The Owner"
_PASSWORD = "correct horse battery"
_REPLACEMENT = "a replacement passphrase"

#: The shape the retired bootstrap credential took, as an upgrading operator
#: still has it sitting in ``config.yaml``.
_RETIRED_KEY = "api" + "_token"
_RETIRED_VALUE = "9f" * 32

#: An 0.32.0 library: rows keyed to user 1, the id everything hangs off.
_THE_LIBRARY = [("goodreads-1", "The Dispossessed"), ("steam-2", "Disco Elysium")]

_SETUP_BODY = {
    "username": _USERNAME,
    "display_name": _DISPLAY_NAME,
    "password": _PASSWORD,
}


@pytest.fixture(autouse=True)
def _forget_failed_logins() -> Iterator[None]:
    """The throttle is process-wide, so one module's failures reach the next."""
    reset_login_throttle()
    yield
    reset_login_throttle()


@pytest.fixture()
def config() -> dict[str, Any]:
    """Bootstrap config still carrying the key this release retired."""
    return {
        "storage": {"database_path": "data/test.db"},
        "web": {"host": "127.0.0.1", "port": 18473, _RETIRED_KEY: _RETIRED_VALUE},
    }


@pytest.fixture()
def upgrading_db(tmp_path: Path) -> Path:
    """A populated database in the shape the previous release left it.

    Built by taking the account columns back out rather than by restating DDL
    ``tests/storage/test_accounts.py`` already pins.
    """
    db_path = tmp_path / "upgrading.db"
    StorageManager(sqlite_path=db_path)

    conn = sqlite3.connect(db_path)
    try:
        conn.executemany(
            """INSERT INTO content_items
                   (user_id, external_id, title, content_type, status)
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
    """That database opened by this build, which is the upgrade itself."""
    return StorageManager(sqlite_path=upgrading_db)


@pytest.fixture()
def client(upgraded: StorageManager, config: dict[str, Any]) -> Iterator[TestClient]:
    """A signed-out client against the upgraded, still-unclaimed instance."""
    with booted_web_app(upgraded, config) as app:
        yield TestClient(app)


def _titles(response: Any) -> list[str]:
    return sorted(item["title"] for item in response.json())


class TestUpgradingAPopulatedInstall:
    """0.32.0 to this one: a full library, and a config key nothing reads.

    The storage tests prove the schema step; only from here is it visible that
    the server comes up on such a database at all.
    """

    def test_the_server_comes_up_and_opens_on_setup(self, client: TestClient) -> None:
        """A populated database is still an unclaimed instance."""
        response = client.get("/api/auth/session")

        assert response.status_code == 200
        assert response.json() == {
            "claimed": False,
            "authenticated": False,
            "user": None,
        }

    def test_claiming_signs_the_claimant_in_and_keeps_the_library(
        self, client: TestClient, upgraded: StorageManager
    ) -> None:
        """The claim renames user 1, so rows keyed to it stay reachable."""
        created = client.post("/api/auth/setup", json=_SETUP_BODY)

        assert created.status_code == 200
        assert created.json() == {
            "id": 1,
            "username": _USERNAME,
            "display_name": _DISPLAY_NAME,
        }
        library = client.get("/api/items", params={"user_id": 1, "limit": 50})
        assert library.status_code == 200
        assert _titles(library) == sorted(title for _, title in _THE_LIBRARY)
        assert [user["id"] for user in upgraded.get_all_users()] == [1]

    def test_setup_is_unreachable_once_that_instance_is_claimed(
        self, client: TestClient, upgraded: StorageManager
    ) -> None:
        """The window an upgrading operator is warned about closes for good."""
        client.post("/api/auth/setup", json=_SETUP_BODY)

        again = client.post(
            "/api/auth/setup",
            json={
                "username": "impostor",
                "display_name": "",
                "password": "another password",
            },
        )

        assert again.status_code == 409
        assert upgraded.verify_password(_USERNAME, _PASSWORD) is not None
        assert upgraded.verify_password("impostor", "another password") is None

    def test_the_retired_config_key_neither_stops_the_boot_nor_leaks(
        self, client: TestClient
    ) -> None:
        """Characterises the leftover key as inert: unread, and echoed nowhere.

        Nothing strips it and nothing warns, so an upgrader learns it is dead
        only from the release notes.
        """
        client.post("/api/auth/setup", json=_SETUP_BODY)

        running = app_state.config or {}
        assert running["web"][_RETIRED_KEY] == _RETIRED_VALUE
        for path in ("/api/status", "/api/settings"):
            answered = client.get(path)
            assert answered.status_code == 200, path
            assert _RETIRED_VALUE not in answered.text


class TestTheHealthCheckReadsTheGatesOwnRefusal:
    """``HEALTHY_STATUS`` is 401, which holds only while the gate answers 401.

    ``tests/docker/test_healthcheck.py`` proves the probe maps that to exit 0.
    This is the other half.
    """

    def test_an_anonymous_status_call_answers_what_the_probe_calls_healthy(
        self, client: TestClient
    ) -> None:
        response = client.get("/api/status")

        assert response.status_code == HEALTHY_STATUS
        assert response.json()["detail"] == UNAUTHORIZED_DETAIL

    def test_a_signed_in_status_call_answers_two_hundred(
        self, client: TestClient
    ) -> None:
        """Anchors the refusal above: the route works, the caller was nobody."""
        client.post("/api/auth/setup", json=_SETUP_BODY)

        assert client.get("/api/status").status_code == 200


class TestTheSessionOutlivesTheBrowserThatOpenedIt:
    """A 30-day cookie is worth only what the row behind it is worth."""

    def test_a_restored_cookie_alone_still_authenticates(
        self, client: TestClient, upgraded: StorageManager, config: dict[str, Any]
    ) -> None:
        """A browser restart: the cookie, and nothing else, comes back."""
        client.post("/api/auth/setup", json=_SETUP_BODY)
        restored = client.cookies[SESSION_COOKIE]

        with booted_web_app(upgraded, config) as restarted:
            reopened = TestClient(restarted, cookies={SESSION_COOKIE: restored})
            response = reopened.get("/api/users/me")

        assert response.status_code == 200
        assert response.json()["username"] == _USERNAME

    def test_signing_out_kills_the_cookie_a_restart_would_restore(
        self, client: TestClient, upgraded: StorageManager, config: dict[str, Any]
    ) -> None:
        """Revoked server-side, or the copy on disk outlives the sign-out."""
        client.post("/api/auth/setup", json=_SETUP_BODY)
        restored = client.cookies[SESSION_COOKIE]
        assert client.post("/api/auth/logout").status_code == 204

        with booted_web_app(upgraded, config) as restarted:
            reopened = TestClient(restarted, cookies={SESSION_COOKIE: restored})
            response = reopened.get("/api/users/me")

        assert response.status_code == 401
        assert response.json()["detail"] == UNAUTHORIZED_DETAIL


class TestTwoBrowsersSignedInAtOnce:
    """One account, two sessions: what each one does to the other."""

    @pytest.fixture()
    def both(self, client: TestClient) -> tuple[TestClient, TestClient]:
        """The claiming browser, and a second signed in beside it."""
        client.post("/api/auth/setup", json=_SETUP_BODY)
        elsewhere = TestClient(client.app)
        signed_in = elsewhere.post(
            "/api/auth/login", json={"username": _USERNAME, "password": _PASSWORD}
        )
        assert signed_in.status_code == 200
        assert client.cookies[SESSION_COOKIE] != elsewhere.cookies[SESSION_COOKIE]
        return client, elsewhere

    def test_both_are_signed_in_on_cookies_of_their_own(
        self, both: tuple[TestClient, TestClient]
    ) -> None:
        """Anchors the two cases below, which a signed-out pair would pass."""
        here, elsewhere = both

        assert here.get("/api/users/me").status_code == 200
        assert elsewhere.get("/api/users/me").status_code == 200

    def test_signing_one_out_leaves_the_other_signed_in(
        self, both: tuple[TestClient, TestClient]
    ) -> None:
        """Sign out ends this browser's session, not the account's."""
        here, elsewhere = both

        assert elsewhere.post("/api/auth/logout").status_code == 204

        assert here.get("/api/users/me").status_code == 200
        assert elsewhere.get("/api/users/me").status_code == 401

    def test_a_password_change_keeps_the_caller_and_drops_the_other(
        self, both: tuple[TestClient, TestClient]
    ) -> None:
        """The point of the change: every browser but this one signs out."""
        here, elsewhere = both

        changed = here.put(
            "/api/users/1/password",
            json={"current_password": _PASSWORD, "new_password": _REPLACEMENT},
        )

        assert changed.status_code == 204
        assert here.get("/api/users/me").status_code == 200
        assert elsewhere.get("/api/users/me").status_code == 401


class TestRenamingTheAccountFromSettings:
    """The username is the login, so a rename that drops it is a lockout."""

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

    def test_clearing_the_display_name_leaves_the_username_to_label_by(
        self, signed_in: TestClient
    ) -> None:
        """What the sidebar falls back to when the optional field is emptied."""
        response = signed_in.patch(
            "/api/users/1", json={"username": _USERNAME, "display_name": "  "}
        )

        assert response.status_code == 200
        assert response.json()["display_name"] is None
        assert signed_in.get("/api/auth/session").json()["user"] == {
            "id": 1,
            "username": _USERNAME,
            "display_name": None,
        }


class TestTheBreakGlassResetAgainstARunningServer:
    """The CLI opens its own manager on the file the server is serving.

    What ``docker compose exec`` does while the container is up.
    """

    @pytest.fixture()
    def signed_in(self, client: TestClient) -> TestClient:
        response = client.post("/api/auth/setup", json=_SETUP_BODY)
        assert response.status_code == 200
        return client

    @staticmethod
    def _reset(db_path: Path) -> Any:
        """Run the reset as an operator does: its own manager on that file."""
        return _invoke_with_mocks(
            CliRunner(),
            ["account", "set-password"],
            StorageManager(sqlite_path=db_path),
            input_text=f"{_REPLACEMENT}\n{_REPLACEMENT}\n",
        )

    def test_the_reset_ends_the_live_browser_session(
        self, signed_in: TestClient, upgrading_db: Path
    ) -> None:
        """The acceptance claim: a session someone else holds dies with it."""
        result = self._reset(upgrading_db)

        assert result.exit_code == 0, result.output
        assert signed_in.get("/api/users/me").status_code == 401

    def test_the_new_password_signs_in_through_the_running_server(
        self, signed_in: TestClient, upgrading_db: Path
    ) -> None:
        """A reset nobody can then use is a lockout with extra steps."""
        self._reset(upgrading_db)

        refused = signed_in.post(
            "/api/auth/login", json={"username": _USERNAME, "password": _PASSWORD}
        )
        accepted = signed_in.post(
            "/api/auth/login", json={"username": _USERNAME, "password": _REPLACEMENT}
        )

        assert refused.status_code == 401
        assert accepted.status_code == 200
        assert signed_in.get("/api/users/me").json()["username"] == _USERNAME


class TestMalformedSignInInput:
    """The four sign-in routes are all an anonymous caller may reach."""

    @pytest.mark.parametrize(
        ("body", "reason"),
        [
            pytest.param({"display_name": "", "password": _PASSWORD}, "no username"),
            pytest.param(
                {"username": "", "display_name": "", "password": _PASSWORD},
                "blank username",
            ),
            pytest.param(
                {"username": "x" * 101, "display_name": "", "password": _PASSWORD},
                "username past the column's width",
            ),
            pytest.param(
                {
                    "username": _USERNAME,
                    "display_name": "d" * 101,
                    "password": _PASSWORD,
                },
                "display name past the column's width",
            ),
            pytest.param(
                {
                    "username": _USERNAME,
                    "display_name": "",
                    "password": "x" * (MIN_PASSWORD_LENGTH - 1),
                },
                "password under the floor",
            ),
            pytest.param({"username": _USERNAME, "display_name": ""}, "no password"),
        ],
    )
    def test_setup_refuses_it_and_leaves_the_instance_unclaimed(
        self,
        client: TestClient,
        upgraded: StorageManager,
        body: dict[str, str],
        reason: str,
    ) -> None:
        """A half-claimed instance would be reachable by nobody at all."""
        response = client.post("/api/auth/setup", json=body)

        assert response.status_code == 422, reason
        assert upgraded.account_is_claimed() is False
        assert client.get("/api/auth/session").json()["claimed"] is False

    @pytest.mark.parametrize(
        ("body", "reason"),
        [
            pytest.param({"password": _PASSWORD}, "no username"),
            pytest.param({"username": "", "password": _PASSWORD}, "blank username"),
            pytest.param({"username": _USERNAME}, "no password"),
        ],
    )
    def test_login_refuses_it_without_opening_a_session(
        self, client: TestClient, body: dict[str, str], reason: str
    ) -> None:
        client.post("/api/auth/setup", json=_SETUP_BODY)
        client.cookies.clear()

        response = client.post("/api/auth/login", json=body)

        assert response.status_code == 422, reason
        assert SESSION_COOKIE not in response.cookies
        assert client.get("/api/users/me").status_code == 401

    def test_an_empty_password_reaches_the_check_and_is_refused(
        self, client: TestClient
    ) -> None:
        """Login carries no minimum, so "" is a credential, not a shape error."""
        client.post("/api/auth/setup", json=_SETUP_BODY)
        client.cookies.clear()

        response = client.post(
            "/api/auth/login", json={"username": _USERNAME, "password": ""}
        )

        assert response.status_code == 401
        assert client.get("/api/users/me").status_code == 401
