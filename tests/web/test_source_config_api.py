"""Tests for the per-source configuration API endpoints.

These endpoints back the data-source accordions in the web UI: schema
introspection, current values (with secrets stripped), one-shot
migration of a YAML entry into the database, and incremental updates of
non-sensitive fields, secrets, and the enabled flag.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import Mock, patch

import pytest
from fastapi.testclient import TestClient

from src.ingestion.sync import SyncResult
from src.llm.client import OllamaClient
from src.llm.embeddings import EmbeddingGenerator
from src.llm.recommendations import RecommendationGenerator
from src.recommendations.engine import RecommendationEngine
from src.storage.manager import StorageManager
from src.web.api import SOURCE_MISCONFIGURED_DETAIL
from tests.factories import authenticated_client, booted_web_app


@pytest.fixture()
def storage(tmp_path: Path) -> StorageManager:
    return StorageManager(sqlite_path=tmp_path / "test.db")


@pytest.fixture()
def base_config() -> dict[str, Any]:
    """A minimal config with two YAML-defined sources."""
    return {
        "ollama": {"base_url": "http://localhost:11434", "model": "x"},
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
    touches the real config file or LLM stack — only ``app_state.config``
    drives behaviour, and tests may mutate it (e.g. to assert the YAML purge
    after a migrate).
    """
    engine = Mock(spec=RecommendationEngine)
    engine.storage = storage
    llm_components = (
        Mock(spec=OllamaClient),
        Mock(spec=EmbeddingGenerator),
        Mock(spec=RecommendationGenerator),
    )

    with (
        patch("src.web.app.migrate_source_labels"),
        patch("src.web.app.migrate_source_config_plugins"),
        booted_web_app(storage, base_config, llm_components, engine=engine) as app,
    ):
        yield authenticated_client(app)


class TestSchemaEndpoint:
    def test_returns_schema_for_yaml_source(self, client: TestClient) -> None:
        response = client.get("/api/sync/sources/my_books/schema")
        assert response.status_code == 200
        body = response.json()
        assert body["source_id"] == "my_books"
        assert body["plugin"] == "fake_file"
        assert body["plugin_display_name"] == "Fake File"
        names = [f["name"] for f in body["fields"]]
        assert names == ["path", "content_type"]
        path_field = body["fields"][0]
        assert path_field["field_type"] == "str"
        assert path_field["required"] is True
        assert path_field["sensitive"] is False

    def test_field_types_serialize_to_known_strings(self, client: TestClient) -> None:
        response = client.get("/api/sync/sources/my_games/schema")
        assert response.status_code == 200
        types = {f["name"]: f["field_type"] for f in response.json()["fields"]}
        assert types == {
            "api_key": "str",
            "user_id": "str",
            "min_minutes": "int",
            "tags": "list",
            "active": "bool",
        }

    def test_marks_sensitive_fields(self, client: TestClient) -> None:
        response = client.get("/api/sync/sources/my_games/schema")
        sensitive = {f["name"]: f["sensitive"] for f in response.json()["fields"]}
        assert sensitive == {
            "api_key": True,
            "user_id": False,
            "min_minutes": False,
            "tags": False,
            "active": False,
        }

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
        storage.upsert_source_config(
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

    def test_post_migration_secret_status_reflects_credentials_table(
        self, client: TestClient, storage: StorageManager
    ) -> None:
        storage.upsert_source_config(
            1, "my_games", "fake_api", {"user_id": "u", "min_minutes": 0}, True
        )
        storage.save_credential(1, "my_games", "api_key", "real_key")
        response = client.get("/api/sync/sources/my_games/config")
        body = response.json()
        assert body["secret_status"] == {"api_key": True}
        assert "api_key" not in body["field_values"]

    def test_post_migration_secret_status_unset_when_no_credential(
        self, client: TestClient, storage: StorageManager
    ) -> None:
        """A migrated source with no stored credential reports secret_status=False."""
        storage.upsert_source_config(
            1, "my_games", "fake_api", {"user_id": "u"}, enabled=True
        )
        response = client.get("/api/sync/sources/my_games/config")
        body = response.json()
        assert body["migrated"] is True
        assert body["secret_status"] == {"api_key": False}

    def test_yaml_secret_status_unset_for_non_string_value(
        self, client: TestClient, base_config: dict[str, Any]
    ) -> None:
        """A YAML secret value of False/None/0 must not be reported as set.

        Regression guard for ``_is_nonempty_secret_value``: a naive
        ``str(value).strip()`` truthiness check would mis-classify
        ``False`` (becomes ``"False"``) as a stored secret.
        """
        # Override yaml to put False in api_key (a non-string value).
        base_config["inputs"]["my_games"]["api_key"] = False
        response = client.get("/api/sync/sources/my_games/config")
        body = response.json()
        assert body["secret_status"] == {"api_key": False}

    def test_returns_404_for_unknown_source(self, client: TestClient) -> None:
        response = client.get("/api/sync/sources/missing/config")
        assert response.status_code == 404


class TestMigrateEndpoint:
    def test_migrates_yaml_into_db(
        self,
        client: TestClient,
        storage: StorageManager,
        base_config: dict[str, Any],
    ) -> None:
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

        row = storage.get_source_config(1, "my_games")
        assert row is not None
        assert row["plugin"] == "fake_api"
        assert row["enabled"] is True
        assert row["config"]["user_id"] == "yaml_user"
        assert row["config"]["min_minutes"] == 30
        assert "api_key" not in row["config"]

        decrypted = storage.get_credential(1, "my_games", "api_key")
        assert decrypted == "yaml_api_key"

        # YAML entry remains in the in-memory config (mutating shared state
        # in a request handler would race with concurrent reads).
        # ``resolve_inputs`` prefers the DB row regardless.
        assert "my_games" in base_config["inputs"]

    def test_migration_is_idempotent(
        self, client: TestClient, storage: StorageManager
    ) -> None:
        first = client.post("/api/sync/sources/my_books/migrate")
        second = client.post("/api/sync/sources/my_books/migrate")
        assert first.status_code == 200
        assert second.status_code == 200
        # Only one row exists
        rows = storage.list_source_configs(1)
        assert len([r for r in rows if r["source_id"] == "my_books"]) == 1

    def test_returns_404_for_unknown_source(self, client: TestClient) -> None:
        response = client.post("/api/sync/sources/nothing/migrate")
        assert response.status_code == 404


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
        row = storage.get_source_config(1, "my_games")
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
        decrypted = storage.get_credential(1, "my_games", "api_key")
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

    def test_empty_values_dict_is_a_no_op(
        self, client: TestClient, storage: StorageManager
    ) -> None:
        """PUT with values={} returns 200 and leaves stored config unchanged."""
        client.post("/api/sync/sources/my_games/migrate")
        before = storage.get_source_config(1, "my_games")
        assert before is not None
        response = client.put("/api/sync/sources/my_games/config", json={"values": {}})
        assert response.status_code == 200
        after = storage.get_source_config(1, "my_games")
        assert after is not None
        assert after["config"] == before["config"]


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
        assert storage.get_credential(1, "my_games", "api_key") == "rotated_key"

    def test_put_secret_404_for_unknown_source(self, client: TestClient) -> None:
        response = client.put(
            "/api/sync/sources/none/secret/api_key", json={"value": "x"}
        )
        assert response.status_code == 404

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
        assert storage.get_credential(1, "my_games", "no_such_field") is None

    def test_delete_secret_404_for_unknown_field_name(
        self, client: TestClient, storage: StorageManager
    ) -> None:
        """A field not declared in the plugin schema returns 404."""
        client.post("/api/sync/sources/my_games/migrate")
        # Pre-populate a real credential to confirm the rejected delete
        # leaves unrelated stored secrets untouched.
        storage.save_credential(1, "my_games", "api_key", "real_key")
        response = client.delete("/api/sync/sources/my_games/secret/no_such_field")
        assert response.status_code == 404
        assert storage.get_credential(1, "my_games", "api_key") == "real_key"

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
        assert storage.get_credential(1, "my_games", "api_key") is None

    def test_delete_secret_404_for_unknown_source(self, client: TestClient) -> None:
        response = client.delete("/api/sync/sources/none/secret/api_key")
        assert response.status_code == 404

    def test_delete_secret_400_for_non_sensitive_field(
        self, client: TestClient, storage: StorageManager
    ) -> None:
        """Refuse DELETE on a non-sensitive field — symmetric with PUT."""
        client.post("/api/sync/sources/my_games/migrate")
        response = client.delete("/api/sync/sources/my_games/secret/user_id")
        assert response.status_code == 400


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
        row = storage.get_source_config(1, "my_books")
        assert row is not None
        assert row["enabled"] is False

    def test_returns_404_when_not_migrated(self, client: TestClient) -> None:
        response = client.put(
            "/api/sync/sources/my_books/enabled", json={"enabled": False}
        )
        assert response.status_code == 404

    def test_re_enables_a_disabled_source(
        self, client: TestClient, storage: StorageManager
    ) -> None:
        """Symmetric round-trip: disable then re-enable a migrated source.

        Verifies both the DB-side state AND the response body so a regression
        that returns stale data after the toggle would be caught.
        """
        client.post("/api/sync/sources/my_books/migrate")
        client.put("/api/sync/sources/my_books/enabled", json={"enabled": False})
        response = client.put(
            "/api/sync/sources/my_books/enabled", json={"enabled": True}
        )
        assert response.status_code == 200
        body = response.json()
        assert body["enabled"] is True
        assert body["source_id"] == "my_books"
        row = storage.get_source_config(1, "my_books")
        assert row is not None
        assert row["enabled"] is True


class TestPluginsEndpoint:
    def test_lists_registered_plugins_with_schemas(self, client: TestClient) -> None:
        """Exact-match assertions on the PluginInfoResponse shape.

        Fixture pins the registry to two fakes; assert the full set so any
        spurious extra plugin appearing in the response is caught.
        """
        response = client.get("/api/plugins")
        assert response.status_code == 200
        body = response.json()
        assert {p["name"] for p in body} == {"fake_file", "fake_api"}

        fake_api = next(p for p in body if p["name"] == "fake_api")
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

    def test_returns_empty_list_when_no_plugins_registered(
        self, client: TestClient
    ) -> None:
        """Endpoint returns ``[]`` (not 404) when the registry is empty."""
        from src.ingestion.registry import PluginRegistry

        registry = PluginRegistry.get_instance()
        registry._discovered = True
        registry._plugins.clear()
        try:
            response = client.get("/api/plugins")
            assert response.status_code == 200
            assert response.json() == []
        finally:
            PluginRegistry.reset_instance()


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
        row = storage.get_source_config(1, "new_books")
        assert row is not None
        assert row["config"]["path"] == "/data/new.csv"

    def test_rejects_existing_yaml_source(self, client: TestClient) -> None:
        """Creating a source whose id is already in YAML returns 409."""
        response = client.post(
            "/api/sync/sources",
            json={"id": "my_books", "plugin": "fake_file", "values": {}},
        )
        assert response.status_code == 409

    def test_rejects_existing_db_source(
        self, client: TestClient, storage: StorageManager
    ) -> None:
        storage.upsert_source_config(
            1, "already_here", "fake_file", {"path": "/x"}, enabled=True
        )
        response = client.post(
            "/api/sync/sources",
            json={
                "id": "already_here",
                "plugin": "fake_file",
                "values": {"path": "/y"},
            },
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
        assert storage.get_source_config(1, "calibre-web") is not None

    def test_rejects_invalid_id(self, client: TestClient) -> None:
        response = client.post(
            "/api/sync/sources",
            json={"id": "Bad-ID!", "plugin": "fake_file", "values": {}},
        )
        assert response.status_code == 400

    def test_rejects_id_starting_with_hyphen(self, client: TestClient) -> None:
        """A hyphen may appear inside an id but never as the first character."""
        response = client.post(
            "/api/sync/sources",
            json={"id": "-nope", "plugin": "fake_file", "values": {}},
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
        assert storage.get_source_config(1, "leaky") is None
        assert storage.get_credential(1, "leaky", "api_key") is None

    def test_rejects_unknown_field(self, client: TestClient) -> None:
        response = client.post(
            "/api/sync/sources",
            json={
                "id": "wrong_field",
                "plugin": "fake_file",
                "values": {"no_such_field": "x"},
            },
        )
        assert response.status_code == 400


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
        with (
            patch("src.web.app.migrate_source_labels"),
            patch("src.web.app.migrate_source_config_plugins"),
            booted_web_app(storage, config) as app,
        ):
            yield authenticated_client(app)

    def test_a_created_path_outside_the_allowed_roots_is_refused_with_the_reason(
        self, client: TestClient, storage: StorageManager
    ) -> None:
        response = client.post(
            "/api/sync/sources",
            json={
                "id": "leaky",
                "plugin": "csv_import",
                "values": {"path": "/etc/passwd", "content_type": "book"},
            },
        )

        assert response.status_code == 400
        assert "outside the allowed source roots" in response.json()["detail"]
        assert storage.get_source_config(1, "leaky") is None

    def test_an_updated_path_outside_the_allowed_roots_is_refused_with_the_reason(
        self, client: TestClient, storage: StorageManager, tmp_path: Path
    ) -> None:
        readable = tmp_path / "books.csv"
        readable.write_text("title\n")
        client.post(
            "/api/sync/sources",
            json={
                "id": "books",
                "plugin": "csv_import",
                "values": {"path": str(readable), "content_type": "book"},
            },
        )

        response = client.put(
            "/api/sync/sources/books/config",
            json={"values": {"path": "/etc/passwd"}},
        )

        assert response.status_code == 400
        assert "outside the allowed source roots" in response.json()["detail"]
        row = storage.get_source_config(1, "books")
        assert row is not None
        assert row["config"]["path"] == str(readable)

    def test_a_missing_file_is_refused_without_saying_it_is_missing(
        self, client: TestClient, storage: StorageManager, tmp_path: Path
    ) -> None:
        """The oracle left open after the sync door closed.

        Inside the allowed roots, only a stat separates this path from a real
        one, so the plugin's "not found" makes the endpoint a disk probe.
        """
        missing = tmp_path / "no-such-library.csv"

        response = client.post(
            "/api/sync/sources",
            json={
                "id": "ghost",
                "plugin": "csv_import",
                "values": {"path": str(missing), "content_type": "book"},
            },
        )

        assert response.status_code == 400
        detail = response.json()["detail"]
        assert "'path'" in detail
        assert "not found" not in detail.lower()
        assert str(missing) not in detail
        assert missing.name not in detail
        assert storage.get_source_config(1, "ghost") is None

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
        """A create writes every field, so blaming all of them says nothing."""
        readable = tmp_path / "books.csv"
        readable.write_text("title\n")

        response = client.post(
            "/api/sync/sources",
            json={
                "id": "mixed",
                "plugin": "csv_import",
                "values": {"path": str(readable), "content_type": "not_a_type"},
            },
        )

        assert response.status_code == 400
        detail = response.json()["detail"]
        assert "'content_type'" in detail
        assert "'path'" not in detail

    def test_an_edit_naming_a_missing_file_names_the_field_too(
        self, client: TestClient, storage: StorageManager, tmp_path: Path
    ) -> None:
        """The second write door, which the create cases cannot reach.

        ``PUT .../config`` is the one a migrated source is edited through, so
        an oracle left open here is the same probe on a different verb.
        """
        readable = tmp_path / "books.csv"
        readable.write_text("title\n")
        client.post(
            "/api/sync/sources",
            json={
                "id": "books",
                "plugin": "csv_import",
                "values": {"path": str(readable), "content_type": "book"},
            },
        )
        missing = tmp_path / "no-such-library.csv"

        response = client.put(
            "/api/sync/sources/books/config",
            json={"values": {"path": str(missing)}},
        )

        assert response.status_code == 400
        detail = response.json()["detail"]
        assert "'path'" in detail
        assert "not found" not in detail.lower()
        assert str(missing) not in detail
        assert missing.name not in detail
        row = storage.get_source_config(1, "books")
        assert row is not None
        assert row["config"]["path"] == str(readable)

    def test_two_jointly_bad_values_name_both_rather_than_neither(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        """Reverting either one alone still leaves the write refused.

        Nothing is individually to blame, so the fallback answers with the
        whole write — silence here would refuse without saying anything.
        """
        missing = tmp_path / "no-such-library.csv"

        response = client.post(
            "/api/sync/sources",
            json={
                "id": "doubly",
                "plugin": "csv_import",
                "values": {"path": str(missing), "content_type": "not_a_type"},
            },
        )

        assert response.status_code == 400
        detail = response.json()["detail"]
        assert "'path'" in detail
        assert "'content_type'" in detail
        assert str(missing) not in detail
        assert "not_a_type" not in detail

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
        assert storage.get_source_config(1, "roms") is None

    def test_a_list_mixing_a_non_string_is_still_contained_entry_by_entry(
        self, client: TestClient, storage: StorageManager
    ) -> None:
        """The guard speaks first, so it cannot lean on the plugin's typing.

        ``validate_config`` would reject the ``123`` afterwards and answer
        with a field name — the wrong refusal for an escaping path.
        """
        response = client.post(
            "/api/sync/sources",
            json={"id": "roms", "plugin": "roms", "values": {"paths": [123, "/etc"]}},
        )

        assert response.status_code == 400
        assert "outside the allowed source roots" in response.json()["detail"]
        assert storage.get_source_config(1, "roms") is None

    def test_the_operator_reads_the_plugins_reason_in_the_log(
        self, client: TestClient, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Refusing with the reason nowhere at all would be unusable."""
        missing = tmp_path / "no-such-library.csv"

        with caplog.at_level(logging.WARNING, logger="src.web.sync_sources"):
            client.post(
                "/api/sync/sources",
                json={
                    "id": "ghost",
                    "plugin": "csv_import",
                    "values": {"path": str(missing), "content_type": "book"},
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
        with (
            patch("src.web.app.migrate_source_labels"),
            patch("src.web.app.migrate_source_config_plugins"),
            booted_web_app(storage, config) as app,
        ):
            yield authenticated_client(app)

    def test_a_missing_path_is_named_in_the_log_and_not_on_the_wire(
        self, client: TestClient, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The write was valid, so no edit will ever refuse this one.

        Containment runs before existence in all six file plugins, so a
        surviving "not found" names a path inside the allowed roots.
        """
        readable = tmp_path / "books.csv"
        readable.write_text("title\n")
        created = client.post(
            "/api/sync/sources",
            json={
                "id": "books",
                "plugin": "csv_import",
                "values": {"path": str(readable), "content_type": "book"},
            },
        )
        readable.unlink()

        with caplog.at_level(logging.WARNING, logger="src.web.api"):
            sync = client.post("/api/update", json={"source": "books"})

        assert created.status_code == 201
        assert sync.status_code == 400
        assert sync.json()["detail"] == SOURCE_MISCONFIGURED_DETAIL
        assert str(readable) not in sync.text
        assert "not found" not in sync.text
        assert "books" in caplog.text
        assert f"CSV file not found: {readable}" in caplog.text

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

        with patch("src.web.api.execute_multi_source_sync", fake_execute):
            sync = client.post("/api/update", json={"source": "games"})
            assert done.wait(timeout=5)

        assert sync.status_code == 200
        assert sync.json()["sources"] == ["games"]

    _MULTILINE_SECRET = "-----KEY-----\nabc\n-----END-----"

    def test_a_multiline_secret_is_redacted_before_the_line_is_escaped(
        self, client: TestClient, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The reverse order would rewrite the secret out of its own match."""
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
            json={"value": self._MULTILINE_SECRET},
        )
        assert (created.status_code, stored.status_code) == (201, 204)

        with (
            patch(
                "src.ingestion.sources.steam.SteamPlugin.validate_config",
                return_value=[f"'api_key' {self._MULTILINE_SECRET} was rejected"],
            ),
            caplog.at_level(logging.WARNING, logger="src.web.api"),
        ):
            sync = client.post("/api/update", json={"source": "games"})

        assert sync.status_code == 400
        assert "-----KEY-----" not in caplog.text
        assert "'api_key' [redacted] was rejected" in caplog.text

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
        assert storage.get_source_config(1, "my_games") is not None
        assert storage.get_credential(1, "my_games", "api_key") == "yaml_api_key"

        response = client.delete("/api/sync/sources/my_games")
        assert response.status_code == 204

        assert storage.get_source_config(1, "my_games") is None
        assert storage.get_credential(1, "my_games", "api_key") is None

    def test_returns_404_when_not_migrated(self, client: TestClient) -> None:
        response = client.delete("/api/sync/sources/my_books")
        assert response.status_code == 404

    def test_returns_400_for_invalid_source_id(self, client: TestClient) -> None:
        """Malformed path id is rejected before any DB lookup."""
        response = client.delete("/api/sync/sources/Bad-ID")
        assert response.status_code == 400

    def test_accepts_hyphenated_source_id(
        self, client: TestClient, storage: StorageManager
    ) -> None:
        """A hyphenated id passes the format gate and deletes its DB row."""
        storage.upsert_source_config(
            1, "calibre-web", "fake_file", {"path": "/x"}, enabled=True
        )
        response = client.delete("/api/sync/sources/calibre-web")
        assert response.status_code == 204
        assert storage.get_source_config(1, "calibre-web") is None

    def test_drops_row_even_when_plugin_no_longer_registered(
        self, client: TestClient, storage: StorageManager
    ) -> None:
        """Plugin renamed/removed post-migration: row still goes away.

        Credentials may linger (we can't introspect the schema without a
        plugin), but the source_configs row is removed regardless.
        """
        storage.upsert_source_config(
            1, "ghost", "this_plugin_was_removed", {"x": 1}, enabled=True
        )
        response = client.delete("/api/sync/sources/ghost")
        assert response.status_code == 204
        assert storage.get_source_config(1, "ghost") is None

    def test_the_last_source_on_a_plugin_takes_the_stranded_row_with_it(
        self,
        client: TestClient,
        storage: StorageManager,
        base_config: dict[str, Any],
    ) -> None:
        """Drop the config the route hands down and the sweep never runs."""
        base_config["inputs"].pop("my_games")
        storage.upsert_source_config(1, "work_games", "fake_api", {}, enabled=True)
        storage.save_credential(1, "fake_api", "api_key", "stranded-by-an-upgrade")

        response = client.delete("/api/sync/sources/work_games")

        assert response.status_code == 204
        assert storage.get_credential(1, "fake_api", "api_key") is None

    def test_a_yaml_sibling_on_the_plugin_keeps_the_stranded_row(
        self, client: TestClient, storage: StorageManager
    ) -> None:
        """``my_games`` stays in the config, and it alone may read that row."""
        storage.upsert_source_config(1, "work_games", "fake_api", {}, enabled=True)
        storage.save_credential(1, "fake_api", "api_key", "stranded-by-an-upgrade")

        response = client.delete("/api/sync/sources/work_games")

        assert response.status_code == 204
        assert storage.get_credential(1, "fake_api", "api_key") == (
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
        with patch("src.web.api.execute_multi_source_sync", captured["fn"]):
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
        storage.upsert_source_config(
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


class TestSourceCredentialExfiltrationRegression:
    """Regression: one PUT repointed a source and the next sync sent its secret.

    Bug: ``url`` and ``verify_ssl`` were freely writable. Fix: both are
    ``credential_bound``, so the write clears the stored password first.
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
        with (
            patch("src.web.app.migrate_source_labels"),
            patch("src.web.app.migrate_source_config_plugins"),
            booted_web_app(storage, config, engine=engine) as app,
        ):
            yield authenticated_client(app)

    def test_the_reported_sequence_leaves_the_password_behind(
        self,
        real_plugin_client: TestClient,
        storage: StorageManager,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        client = real_plugin_client
        create = client.post(
            "/api/sync/sources",
            json={
                "id": "calibre",
                "plugin": "calibre_web",
                "values": {
                    "url": "http://localhost:8083",
                    "username": "reader",
                    "verify_ssl": True,
                },
                "enabled": True,
            },
        )
        assert create.status_code == 201
        stored = client.put(
            "/api/sync/sources/calibre/secret/password", json={"value": "hunter2"}
        )
        assert stored.status_code == 204
        assert storage.get_credential(1, "calibre", "password") == "hunter2"

        repointed = client.put(
            "/api/sync/sources/calibre/config",
            json={"values": {"url": "https://attacker.example", "verify_ssl": False}},
        )

        assert repointed.status_code == 200
        body = repointed.json()
        assert body["secret_status"]["password"] is False
        assert body["field_values"]["url"] == "https://attacker.example"
        assert storage.get_credential(1, "calibre", "password") is None

        with patch("requests.get") as requested, caplog.at_level(logging.WARNING):
            sync = client.post("/api/update", json={"source": "calibre"})

        assert sync.status_code == 400
        assert sync.json()["detail"] == (
            "Source is not properly configured — check its 'password' setting."
        )
        assert "'password' is required" in caplog.text
        requested.assert_not_called()
