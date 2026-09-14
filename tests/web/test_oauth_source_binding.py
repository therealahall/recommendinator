from __future__ import annotations

from collections.abc import Iterator
from dataclasses import asdict
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from src.ingestion.plugin_base import DevicePollResult, DevicePollStatus
from src.storage.manager import StorageManager
from tests.factories import MALFORMED_IDS, authenticated_client, booted_web_app
from tests.fakes.source_plugins import (
    FAKE_AUTH_URL,
    FAKE_AUTHORIZATION,
    FAKE_CODE,
    FAKE_REFRESH_TOKEN,
    FakeCodePasteFlow,
    FakeDeviceCodeFlow,
)

USER_ID = 1
TOKEN = "refresh_token"
PASTE = "/api/oauth/fake_paste"
DEVICE = "/api/oauth/fake_device"
APPROVAL = {"device_code": FAKE_AUTHORIZATION.device_code}


@pytest.fixture()
def storage(tmp_path: Path) -> StorageManager:
    return StorageManager(sqlite_path=tmp_path / "test.db")


@pytest.fixture()
def client(
    registry_with_oauth_fakes: None, storage: StorageManager
) -> Iterator[TestClient]:
    storage.sources.upsert(USER_ID, "paste_work", "fake_paste", {}, enabled=True)
    storage.sources.upsert(
        USER_ID, "device_work", "fake_device", {"client_id": "cid"}, enabled=True
    )
    storage.sources.upsert(USER_ID, "books", "fake_file", {"path": "b.csv"})

    with booted_web_app(storage, {"storage": {"database_path": "data/test.db"}}) as app:
        yield authenticated_client(app)


def stored(storage: StorageManager, source_id: str) -> str | None:
    return storage.credentials.get(USER_ID, source_id, TOKEN)


def query(source_id: str) -> dict[str, str]:
    return {"source_id": source_id}


WRITE_ROUTES: list[tuple[str, dict[str, str] | None, type, str]] = [
    (f"{PASTE}/exchange", {"code": FAKE_CODE}, FakeCodePasteFlow, "exchange_code"),
    (f"{DEVICE}/start-device-flow", None, FakeDeviceCodeFlow, "start"),
    (f"{DEVICE}/poll-device-approval", APPROVAL, FakeDeviceCodeFlow, "poll"),
]

READ_ROUTES = [
    ("GET", f"{PASTE}/status"),
    ("GET", f"{DEVICE}/status"),
    ("DELETE", f"{PASTE}/token"),
    ("DELETE", f"{DEVICE}/token"),
]

SOURCES = [(PASTE, "paste_work"), (DEVICE, "device_work")]


class TestAPluginDeclaringAFlowConnectsWithNoCoreEditRegression:
    def test_a_code_paste_flow_connects_reads_connected_and_disconnects(
        self, client: TestClient, storage: StorageManager
    ) -> None:
        storage.credentials.save(USER_ID, "fake_paste", TOKEN, "the-plugin-name-row")

        assert client.get(f"{PASTE}/status", params=query("paste_work")).json() == {
            "enabled": True,
            "connected": False,
            "auth_url": FAKE_AUTH_URL,
        }
        exchanged = client.post(
            f"{PASTE}/exchange", params=query("paste_work"), json={"code": FAKE_CODE}
        )
        assert exchanged.status_code == 200, exchanged.text
        assert stored(storage, "paste_work") == FAKE_REFRESH_TOKEN
        assert client.get(f"{PASTE}/status", params=query("paste_work")).json()[
            "connected"
        ]

        revoked = client.delete(f"{PASTE}/token", params=query("paste_work"))

        assert revoked.status_code == 200, revoked.text
        assert stored(storage, "paste_work") is None
        assert stored(storage, "fake_paste") == "the-plugin-name-row"

    def test_a_device_code_flow_connects_reads_connected_and_disconnects(
        self, client: TestClient, storage: StorageManager
    ) -> None:
        status = f"{DEVICE}/status"
        assert client.get(status, params=query("device_work")).json() == {
            "enabled": True,
            "connected": False,
        }
        started = client.post(
            f"{DEVICE}/start-device-flow", params=query("device_work")
        )
        assert started.json() == asdict(FAKE_AUTHORIZATION)
        polled = client.post(
            f"{DEVICE}/poll-device-approval",
            params=query("device_work"),
            json=APPROVAL,
        )
        assert polled.json()["connected"] is True
        assert stored(storage, "device_work") == FAKE_REFRESH_TOKEN
        assert client.get(status, params=query("device_work")).json()["connected"]

        revoked = client.delete(f"{DEVICE}/token", params=query("device_work"))

        assert revoked.status_code == 200, revoked.text
        assert not client.get(status, params=query("device_work")).json()["connected"]

    def test_the_schema_carries_what_each_flow_declares(
        self, client: TestClient
    ) -> None:
        def declared(source_id: str) -> Any:
            return client.get(f"/api/sync/sources/{source_id}/schema").json()["oauth"]

        assert declared("books") is None
        assert declared("paste_work") == {
            "flow": "code_paste",
            "code_help": FakeCodePasteFlow.code_help,
            "setup_hint": "",
        }
        assert declared("device_work") == {
            "flow": "device_code",
            "code_help": "",
            "setup_hint": FakeDeviceCodeFlow.setup_hint,
        }


class TestAFlowsFailureReachesTheClientAsAFixedString:
    def test_a_refused_code_is_a_400_that_leaves_the_reason_out(
        self, client: TestClient, storage: StorageManager
    ) -> None:
        response = client.post(
            f"{PASTE}/exchange", params=query("paste_work"), json={"code": "wrong"}
        )

        assert response.status_code == 400
        assert response.json()["detail"] == "Fake Paste authentication failed"
        assert "refused" not in response.text
        assert stored(storage, "paste_work") is None

    def test_an_unexpected_fault_is_a_500_that_leaves_the_reason_out(
        self, client: TestClient
    ) -> None:
        with patch.object(
            FakeCodePasteFlow,
            "exchange_code",
            side_effect=RuntimeError("Internal database state is corrupt"),
        ):
            response = client.post(
                f"{PASTE}/exchange", params=query("paste_work"), json={"code": "x"}
            )

        assert response.status_code == 500
        assert (
            response.json()["detail"]
            == "Unexpected error during Fake Paste authentication"
        )
        assert "Internal database state" not in response.text

    def test_a_sign_in_link_the_flow_cannot_build_reads_as_none(
        self, client: TestClient
    ) -> None:
        with patch.object(
            FakeCodePasteFlow, "auth_url", side_effect=RuntimeError("builder broken")
        ):
            body = client.get(f"{PASTE}/status", params=query("paste_work")).json()

        assert body == {"enabled": True, "connected": False, "auth_url": None}

    @pytest.mark.parametrize(
        "status",
        [
            DevicePollStatus.PENDING,
            DevicePollStatus.SLOW_DOWN,
            DevicePollStatus.EXPIRED,
            DevicePollStatus.DENIED,
        ],
    )
    def test_an_unfinished_poll_saves_nothing_and_says_where_it_stands(
        self, client: TestClient, storage: StorageManager, status: DevicePollStatus
    ) -> None:
        with patch.object(
            FakeDeviceCodeFlow, "poll", return_value=DevicePollResult(status)
        ):
            body = client.post(
                f"{DEVICE}/poll-device-approval",
                params=query("device_work"),
                json=APPROVAL,
            ).json()

        assert body["connected"] is False
        assert body["status"] == status.value
        assert body["message"]
        assert stored(storage, "device_work") is None

    def test_an_approval_carrying_no_token_saves_nothing(
        self, client: TestClient, storage: StorageManager
    ) -> None:
        with patch.object(
            FakeDeviceCodeFlow,
            "poll",
            return_value=DevicePollResult(DevicePollStatus.SUCCESS, None),
        ):
            response = client.post(
                f"{DEVICE}/poll-device-approval",
                params=query("device_work"),
                json=APPROVAL,
            )

        assert response.status_code == 400
        assert response.json()["detail"] == "Fake Device authentication failed"
        assert stored(storage, "device_work") is None


class TestARouteActsOnlyOnASourceOfItsOwnService:
    def test_an_exchange_does_not_overwrite_another_plugins_source(
        self, client: TestClient, storage: StorageManager
    ) -> None:
        storage.credentials.save(USER_ID, "device_work", TOKEN, "device-token")

        response = client.post(
            f"{PASTE}/exchange", params=query("device_work"), json={"code": FAKE_CODE}
        )

        assert response.status_code == 400, response.text
        assert stored(storage, "device_work") == "device-token"

    def test_a_disconnect_does_not_delete_another_plugins_token(
        self, client: TestClient, storage: StorageManager
    ) -> None:
        storage.credentials.save(USER_ID, "device_work", TOKEN, "device-token")

        response = client.delete(f"{PASTE}/token", params=query("device_work"))

        assert response.status_code == 404, response.text
        assert stored(storage, "device_work") == "device-token"

    @pytest.mark.parametrize(("endpoint", "body", "flow", "method"), WRITE_ROUTES)
    def test_a_write_route_refuses_an_id_no_source_uses(
        self,
        client: TestClient,
        storage: StorageManager,
        endpoint: str,
        body: dict[str, str] | None,
        flow: type,
        method: str,
    ) -> None:
        with patch.object(flow, method) as reached:
            response = client.post(endpoint, params=query("no_such_source"), json=body)

        assert response.status_code == 400, response.text
        reached.assert_not_called()
        assert stored(storage, "no_such_source") is None

    def test_a_service_no_plugin_declares_is_not_found(
        self, client: TestClient
    ) -> None:
        response = client.get("/api/oauth/nobody/status", params=query("paste_work"))

        assert response.status_code == 404

    def test_the_other_flows_verb_is_refused(
        self, client: TestClient, storage: StorageManager
    ) -> None:
        response = client.post(
            f"{DEVICE}/exchange", params=query("device_work"), json={"code": FAKE_CODE}
        )

        assert response.status_code == 400, response.text
        assert stored(storage, "device_work") is None


class TestDisconnectingAnIdNoSourceClaimsRegression:
    @pytest.mark.parametrize("route", [PASTE, DEVICE])
    def test_a_stranded_token_can_still_be_revoked(
        self, client: TestClient, storage: StorageManager, route: str
    ) -> None:
        storage.credentials.save(USER_ID, "leftover", TOKEN, "stranded")

        body = client.get(f"{route}/status", params=query("leftover")).json()
        assert body["enabled"] is False
        assert body["connected"] is True

        response = client.delete(f"{route}/token", params=query("leftover"))

        assert response.status_code == 200, response.text
        assert stored(storage, "leftover") is None

    @pytest.mark.parametrize("route", [PASTE, DEVICE])
    def test_an_id_holding_nothing_is_still_a_404(
        self, client: TestClient, route: str
    ) -> None:
        response = client.delete(f"{route}/token", params=query("leftover"))

        assert response.status_code == 404, response.text


class TestADisabledSourceCanStillBeDisconnectedRegression:
    @pytest.mark.parametrize(("route", "source_id"), SOURCES)
    def test_disconnect_still_deletes_the_token(
        self,
        client: TestClient,
        storage: StorageManager,
        route: str,
        source_id: str,
    ) -> None:
        storage.credentials.save(USER_ID, source_id, TOKEN, "still-live")
        storage.sources.set_enabled(USER_ID, source_id, False)

        response = client.delete(f"{route}/token", params=query(source_id))

        assert response.status_code == 200, response.text
        assert stored(storage, source_id) is None


class TestConnectingADisabledSourceIsRefused:
    @pytest.mark.parametrize("enabled", [True, False])
    def test_exchange_writes_a_token_only_for_an_enabled_source(
        self, client: TestClient, storage: StorageManager, enabled: bool
    ) -> None:
        storage.sources.set_enabled(USER_ID, "paste_work", enabled)

        response = client.post(
            f"{PASTE}/exchange", params=query("paste_work"), json={"code": FAKE_CODE}
        )

        assert response.status_code == (200 if enabled else 400), response.text
        assert stored(storage, "paste_work") == (
            FAKE_REFRESH_TOKEN if enabled else None
        )

    @pytest.mark.parametrize("enabled", [True, False])
    def test_poll_saves_a_token_only_for_an_enabled_source(
        self, client: TestClient, storage: StorageManager, enabled: bool
    ) -> None:
        storage.sources.set_enabled(USER_ID, "device_work", enabled)

        response = client.post(
            f"{DEVICE}/poll-device-approval",
            params=query("device_work"),
            json=APPROVAL,
        )

        assert response.status_code == (200 if enabled else 400), response.text
        assert stored(storage, "device_work") == (
            FAKE_REFRESH_TOKEN if enabled else None
        )


class TestUnsavedSetupLeavesTheTokenVisibleRegression:
    def test_connected_survives_a_cleared_setup(
        self, client: TestClient, storage: StorageManager
    ) -> None:
        storage.credentials.save(USER_ID, "device_work", TOKEN, "still-live")
        storage.sources.upsert(USER_ID, "device_work", "fake_device", {}, enabled=True)

        body = client.get(f"{DEVICE}/status", params=query("device_work")).json()

        assert body == {"enabled": False, "connected": True}


class TestStatusSeparatesEnabledFromConnected:
    @pytest.mark.parametrize("enabled", [True, False])
    @pytest.mark.parametrize(("route", "source_id"), SOURCES)
    def test_a_stored_token_reads_connected_whatever_the_enabled_flag(
        self,
        client: TestClient,
        storage: StorageManager,
        enabled: bool,
        route: str,
        source_id: str,
    ) -> None:
        storage.credentials.save(USER_ID, source_id, TOKEN, "still-live")
        storage.sources.set_enabled(USER_ID, source_id, enabled)

        body = client.get(f"{route}/status", params=query(source_id)).json()

        assert body["enabled"] is enabled
        assert body["connected"] is True


class TestEveryOAuthRouteValidatesTheSourceId:
    @pytest.mark.parametrize(("endpoint", "body", "flow", "method"), WRITE_ROUTES)
    @pytest.mark.parametrize("bad_id", MALFORMED_IDS)
    def test_write_route_rejects_a_malformed_id(
        self,
        client: TestClient,
        storage: StorageManager,
        endpoint: str,
        body: dict[str, str] | None,
        flow: type,
        method: str,
        bad_id: str,
    ) -> None:
        with patch.object(flow, method) as reached:
            response = client.post(endpoint, params=query(bad_id), json=body)

        assert response.status_code == 422, response.text
        reached.assert_not_called()
        assert stored(storage, bad_id) is None

    @pytest.mark.parametrize(("method", "endpoint"), READ_ROUTES)
    @pytest.mark.parametrize("bad_id", MALFORMED_IDS)
    def test_read_route_rejects_a_malformed_id(
        self, client: TestClient, method: str, endpoint: str, bad_id: str
    ) -> None:
        response = client.request(method, endpoint, params=query(bad_id))

        assert response.status_code == 422, response.text
