"""The sign-in surface: setup, login, logout, session, and the account routes.

Every case runs against a real ``StorageManager``, because what is under test
is a credential check and a session row rather than a call being made.
"""

from __future__ import annotations

from collections.abc import Iterator
from http.cookies import SimpleCookie
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.storage.accounts import MIN_PASSWORD_LENGTH, SESSION_LIFETIME
from src.storage.manager import StorageManager
from src.web.api import router as api_router
from src.web.auth import SESSION_COOKIE, UNAUTHORIZED_DETAIL
from src.web.auth_api import _MAX_FAILURES, reset_login_throttle
from src.web.auth_api import router as auth_router
from tests.factories import booted_web_app

_USERNAME = "owner"
_PASSWORD = "correct horse battery"


@pytest.fixture(autouse=True)
def _forget_failed_logins() -> Iterator[None]:
    """The throttle is process-wide, so one test's failures reach the next."""
    reset_login_throttle()
    yield
    reset_login_throttle()


@pytest.fixture()
def storage(tmp_path: Path) -> StorageManager:
    return StorageManager(sqlite_path=tmp_path / "accounts.db")


@pytest.fixture()
def config() -> dict[str, Any]:
    return {"storage": {"database_path": "data/test.db"}}


@pytest.fixture()
def client(storage: StorageManager, config: dict[str, Any]) -> Iterator[TestClient]:
    """A signed-out client against a booted app on an unclaimed instance."""
    with booted_web_app(storage, config) as app:
        yield TestClient(app)


@pytest.fixture()
def claimed(storage: StorageManager, client: TestClient) -> TestClient:
    """The same client, signed in as the account that claimed the instance."""
    storage.claim_account(_USERNAME, "The Owner", _PASSWORD)
    response = client.post(
        "/api/auth/login", json={"username": _USERNAME, "password": _PASSWORD}
    )
    assert response.status_code == 200
    return client


def _set_cookie(response: Any) -> SimpleCookie:
    parsed = SimpleCookie()
    parsed.load(response.headers["set-cookie"])
    return parsed


class TestFirstRunSetup:
    """The one moment anybody may claim this instance."""

    def test_setup_claims_the_account_and_signs_the_claimant_in(
        self, client: TestClient, storage: StorageManager
    ) -> None:
        """The claimant does not then have to type the password again."""
        response = client.post(
            "/api/auth/setup",
            json={
                "username": _USERNAME,
                "display_name": "The Owner",
                "password": _PASSWORD,
            },
        )

        assert response.status_code == 200
        assert response.json() == {
            "id": 1,
            "username": _USERNAME,
            "display_name": "The Owner",
        }
        assert storage.account_is_claimed() is True
        assert client.get("/api/users/me").status_code == 200

    def test_a_blank_display_name_falls_back_to_the_username(
        self, client: TestClient
    ) -> None:
        """The form sends "" for the optional field, which is not a name."""
        response = client.post(
            "/api/auth/setup",
            json={"username": _USERNAME, "display_name": "  ", "password": _PASSWORD},
        )

        assert response.status_code == 200
        assert response.json()["display_name"] is None

    def test_a_second_setup_is_refused_and_writes_nothing(
        self, claimed: TestClient, storage: StorageManager
    ) -> None:
        """Otherwise the library goes to whoever asks for it second."""
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
        assert storage.verify_password(_USERNAME, _PASSWORD) is not None

    def test_a_short_password_is_refused(self, client: TestClient) -> None:
        """The one rule the server keeps, since no form may be trusted to."""
        response = client.post(
            "/api/auth/setup",
            json={
                "username": _USERNAME,
                "display_name": "",
                "password": "x" * (MIN_PASSWORD_LENGTH - 1),
            },
        )

        assert response.status_code == 422


class TestLogin:
    """What a password buys, and what a wrong one does not."""

    def test_the_right_password_opens_a_session(self, claimed: TestClient) -> None:
        assert claimed.get("/api/users/me").json()["username"] == _USERNAME

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
        """A message naming the username tells a guesser which one exists."""
        response = claimed.post(
            "/api/auth/login", json={"username": username, "password": password}
        )

        assert response.status_code == 401
        detail = response.json()["detail"]
        assert detail == "That username and password do not match an account."
        assert username not in detail

    def test_repeated_failures_are_throttled(self, claimed: TestClient) -> None:
        """An online guesser gets a handful of tries, not an unbounded run."""
        wrong = {"username": _USERNAME, "password": "not the password"}
        refusals = [
            claimed.post("/api/auth/login", json=wrong).status_code
            for _ in range(_MAX_FAILURES + 1)
        ]

        assert refusals[:_MAX_FAILURES] == [401] * _MAX_FAILURES
        assert refusals[-1] == 429

    def test_the_throttle_holds_against_the_right_password_too(
        self, claimed: TestClient
    ) -> None:
        """Otherwise a guesser who lands on it is simply let through."""
        wrong = {"username": _USERNAME, "password": "not the password"}
        for _ in range(_MAX_FAILURES):
            claimed.post("/api/auth/login", json=wrong)

        response = claimed.post(
            "/api/auth/login", json={"username": _USERNAME, "password": _PASSWORD}
        )

        assert response.status_code == 429

    def test_a_sign_in_that_works_clears_the_count(self, claimed: TestClient) -> None:
        """A typo before the right password must not spend the allowance."""
        wrong = {"username": _USERNAME, "password": "not the password"}
        for _ in range(_MAX_FAILURES - 1):
            claimed.post("/api/auth/login", json=wrong)
        claimed.post(
            "/api/auth/login", json={"username": _USERNAME, "password": _PASSWORD}
        )

        again = [
            claimed.post("/api/auth/login", json=wrong).status_code
            for _ in range(_MAX_FAILURES)
        ]

        assert again == [401] * _MAX_FAILURES


class TestTheSessionCookie:
    """The attributes are the whole of what protects it in a browser."""

    def test_it_is_httponly_strict_and_site_wide(self, client: TestClient) -> None:
        """No ``Secure``: this app serves no TLS, so the flag would silence it."""
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

    def test_it_lasts_exactly_as_long_as_the_session_row(
        self, client: TestClient
    ) -> None:
        """A browser dropping it early signs the user out of a live session."""
        response = client.post(
            "/api/auth/setup",
            json={"username": _USERNAME, "display_name": "", "password": _PASSWORD},
        )

        cookie = _set_cookie(response)[SESSION_COOKIE]
        assert int(cookie["max-age"]) == int(SESSION_LIFETIME.total_seconds())


class TestLogout:
    """Signing out has to reach the server, not just the browser."""

    def test_the_revoked_cookie_no_longer_authenticates(
        self, claimed: TestClient
    ) -> None:
        """Replayed by anyone who copied it, the old cookie must be dead."""
        stolen = claimed.cookies[SESSION_COOKIE]

        assert claimed.post("/api/auth/logout").status_code == 204

        claimed.cookies.set(SESSION_COOKIE, stolen)
        replay = claimed.get("/api/users/me")

        assert replay.status_code == 401
        assert replay.json()["detail"] == UNAUTHORIZED_DETAIL

    def test_it_clears_the_browser_copy_too(self, claimed: TestClient) -> None:
        """A cleared cookie is what stops the next page load 401ing."""
        response = claimed.post("/api/auth/logout")

        assert _set_cookie(response)[SESSION_COOKIE].value == ""

    def test_a_signed_out_caller_may_call_it(self, client: TestClient) -> None:
        """A 401 here would leave a stale cookie nobody can get rid of."""
        assert client.post("/api/auth/logout").status_code == 204


class TestTheSessionReport:
    """One call on boot decides between setup, login and the app itself."""

    def test_an_unclaimed_instance_reports_neither(self, client: TestClient) -> None:
        response = client.get("/api/auth/session")

        assert response.status_code == 200
        assert response.json() == {
            "claimed": False,
            "authenticated": False,
            "user": None,
        }

    def test_a_claimed_instance_signed_out_reports_the_claim_only(
        self, client: TestClient, storage: StorageManager
    ) -> None:
        """The SPA opens on the login form rather than offering setup again."""
        storage.claim_account(_USERNAME, "The Owner", _PASSWORD)

        body = client.get("/api/auth/session").json()

        assert body["claimed"] is True
        assert body["authenticated"] is False
        assert body["user"] is None

    def test_a_signed_in_caller_gets_the_account(self, claimed: TestClient) -> None:
        body = claimed.get("/api/auth/session").json()

        assert body == {
            "claimed": True,
            "authenticated": True,
            "user": {"id": 1, "username": _USERNAME, "display_name": "The Owner"},
        }


class TestTheAccountRoutes:
    """Shaped so a Users page is a new view rather than new plumbing."""

    def test_me_returns_the_signed_in_account(self, claimed: TestClient) -> None:
        response = claimed.get("/api/users/me")

        assert response.status_code == 200
        assert response.json() == {
            "id": 1,
            "username": _USERNAME,
            "display_name": "The Owner",
        }

    def test_a_rename_survives_the_next_request(self, claimed: TestClient) -> None:
        """The rename is not a session change, so the cookie keeps working."""
        response = claimed.patch(
            "/api/users/1", json={"username": "renamed", "display_name": "Renamed"}
        )

        assert response.status_code == 200
        assert claimed.get("/api/users/me").json() == {
            "id": 1,
            "username": "renamed",
            "display_name": "Renamed",
        }

    def test_a_rename_of_somebody_else_is_refused(self, claimed: TestClient) -> None:
        """A 404 would say which ids exist; nobody may edit another account."""
        response = claimed.patch(
            "/api/users/2", json={"username": "someone", "display_name": ""}
        )

        assert response.status_code == 403

    def test_a_password_change_costs_the_current_password(
        self, claimed: TestClient, storage: StorageManager
    ) -> None:
        """A borrowed unlocked browser must not be a permanent takeover."""
        response = claimed.put(
            "/api/users/1/password",
            json={"current_password": "not it", "new_password": "a new password"},
        )

        assert response.status_code == 401
        assert storage.verify_password(_USERNAME, _PASSWORD) is not None

    def test_a_password_change_signs_the_other_browsers_out(
        self, claimed: TestClient, storage: StorageManager
    ) -> None:
        """The point of changing it: whoever else is signed in stops being."""
        elsewhere = storage.create_session(1)

        response = claimed.put(
            "/api/users/1/password",
            json={"current_password": _PASSWORD, "new_password": "a new password"},
        )

        assert response.status_code == 204
        assert storage.lookup_session(elsewhere) is None
        # The browser that made the change keeps working, on the same cookie.
        assert claimed.get("/api/users/me").status_code == 200
        assert storage.verify_password(_USERNAME, "a new password") is not None

    def test_a_password_change_for_another_account_is_refused(
        self, claimed: TestClient
    ) -> None:
        response = claimed.put(
            "/api/users/2/password",
            json={"current_password": _PASSWORD, "new_password": "a new password"},
        )

        assert response.status_code == 403


class TestABareMountedRouterStillAuthenticates:
    """Regression: two test modules mounted the routers on a plain ``FastAPI``.

    Bug: the dependency was applied at ``include_router`` in ``create_app``, so
    every test in ``test_chat_api.py`` and ``test_themes.py`` ran signed out
    and nothing said so.
    Fix: the routers carry it themselves.
    """

    def test_the_api_router_refuses_an_anonymous_request(
        self, storage: StorageManager, config: dict[str, Any]
    ) -> None:
        bare = FastAPI()
        bare.include_router(api_router)

        # Booted only for the app state the routes read; the request goes to
        # the bare mount above.
        with booted_web_app(storage, config):
            response = TestClient(bare).get("/api/status")

        assert response.status_code == 401

    def test_the_sign_in_router_is_still_reachable_bare(
        self, storage: StorageManager, config: dict[str, Any]
    ) -> None:
        """The exemption travels with the router too, or setup is unreachable."""
        bare = FastAPI()
        bare.include_router(auth_router)

        with booted_web_app(storage, config):
            response = TestClient(bare).get("/api/auth/session")

        assert response.status_code == 200
