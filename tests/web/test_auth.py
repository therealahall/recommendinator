from __future__ import annotations

from collections.abc import Iterator
from http.cookies import SimpleCookie
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.storage.accounts import (
    MIN_PASSWORD_LENGTH,
    PASSWORD_TOO_SHORT,
    SESSION_LIFETIME,
)
from src.storage.manager import StorageManager
from src.web.api import router as api_router
from src.web.auth import SESSION_COOKIE, UNAUTHORIZED_DETAIL
from src.web.auth_api import router as auth_router
from tests.factories import booted_web_app

_USERNAME = "owner"
_PASSWORD = "correct horse battery"


@pytest.fixture()
def storage(tmp_path: Path) -> StorageManager:
    return StorageManager(sqlite_path=tmp_path / "accounts.db")


@pytest.fixture()
def config() -> dict[str, Any]:
    return {"storage": {"database_path": "data/test.db"}}


@pytest.fixture()
def client(storage: StorageManager, config: dict[str, Any]) -> Iterator[TestClient]:
    with booted_web_app(storage, config) as app:
        yield TestClient(app)


@pytest.fixture()
def claimed(storage: StorageManager, client: TestClient) -> TestClient:
    storage.accounts.claim(_USERNAME, "The Owner", _PASSWORD)
    response = client.post(
        "/api/auth/login", json={"username": _USERNAME, "password": _PASSWORD}
    )
    assert response.status_code == 200
    return client


def _set_cookie(response: Any) -> SimpleCookie:
    parsed = SimpleCookie()
    parsed.load(response.headers["set-cookie"])
    return parsed


def _account(storage: StorageManager, **names: Any) -> dict[str, Any]:
    record = storage.accounts.describe(1)
    assert record is not None
    return {"id": 1, **names, "password_updated_at": record["password_updated_at"]}


class TestFirstRunSetup:
    def test_setup_claims_the_account_and_signs_the_claimant_in(
        self, client: TestClient, storage: StorageManager
    ) -> None:
        response = client.post(
            "/api/auth/setup",
            json={
                "username": _USERNAME,
                "display_name": "The Owner",
                "password": _PASSWORD,
            },
        )

        assert response.status_code == 200
        assert response.json() == _account(
            storage, username=_USERNAME, display_name="The Owner"
        )
        assert storage.accounts.is_claimed() is True
        assert client.get("/api/auth/session").json()["authenticated"] is True

    def test_a_blank_display_name_falls_back_to_the_username(
        self, client: TestClient
    ) -> None:
        response = client.post(
            "/api/auth/setup",
            json={"username": _USERNAME, "display_name": "  ", "password": _PASSWORD},
        )

        assert response.status_code == 200
        assert response.json()["display_name"] is None

    def test_a_second_setup_is_refused_and_writes_nothing(
        self, claimed: TestClient, storage: StorageManager
    ) -> None:
        response = claimed.post(
            "/api/auth/setup",
            json={
                "username": "impostor",
                "display_name": "Impostor",
                "password": "another password",
            },
        )

        assert response.status_code == 409
        assert storage.get_all_users()[0]["username"] == _USERNAME
        assert storage.accounts.verify_password(_USERNAME, _PASSWORD) is not None

    def test_a_short_password_is_refused_in_words_the_form_can_show(
        self, client: TestClient, storage: StorageManager
    ) -> None:
        response = client.post(
            "/api/auth/setup",
            json={
                "username": _USERNAME,
                "display_name": "",
                "password": "x" * (MIN_PASSWORD_LENGTH - 1),
            },
        )

        assert response.status_code == 400
        assert response.json()["detail"] == PASSWORD_TOO_SHORT
        assert storage.accounts.is_claimed() is False


class TestLogin:
    def test_the_right_password_opens_a_session(self, claimed: TestClient) -> None:
        body = claimed.get("/api/auth/session").json()

        assert body["authenticated"] is True
        assert body["user"]["username"] == _USERNAME

    @pytest.mark.parametrize(
        ("username", "password"),
        [
            pytest.param(_USERNAME, "not the password", id="wrong-password"),
            pytest.param("nobody", _PASSWORD, id="unknown-username"),
        ],
    )
    def test_a_refusal_names_neither_half(
        self, claimed: TestClient, username: str, password: str
    ) -> None:
        response = claimed.post(
            "/api/auth/login", json={"username": username, "password": password}
        )

        assert response.status_code == 401
        detail = response.json()["detail"]
        assert detail == "That username and password do not match an account."
        assert username not in detail

    def test_a_typo_costs_the_operator_nothing_but_the_retry(
        self, claimed: TestClient
    ) -> None:
        wrong = {"username": _USERNAME, "password": "not the password"}
        refusals = [
            claimed.post("/api/auth/login", json=wrong).status_code for _ in range(10)
        ]

        response = claimed.post(
            "/api/auth/login", json={"username": _USERNAME, "password": _PASSWORD}
        )

        assert refusals == [401] * 10
        assert response.status_code == 200


class TestTheSessionCookie:
    def test_it_is_httponly_strict_and_site_wide(self, client: TestClient) -> None:
        client.post(
            "/api/auth/setup",
            json={"username": _USERNAME, "display_name": "", "password": _PASSWORD},
        )
        response = client.post(
            "/api/auth/login", json={"username": _USERNAME, "password": _PASSWORD}
        )

        cookie = _set_cookie(response)[SESSION_COOKIE]
        assert cookie["httponly"] is True
        assert cookie["samesite"].lower() == "strict"
        assert cookie["path"] == "/"
        assert cookie["secure"] == ""

    def test_every_authenticated_request_re_issues_it(
        self, claimed: TestClient
    ) -> None:
        later = claimed.get("/api/users")

        assert later.status_code == 200
        cookie = _set_cookie(later)[SESSION_COOKIE]
        assert cookie.value == claimed.cookies[SESSION_COOKIE]
        assert int(cookie["max-age"]) == int(SESSION_LIFETIME.total_seconds())

    def test_a_route_returning_a_response_re_issues_it_too_regression(
        self, claimed: TestClient
    ) -> None:
        export = claimed.get("/api/items/export?type=book&format=json")

        assert export.status_code == 200
        cookie = _set_cookie(export)[SESSION_COOKIE]
        assert cookie.value == claimed.cookies[SESSION_COOKIE]
        assert int(cookie["max-age"]) == int(SESSION_LIFETIME.total_seconds())

    def test_a_lapsed_session_is_not_handed_its_cookie_back(
        self, claimed: TestClient, storage: StorageManager
    ) -> None:
        _lapse_every_session(storage)

        response = claimed.get("/api/users")

        assert response.status_code == 401
        assert "set-cookie" not in response.headers


class TestLogout:
    def test_the_revoked_cookie_no_longer_authenticates(
        self, claimed: TestClient
    ) -> None:
        stolen = claimed.cookies[SESSION_COOKIE]

        assert claimed.post("/api/auth/logout").status_code == 204

        claimed.cookies.set(SESSION_COOKIE, stolen)
        replay = claimed.get("/api/users")

        assert replay.status_code == 401
        assert replay.json()["detail"] == UNAUTHORIZED_DETAIL

    def test_it_clears_the_browser_copy_too(self, claimed: TestClient) -> None:
        response = claimed.post("/api/auth/logout")

        assert _set_cookie(response)[SESSION_COOKIE].value == ""

    def test_a_signed_out_caller_may_call_it(self, client: TestClient) -> None:
        assert client.post("/api/auth/logout").status_code == 204


class TestTheSessionReport:
    def test_an_unclaimed_instance_reports_neither(self, client: TestClient) -> None:
        response = client.get("/api/auth/session")

        assert response.status_code == 200
        assert response.json() == {
            "claimed": False,
            "authenticated": False,
            "user": None,
            "min_password_length": MIN_PASSWORD_LENGTH,
        }

    def test_a_claimed_instance_signed_out_reports_the_claim_only(
        self, client: TestClient, storage: StorageManager
    ) -> None:
        storage.accounts.claim(_USERNAME, "The Owner", _PASSWORD)

        body = client.get("/api/auth/session").json()

        assert body["claimed"] is True
        assert body["authenticated"] is False
        assert body["user"] is None

    def test_a_signed_in_caller_gets_the_account(
        self, claimed: TestClient, storage: StorageManager
    ) -> None:
        body = claimed.get("/api/auth/session").json()

        assert body == {
            "claimed": True,
            "authenticated": True,
            "user": _account(storage, username=_USERNAME, display_name="The Owner"),
            "min_password_length": MIN_PASSWORD_LENGTH,
        }


class TestTheAccountRoutes:
    def test_a_rename_survives_the_next_request(
        self, claimed: TestClient, storage: StorageManager
    ) -> None:
        response = claimed.patch(
            "/api/users/1", json={"username": "renamed", "display_name": "Renamed"}
        )

        assert response.status_code == 200
        assert claimed.get("/api/auth/session").json()["user"] == _account(
            storage, username="renamed", display_name="Renamed"
        )

    @pytest.mark.parametrize(
        "username",
        [
            pytest.param("   ", id="only-spaces"),
            pytest.param("x" * 101, id="past-the-column"),
        ],
    )
    def test_a_username_no_sign_in_form_could_send_is_refused(
        self, claimed: TestClient, storage: StorageManager, username: str
    ) -> None:
        response = claimed.patch(
            "/api/users/1", json={"username": username, "display_name": ""}
        )

        assert response.status_code == 422
        assert storage.get_all_users()[0]["username"] == _USERNAME

    def test_a_padded_username_is_stored_trimmed(
        self, claimed: TestClient, storage: StorageManager
    ) -> None:
        response = claimed.patch(
            "/api/users/1", json={"username": "  keeper  ", "display_name": "  Kee  "}
        )

        assert response.status_code == 200
        assert response.json() == _account(
            storage, username="keeper", display_name="Kee"
        )

    def test_a_rename_of_somebody_else_is_refused(self, claimed: TestClient) -> None:
        response = claimed.patch(
            "/api/users/2", json={"username": "someone", "display_name": ""}
        )

        assert response.status_code == 403

    def test_a_password_change_costs_the_current_password(
        self, claimed: TestClient, storage: StorageManager
    ) -> None:
        response = claimed.put(
            "/api/users/1/password",
            json={"current_password": "not it", "new_password": "a new password"},
        )

        assert response.status_code == 401
        assert storage.accounts.verify_password(_USERNAME, _PASSWORD) is not None

    def test_a_password_change_signs_the_other_browsers_out(
        self, claimed: TestClient, storage: StorageManager
    ) -> None:
        elsewhere = storage.accounts.create_session(1)

        response = claimed.put(
            "/api/users/1/password",
            json={"current_password": _PASSWORD, "new_password": "a new password"},
        )

        assert response.status_code == 204
        assert storage.accounts.lookup_session(elsewhere) is None
        assert claimed.get("/api/users").status_code == 200
        assert storage.accounts.verify_password(_USERNAME, "a new password") is not None

    def test_a_password_change_for_another_account_is_refused(
        self, claimed: TestClient
    ) -> None:
        response = claimed.put(
            "/api/users/2/password",
            json={"current_password": _PASSWORD, "new_password": "a new password"},
        )

        assert response.status_code == 403

    def test_a_short_new_password_is_refused_in_words_the_form_can_show(
        self, claimed: TestClient, storage: StorageManager
    ) -> None:
        response = claimed.put(
            "/api/users/1/password",
            json={
                "current_password": _PASSWORD,
                "new_password": "x" * (MIN_PASSWORD_LENGTH - 1),
            },
        )

        assert response.status_code == 400
        assert response.json()["detail"] == PASSWORD_TOO_SHORT
        assert storage.accounts.verify_password(_USERNAME, _PASSWORD) is not None

    @pytest.mark.parametrize(
        "username",
        [
            pytest.param("  ", id="non-breaking-space"),
            pytest.param("　", id="ideographic-space"),
            pytest.param("\t\n", id="tab-and-a-newline"),
        ],
    )
    def test_a_username_blank_in_any_alphabet_is_refused(
        self, claimed: TestClient, storage: StorageManager, username: str
    ) -> None:
        response = claimed.patch(
            "/api/users/1", json={"username": username, "display_name": ""}
        )

        assert response.status_code == 422
        assert storage.get_all_users()[0]["username"] == _USERNAME


def _lapse_every_session(storage: StorageManager) -> None:
    with storage.sqlite_db.connection() as conn:
        conn.execute("UPDATE sessions SET expires_at = '2000-01-01T00:00:00'")
        conn.commit()


def _session_rows(storage: StorageManager) -> int:
    with storage.sqlite_db.connection() as conn:
        return int(conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0])


class TestLapsedSessionsAreSweptAtStartup:
    def test_boot_drops_them_and_leaves_the_live_session_alone(
        self, storage: StorageManager, config: dict[str, Any]
    ) -> None:
        storage.accounts.claim(_USERNAME, "The Owner", _PASSWORD)
        storage.accounts.create_session(1)
        _lapse_every_session(storage)
        live = storage.accounts.create_session(1)
        assert _session_rows(storage) == 2

        with booted_web_app(storage, config):
            pass

        assert _session_rows(storage) == 1
        assert storage.accounts.lookup_session(live) is not None


class TestABareMountedRouterStillAuthenticates:
    def test_the_api_router_refuses_an_anonymous_request(
        self, storage: StorageManager, config: dict[str, Any]
    ) -> None:
        bare = FastAPI()
        bare.include_router(api_router)

        with booted_web_app(storage, config):
            response = TestClient(bare).get("/api/status")

        assert response.status_code == 401

    def test_the_sign_in_router_is_still_reachable_bare(
        self, storage: StorageManager, config: dict[str, Any]
    ) -> None:
        bare = FastAPI()
        bare.include_router(auth_router)

        with booted_web_app(storage, config):
            response = TestClient(bare).get("/api/auth/session")

        assert response.status_code == 200
