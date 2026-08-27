"""Tests for the per-source configuration API endpoints behind the web UI's
data-source accordions."""

from __future__ import annotations

import logging
import threading
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import Mock, patch

import pytest
from fastapi.testclient import TestClient

from src.ingestion.schedule import SYNC_INTERVAL_KEYS
from src.ingestion.sync import SyncResult
from src.recommendations.engine import RecommendationEngine
from src.sources.service import SOURCE_MISCONFIGURED_DETAIL, build_runs_view
from src.storage.manager import StorageManager
from src.storage.schema import SyncRunStatus
from tests.factories import authenticated_client, booted_web_app
from tests.fakes.source_plugins import (
    BROKEN_PRIVATE_MODULE,
    BROKEN_PRIVATE_REASON,
    FAILED_PLUGIN_MODULE,
    FAILED_PLUGIN_REASON,
    UNLOADED_PLUGIN,
    UNLOADED_PLUGIN_DETAIL,
)


@pytest.fixture()
def storage(tmp_path: Path) -> StorageManager:
    return StorageManager(sqlite_path=tmp_path / "test.db")


@pytest.fixture()
def base_config() -> dict[str, Any]:
    """A minimal config with two YAML-defined sources."""
    return {
        "storage": {"database_path": "data/test.db"},
        "inputs": {
            "my_books": {
                "plugin": "fake_file",
                "enabled": True,
                "path": "/yaml/books.csv",
                "content_type": "book",
            },
            "my_games": {
                "plugin": "fake_api",
                "enabled": True,
                "api_key": "yaml_api_key",
                "user_id": "yaml_user",
                "min_minutes": 30,
                "tags": ["rpg", "indie"],
                "active": True,
            },
        },
    }


@pytest.fixture()
def client(
    registry_with_source_fakes: None,
    storage: StorageManager,
    base_config: dict[str, Any],
) -> Iterator[TestClient]:
    """TestClient with a real StorageManager and an in-memory test config.

    ``booted_web_app`` patches the boot's I/O boundaries so the suite never
    touches the real config file — only ``app_state.config`` drives behaviour,
    and tests may mutate it (e.g. to assert the YAML purge after a migrate).
    """
    engine = Mock(spec=RecommendationEngine)
    engine.storage = storage

    with booted_web_app(storage, base_config, engine=engine) as app:
        yield authenticated_client(app)


class TestSchemaEndpoint:
    def test_returns_schema_for_yaml_source(self, client: TestClient) -> None:
        response = client.get("/api/sync/sources/my_books/schema")
        assert response.status_code == 200
        body = response.json()
        assert body["source_id"] == "my_books"
        assert body["plugin"] == "fake_file"
        assert body["plugin_display_name"] == "Fake File"
        fields = {f["name"]: f for f in body["fields"]}
        assert set(fields) == {"path", "content_type"}
        path_field = fields["path"]
        assert path_field["field_type"] == "str"
        assert path_field["required"] is True
        assert path_field["sensitive"] is False

    def test_carries_every_cadence_preset_with_a_label(
        self, client: TestClient
    ) -> None:
        options = client.get("/api/sync/sources/my_books/schema").json()[
            "sync_intervals"
        ]

        assert {option["key"] for option in options} == set(SYNC_INTERVAL_KEYS)
        assert all(option["label"] for option in options)

    def test_returns_404_for_unknown_source(self, client: TestClient) -> None:
        response = client.get("/api/sync/sources/missing/schema")
        assert response.status_code == 404


class TestConfigEndpoint:
    def test_yaml_only_source_returns_yaml_values(self, client: TestClient) -> None:
        response = client.get("/api/sync/sources/my_books/config")
        assert response.status_code == 200
        body = response.json()
        assert body["source_id"] == "my_books"
        assert body["plugin"] == "fake_file"
        assert body["enabled"] is True
        assert body["migrated"] is False
        assert body["migrated_at"] is None
        assert body["field_values"] == {
            "path": "/yaml/books.csv",
            "content_type": "book",
        }
        assert body["secret_status"] == {}

    def test_yaml_secret_value_is_never_returned(self, client: TestClient) -> None:
        response = client.get("/api/sync/sources/my_games/config")
        body = response.json()
        assert "api_key" not in body["field_values"]
        assert body["secret_status"] == {"api_key": True}
        assert body["field_values"]["user_id"] == "yaml_user"
        assert body["field_values"]["min_minutes"] == 30
        assert body["field_values"]["tags"] == ["rpg", "indie"]
        assert body["field_values"]["active"] is True

    def test_post_migration_returns_db_values(
        self, client: TestClient, storage: StorageManager
    ) -> None:
        storage.sources.upsert(
            1,
            "my_books",
            "fake_file",
            {"path": "/db/books.csv", "content_type": "book"},
            enabled=False,
        )
        response = client.get("/api/sync/sources/my_books/config")
        body = response.json()
        assert body["migrated"] is True
        assert body["migrated_at"] is not None
        assert body["enabled"] is False
        assert body["field_values"]["path"] == "/db/books.csv"

    def test_yaml_secret_status_unset_for_non_string_value(
        self, client: TestClient, base_config: dict[str, Any]
    ) -> None:
        """A YAML secret value of False/None/0 must not be reported as set.

        Regression guard for ``is_nonempty_secret_value``: a naive
        ``str(value).strip()`` truthiness check would mis-classify
        ``False`` (becomes ``"False"``) as a stored secret.
        """
        # Override yaml to put False in api_key (a non-string value).
        base_config["inputs"]["my_games"]["api_key"] = False
        response = client.get("/api/sync/sources/my_games/config")
        body = response.json()
        assert body["secret_status"] == {"api_key": False}


class TestMigrateEndpoint:
    def test_migrates_yaml_into_db_when_no_boot_pass_ran(
        self,
        client: TestClient,
        storage: StorageManager,
        base_config: dict[str, Any],
    ) -> None:
        """``migrate_source``'s own work: the boot migration is stubbed here.

        The shipped ordering — startup encrypts the secret first — is the
        class below.
        """
        response = client.post("/api/sync/sources/my_games/migrate")
        assert response.status_code == 200
        body = response.json()
        assert body["source_id"] == "my_games"
        assert set(body["fields_migrated"]) == {
            "user_id",
            "min_minutes",
            "tags",
            "active",
        }
        assert body["secrets_migrated"] == ["api_key"]

        row = storage.sources.get(1, "my_games")
        assert row is not None
        assert row["plugin"] == "fake_api"
        assert row["enabled"] is True
        assert row["config"]["user_id"] == "yaml_user"
        assert row["config"]["min_minutes"] == 30
        assert "api_key" not in row["config"]

        decrypted = storage.credentials.get(1, "my_games", "api_key")
        assert decrypted == "yaml_api_key"

        # YAML entry remains in the in-memory config (mutating shared state
        # in a request handler would race with concurrent reads).
        # ``resolve_inputs`` prefers the DB row regardless.
        assert "my_games" in base_config["inputs"]

    def test_a_secret_no_key_can_decrypt_is_still_reported(
        self,
        client: TestClient,
        storage: StorageManager,
        base_config: dict[str, Any],
    ) -> None:
        """Storage, not readability — the answer ``secret_status`` also gives.

        The migration keeps a row an encryption-key change made unreadable, so
        omitting it here would offer the operator a migration that never comes.
        """
        # Nothing left in the file to re-encrypt from, so the stale row is the
        # only thing the answer can be built out of.
        del base_config["inputs"]["my_games"]["api_key"]
        with storage.connection() as conn:
            conn.execute(
                "INSERT INTO credentials "
                "(user_id, source_id, credential_key, credential_value) "
                "VALUES (1, 'my_games', 'api_key', 'stale_garbage')"
            )
            conn.commit()

        response = client.post("/api/sync/sources/my_games/migrate")

        assert response.status_code == 200, response.text
        assert storage.credentials.get(1, "my_games", "api_key") is None
        assert response.json()["secrets_migrated"] == ["api_key"]

    def test_migration_is_idempotent(
        self, client: TestClient, storage: StorageManager
    ) -> None:
        first = client.post("/api/sync/sources/my_books/migrate")
        second = client.post("/api/sync/sources/my_books/migrate")
        assert first.status_code == 200
        assert second.status_code == 200
        # Only one row exists
        rows = storage.sources.list(1)
        assert len([r for r in rows if r["source_id"] == "my_books"]) == 1


class TestMigrateNamesASecretTheBootPassEncryptedRegression:
    """Reported: ``source migrate`` printed no ``Secrets:`` line.

    Cause: the answer counted what the call itself moved, and the startup
    migration had already emptied the YAML entry. Fix: it counts the rows.
    """

    def test_a_real_boot_still_reports_the_secret(
        self,
        registry_with_source_fakes: None,
        storage: StorageManager,
        base_config: dict[str, Any],
    ) -> None:
        with booted_web_app(storage, base_config, migrate_credentials=True) as app:
            client = authenticated_client(app)
            # Anchored: startup, not this request, is what encrypted it.
            assert storage.credentials.get(1, "my_games", "api_key") == "yaml_api_key"
            response = client.post("/api/sync/sources/my_games/migrate")

        assert response.status_code == 200, response.text
        assert response.json()["secrets_migrated"] == ["api_key"]
        assert storage.credentials.get(1, "my_games", "api_key") == "yaml_api_key"


class TestUpdateConfigEndpoint:
    def test_updates_non_sensitive_fields(
        self, client: TestClient, storage: StorageManager
    ) -> None:
        client.post("/api/sync/sources/my_games/migrate")
        response = client.put(
            "/api/sync/sources/my_games/config",
            json={
                "values": {
                    "user_id": "new_user",
                    "min_minutes": 60,
                    "tags": ["rpg"],
                    "active": False,
                }
            },
        )
        assert response.status_code == 200
        body = response.json()
        # Response body reflects the freshly-saved values — guards against a
        # regression where the endpoint returns stale data after the write.
        assert body["source_id"] == "my_games"
        assert body["field_values"]["user_id"] == "new_user"
        assert body["field_values"]["min_minutes"] == 60
        assert body["field_values"]["tags"] == ["rpg"]
        assert body["field_values"]["active"] is False
        row = storage.sources.get(1, "my_games")
        assert row is not None
        assert row["config"]["user_id"] == "new_user"
        assert row["config"]["min_minutes"] == 60
        assert row["config"]["tags"] == ["rpg"]
        assert row["config"]["active"] is False

    def test_rejects_attempt_to_set_sensitive_field_through_config(
        self, client: TestClient, storage: StorageManager
    ) -> None:
        client.post("/api/sync/sources/my_games/migrate")
        response = client.put(
            "/api/sync/sources/my_games/config",
            json={"values": {"api_key": "leaked"}},
        )
        assert response.status_code == 400
        decrypted = storage.credentials.get(1, "my_games", "api_key")
        assert decrypted == "yaml_api_key"

    def test_rejects_unknown_field(
        self, client: TestClient, storage: StorageManager
    ) -> None:
        client.post("/api/sync/sources/my_games/migrate")
        response = client.put(
            "/api/sync/sources/my_games/config",
            json={"values": {"random_field": "x"}},
        )
        assert response.status_code == 400

    def test_returns_404_when_not_migrated(self, client: TestClient) -> None:
        response = client.put(
            "/api/sync/sources/my_books/config",
            json={"values": {"path": "/x"}},
        )
        assert response.status_code == 404


class TestSecretEndpoints:
    def test_put_secret_stores_encrypted_credential(
        self, client: TestClient, storage: StorageManager
    ) -> None:
        client.post("/api/sync/sources/my_games/migrate")
        response = client.put(
            "/api/sync/sources/my_games/secret/api_key",
            json={"value": "rotated_key"},
        )
        assert response.status_code == 204
        assert storage.credentials.get(1, "my_games", "api_key") == "rotated_key"

    def test_put_secret_404_for_unknown_field_name(
        self, client: TestClient, storage: StorageManager
    ) -> None:
        """A field not declared in the plugin schema returns 404."""
        client.post("/api/sync/sources/my_games/migrate")
        response = client.put(
            "/api/sync/sources/my_games/secret/no_such_field",
            json={"value": "x"},
        )
        assert response.status_code == 404
        # Guard fired before any DB write — no credential row created.
        assert storage.credentials.get(1, "my_games", "no_such_field") is None

    def test_put_secret_400_for_non_sensitive_field(
        self, client: TestClient, storage: StorageManager
    ) -> None:
        client.post("/api/sync/sources/my_games/migrate")
        response = client.put(
            "/api/sync/sources/my_games/secret/user_id",
            json={"value": "x"},
        )
        assert response.status_code == 400

    def test_delete_secret_removes_credential(
        self, client: TestClient, storage: StorageManager
    ) -> None:
        client.post("/api/sync/sources/my_games/migrate")
        response = client.delete("/api/sync/sources/my_games/secret/api_key")
        assert response.status_code == 204
        assert storage.credentials.get(1, "my_games", "api_key") is None


class TestEnabledEndpoint:
    def test_toggles_enabled_on_migrated_source(
        self, client: TestClient, storage: StorageManager
    ) -> None:
        client.post("/api/sync/sources/my_books/migrate")
        response = client.put(
            "/api/sync/sources/my_books/enabled", json={"enabled": False}
        )
        assert response.status_code == 200
        body = response.json()
        # Response body reflects the freshly-toggled state, not stale data.
        assert body["enabled"] is False
        assert body["source_id"] == "my_books"
        # migrated_at survives the toggle (a regression that returned None
        # would still satisfy the boolean assertion above).
        assert body["migrated_at"] is not None
        row = storage.sources.get(1, "my_books")
        assert row is not None
        assert row["enabled"] is False

    def test_returns_404_when_not_migrated(self, client: TestClient) -> None:
        response = client.put(
            "/api/sync/sources/my_books/enabled", json={"enabled": False}
        )
        assert response.status_code == 404


_RUN_START = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)


def _record_run(
    storage: StorageManager,
    *,
    source_id: str = "my_books",
    status: SyncRunStatus = "completed",
    minute: int = 0,
    errors: tuple[str, ...] = (),
) -> None:
    started_at = _RUN_START + timedelta(minutes=minute)
    storage.sync_runs.record(
        1,
        source_id,
        started_at=started_at,
        finished_at=started_at + timedelta(seconds=30),
        status=status,
        errors=errors,
    )


def _listing_entry(client: TestClient, source_id: str) -> dict[str, Any]:
    body = client.get("/api/sync/sources").json()
    return next(entry for entry in body if entry["id"] == source_id)


class TestScheduleEndpoint:
    def test_stores_the_interval_and_the_listing_reports_it(
        self, client: TestClient
    ) -> None:
        client.post("/api/sync/sources/my_books/migrate")

        response = client.put(
            "/api/sync/sources/my_books/schedule", json={"interval": "6h"}
        )

        assert response.status_code == 200
        assert response.json()["sync_interval"] == "6h"
        entry = _listing_entry(client, "my_books")
        assert entry["sync_interval"] == "6h"

    def test_an_interval_outside_the_presets_is_refused_and_stores_nothing(
        self, client: TestClient, storage: StorageManager
    ) -> None:
        client.post("/api/sync/sources/my_books/migrate")
        client.put("/api/sync/sources/my_books/schedule", json={"interval": "6h"})

        response = client.put(
            "/api/sync/sources/my_books/schedule", json={"interval": "fortnightly"}
        )

        assert response.status_code == 400
        row = storage.sources.get(1, "my_books")
        assert row is not None
        assert row["sync_interval"] == "6h"

    def test_returns_404_when_not_migrated(self, client: TestClient) -> None:
        response = client.put(
            "/api/sync/sources/my_books/schedule", json={"interval": "daily"}
        )
        assert response.status_code == 404


class TestSourceListingReportsTheSchedule:
    def test_a_yaml_only_source_is_off_until_it_is_migrated(
        self, client: TestClient
    ) -> None:
        entry = _listing_entry(client, "my_books")

        assert entry["sync_interval"] == "off"
        assert entry["last_run_at"] is None
        assert entry["next_run_at"] is None

    def test_a_migrated_source_takes_the_plugin_default_and_is_due_now(
        self, client: TestClient
    ) -> None:
        client.post("/api/sync/sources/my_books/migrate")

        entry = _listing_entry(client, "my_books")

        assert entry["sync_interval"] == "daily"
        assert entry["last_run_status"] is None
        assert datetime.fromisoformat(entry["next_run_at"]) <= datetime.now(UTC)

    def test_a_source_switched_off_reports_no_next_run(
        self, client: TestClient
    ) -> None:
        client.post("/api/sync/sources/my_books/migrate")
        client.put("/api/sync/sources/my_books/schedule", json={"interval": "off"})

        assert _listing_entry(client, "my_books")["next_run_at"] is None

    def test_a_disabled_source_reports_no_next_run_it_would_never_get(
        self, client: TestClient
    ) -> None:
        client.post("/api/sync/sources/my_books/migrate")
        client.put("/api/sync/sources/my_books/enabled", json={"enabled": False})

        entry = _listing_entry(client, "my_books")

        assert entry["sync_interval"] == "daily"
        assert entry["next_run_at"] is None

    def test_a_recorded_run_reaches_the_listing_with_a_due_time(
        self, client: TestClient, storage: StorageManager
    ) -> None:
        client.post("/api/sync/sources/my_books/migrate")
        _record_run(storage, status="failed")

        entry = _listing_entry(client, "my_books")

        assert entry["last_run_at"].startswith("2026-03-01T12:00:30")
        assert entry["last_run_status"] == "failed"
        assert entry["next_run_at"] is not None

    def test_removing_a_source_leaves_a_namesake_no_history(
        self, client: TestClient, storage: StorageManager
    ) -> None:
        client.post("/api/sync/sources/my_books/migrate")
        _record_run(storage, status="failed")

        client.delete("/api/sync/sources/my_books")
        client.post("/api/sync/sources/my_books/migrate")

        entry = _listing_entry(client, "my_books")
        assert entry["last_run_at"] is None
        assert entry["last_run_status"] is None


class TestSyncRunsEndpoint:
    def test_reports_runs_newest_first_with_a_failure_error(
        self, client: TestClient, storage: StorageManager
    ) -> None:
        _record_run(storage, minute=0)
        _record_run(storage, minute=10, status="failed", errors=("429 from the API",))

        body = client.get("/api/sync/runs").json()

        assert [run["status"] for run in body] == ["failed", "completed"]
        assert body[0]["source_id"] == "my_books"
        assert body[0]["errors"] == ["429 from the API"]

    def test_source_id_keeps_another_sources_runs_out(
        self, client: TestClient, storage: StorageManager
    ) -> None:
        _record_run(storage, source_id="my_books", minute=0)
        _record_run(storage, source_id="my_games", minute=10)

        body = client.get("/api/sync/runs", params={"source_id": "my_books"}).json()

        assert [run["source_id"] for run in body] == ["my_books"]

    def test_body_is_the_builder_output_so_a_field_added_here_alone_is_caught(
        self, client: TestClient, storage: StorageManager
    ) -> None:
        _record_run(storage, status="failed", errors=("429 from the API",))

        body = client.get("/api/sync/runs").json()

        assert body == build_runs_view(storage.sync_runs.list_recent(1, 20))


class TestPluginsEndpoint:
    def test_lists_registered_plugins_with_schemas(self, client: TestClient) -> None:
        """Exact-match assertions on the PluginInfoResponse shape.

        Fixture pins the registry to two fakes; assert the full set so any
        spurious extra plugin appearing in the response is caught.
        """
        response = client.get("/api/plugins")
        assert response.status_code == 200
        body = response.json()
        assert set(body.keys()) == {"plugins", "import_errors"}
        assert {p["name"] for p in body["plugins"]} == {"fake_file", "fake_api"}
        assert body["import_errors"] == []

        fake_api = next(p for p in body["plugins"] if p["name"] == "fake_api")
        # Top-level fields cover the entire PluginInfoResponse shape.
        assert set(fake_api.keys()) == {
            "name",
            "display_name",
            "description",
            "content_types",
            "requires_api_key",
            "requires_network",
            "fields",
        }
        assert fake_api["display_name"] == "Fake API"
        assert fake_api["description"]  # non-empty string
        assert fake_api["content_types"] == ["video_game"]
        assert fake_api["requires_api_key"] is True
        assert fake_api["requires_network"] is True

        # Per-field shape: every field must include the SourceFieldSchema keys.
        for field in fake_api["fields"]:
            assert set(field.keys()) == {
                "name",
                "field_type",
                "required",
                "default",
                "description",
                "sensitive",
            }
        sensitive_map = {f["name"]: f["sensitive"] for f in fake_api["fields"]}
        assert sensitive_map["api_key"] is True
        # Sensitive field defaults are masked to None on the wire.
        api_key_field = next(f for f in fake_api["fields"] if f["name"] == "api_key")
        assert api_key_field["default"] is None


@pytest.fixture()
def broken_plugin_client(
    registry_with_a_failed_import: None,
    storage: StorageManager,
    base_config: dict[str, Any],
) -> Iterator[TestClient]:
    """A client configured against a plugin whose module never imported."""
    base_config["inputs"]["my_books"] = {
        "plugin": UNLOADED_PLUGIN,
        "enabled": True,
    }
    engine = Mock(spec=RecommendationEngine)
    engine.storage = storage

    with booted_web_app(storage, base_config, engine=engine) as app:
        yield authenticated_client(app)


class TestFailedPluginImportIsReportedRegression:
    """Symptom: goodreads_rss needs defusedxml, the container lacked it, and
    the plugin was simply absent from the Data page and the Add-Source picker.

    Cause: the registry dropped the module silently. Fix: report it on both.
    """

    def test_the_source_listing_names_the_module_and_the_reason(
        self, broken_plugin_client: TestClient
    ) -> None:
        """The Data page renders this entry instead of losing the source."""
        response = broken_plugin_client.get("/api/sync/sources")

        assert response.status_code == 200
        entry = next(item for item in response.json() if item["id"] == "my_books")
        assert entry["enabled"] is False
        assert entry["plugin_not_loaded"] == {
            "plugin": UNLOADED_PLUGIN,
            "failures": [
                {"module": FAILED_PLUGIN_MODULE, "reason": FAILED_PLUGIN_REASON}
            ],
        }

    def test_the_picker_says_why_a_plugin_is_missing_from_it(
        self, broken_plugin_client: TestClient
    ) -> None:
        """Add Source can only explain a gap the endpoint reports."""
        body = broken_plugin_client.get("/api/plugins").json()

        assert UNLOADED_PLUGIN not in {p["name"] for p in body["plugins"]}
        assert body["import_errors"] == [
            {"module": FAILED_PLUGIN_MODULE, "reason": FAILED_PLUGIN_REASON}
        ]

    def test_reading_the_source_says_why_it_cannot_be_used_not_that_it_is_gone(
        self, broken_plugin_client: TestClient
    ) -> None:
        """A 404 contradicted the listing the user was looking at."""
        response = broken_plugin_client.get("/api/sync/sources/my_books/config")

        assert response.status_code == 400
        assert response.json()["detail"] == UNLOADED_PLUGIN_DETAIL

    def test_syncing_it_is_refused_with_the_import_failure(
        self, broken_plugin_client: TestClient
    ) -> None:
        """Mirrors the CLI abort; the source still cannot run."""
        response = broken_plugin_client.post("/api/update", json={"source": "my_books"})

        assert response.status_code == 400
        assert response.json()["detail"] == UNLOADED_PLUGIN_DETAIL


class TestPrivateModuleImportFailureReachesThePicker:
    """``private/plugins/`` holds source plugins and enrichment providers alike,
    and one scan reports for both. A module that dies is otherwise a module the
    operator cannot tell from one never installed.
    """

    def test_the_picker_names_the_private_module_and_why_it_died(
        self,
        registry_with_a_broken_private_module: None,
        storage: StorageManager,
        base_config: dict[str, Any],
    ) -> None:
        engine = Mock(spec=RecommendationEngine)
        engine.storage = storage

        with booted_web_app(storage, base_config, engine=engine) as app:
            body = authenticated_client(app).get("/api/plugins").json()

        assert {
            "module": BROKEN_PRIVATE_MODULE,
            "reason": BROKEN_PRIVATE_REASON,
        } in body["import_errors"]


class TestCreateSourceEndpoint:
    def test_creates_db_backed_source(
        self, client: TestClient, storage: StorageManager
    ) -> None:
        response = client.post(
            "/api/sync/sources",
            json={
                "id": "new_books",
                "plugin": "fake_file",
                "values": {"path": "/data/new.csv", "content_type": "book"},
                "enabled": True,
            },
        )
        assert response.status_code == 201
        body = response.json()
        assert body["source_id"] == "new_books"
        assert body["plugin"] == "fake_file"
        assert body["plugin_display_name"] == "Fake File"
        assert body["enabled"] is True
        assert body["migrated"] is True
        assert body["field_values"] == {
            "path": "/data/new.csv",
            "content_type": "book",
        }
        assert body["secret_status"] == {}
        row = storage.sources.get(1, "new_books")
        assert row is not None
        assert row["config"]["path"] == "/data/new.csv"

    def test_rejects_existing_yaml_source(self, client: TestClient) -> None:
        """Creating a source whose id is already in YAML returns 409."""
        response = client.post(
            "/api/sync/sources",
            json={"id": "my_books", "plugin": "fake_file", "values": {}},
        )
        assert response.status_code == 409

    def test_accepts_hyphenated_id(
        self, client: TestClient, storage: StorageManager
    ) -> None:
        """A hyphenated id like ``calibre-web`` is a valid source id.

        Hyphens are allowed so the Add-source modal can prefill plugin names
        (e.g. ``calibre-web``) and users can type ids matching upstream
        service names without hitting a silently-disabled Create button.
        """
        response = client.post(
            "/api/sync/sources",
            json={
                "id": "calibre-web",
                "plugin": "fake_file",
                "values": {"path": "/data/cw.csv", "content_type": "book"},
                "enabled": True,
            },
        )
        assert response.status_code == 201
        assert response.json()["source_id"] == "calibre-web"
        assert storage.sources.get(1, "calibre-web") is not None

    def test_rejects_invalid_id(self, client: TestClient) -> None:
        response = client.post(
            "/api/sync/sources",
            json={"id": "Bad-ID!", "plugin": "fake_file", "values": {}},
        )
        assert response.status_code == 400

    def test_rejects_unknown_plugin(self, client: TestClient) -> None:
        response = client.post(
            "/api/sync/sources",
            json={"id": "good_id", "plugin": "no_such_plugin", "values": {}},
        )
        assert response.status_code == 400

    def test_rejects_sensitive_field_in_values(
        self, client: TestClient, storage: StorageManager
    ) -> None:
        response = client.post(
            "/api/sync/sources",
            json={
                "id": "leaky",
                "plugin": "fake_api",
                "values": {"api_key": "leaked"},
            },
        )
        assert response.status_code == 400
        assert storage.sources.get(1, "leaky") is None
        assert storage.credentials.get(1, "leaky", "api_key") is None


class TestTheWriteBoundaryRefusesWhatTheSyncWouldReject:
    """Reported: the reason quoted the plugin, which says if a path exists.

    The write door now blames a *field*; the plugin's sentence goes to the
    log. Containment still answers, reading no disk.
    """

    @pytest.fixture()
    def client(self, storage: StorageManager) -> Iterator[TestClient]:
        """Against the real plugin registry: the fakes validate nothing."""
        config: dict[str, Any] = {
            "storage": {"database_path": "data/test.db"},
            "inputs": {},
        }
        with booted_web_app(storage, config) as app:
            yield authenticated_client(app)

    def test_a_missing_directory_is_refused_without_saying_it_is_missing(
        self, client: TestClient, storage: StorageManager, tmp_path: Path
    ) -> None:
        """The oracle left open after the sync door closed.

        Inside the allowed roots, only a stat separates this path from a real
        one, so the plugin's "not found" makes the endpoint a disk probe.
        """
        missing = tmp_path / "no-such-library"

        response = client.post(
            "/api/sync/sources",
            json={
                "id": "ghost",
                "plugin": "roms",
                "values": {"paths": [str(missing)]},
            },
        )

        assert response.status_code == 400
        detail = response.json()["detail"]
        assert "'paths'" in detail
        assert "not found" not in detail.lower()
        assert str(missing) not in detail
        assert missing.name not in detail
        assert storage.sources.get(1, "ghost") is None

    def test_the_refused_field_is_named_rather_than_the_plugins_reason(
        self, client: TestClient
    ) -> None:
        """``source_url_error`` was reachable from sync alone until now."""
        response = client.post(
            "/api/sync/sources",
            json={
                "id": "sonarr",
                "plugin": "sonarr",
                "values": {"url": "file:///etc/passwd"},
            },
        )

        assert response.status_code == 400
        detail = response.json()["detail"]
        assert "'url'" in detail
        assert "http" not in detail

    def test_only_the_field_the_write_broke_is_named(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        """An edit resends every field, so blaming all of them says nothing."""
        created = client.post(
            "/api/sync/sources",
            json={
                "id": "mixed",
                "plugin": "roms",
                "values": {"paths": [str(tmp_path)]},
            },
        )

        response = client.put(
            "/api/sync/sources/mixed/config",
            json={"values": {"paths": [str(tmp_path)], "extra_strip_patterns": ["("]}},
        )

        assert created.status_code == 201
        assert response.status_code == 400
        detail = response.json()["detail"]
        assert "'extra_strip_patterns'" in detail
        assert "'paths'" not in detail

    def test_two_bad_values_name_both_fields_rather_than_neither(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        """Reverting either one alone still leaves the write refused, so the
        answer has to carry both names or the operator fixes half of it.
        """
        created = client.post(
            "/api/sync/sources",
            json={
                "id": "doubly",
                "plugin": "roms",
                "values": {"paths": [str(tmp_path)]},
            },
        )

        response = client.put(
            "/api/sync/sources/doubly/config",
            json={
                "values": {
                    "paths": [str(tmp_path)],
                    "include_extensions": [7],
                    "exclude_extensions": [7],
                }
            },
        )

        assert created.status_code == 201
        assert response.status_code == 400
        detail = response.json()["detail"]
        assert "'include_extensions'" in detail
        assert "'exclude_extensions'" in detail
        assert "must be strings" not in detail

    def test_a_path_field_holding_a_list_is_contained_entry_by_entry(
        self, client: TestClient, storage: StorageManager
    ) -> None:
        """``roms`` declares ``paths``, so containment reads a list here.

        A guard that only understood a string field would wave this through
        to the plugin, and the caller would get a field name instead of the
        setting to widen.
        """
        response = client.post(
            "/api/sync/sources",
            json={"id": "roms", "plugin": "roms", "values": {"paths": ["/etc"]}},
        )

        assert response.status_code == 400
        assert "outside the allowed source roots" in response.json()["detail"]
        assert storage.sources.get(1, "roms") is None

    def test_the_operator_reads_the_plugins_reason_in_the_log(
        self, client: TestClient, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Refusing with the reason nowhere at all would be unusable."""
        missing = tmp_path / "no-such-library"

        with caplog.at_level(logging.WARNING, logger="src.sources.service"):
            client.post(
                "/api/sync/sources",
                json={
                    "id": "ghost",
                    "plugin": "roms",
                    "values": {"paths": [str(missing)]},
                },
            )

        assert str(missing) in caplog.text

    def test_an_unparseable_url_answers_400_rather_than_500(
        self, client: TestClient
    ) -> None:
        """``urlsplit`` raises on this netloc, and nothing above catches it.

        Uncaught it leaves ``validate_config`` raising instead of returning
        errors, which the API answers 500 with a traceback.
        """
        response = client.post(
            "/api/sync/sources",
            json={
                "id": "sonarr",
                "plugin": "sonarr",
                "values": {"url": "http://[foo]"},
            },
        )

        assert response.status_code == 400
        assert "'url'" in response.json()["detail"]

    def test_a_source_whose_secret_is_not_entered_yet_stays_editable(
        self, client: TestClient
    ) -> None:
        """Only what the write breaks is refused.

        The secret endpoint runs after create, so a network source is briefly
        missing its api_key — refusing edits then would deadlock it.
        """
        created = client.post(
            "/api/sync/sources",
            json={
                "id": "sonarr",
                "plugin": "sonarr",
                "values": {"url": "http://sonarr.internal:8989"},
            },
        )

        moved = client.put(
            "/api/sync/sources/sonarr/config",
            json={"values": {"url": "http://sonarr.internal:9999"}},
        )

        assert (created.status_code, moved.status_code) == (201, 200)
        assert moved.json()["field_values"]["url"] == "http://sonarr.internal:9999"


class TestSyncLogsTheReasonItRefused:
    """Reported: the 400 quoted the plugin, which names the path it looked
    for — a file-existence oracle, one probe per request. The operator reads
    the reason in the log now; the wire gets a fixed string.
    """

    @pytest.fixture()
    def client(self, storage: StorageManager) -> Iterator[TestClient]:
        """Against the real plugin registry: the fakes validate nothing."""
        config: dict[str, Any] = {
            "storage": {"database_path": "data/test.db"},
            "inputs": {},
        }
        with booted_web_app(storage, config) as app:
            yield authenticated_client(app)

    def test_a_missing_path_is_named_in_the_log_and_not_on_the_wire(
        self, client: TestClient, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The write was valid, so no edit will ever refuse this one.

        Containment runs before existence, so a surviving "not found" names a
        path inside the allowed roots.
        """
        readable = tmp_path / "games"
        readable.mkdir()
        created = client.post(
            "/api/sync/sources",
            json={
                "id": "games",
                "plugin": "roms",
                "values": {"paths": [str(readable)]},
            },
        )
        readable.rmdir()

        with caplog.at_level(logging.WARNING, logger="src.web.api"):
            sync = client.post("/api/update", json={"source": "games"})

        assert created.status_code == 201
        assert sync.status_code == 400
        assert sync.json()["detail"] == SOURCE_MISCONFIGURED_DETAIL
        assert str(readable) not in sync.text
        assert "not found" not in sync.text
        assert "games" in caplog.text
        assert f"Scan path not found: {readable}" in caplog.text

    def _steam_source_with_a_stored_key(self, client: TestClient) -> None:
        """A steam source whose api_key exists only in the credential store."""
        created = client.post(
            "/api/sync/sources",
            json={
                "id": "games",
                "plugin": "steam",
                "values": {"steam_id": "76561198000000000"},
            },
        )
        stored = client.put(
            "/api/sync/sources/games/secret/api_key",
            json={"value": "super-secret-key"},
        )
        assert (created.status_code, stored.status_code) == (201, 204)

    def test_a_plugin_quoting_a_credential_has_it_redacted_in_the_log(
        self, client: TestClient, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The sync door validates the config with the secret layered on.

        The write door strips credentials before validating instead, which
        steam cannot survive: it reads ``api_key`` from the config alone.
        """
        self._steam_source_with_a_stored_key(client)

        with (
            patch(
                "src.ingestion.sources.steam.SteamPlugin.validate_config",
                return_value=["'api_key' super-secret-key was rejected"],
            ),
            caplog.at_level(logging.WARNING, logger="src.web.api"),
        ):
            sync = client.post("/api/update", json={"source": "games"})

        assert sync.status_code == 400
        assert "super-secret-key" not in caplog.text
        assert "'api_key' [redacted] was rejected" in caplog.text

    def test_a_source_whose_secret_is_only_stored_still_syncs(
        self, client: TestClient
    ) -> None:
        """Validating without credentials would refuse this one outright.

        ``steam.validate_config`` never consults storage, so the sync door
        has to judge the config the sync would really run.
        """
        self._steam_source_with_a_stored_key(client)

        done = threading.Event()

        def fake_execute(*, sources: list[Any], **_: Any) -> list[SyncResult]:
            done.set()
            return [SyncResult(source_name=plugin.name) for plugin, _cfg in sources]

        with patch("src.web.sync_dispatch.execute_multi_source_sync", fake_execute):
            sync = client.post("/api/update", json={"source": "games"})
            assert done.wait(timeout=5)

        assert sync.status_code == 200
        assert sync.json()["sources"] == ["games"]

    def test_a_newline_the_plugin_wrote_cannot_forge_a_log_line(
        self, client: TestClient, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A path is a plugin's to quote, and a path may hold a newline."""
        self._steam_source_with_a_stored_key(client)

        with (
            patch(
                "src.ingestion.sources.steam.SteamPlugin.validate_config",
                return_value=["not found: /srv/a\nWARNING  | forged | line"],
            ),
            caplog.at_level(logging.WARNING, logger="src.web.api"),
        ):
            sync = client.post("/api/update", json={"source": "games"})

        assert sync.status_code == 400
        assert "/srv/a\\nWARNING" in caplog.text
        assert "/srv/a\nWARNING" not in caplog.text


class TestDeleteSourceEndpoint:
    def test_removes_db_row_and_credentials(
        self, client: TestClient, storage: StorageManager
    ) -> None:
        client.post("/api/sync/sources/my_games/migrate")
        assert storage.sources.get(1, "my_games") is not None
        assert storage.credentials.get(1, "my_games", "api_key") == "yaml_api_key"

        response = client.delete("/api/sync/sources/my_games")
        assert response.status_code == 204

        assert storage.sources.get(1, "my_games") is None
        assert storage.credentials.get(1, "my_games", "api_key") is None

    def test_returns_404_when_not_migrated(self, client: TestClient) -> None:
        response = client.delete("/api/sync/sources/my_books")
        assert response.status_code == 404

    def test_returns_400_for_invalid_source_id(self, client: TestClient) -> None:
        """Malformed path id is rejected before any DB lookup."""
        response = client.delete("/api/sync/sources/Bad-ID")
        assert response.status_code == 400

    def test_the_last_source_on_a_plugin_takes_the_stranded_row_with_it(
        self,
        client: TestClient,
        storage: StorageManager,
        base_config: dict[str, Any],
    ) -> None:
        """Drop the config the route hands down and the sweep never runs."""
        base_config["inputs"].pop("my_games")
        storage.sources.upsert(1, "work_games", "fake_api", {}, enabled=True)
        storage.credentials.save(1, "fake_api", "api_key", "stranded-by-an-upgrade")

        response = client.delete("/api/sync/sources/work_games")

        assert response.status_code == 204
        assert storage.credentials.get(1, "fake_api", "api_key") is None

    def test_a_yaml_sibling_on_the_plugin_keeps_the_stranded_row(
        self, client: TestClient, storage: StorageManager
    ) -> None:
        """``my_games`` stays in the config, and it alone may read that row."""
        storage.sources.upsert(1, "work_games", "fake_api", {}, enabled=True)
        storage.credentials.save(1, "fake_api", "api_key", "stranded-by-an-upgrade")

        response = client.delete("/api/sync/sources/work_games")

        assert response.status_code == 204
        assert storage.credentials.get(1, "fake_api", "api_key") == (
            "stranded-by-an-upgrade"
        )


class TestUpdateEndpointDbOnlySourcesRegression:
    """Per-source sync (POST /api/update) must resolve DB-only sources.

    Bug: a source created via ``create_source`` / the web Add-source modal
    lives only in the ``source_configs`` table (plus its secret in
    ``credentials``) with no YAML ``inputs`` entry. Clicking Sync errored
    "disabled or not configured" and — because the endpoint answered HTTP 200
    with a message body — the web UI's Sync button stuck spinning (no SyncJob
    is created to end the frontend polling).

    Root cause: the single-source ``/update`` branch gated on the YAML
    ``inputs`` map only (``config.get("inputs", {}).get(source)``), so a DB-only
    source was invisible to it.

    Fix: the branch resolves through ``resolve_inputs`` (which merges DB
    sources, injects ``_source_id`` and decrypts credentials) filtered by
    ``source_id``, and answers 400 when no enabled source matches.
    """

    def _capture_sync(self) -> tuple[dict[str, Any], threading.Event]:
        """Patchable stand-in for execute_multi_source_sync.

        Records the ``sources`` list it was called with (so the resolved
        config can be asserted) and signals an Event so the test can wait for
        the background sync thread deterministically.
        """
        captured: dict[str, Any] = {}
        done = threading.Event()

        def fake_execute(*, sources: list[Any], **_: Any) -> list[SyncResult]:
            captured["sources"] = sources
            done.set()
            return [SyncResult(source_name=plugin.name) for plugin, _cfg in sources]

        captured["fn"] = fake_execute
        return captured, done

    def test_enabled_db_only_source_syncs_regression(
        self, client: TestClient, storage: StorageManager
    ) -> None:
        """An enabled DB-only source starts a sync instead of erroring.

        Asserts end to end (not just the returned status): the plugin the sync
        boundary receives carries the injected ``_source_id`` and the decrypted
        ``api_key`` credential plus the DB config, proving the single-source
        path actually resolves and runs the DB source — not merely passes the
        pre-check.
        """
        # Create a DB-only source (no YAML inputs entry) with its secret set.
        create = client.post(
            "/api/sync/sources",
            json={
                "id": "calibre-web",
                "plugin": "fake_api",
                "values": {"user_id": "reader"},
                "enabled": True,
            },
        )
        assert create.status_code == 201
        client.put(
            "/api/sync/sources/calibre-web/secret/api_key",
            json={"value": "top-secret"},
        )

        captured, done = self._capture_sync()
        with patch("src.web.sync_dispatch.execute_multi_source_sync", captured["fn"]):
            response = client.post("/api/update", json={"source": "calibre-web"})
            assert response.status_code == 200
            body = response.json()
            # Not the old 200 "disabled or not configured" dead-end.
            assert "disabled or not configured" not in body.get("message", "")
            assert body["sources"] == ["calibre-web"]
            assert done.wait(timeout=5)

        # End-to-end: the plugin received the fully-resolved config — the
        # injected _source_id and the decrypted secret, proving the single
        # source path runs, not just passes the pre-check.
        sources = captured["sources"]
        assert len(sources) == 1
        _plugin, resolved_config = sources[0]
        assert resolved_config["_source_id"] == "calibre-web"
        assert resolved_config["api_key"] == "top-secret"
        assert resolved_config["user_id"] == "reader"

    def test_disabled_db_only_source_returns_400_regression(
        self, client: TestClient, storage: StorageManager
    ) -> None:
        """A disabled DB-only source answers 4xx, not a 200 dead-end."""
        storage.sources.upsert(
            1, "calibre-web", "fake_api", {"user_id": "reader"}, enabled=False
        )
        response = client.post("/api/update", json={"source": "calibre-web"})
        assert response.status_code == 400
        assert "disabled or not configured" in response.json()["detail"]

    def test_unknown_source_returns_400_regression(self, client: TestClient) -> None:
        """A source id that matches nothing answers 4xx, not a 200 dead-end."""
        response = client.post("/api/update", json={"source": "no_such_source"})
        assert response.status_code == 400
        assert "disabled or not configured" in response.json()["detail"]


class TestSourceCredentialMoveRegression:
    """Regression: an https edit wiped the source's password.

    Any change to a ``credential_bound`` field cleared the secret, so the next
    sync blamed 'password'. Fix: only a change of host does, and that is
    refused rather than applied.
    """

    @pytest.fixture()
    def real_plugin_client(self, storage: StorageManager) -> Iterator[TestClient]:
        """A booted app over the real plugin registry, so calibre_web resolves."""
        engine = Mock(spec=RecommendationEngine)
        engine.storage = storage
        config: dict[str, Any] = {
            "storage": {"database_path": "data/test.db"},
            "inputs": {},
        }
        with booted_web_app(storage, config, engine=engine) as app:
            yield authenticated_client(app)

    @staticmethod
    def _source_with_a_secret(
        client: TestClient,
        source_id: str,
        plugin: str,
        values: dict[str, Any],
        secret: tuple[str, str],
    ) -> None:
        """The state before the edit: migrated, enabled, credential entered."""
        create = client.post(
            "/api/sync/sources",
            json={
                "id": source_id,
                "plugin": plugin,
                "values": values,
                "enabled": True,
            },
        )
        assert create.status_code == 201
        key, value = secret
        stored = client.put(
            f"/api/sync/sources/{source_id}/secret/{key}", json={"value": value}
        )
        assert stored.status_code == 204

    def test_the_reported_https_edit_keeps_the_password_and_the_sync(
        self, real_plugin_client: TestClient, storage: StorageManager
    ) -> None:
        """The reported sequence: same host over https, and no 400 after it."""
        client = real_plugin_client
        self._source_with_a_secret(
            client,
            "calibre",
            "calibre_web",
            {"url": "http://books.lan:8083", "username": "reader", "verify_ssl": True},
            ("password", "hunter2"),
        )

        saved = client.put(
            "/api/sync/sources/calibre/config",
            json={"values": {"url": "https://books.lan:8083"}},
        )

        assert saved.status_code == 200
        assert saved.json()["secret_status"]["password"] is True
        assert storage.credentials.get(1, "calibre", "password") == "hunter2"

        started = threading.Event()

        def fake_execute(*, sources: list[Any], **_: Any) -> list[SyncResult]:
            started.set()
            return [SyncResult(source_name=plugin.name) for plugin, _cfg in sources]

        with patch("src.web.sync_dispatch.execute_multi_source_sync", fake_execute):
            sync = client.post("/api/update", json={"source": "calibre"})

        assert sync.status_code == 200
        assert started.wait(timeout=5)

    def test_toggling_verify_ssl_keeps_the_password(
        self, real_plugin_client: TestClient, storage: StorageManager
    ) -> None:
        """The flag exists for self-signed certs — using it cost a password."""
        client = real_plugin_client
        self._source_with_a_secret(
            client,
            "calibre",
            "calibre_web",
            {"url": "https://books.lan:8083", "username": "reader", "verify_ssl": True},
            ("password", "hunter2"),
        )

        saved = client.put(
            "/api/sync/sources/calibre/config", json={"values": {"verify_ssl": False}}
        )

        assert saved.status_code == 200
        assert saved.json()["field_values"]["verify_ssl"] is False
        assert storage.credentials.get(1, "calibre", "password") == "hunter2"

    def test_repointing_at_another_host_is_refused_with_the_secret_intact(
        self, real_plugin_client: TestClient, storage: StorageManager
    ) -> None:
        """The original exfiltration, now closed without destroying anything."""
        client = real_plugin_client
        self._source_with_a_secret(
            client,
            "calibre",
            "calibre_web",
            {"url": "http://books.lan:8083", "username": "reader", "verify_ssl": True},
            ("password", "hunter2"),
        )

        refused = client.put(
            "/api/sync/sources/calibre/config",
            json={"values": {"url": "https://attacker.example"}},
        )

        assert refused.status_code == 400
        assert refused.json()["detail"] == (
            "Changing 'url' points this source at a different host. Clear its "
            "stored 'password' first, then save this change and enter the "
            "credential the new host expects."
        )
        current = client.get("/api/sync/sources/calibre/config").json()
        assert current["field_values"]["url"] == "http://books.lan:8083"
        assert storage.credentials.get(1, "calibre", "password") == "hunter2"

    def test_clearing_the_secret_first_lets_the_move_through(
        self, real_plugin_client: TestClient, storage: StorageManager
    ) -> None:
        """The remedy the refusal names has to actually work."""
        client = real_plugin_client
        self._source_with_a_secret(
            client,
            "calibre",
            "calibre_web",
            {"url": "http://books.lan:8083", "username": "reader", "verify_ssl": True},
            ("password", "hunter2"),
        )

        cleared = client.delete("/api/sync/sources/calibre/secret/password")
        moved = client.put(
            "/api/sync/sources/calibre/config",
            json={"values": {"url": "https://books.example:8083"}},
        )

        assert cleared.status_code == 204
        assert moved.status_code == 200
        assert moved.json()["field_values"]["url"] == "https://books.example:8083"
        assert storage.credentials.get(1, "calibre", "password") is None
