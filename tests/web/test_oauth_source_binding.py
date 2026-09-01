from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from src.auth.trakt import DevicePollResult, DevicePollStatus
from src.sources.service import delete_source, resolve_inputs
from src.storage.manager import StorageManager
from tests.factories import MALFORMED_IDS, authenticated_client, booted_web_app

USER_ID = 1


@pytest.fixture()
def storage(tmp_path: Path) -> StorageManager:
    return StorageManager(sqlite_path=tmp_path / "test.db")


@pytest.fixture()
def config() -> dict[str, Any]:
    return {
        "storage": {"database_path": "data/test.db"},
        "inputs": {
            "gog_work": {"plugin": "gog", "enabled": True},
            "epic_work": {"plugin": "epic_games", "enabled": True},
            "trakt_work": {"plugin": "trakt", "enabled": True, "client_id": "cid"},
        },
    }


@contextmanager
def booted_client(
    storage: StorageManager,
    config: dict[str, Any],
    migrate_credentials: bool = False,
) -> Iterator[TestClient]:
    with booted_web_app(
        storage, config, migrate_credentials=migrate_credentials
    ) as app:
        yield authenticated_client(app)


@pytest.fixture()
def client(storage: StorageManager, config: dict[str, Any]) -> Iterator[TestClient]:
    for source_id, entry in config["inputs"].items():
        fields = {k: v for k, v in entry.items() if k not in ("plugin", "enabled")}
        storage.sources.upsert(
            USER_ID, source_id, entry["plugin"], fields, enabled=True
        )

    with booted_client(storage, config) as test_client:
        yield test_client


WRITE_ROUTES: list[tuple[str, dict[str, str] | None, str]] = [
    (
        "/api/gog/exchange",
        {"code_or_url": "an-authorization-code-long-enough"},
        "src.web.api._oauth.exchange_gog_tokens",
    ),
    (
        "/api/epic/exchange",
        {"code_or_json": "an-authorization-code-long-enough"},
        "src.web.api._oauth.exchange_epic_tokens",
    ),
    (
        "/api/trakt/start-device-flow",
        None,
        "src.web.api._oauth.start_device_auth_flow",
    ),
    (
        "/api/trakt/poll-device-approval",
        {"device_code": "dev1234567"},
        "src.web.api._oauth.poll_device_token",
    ),
]

READ_ROUTES = [
    ("GET", "/api/gog/status"),
    ("GET", "/api/epic/status"),
    ("GET", "/api/trakt/status"),
    ("DELETE", "/api/gog/token"),
    ("DELETE", "/api/epic/token"),
    ("DELETE", "/api/trakt/token"),
]


def resolved_config(
    config: dict[str, Any], storage: StorageManager, source_id: str
) -> dict[str, Any]:
    for resolved in resolve_inputs(config, storage=storage, user_id=USER_ID):
        if resolved.source_id == source_id:
            return resolved.config
    raise AssertionError(f"{source_id} did not resolve, so its reader is unchecked")


class TestOAuthConnectSourceBindingRegression:
    def test_gog_round_trip_regression(
        self, client: TestClient, storage: StorageManager, config: dict[str, Any]
    ) -> None:
        with (
            patch("src.web.api._oauth.extract_gog_code", return_value="code"),
            patch(
                "src.web.api._oauth.exchange_gog_tokens",
                return_value={"refresh_token": "gog-work-token"},
            ),
        ):
            response = client.post(
                "/api/gog/exchange?source_id=gog_work", json={"code_or_url": "code"}
            )

        assert response.status_code == 200
        assert (
            resolved_config(config, storage, "gog_work")["refresh_token"]
            == "gog-work-token"
        )
        assert storage.credentials.get(USER_ID, "gog", "refresh_token") is None
        assert client.get("/api/gog/status?source_id=gog_work").json()["connected"]
        assert not client.get("/api/gog/status").json()["connected"]

        delete_source("gog_work", storage, config, user_id=USER_ID)

        assert storage.credentials.get(USER_ID, "gog_work", "refresh_token") is None

    def test_trakt_round_trip_regression(
        self, client: TestClient, storage: StorageManager, config: dict[str, Any]
    ) -> None:
        storage.credentials.save(USER_ID, "trakt_work", "client_secret", "secret")

        with patch(
            "src.web.api._oauth.poll_device_token",
            return_value=DevicePollResult(DevicePollStatus.SUCCESS, "trakt-work-token"),
        ):
            response = client.post(
                "/api/trakt/poll-device-approval?source_id=trakt_work",
                json={"device_code": "dev1234567"},
            )

        assert response.status_code == 200
        assert (
            resolved_config(config, storage, "trakt_work")["refresh_token"]
            == "trakt-work-token"
        )
        assert storage.credentials.get(USER_ID, "trakt", "refresh_token") is None
        assert client.get("/api/trakt/status?source_id=trakt_work").json()["connected"]
        assert not client.get("/api/trakt/status").json()["connected"]

        delete_source("trakt_work", storage, config, user_id=USER_ID)

        assert storage.credentials.get(USER_ID, "trakt_work", "refresh_token") is None


@pytest.fixture()
def db_only_config() -> dict[str, Any]:
    return {
        "storage": {"database_path": "data/test.db"},
        "inputs": {},
    }


@pytest.fixture()
def db_only_client(
    storage: StorageManager, db_only_config: dict[str, Any]
) -> Iterator[TestClient]:
    storage.sources.upsert(USER_ID, "gog_db", "gog", {}, enabled=True)
    storage.sources.upsert(USER_ID, "epic_db", "epic_games", {}, enabled=True)
    storage.sources.upsert(
        USER_ID, "trakt_db", "trakt", {"client_id": "cid"}, enabled=True
    )
    storage.credentials.save(USER_ID, "trakt_db", "client_secret", "secret")

    with booted_client(storage, db_only_config) as test_client:
        yield test_client


class TestDatabaseBackedSourceCanConnectRegression:
    def test_trakt_db_only_source_connects(
        self, db_only_client: TestClient, storage: StorageManager
    ) -> None:
        assert db_only_client.get("/api/trakt/status?source_id=trakt_db").json() == {
            "enabled": True,
            "connected": False,
        }

        with patch(
            "src.web.api._oauth.poll_device_token",
            return_value=DevicePollResult(DevicePollStatus.SUCCESS, "trakt-db-token"),
        ):
            response = db_only_client.post(
                "/api/trakt/poll-device-approval?source_id=trakt_db",
                json={"device_code": "dev1234567"},
            )

        assert response.status_code == 200
        assert (
            storage.credentials.get(USER_ID, "trakt_db", "refresh_token")
            == "trakt-db-token"
        )

    def test_gog_db_only_source_connects(
        self, db_only_client: TestClient, storage: StorageManager
    ) -> None:
        assert db_only_client.get("/api/gog/status?source_id=gog_db").json()["enabled"]

        with (
            patch("src.web.api._oauth.extract_gog_code", return_value="code"),
            patch(
                "src.web.api._oauth.exchange_gog_tokens",
                return_value={"refresh_token": "gog-db-token"},
            ),
        ):
            response = db_only_client.post(
                "/api/gog/exchange?source_id=gog_db", json={"code_or_url": "code"}
            )

        assert response.status_code == 200, response.text
        assert (
            storage.credentials.get(USER_ID, "gog_db", "refresh_token")
            == "gog-db-token"
        )


class TestDisconnectTargetsTheNamedSource:
    @pytest.mark.parametrize(
        ("provider", "source_id"),
        [("gog", "gog_work"), ("epic", "epic_work"), ("trakt", "trakt_work")],
    )
    def test_leaves_every_other_source_alone(
        self,
        client: TestClient,
        storage: StorageManager,
        provider: str,
        source_id: str,
    ) -> None:
        storage.credentials.save(USER_ID, source_id, "refresh_token", "mine")
        storage.credentials.save(USER_ID, "other_source", "refresh_token", "theirs")

        response = client.delete(f"/api/{provider}/token?source_id={source_id}")

        assert response.status_code == 200
        assert storage.credentials.get(USER_ID, source_id, "refresh_token") is None
        assert (
            storage.credentials.get(USER_ID, "other_source", "refresh_token")
            == "theirs"
        )


YAML_HELD_SOURCES = [
    ("gog", "gog_work", "gog"),
    ("epic", "epic_work", "epic_games"),
    ("trakt", "trakt_work", "trakt"),
]


def yaml_held_token_config(source_id: str, plugin: str) -> dict[str, Any]:
    return {
        "storage": {"database_path": "data/test.db"},
        "inputs": {
            source_id: {
                "plugin": plugin,
                "enabled": True,
                "refresh_token": "from-yaml",
            },
        },
    }


class TestAFileHeldTokenReachesBothWebVerbsRegression:
    @pytest.mark.parametrize(("provider", "source_id", "plugin"), YAML_HELD_SOURCES)
    def test_status_reads_connected_and_disconnect_deletes_it(
        self,
        storage: StorageManager,
        provider: str,
        source_id: str,
        plugin: str,
    ) -> None:
        config = yaml_held_token_config(source_id, plugin)

        with booted_client(storage, config, migrate_credentials=True) as client:
            assert storage.credentials.get(USER_ID, source_id, "refresh_token") == (
                "from-yaml"
            )
            assert client.get(f"/api/{provider}/status?source_id={source_id}").json()[
                "connected"
            ]
            assert (
                client.delete(
                    f"/api/{provider}/token?source_id={source_id}"
                ).status_code
                == 200
            )

        assert storage.credentials.get(USER_ID, source_id, "refresh_token") is None

    @pytest.mark.parametrize(("provider", "source_id", "plugin"), YAML_HELD_SOURCES)
    def test_a_file_token_on_a_migrated_source_stays_out_of_reach(
        self,
        storage: StorageManager,
        provider: str,
        source_id: str,
        plugin: str,
    ) -> None:
        storage.sources.upsert(USER_ID, source_id, plugin, {}, enabled=True)
        config = yaml_held_token_config(source_id, plugin)
        assert config["inputs"][source_id]["refresh_token"] == "from-yaml"

        with booted_client(storage, config, migrate_credentials=True) as client:
            assert "refresh_token" not in config["inputs"][source_id]
            assert storage.credentials.get(USER_ID, source_id, "refresh_token") is None
            assert not client.get(
                f"/api/{provider}/status?source_id={source_id}"
            ).json()["connected"]
            assert (
                client.delete(
                    f"/api/{provider}/token?source_id={source_id}"
                ).status_code
                == 404
            )


class TestRouteRefusesASourceRunningAnotherPlugin:
    @pytest.mark.parametrize(
        ("endpoint", "extract", "exchange", "body"),
        [
            (
                "/api/gog/exchange",
                "src.web.api._oauth.extract_gog_code",
                "src.web.api._oauth.exchange_gog_tokens",
                {"code_or_url": "code"},
            ),
            (
                "/api/epic/exchange",
                "src.web.api._oauth.extract_epic_code",
                "src.web.api._oauth.exchange_epic_tokens",
                {"code_or_json": "code"},
            ),
        ],
    )
    def test_exchange_does_not_overwrite_a_trakt_source(
        self,
        client: TestClient,
        storage: StorageManager,
        endpoint: str,
        extract: str,
        exchange: str,
        body: dict[str, str],
    ) -> None:
        storage.credentials.save(USER_ID, "trakt_work", "refresh_token", "trakt-token")

        with (
            patch(extract, return_value="code"),
            patch(exchange, return_value={"refresh_token": "other-token"}),
        ):
            response = client.post(f"{endpoint}?source_id=trakt_work", json=body)

        assert response.status_code == 400, response.text
        assert (
            storage.credentials.get(USER_ID, "trakt_work", "refresh_token")
            == "trakt-token"
        )

    @pytest.mark.parametrize("provider", ["gog", "epic"])
    def test_disconnect_does_not_delete_a_trakt_source_token(
        self, client: TestClient, storage: StorageManager, provider: str
    ) -> None:
        storage.credentials.save(USER_ID, "trakt_work", "refresh_token", "trakt-token")

        response = client.delete(f"/api/{provider}/token?source_id=trakt_work")

        assert response.status_code == 404, response.text
        assert (
            storage.credentials.get(USER_ID, "trakt_work", "refresh_token")
            == "trakt-token"
        )

    @pytest.mark.parametrize(("endpoint", "body", "outward"), WRITE_ROUTES)
    def test_a_write_route_refuses_an_id_no_source_uses(
        self,
        client: TestClient,
        storage: StorageManager,
        endpoint: str,
        body: dict[str, str] | None,
        outward: str,
    ) -> None:
        with patch(outward) as reached:
            response = client.post(f"{endpoint}?source_id=no_such_source", json=body)

        assert response.status_code == 400, response.text
        reached.assert_not_called()
        assert (
            storage.credentials.get(USER_ID, "no_such_source", "refresh_token") is None
        )


class TestDisconnectingAnIdNoSourceClaimsRegression:
    @pytest.mark.parametrize("provider", ["gog", "epic", "trakt"])
    def test_a_stranded_token_can_still_be_revoked(
        self, client: TestClient, storage: StorageManager, provider: str
    ) -> None:
        storage.credentials.save(USER_ID, "leftover", "refresh_token", "stranded")

        body = client.get(f"/api/{provider}/status?source_id=leftover").json()
        assert body["enabled"] is False
        assert body["connected"] is True

        response = client.delete(f"/api/{provider}/token?source_id=leftover")

        assert response.status_code == 200, response.text
        assert storage.credentials.get(USER_ID, "leftover", "refresh_token") is None

    @pytest.mark.parametrize("provider", ["gog", "epic", "trakt"])
    def test_an_id_holding_nothing_is_still_a_404(
        self, client: TestClient, storage: StorageManager, provider: str
    ) -> None:
        response = client.delete(f"/api/{provider}/token?source_id=leftover")

        assert response.status_code == 404, response.text


class TestADisabledSourceCanStillBeDisconnectedRegression:
    @pytest.fixture()
    def disabled(
        self, client: TestClient, storage: StorageManager, source_id: str
    ) -> StorageManager:
        storage.credentials.save(USER_ID, source_id, "refresh_token", "still-live")
        storage.sources.set_enabled(USER_ID, source_id, False)
        return storage

    @pytest.mark.parametrize(
        ("provider", "source_id"),
        [("gog", "gog_work"), ("epic", "epic_work"), ("trakt", "trakt_work")],
    )
    def test_disconnect_still_deletes_the_token(
        self,
        client: TestClient,
        disabled: StorageManager,
        provider: str,
        source_id: str,
    ) -> None:
        response = client.delete(f"/api/{provider}/token?source_id={source_id}")

        assert response.status_code == 200, response.text
        assert disabled.credentials.get(USER_ID, source_id, "refresh_token") is None


CONNECT_EXCHANGES = [
    (
        "/api/gog/exchange",
        "gog_work",
        "src.web.api._oauth.extract_gog_code",
        "src.web.api._oauth.exchange_gog_tokens",
        {"code_or_url": "code"},
    ),
    (
        "/api/epic/exchange",
        "epic_work",
        "src.web.api._oauth.extract_epic_code",
        "src.web.api._oauth.exchange_epic_tokens",
        {"code_or_json": "code"},
    ),
]


class TestConnectingADisabledSourceIsRefused:
    @pytest.mark.parametrize("enabled", [True, False])
    @pytest.mark.parametrize(
        ("endpoint", "source_id", "extract", "exchange", "body"), CONNECT_EXCHANGES
    )
    def test_exchange_writes_a_token_only_for_an_enabled_source(
        self,
        client: TestClient,
        storage: StorageManager,
        enabled: bool,
        endpoint: str,
        source_id: str,
        extract: str,
        exchange: str,
        body: dict[str, str],
    ) -> None:
        storage.sources.set_enabled(USER_ID, source_id, enabled)

        with (
            patch(extract, return_value="code"),
            patch(exchange, return_value={"refresh_token": "fresh-token"}),
        ):
            response = client.post(f"{endpoint}?source_id={source_id}", json=body)

        assert response.status_code == (200 if enabled else 400), response.text
        assert storage.credentials.get(USER_ID, source_id, "refresh_token") == (
            "fresh-token" if enabled else None
        )

    @pytest.mark.parametrize("enabled", [True, False])
    def test_poll_saves_a_token_only_for_an_enabled_source(
        self, client: TestClient, storage: StorageManager, enabled: bool
    ) -> None:
        storage.credentials.save(USER_ID, "trakt_work", "client_secret", "secret")
        storage.sources.set_enabled(USER_ID, "trakt_work", enabled)

        with patch(
            "src.web.api._oauth.poll_device_token",
            return_value=DevicePollResult(DevicePollStatus.SUCCESS, "trakt-token"),
        ):
            response = client.post(
                "/api/trakt/poll-device-approval?source_id=trakt_work",
                json={"device_code": "dev1234567"},
            )

        assert response.status_code == (200 if enabled else 400), response.text
        assert storage.credentials.get(USER_ID, "trakt_work", "refresh_token") == (
            "trakt-token" if enabled else None
        )


STATUS_SOURCES = [
    ("gog", "gog_work", {}),
    ("epic", "epic_work", {}),
    ("trakt", "trakt_work", {"client_secret": "secret"}),
]


class TestClearingTheTraktClientSecretLeavesTheTokenVisibleRegression:
    def test_connected_survives_a_cleared_client_secret(
        self, client: TestClient, storage: StorageManager
    ) -> None:
        storage.credentials.save(USER_ID, "trakt_work", "client_secret", "secret")
        storage.credentials.save(USER_ID, "trakt_work", "refresh_token", "still-live")

        assert client.get("/api/trakt/status?source_id=trakt_work").json() == {
            "enabled": True,
            "connected": True,
        }

        storage.credentials.delete(USER_ID, "trakt_work", "client_secret")

        assert client.get("/api/trakt/status?source_id=trakt_work").json() == {
            "enabled": False,
            "connected": True,
        }


class TestStatusSeparatesEnabledFromConnected:
    @pytest.mark.parametrize("enabled", [True, False])
    @pytest.mark.parametrize(("provider", "source_id", "secrets"), STATUS_SOURCES)
    def test_a_stored_token_reads_connected_whatever_the_enabled_flag(
        self,
        client: TestClient,
        storage: StorageManager,
        enabled: bool,
        provider: str,
        source_id: str,
        secrets: dict[str, str],
    ) -> None:
        for key, value in secrets.items():
            storage.credentials.save(USER_ID, source_id, key, value)
        storage.credentials.save(USER_ID, source_id, "refresh_token", "still-live")
        storage.sources.set_enabled(USER_ID, source_id, enabled)

        body = client.get(f"/api/{provider}/status?source_id={source_id}").json()

        assert body["enabled"] is enabled
        assert body["connected"] is True


class TestEveryOAuthRouteValidatesTheSourceId:
    @pytest.mark.parametrize(("endpoint", "body", "outward"), WRITE_ROUTES)
    @pytest.mark.parametrize("bad_id", MALFORMED_IDS)
    def test_write_route_rejects_a_malformed_id(
        self,
        client: TestClient,
        storage: StorageManager,
        endpoint: str,
        body: dict[str, str] | None,
        outward: str,
        bad_id: str,
    ) -> None:
        with patch(outward) as reached:
            response = client.post(endpoint, params={"source_id": bad_id}, json=body)

        assert response.status_code == 422, response.text
        reached.assert_not_called()
        assert storage.credentials.get(USER_ID, bad_id, "refresh_token") is None

    @pytest.mark.parametrize(("method", "endpoint"), READ_ROUTES)
    @pytest.mark.parametrize("bad_id", MALFORMED_IDS)
    def test_read_route_rejects_a_malformed_id(
        self, client: TestClient, method: str, endpoint: str, bad_id: str
    ) -> None:
        response = client.request(method, endpoint, params={"source_id": bad_id})

        assert response.status_code == 422, response.text
