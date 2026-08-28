from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from src.storage.manager import StorageManager
from src.web.auth import SESSION_COOKIE
from src.web.csrf import CROSS_ORIGIN_DETAIL
from tests.factories import booted_web_app, served_api_operations

_USERNAME = "owner"
_PASSWORD = "correct horse battery"

_BODYLESS_POSTS = [
    "/api/config/reload",
    "/api/enrichment/stop",
    "/api/auth/logout",
]

_READ_ONLY_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def _state_changing_routes(client: TestClient) -> list[tuple[str, str]]:
    return sorted(
        (method, re.sub(r"{[^}]+}", "1", path))
        for method, path, _ in served_api_operations(client.app)
        if method not in _READ_ONLY_METHODS
    )


@pytest.fixture()
def storage(tmp_path: Path) -> StorageManager:
    return StorageManager(sqlite_path=tmp_path / "csrf.db")


@pytest.fixture()
def config() -> dict[str, Any]:
    return {"storage": {"database_path": "data/test.db"}}


@pytest.fixture()
def signed_in(storage: StorageManager, config: dict[str, Any]) -> Iterator[TestClient]:
    with booted_web_app(storage, config) as app:
        client = TestClient(app)
        claimed = client.post(
            "/api/auth/setup",
            json={"username": _USERNAME, "display_name": "", "password": _PASSWORD},
        )
        assert claimed.status_code == 200
        yield client


class TestAStateChangeFromAnotherOrigin:
    @pytest.mark.parametrize("target", _BODYLESS_POSTS)
    def test_it_is_refused_by_the_header_the_browser_sets(
        self, signed_in: TestClient, target: str
    ) -> None:
        response = signed_in.post(target, headers={"Sec-Fetch-Site": "same-site"})

        assert response.status_code == 403
        assert response.json()["detail"] == CROSS_ORIGIN_DETAIL

    def test_the_refused_sign_out_leaves_the_session_open(
        self, signed_in: TestClient
    ) -> None:
        signed_in.post("/api/auth/logout", headers={"Sec-Fetch-Site": "cross-site"})

        assert signed_in.get("/api/users").status_code == 200

    def test_a_read_is_never_refused(self, signed_in: TestClient) -> None:
        response = signed_in.get(
            "/api/status", headers={"Sec-Fetch-Site": "cross-site"}
        )

        assert response.status_code == 200


class TestNoStateChangingRouteIsLeftOpen:
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
    @pytest.mark.parametrize("target", _BODYLESS_POSTS)
    def test_a_client_that_sends_no_header_reaches_it(
        self, signed_in: TestClient, target: str
    ) -> None:
        response = signed_in.post(target)

        assert response.status_code != 403

    def test_a_bookmark_or_typed_address_reaches_it(
        self, signed_in: TestClient
    ) -> None:
        response = signed_in.post(
            "/api/auth/logout", headers={"Sec-Fetch-Site": "none"}
        )

        assert response.status_code == 204
        assert SESSION_COOKIE not in signed_in.cookies
