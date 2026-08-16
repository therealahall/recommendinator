"""The cross-origin refusal ``SameSite=Strict`` cannot make on its own.

A cookie's site ignores the port, so the Vite dev server carries it too. CORS
holds a preflight on ``PUT`` and on JSON bodies; the body-less ``POST`` was
what stayed open.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from src.storage.manager import StorageManager
from src.web.auth import SESSION_COOKIE
from src.web.csrf import CROSS_ORIGIN_DETAIL
from tests.factories import booted_web_app

_USERNAME = "owner"
_PASSWORD = "correct horse battery"

#: Every state change a browser can drive without a preflight: no request body,
#: so no ``Content-Type`` for CORS to hold it on.
_BODYLESS_POSTS = [
    "/api/config/reload",
    "/api/enrichment/stop",
    "/api/auth/logout",
]

_ELSEWHERE = ["cross-site", "same-site"]

_READ_ONLY_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def _state_changing_routes(client: TestClient) -> list[tuple[str, str]]:
    """Every ``(method, path)`` a browser could drive as a state change.

    Read off the running app rather than listed by hand, so a route added to
    any of the three routers is covered the day it is registered.
    """
    return sorted(
        (method, re.sub(r"{[^}]+}", "1", route.path))
        for route in client.app.routes
        if isinstance(route, APIRoute)
        for method in route.methods - _READ_ONLY_METHODS
    )


@pytest.fixture()
def storage(tmp_path: Path) -> StorageManager:
    return StorageManager(sqlite_path=tmp_path / "csrf.db")


@pytest.fixture()
def config() -> dict[str, Any]:
    return {"storage": {"database_path": "data/test.db"}}


@pytest.fixture()
def signed_in(storage: StorageManager, config: dict[str, Any]) -> Iterator[TestClient]:
    """A client holding the session cookie a neighbouring port would inherit."""
    with booted_web_app(storage, config) as app:
        client = TestClient(app)
        claimed = client.post(
            "/api/auth/setup",
            json={"username": _USERNAME, "display_name": "", "password": _PASSWORD},
        )
        assert claimed.status_code == 200
        yield client


class TestAStateChangeFromAnotherOrigin:
    """What a page on another localhost port can make the browser send."""

    @pytest.mark.parametrize("target", _BODYLESS_POSTS)
    @pytest.mark.parametrize("site", _ELSEWHERE)
    def test_it_is_refused_by_the_header_the_browser_sets(
        self, signed_in: TestClient, target: str, site: str
    ) -> None:
        """Page script cannot forge ``Sec-Fetch-Site``; only a browser sets it."""
        response = signed_in.post(target, headers={"Sec-Fetch-Site": site})

        assert response.status_code == 403
        assert response.json()["detail"] == CROSS_ORIGIN_DETAIL

    def test_the_refused_sign_out_leaves_the_session_open(
        self, signed_in: TestClient
    ) -> None:
        """The refusal has to be before the handler, not a message after it."""
        signed_in.post("/api/auth/logout", headers={"Sec-Fetch-Site": "cross-site"})

        assert signed_in.get("/api/users").status_code == 200

    def test_a_read_is_never_refused(self, signed_in: TestClient) -> None:
        """Reading is what ``SameSite`` already covers; refusing it buys nothing."""
        response = signed_in.get(
            "/api/status", headers={"Sec-Fetch-Site": "cross-site"}
        )

        assert response.status_code == 200


class TestNoStateChangingRouteIsLeftOpen:
    """The guard is a router dependency, so the sweep is what proves the reach.

    The three routes above are the ones CORS cannot hold, but a route that
    answered anything but 403 here would be one the guard never saw.
    """

    def test_every_one_of_them_refuses_a_cross_site_caller(
        self, signed_in: TestClient
    ) -> None:
        answered = {
            f"{method} {path}": signed_in.request(
                method, path, headers={"Sec-Fetch-Site": "cross-site"}
            ).status_code
            for method, path in _state_changing_routes(signed_in)
        }

        assert set(answered.values()) == {403}


class TestTheSameRequestFromTheAppItself:
    """Anchors the refusals above, which a route that 403s on everything passes."""

    @pytest.mark.parametrize("target", _BODYLESS_POSTS)
    def test_a_client_that_sends_no_header_reaches_it(
        self, signed_in: TestClient, target: str
    ) -> None:
        """The Docker health check sends none, and it is not a browser."""
        response = signed_in.post(target)

        assert response.status_code != 403

    def test_a_bookmark_or_typed_address_reaches_it(
        self, signed_in: TestClient
    ) -> None:
        """``none`` means no initiator at all, which no other page can cause."""
        response = signed_in.post(
            "/api/auth/logout", headers={"Sec-Fetch-Site": "none"}
        )

        assert response.status_code == 204
        assert SESSION_COOKIE not in signed_in.cookies
