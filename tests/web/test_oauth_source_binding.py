"""The web OAuth connect flow keys every credential on the source id."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from src.storage.manager import StorageManager
from src.web.sync_sources import delete_source, resolve_inputs
from src.web.trakt_auth import DevicePollResult, DevicePollStatus
from tests.factories import authenticated_client, booted_web_app

USER_ID = 1


@pytest.fixture()
def storage(tmp_path: Path) -> StorageManager:
    return StorageManager(sqlite_path=tmp_path / "test.db")


@pytest.fixture()
def config() -> dict[str, Any]:
    """Three OAuth sources, none named after its plugin.

    ``is_gog_enabled`` and ``is_epic_enabled`` read the YAML ``inputs`` block,
    so each source is declared there as well as in the database.
    """
    return {
        "ollama": {"base_url": "http://localhost:11434", "model": "x"},
        "storage": {"database_path": "data/test.db"},
        "inputs": {
            "gog_work": {"plugin": "gog", "enabled": True},
            "epic_work": {"plugin": "epic_games", "enabled": True},
            "trakt_work": {"plugin": "trakt", "enabled": True, "client_id": "cid"},
        },
    }


@contextmanager
def booted_client(
    storage: StorageManager, config: dict[str, Any]
) -> Iterator[TestClient]:
    """A booted app over *storage*, with the startup source migrations stubbed."""
    with (
        patch("src.web.app.migrate_source_labels"),
        patch("src.web.app.migrate_source_config_plugins"),
        booted_web_app(storage, config) as app,
    ):
        yield authenticated_client(app)


@pytest.fixture()
def client(storage: StorageManager, config: dict[str, Any]) -> Iterator[TestClient]:
    """A booted app over a real credential database, sources already migrated."""
    for source_id, entry in config["inputs"].items():
        fields = {k: v for k, v in entry.items() if k not in ("plugin", "enabled")}
        storage.upsert_source_config(
            USER_ID, source_id, entry["plugin"], fields, enabled=True
        )

    with booted_client(storage, config) as test_client:
        yield test_client


def resolved_config(
    config: dict[str, Any], storage: StorageManager, source_id: str
) -> dict[str, Any]:
    """The config the next sync of *source_id* would run with."""
    for resolved in resolve_inputs(config, storage=storage, user_id=USER_ID):
        if resolved.source_id == source_id:
            return resolved.config
    raise AssertionError(f"{source_id} did not resolve, so its reader is unchecked")


class TestOAuthConnectSourceBindingRegression:
    """Regression: the connect routes saved the token under the plugin name.

    Bug reported: a source named ``gog_work`` never read its own token back.
    Root cause: the routes carried no source id.
    Fix: every OAuth route takes one.
    """

    def test_gog_round_trip_regression(
        self, client: TestClient, storage: StorageManager, config: dict[str, Any]
    ) -> None:
        with (
            patch("src.web.api.extract_gog_code", return_value="code"),
            patch(
                "src.web.api.exchange_gog_tokens",
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
        assert storage.get_credential(USER_ID, "gog", "refresh_token") is None
        assert client.get("/api/gog/status?source_id=gog_work").json()["connected"]
        assert not client.get("/api/gog/status").json()["connected"]

        delete_source("gog_work", storage, user_id=USER_ID)

        assert storage.get_credential(USER_ID, "gog_work", "refresh_token") is None

    def test_epic_round_trip_regression(
        self, client: TestClient, storage: StorageManager, config: dict[str, Any]
    ) -> None:
        with (
            patch("src.web.api.extract_epic_code", return_value="code"),
            patch(
                "src.web.api.exchange_epic_tokens",
                return_value={"refresh_token": "epic-work-token"},
            ),
        ):
            response = client.post(
                "/api/epic/exchange?source_id=epic_work", json={"code_or_json": "code"}
            )

        assert response.status_code == 200
        assert (
            resolved_config(config, storage, "epic_work")["refresh_token"]
            == "epic-work-token"
        )
        assert storage.get_credential(USER_ID, "epic_games", "refresh_token") is None
        assert client.get("/api/epic/status?source_id=epic_work").json()["connected"]
        assert not client.get("/api/epic/status").json()["connected"]

        delete_source("epic_work", storage, user_id=USER_ID)

        assert storage.get_credential(USER_ID, "epic_work", "refresh_token") is None

    def test_trakt_round_trip_regression(
        self, client: TestClient, storage: StorageManager, config: dict[str, Any]
    ) -> None:
        # The device flow resolves this source's own client credentials, so the
        # secret has to sit under the id the poll is asked about.
        storage.save_credential(USER_ID, "trakt_work", "client_secret", "secret")

        with patch(
            "src.web.api.poll_device_token",
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
        assert storage.get_credential(USER_ID, "trakt", "refresh_token") is None
        assert client.get("/api/trakt/status?source_id=trakt_work").json()["connected"]
        assert not client.get("/api/trakt/status").json()["connected"]

        delete_source("trakt_work", storage, user_id=USER_ID)

        assert storage.get_credential(USER_ID, "trakt_work", "refresh_token") is None


@pytest.fixture()
def db_only_config() -> dict[str, Any]:
    """No ``inputs`` block at all — what the Data tab produces.

    ``create_source`` writes a ``source_configs`` row and never touches
    config.yaml.
    """
    return {
        "ollama": {"base_url": "http://localhost:11434", "model": "x"},
        "storage": {"database_path": "data/test.db"},
        "inputs": {},
    }


@pytest.fixture()
def db_only_client(
    storage: StorageManager, db_only_config: dict[str, Any]
) -> Iterator[TestClient]:
    """A booted app whose three OAuth sources exist only in the database."""
    storage.upsert_source_config(USER_ID, "gog_db", "gog", {}, enabled=True)
    storage.upsert_source_config(USER_ID, "epic_db", "epic_games", {}, enabled=True)
    storage.upsert_source_config(
        USER_ID, "trakt_db", "trakt", {"client_id": "cid"}, enabled=True
    )
    storage.save_credential(USER_ID, "trakt_db", "client_secret", "secret")

    with booted_client(storage, db_only_config) as test_client:
        yield test_client


class TestDatabaseBackedSourceCanConnectRegression:
    """Regression: a source added from the Data tab could not be connected.

    Reported: in-app ``gog_work`` answered ``enabled: false`` and 400.
    Cause: the GOG and Epic enablement checks read config.yaml ``inputs``
    alone. Fix: resolve the source the way sync does.
    """

    def test_trakt_db_only_source_connects(
        self, db_only_client: TestClient, storage: StorageManager
    ) -> None:
        """The anchor: Trakt already resolves through ``resolve_inputs``.

        Without it the two failures below read as "no OAuth source may be
        DB-only" — a design limit rather than a defect in two of three modules.
        """
        assert db_only_client.get("/api/trakt/status?source_id=trakt_db").json() == {
            "enabled": True,
            "connected": False,
        }

        with patch(
            "src.web.api.poll_device_token",
            return_value=DevicePollResult(DevicePollStatus.SUCCESS, "trakt-db-token"),
        ):
            response = db_only_client.post(
                "/api/trakt/poll-device-approval?source_id=trakt_db",
                json={"device_code": "dev1234567"},
            )

        assert response.status_code == 200
        assert (
            storage.get_credential(USER_ID, "trakt_db", "refresh_token")
            == "trakt-db-token"
        )

    def test_gog_db_only_source_connects(
        self, db_only_client: TestClient, storage: StorageManager
    ) -> None:
        assert db_only_client.get("/api/gog/status?source_id=gog_db").json()["enabled"]

        with (
            patch("src.web.api.extract_gog_code", return_value="code"),
            patch(
                "src.web.api.exchange_gog_tokens",
                return_value={"refresh_token": "gog-db-token"},
            ),
        ):
            response = db_only_client.post(
                "/api/gog/exchange?source_id=gog_db", json={"code_or_url": "code"}
            )

        assert response.status_code == 200, response.text
        assert (
            storage.get_credential(USER_ID, "gog_db", "refresh_token") == "gog-db-token"
        )

    def test_epic_db_only_source_connects(
        self, db_only_client: TestClient, storage: StorageManager
    ) -> None:
        assert db_only_client.get("/api/epic/status?source_id=epic_db").json()[
            "enabled"
        ]

        with (
            patch("src.web.api.extract_epic_code", return_value="code"),
            patch(
                "src.web.api.exchange_epic_tokens",
                return_value={"refresh_token": "epic-db-token"},
            ),
        ):
            response = db_only_client.post(
                "/api/epic/exchange?source_id=epic_db", json={"code_or_json": "code"}
            )

        assert response.status_code == 200, response.text
        assert (
            storage.get_credential(USER_ID, "epic_db", "refresh_token")
            == "epic-db-token"
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
        storage.save_credential(USER_ID, source_id, "refresh_token", "mine")
        storage.save_credential(USER_ID, "other_source", "refresh_token", "theirs")

        response = client.delete(f"/api/{provider}/token?source_id={source_id}")

        assert response.status_code == 200
        assert storage.get_credential(USER_ID, source_id, "refresh_token") is None
        assert (
            storage.get_credential(USER_ID, "other_source", "refresh_token") == "theirs"
        )


class TestYamlTokenFallbackIsPerSource:
    """``has_*_token`` falls back to the YAML entry when the DB has no row.

    Nothing else reaches that branch: every other test here saves the token to
    the credential store first, which wins before the fallback is consulted.
    """

    @pytest.mark.parametrize(
        ("provider", "source_id", "plugin"),
        [("gog", "gog_work", "gog"), ("epic", "epic_work", "epic_games")],
    )
    def test_status_reads_the_named_source_entry(
        self,
        storage: StorageManager,
        provider: str,
        source_id: str,
        plugin: str,
    ) -> None:
        yaml_config = {
            "ollama": {"base_url": "http://localhost:11434", "model": "x"},
            "storage": {"database_path": "data/test.db"},
            "inputs": {
                source_id: {
                    "plugin": plugin,
                    "enabled": True,
                    "refresh_token": "from-yaml",
                },
                plugin: {"plugin": plugin, "enabled": True},
            },
        }

        with booted_client(storage, yaml_config) as client:
            assert client.get(f"/api/{provider}/status?source_id={source_id}").json()[
                "connected"
            ]
            # The plugin-named sibling is declared and enabled but has no token,
            # so a reader still keyed on the plugin name would answer True above
            # for the wrong entry and False here for the right one.
            assert not client.get(f"/api/{provider}/status?source_id={plugin}").json()[
                "connected"
            ]


class TestRouteRefusesASourceRunningAnotherPlugin:
    """The id is client-supplied, so the route has to own who it may write to.

    A GOG token written under a Trakt source's id is a credential the Trakt
    plugin will hand to api.trakt.tv on the next sync.
    """

    @pytest.mark.parametrize(
        ("endpoint", "extract", "exchange", "body"),
        [
            (
                "/api/gog/exchange",
                "src.web.api.extract_gog_code",
                "src.web.api.exchange_gog_tokens",
                {"code_or_url": "code"},
            ),
            (
                "/api/epic/exchange",
                "src.web.api.extract_epic_code",
                "src.web.api.exchange_epic_tokens",
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
        storage.save_credential(USER_ID, "trakt_work", "refresh_token", "trakt-token")

        with (
            patch(extract, return_value="code"),
            patch(exchange, return_value={"refresh_token": "other-token"}),
        ):
            response = client.post(f"{endpoint}?source_id=trakt_work", json=body)

        assert response.status_code == 400, response.text
        assert (
            storage.get_credential(USER_ID, "trakt_work", "refresh_token")
            == "trakt-token"
        )

    @pytest.mark.parametrize("provider", ["gog", "epic"])
    def test_disconnect_does_not_delete_a_trakt_source_token(
        self, client: TestClient, storage: StorageManager, provider: str
    ) -> None:
        storage.save_credential(USER_ID, "trakt_work", "refresh_token", "trakt-token")

        response = client.delete(f"/api/{provider}/token?source_id=trakt_work")

        assert response.status_code == 404, response.text
        assert (
            storage.get_credential(USER_ID, "trakt_work", "refresh_token")
            == "trakt-token"
        )

    def test_trakt_poll_already_refuses_a_gog_source(
        self, client: TestClient, storage: StorageManager
    ) -> None:
        """The anchor: Trakt's route resolves this source's own client secret.

        That resolution is what makes the two failures above a gap in the GOG
        and Epic routes rather than an unreachable state.
        """
        storage.save_credential(USER_ID, "gog_work", "refresh_token", "gog-token")

        with patch(
            "src.web.api.poll_device_token",
            return_value=DevicePollResult(DevicePollStatus.SUCCESS, "trakt-token"),
        ):
            response = client.post(
                "/api/trakt/poll-device-approval?source_id=gog_work",
                json={"device_code": "dev1234567"},
            )

        assert response.status_code == 400, response.text
        assert (
            storage.get_credential(USER_ID, "gog_work", "refresh_token") == "gog-token"
        )

    @pytest.mark.parametrize("provider", ["gog", "epic"])
    def test_status_reads_no_other_plugins_credential(
        self, client: TestClient, storage: StorageManager, provider: str
    ) -> None:
        """Status is a read, and it was reading whatever row the id named."""
        storage.save_credential(USER_ID, "trakt_work", "refresh_token", "trakt-token")

        body = client.get(f"/api/{provider}/status?source_id=trakt_work").json()

        assert body["enabled"] is False
        assert body["connected"] is False

    @pytest.mark.parametrize("provider", ["gog", "epic", "trakt"])
    def test_disconnect_refuses_an_id_no_source_uses(
        self, client: TestClient, storage: StorageManager, provider: str
    ) -> None:
        storage.save_credential(USER_ID, "leftover", "refresh_token", "not-addressable")

        response = client.delete(f"/api/{provider}/token?source_id=leftover")

        assert response.status_code == 404, response.text
        assert (
            storage.get_credential(USER_ID, "leftover", "refresh_token")
            == "not-addressable"
        )

    def test_trakt_disconnect_does_not_delete_a_gog_source_token(
        self, client: TestClient, storage: StorageManager
    ) -> None:
        """The third route deletes too, and its own plugin is the other one."""
        storage.save_credential(USER_ID, "gog_work", "refresh_token", "gog-token")

        response = client.delete("/api/trakt/token?source_id=gog_work")

        assert response.status_code == 404, response.text
        assert (
            storage.get_credential(USER_ID, "gog_work", "refresh_token") == "gog-token"
        )

    def test_start_device_flow_refuses_a_source_running_another_plugin(
        self, client: TestClient, storage: StorageManager
    ) -> None:
        """The fourth Trakt verb: it hands this source's client id to Trakt.

        The client credentials are stored so the refusal cannot be the generic
        "no client id" one — unbound, this call reaches Trakt.
        """
        storage.save_credential(USER_ID, "gog_work", "client_id", "cid")
        storage.save_credential(USER_ID, "gog_work", "client_secret", "secret")

        with patch("src.web.api.start_device_auth_flow") as started:
            response = client.post(
                "/api/trakt/start-device-flow?source_id=gog_work",
            )

        assert response.status_code == 400, response.text
        started.assert_not_called()

    def test_trakt_status_reads_no_other_plugins_credential(
        self, client: TestClient, storage: StorageManager
    ) -> None:
        # Stored credentials land in the resolved config whatever the schema
        # says, so unbound this source answers every question Trakt's status
        # asks and reports itself connected.
        storage.save_credential(USER_ID, "gog_work", "client_id", "cid")
        storage.save_credential(USER_ID, "gog_work", "client_secret", "secret")
        storage.save_credential(USER_ID, "gog_work", "refresh_token", "gog-token")

        body = client.get("/api/trakt/status?source_id=gog_work").json()

        assert body == {"enabled": False, "connected": False}

    def test_exchange_refuses_an_id_no_source_uses(
        self, client: TestClient, storage: StorageManager
    ) -> None:
        with (
            patch("src.web.api.extract_gog_code", return_value="code"),
            patch(
                "src.web.api.exchange_gog_tokens",
                return_value={"refresh_token": "gog-token"},
            ),
        ):
            response = client.post(
                "/api/gog/exchange?source_id=no_such_source",
                json={"code_or_url": "code"},
            )

        assert response.status_code == 400, response.text
        assert (
            storage.get_credential(USER_ID, "no_such_source", "refresh_token") is None
        )


class TestASourceOnAPluginThisBuildDoesNotShipIsRefused:
    """A ``source_configs`` row can name a plugin a later build dropped.

    Its id still spells a credential key, so the routes have to answer without
    a plugin to compare against.
    """

    @pytest.fixture()
    def ghost(self, client: TestClient, storage: StorageManager) -> StorageManager:
        storage.upsert_source_config(
            USER_ID, "ghost", "no_such_plugin", {}, enabled=True
        )
        storage.save_credential(USER_ID, "ghost", "refresh_token", "orphan")
        return storage

    @pytest.mark.parametrize("provider", ["gog", "epic", "trakt"])
    def test_status_answers_rather_than_500s(
        self, client: TestClient, ghost: StorageManager, provider: str
    ) -> None:
        body = client.get(f"/api/{provider}/status?source_id=ghost").json()

        assert body["enabled"] is False
        assert body["connected"] is False

    @pytest.mark.parametrize("provider", ["gog", "epic", "trakt"])
    def test_disconnect_leaves_the_row_alone(
        self, client: TestClient, ghost: StorageManager, provider: str
    ) -> None:
        response = client.delete(f"/api/{provider}/token?source_id=ghost")

        assert response.status_code == 404, response.text
        assert ghost.get_credential(USER_ID, "ghost", "refresh_token") == "orphan"

    @pytest.mark.parametrize(
        ("endpoint", "body"),
        [
            ("/api/gog/exchange", {"code_or_url": "code"}),
            ("/api/epic/exchange", {"code_or_json": "code"}),
        ],
    )
    def test_exchange_writes_nothing(
        self,
        client: TestClient,
        ghost: StorageManager,
        endpoint: str,
        body: dict[str, str],
    ) -> None:
        response = client.post(f"{endpoint}?source_id=ghost", json=body)

        assert response.status_code == 400, response.text
        assert ghost.get_credential(USER_ID, "ghost", "refresh_token") == "orphan"


class TestADisabledSourceCanStillBeDisconnectedRegression:
    """Reported: disabling a connected source strands its refresh token.

    Cause: the routes resolve through ``get_sync_handler``, which drops
    disabled sources. Fix: bind on the source's plugin, not its enabled flag.
    """

    @pytest.fixture()
    def disabled(
        self, client: TestClient, storage: StorageManager, source_id: str
    ) -> StorageManager:
        storage.save_credential(USER_ID, source_id, "refresh_token", "still-live")
        storage.set_source_config_enabled(USER_ID, source_id, False)
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
        assert disabled.get_credential(USER_ID, source_id, "refresh_token") is None


class TestSourceIdIsValidated:
    """The id becomes a credential key, so a malformed one never reaches storage."""

    @pytest.mark.parametrize(
        ("method", "endpoint"),
        [
            ("GET", "/api/gog/status"),
            ("GET", "/api/epic/status"),
            ("GET", "/api/trakt/status"),
            ("DELETE", "/api/gog/token"),
            ("DELETE", "/api/epic/token"),
            ("DELETE", "/api/trakt/token"),
        ],
    )
    def test_rejects_an_id_outside_the_pattern(
        self, client: TestClient, method: str, endpoint: str
    ) -> None:
        response = client.request(method, f"{endpoint}?source_id=Not%20An%20Id")

        assert response.status_code == 422


# Every route that takes the parameter, with a body that would otherwise be
# accepted. Listing them here rather than only the GET/DELETE pair is the point:
# the write routes are the ones that turn the id into a credential key.
WRITE_ROUTES: list[tuple[str, dict[str, str] | None]] = [
    ("/api/gog/exchange", {"code_or_url": "an-authorization-code-long-enough"}),
    ("/api/epic/exchange", {"code_or_json": "an-authorization-code-long-enough"}),
    ("/api/trakt/start-device-flow", None),
    ("/api/trakt/poll-device-approval", {"device_code": "dev1234567"}),
]

# `^…$` is end-of-line in Python's ``re``, not end-of-string, so a trailing
# newline is the payload that separates a full-match check from a search.
MALFORMED_IDS = ["Not An Id", "gog\n", "1gog", "../gog", "gog work", "gög", ""]


class TestEveryOAuthRouteValidatesTheSourceId:
    """The parameter is a credential key on ten routes, not six."""

    @pytest.mark.parametrize(("endpoint", "body"), WRITE_ROUTES)
    @pytest.mark.parametrize("bad_id", MALFORMED_IDS)
    def test_write_route_rejects_a_malformed_id(
        self,
        client: TestClient,
        storage: StorageManager,
        endpoint: str,
        body: dict[str, str] | None,
        bad_id: str,
    ) -> None:
        response = client.post(endpoint, params={"source_id": bad_id}, json=body)

        assert response.status_code == 422, response.text
        assert storage.get_credential(USER_ID, bad_id, "refresh_token") is None

    @pytest.mark.parametrize(
        ("method", "endpoint"),
        [
            ("GET", "/api/gog/status"),
            ("GET", "/api/epic/status"),
            ("GET", "/api/trakt/status"),
            ("DELETE", "/api/gog/token"),
            ("DELETE", "/api/epic/token"),
            ("DELETE", "/api/trakt/token"),
        ],
    )
    @pytest.mark.parametrize("bad_id", MALFORMED_IDS)
    def test_read_route_rejects_a_malformed_id(
        self, client: TestClient, method: str, endpoint: str, bad_id: str
    ) -> None:
        response = client.request(method, endpoint, params={"source_id": bad_id})

        assert response.status_code == 422, response.text
