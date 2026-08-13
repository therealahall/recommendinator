"""The sign-in surface: setup, login, logout, session, and the account routes.

Every case runs against a real ``StorageManager``, because what is under test
is a credential check and a session row rather than a call being made.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import timedelta
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
from src.utils.dates import utc_now
from src.web.api import router as api_router
from src.web.auth import SESSION_COOKIE, UNAUTHORIZED_DETAIL
from src.web.auth_api import (
    _LOCKOUT,
    _MAX_FAILURES,
    _MAX_TOTAL_FAILURES,
    reset_login_throttle,
)
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
        assert client.get("/api/auth/session").json()["authenticated"] is True

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

    def test_a_short_password_is_refused_in_words_the_form_can_show(
        self, client: TestClient, storage: StorageManager
    ) -> None:
        """Regression: the floor was a Pydantic ``min_length``.

        A 422 renders ``detail`` as a list, ``stringDetail`` returns undefined
        for one, and the setup screen — which nobody can skip — showed "check
        the details and try again" instead of the rule.
        """
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
        assert str(MIN_PASSWORD_LENGTH) in PASSWORD_TOO_SHORT
        assert storage.account_is_claimed() is False


class TestLogin:
    """What a password buys, and what a wrong one does not."""

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

    def test_a_fresh_username_every_time_still_runs_out(
        self, claimed: TestClient
    ) -> None:
        """Regression: the count was per username, and nothing bounded the sum.

        An unknown username costs a full scrypt run, by design, so varying it
        bought unlimited 16 MiB hashes from an anonymous caller.
        """
        refusals = [
            claimed.post(
                "/api/auth/login", json={"username": f"guess{n}", "password": "x"}
            ).status_code
            for n in range(_MAX_TOTAL_FAILURES + 1)
        ]

        assert refusals[:_MAX_TOTAL_FAILURES] == [401] * _MAX_TOTAL_FAILURES
        assert refusals[-1] == 429

    def test_the_lockout_ends_when_the_window_does(
        self, claimed: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The refusal tells the operator to wait, so waiting has to work.

        Every other case reads the counter from inside the window: drop the
        comparison against it and they all stay green.
        """
        now = utc_now()
        monkeypatch.setattr("src.web.auth_api.utc_now", lambda: now)
        wrong = {"username": _USERNAME, "password": "not the password"}
        for _ in range(_MAX_FAILURES):
            claimed.post("/api/auth/login", json=wrong)
        assert claimed.post("/api/auth/login", json=wrong).status_code == 429

        later = now + _LOCKOUT + timedelta(seconds=1)
        monkeypatch.setattr("src.web.auth_api.utc_now", lambda: later)
        response = claimed.post(
            "/api/auth/login", json={"username": _USERNAME, "password": _PASSWORD}
        )

        assert response.status_code == 200


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

    def test_every_authenticated_request_re_issues_it(
        self, claimed: TestClient
    ) -> None:
        """Regression: the row rolled forward and the browser's copy did not.

        ``Max-Age`` was fixed at sign-in, so a daily user was signed out on day
        30 of a session that was still live.
        """
        later = claimed.get("/api/users")

        assert later.status_code == 200
        cookie = _set_cookie(later)[SESSION_COOKIE]
        assert cookie.value == claimed.cookies[SESSION_COOKIE]
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
        replay = claimed.get("/api/users")

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
            "min_password_length": MIN_PASSWORD_LENGTH,
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
        """The web counterpart of ``account show``: who this browser is."""
        body = claimed.get("/api/auth/session").json()

        assert body == {
            "claimed": True,
            "authenticated": True,
            "user": {"id": 1, "username": _USERNAME, "display_name": "The Owner"},
            "min_password_length": MIN_PASSWORD_LENGTH,
        }


class TestTheAccountRoutes:
    """Shaped so a Users page is a new view rather than new plumbing."""

    def test_a_rename_survives_the_next_request(self, claimed: TestClient) -> None:
        """The rename is not a session change, so the cookie keeps working."""
        response = claimed.patch(
            "/api/users/1", json={"username": "renamed", "display_name": "Renamed"}
        )

        assert response.status_code == 200
        assert claimed.get("/api/auth/session").json()["user"] == {
            "id": 1,
            "username": "renamed",
            "display_name": "Renamed",
        }

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
        """Regression: ``min_length=1`` with no strip stored "  " as a username.

        The sign-in form trims, so that account could never be authenticated
        again and this instance has no reset link.
        """
        response = claimed.patch(
            "/api/users/1", json={"username": username, "display_name": ""}
        )

        assert response.status_code == 422
        assert storage.get_all_users()[0]["username"] == _USERNAME

    def test_a_padded_username_is_stored_trimmed(self, claimed: TestClient) -> None:
        """Anchors the refusals above: padding alone is not what is rejected."""
        response = claimed.patch(
            "/api/users/1", json={"username": "  keeper  ", "display_name": "  Kee  "}
        )

        assert response.status_code == 200
        assert response.json() == {
            "id": 1,
            "username": "keeper",
            "display_name": "Kee",
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
        assert claimed.get("/api/users").status_code == 200
        assert storage.verify_password(_USERNAME, "a new password") is not None

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
        """The same floor as setup, and reported the same way — see setup's case."""
        response = claimed.put(
            "/api/users/1/password",
            json={
                "current_password": _PASSWORD,
                "new_password": "x" * (MIN_PASSWORD_LENGTH - 1),
            },
        )

        assert response.status_code == 400
        assert response.json()["detail"] == PASSWORD_TOO_SHORT
        assert storage.verify_password(_USERNAME, _PASSWORD) is not None

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
        """The blank check is ``str.strip``, which is not ASCII-space only.

        A name a form's own trim would empty must not become the one this
        instance signs in with, whichever space character it is built from.
        """
        response = claimed.patch(
            "/api/users/1", json={"username": username, "display_name": ""}
        )

        assert response.status_code == 422
        assert storage.get_all_users()[0]["username"] == _USERNAME

    def test_a_username_at_the_cap_is_stored(self, claimed: TestClient) -> None:
        """Anchors the width refusals: the boundary is past 100, not at it."""
        response = claimed.patch(
            "/api/users/1", json={"username": "x" * 100, "display_name": ""}
        )

        assert response.status_code == 200
        assert response.json()["username"] == "x" * 100


class TestBothCredentialsAreRequired:
    """A field left out of the body is not a field the server may default."""

    @pytest.mark.parametrize(
        ("route", "body"),
        [
            pytest.param(
                "/api/auth/setup",
                {"display_name": "", "password": _PASSWORD},
                id="setup-no-username",
            ),
            pytest.param(
                "/api/auth/setup",
                {"username": _USERNAME, "display_name": ""},
                id="setup-no-password",
            ),
            pytest.param(
                "/api/auth/login", {"password": _PASSWORD}, id="login-no-username"
            ),
            pytest.param(
                "/api/auth/login", {"username": _USERNAME}, id="login-no-password"
            ),
        ],
    )
    def test_a_missing_field_is_refused_without_claiming_or_signing_in(
        self,
        client: TestClient,
        storage: StorageManager,
        route: str,
        body: dict[str, str],
    ) -> None:
        response = client.post(route, json=body)

        assert response.status_code == 422
        assert SESSION_COOKIE not in response.cookies
        assert storage.account_is_claimed() is False


class TestTheInstanceWideLockoutAlsoEnds:
    """The total counter locks out every username at once, the operator's too.

    Its window is the same one, and nothing else proves that half ages out: a
    total that never decayed would end the instance on the fiftieth guess.
    """

    def test_the_operator_signs_in_again_once_the_window_passes(
        self, claimed: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        now = utc_now()
        monkeypatch.setattr("src.web.auth_api.utc_now", lambda: now)
        for n in range(_MAX_TOTAL_FAILURES):
            claimed.post(
                "/api/auth/login", json={"username": f"guess{n}", "password": "x"}
            )
        right = {"username": _USERNAME, "password": _PASSWORD}
        assert claimed.post("/api/auth/login", json=right).status_code == 429

        later = now + _LOCKOUT + timedelta(seconds=1)
        monkeypatch.setattr("src.web.auth_api.utc_now", lambda: later)

        assert claimed.post("/api/auth/login", json=right).status_code == 200


def _lapse_every_session(storage: StorageManager) -> None:
    """Age every session row out. Only its digest is stored, not its token."""
    with storage.sqlite_db.connection() as conn:
        conn.execute("UPDATE sessions SET expires_at = '2000-01-01T00:00:00'")
        conn.commit()


def _session_rows(storage: StorageManager) -> int:
    with storage.sqlite_db.connection() as conn:
        return int(conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0])


class TestLapsedSessionsAreSweptAtStartup:
    """Nothing else deleted one, so the table grew for the database's life."""

    def test_boot_drops_them_and_leaves_the_live_session_alone(
        self, storage: StorageManager, config: dict[str, Any]
    ) -> None:
        storage.claim_account(_USERNAME, "The Owner", _PASSWORD)
        storage.create_session(1)
        _lapse_every_session(storage)
        live = storage.create_session(1)
        assert _session_rows(storage) == 2

        with booted_web_app(storage, config):
            pass

        assert _session_rows(storage) == 1
        assert storage.lookup_session(live) is not None


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
