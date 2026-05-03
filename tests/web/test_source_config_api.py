"""Tests for the per-source configuration API endpoints.

These endpoints back the data-source accordions in the web UI: schema
introspection, current values (with secrets stripped), one-shot
migration of a YAML entry into the database, and incremental updates of
non-sensitive fields, secrets, and the enabled flag.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import fields
from pathlib import Path
from typing import Any
from unittest.mock import Mock, patch

import pytest
from fastapi.testclient import TestClient

from src.ingestion.plugin_base import ConfigField, SourcePlugin
from src.ingestion.registry import PluginRegistry
from src.llm.client import OllamaClient
from src.llm.embeddings import EmbeddingGenerator
from src.llm.recommendations import RecommendationGenerator
from src.models.content import ConsumptionStatus, ContentItem, ContentType
from src.recommendations.engine import RecommendationEngine
from src.storage.manager import StorageManager
from src.web.app import create_app
from src.web.state import AppState, app_state


class FakeFilePlugin(SourcePlugin):
    """File-based fake plugin: one path field, no secrets."""

    @property
    def name(self) -> str:
        return "fake_file"

    @property
    def display_name(self) -> str:
        return "Fake File"

    @property
    def content_types(self) -> list[ContentType]:
        return [ContentType.BOOK]

    @property
    def requires_api_key(self) -> bool:
        return False

    def get_config_schema(self) -> list[ConfigField]:
        return [
            ConfigField(
                name="path",
                field_type=str,
                required=True,
                description="Path to data file",
            ),
            ConfigField(
                name="content_type",
                field_type=str,
                required=False,
                default="book",
                description="Content type for items",
            ),
        ]

    def validate_config(self, config: dict[str, Any], **kwargs: Any) -> list[str]:
        if not config.get("path"):
            return ["'path' is required"]
        return []

    def fetch(self, config: dict[str, Any]) -> Iterator[ContentItem]:
        yield ContentItem(
            id="x",
            title="Stub",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
            source=self.get_source_identifier(config),
        )


class FakeApiPlugin(SourcePlugin):
    """API-based fake plugin: api_key (sensitive) + non-sensitive fields."""

    @property
    def name(self) -> str:
        return "fake_api"

    @property
    def display_name(self) -> str:
        return "Fake API"

    @property
    def content_types(self) -> list[ContentType]:
        return [ContentType.VIDEO_GAME]

    @property
    def requires_api_key(self) -> bool:
        return True

    def get_config_schema(self) -> list[ConfigField]:
        return [
            ConfigField(
                name="api_key",
                field_type=str,
                required=True,
                sensitive=True,
                description="API key",
            ),
            ConfigField(
                name="user_id",
                field_type=str,
                required=False,
                default="",
                description="User identifier",
            ),
            ConfigField(
                name="min_minutes",
                field_type=int,
                required=False,
                default=0,
                description="Minimum minutes",
            ),
            ConfigField(
                name="include_categories",
                field_type=list,
                required=False,
                default=[],
                description="Category filters",
            ),
            ConfigField(
                name="enabled_filter",
                field_type=bool,
                required=False,
                default=False,
                description="Toggle",
            ),
        ]

    def validate_config(self, config: dict[str, Any], **kwargs: Any) -> list[str]:
        return []

    def fetch(self, config: dict[str, Any]) -> Iterator[ContentItem]:
        yield ContentItem(
            id="g",
            title="Stub",
            content_type=ContentType.VIDEO_GAME,
            status=ConsumptionStatus.UNREAD,
            source=self.get_source_identifier(config),
        )


@pytest.fixture()
def _registry_with_fakes() -> Iterator[None]:
    """Replace the plugin registry with two fake plugins for the test."""
    registry = PluginRegistry.get_instance()
    registry._discovered = True
    registry._plugins.clear()
    registry.register(FakeFilePlugin())
    registry.register(FakeApiPlugin())
    yield
    PluginRegistry.reset_instance()


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
                "include_categories": ["rpg", "indie"],
                "enabled_filter": True,
            },
        },
    }


@pytest.fixture()
def client(
    _registry_with_fakes: None,
    storage: StorageManager,
    base_config: dict[str, Any],
) -> Iterator[TestClient]:
    """TestClient with a real StorageManager and an in-memory test config.

    Patches ``create_app`` boot dependencies so the suite never touches the
    real config file or LLM stack — only ``app_state.config`` (mutated below)
    drives behaviour.
    """
    fresh = AppState()
    for f in fields(fresh):
        setattr(app_state, f.name, getattr(fresh, f.name))

    with (
        patch("src.web.app.load_config", return_value=base_config),
        patch("src.web.app.create_storage_manager", return_value=storage),
        patch("src.web.app.create_llm_components") as mock_llm,
        patch("src.web.app.create_recommendation_engine") as mock_engine,
        patch("src.web.app.migrate_config_credentials"),
    ):
        mock_llm.return_value = (
            Mock(spec=OllamaClient),
            Mock(spec=EmbeddingGenerator),
            Mock(spec=RecommendationGenerator),
        )
        engine_instance = Mock(spec=RecommendationEngine)
        engine_instance.storage = storage
        mock_engine.return_value = engine_instance

        app = create_app()
        # ``create_app`` resets ``app_state.config`` to the patched dict; tests
        # may further mutate it (e.g. assert YAML purge after migrate).
        app_state.config = base_config
        app_state.storage = storage
        yield TestClient(app)

    fresh = AppState()
    for f in fields(fresh):
        setattr(app_state, f.name, getattr(fresh, f.name))


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
            "include_categories": "list",
            "enabled_filter": "bool",
        }

    def test_marks_sensitive_fields(self, client: TestClient) -> None:
        response = client.get("/api/sync/sources/my_games/schema")
        sensitive = {f["name"]: f["sensitive"] for f in response.json()["fields"]}
        assert sensitive == {
            "api_key": True,
            "user_id": False,
            "min_minutes": False,
            "include_categories": False,
            "enabled_filter": False,
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
        assert body["field_values"]["include_categories"] == ["rpg", "indie"]
        assert body["field_values"]["enabled_filter"] is True

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
            "include_categories",
            "enabled_filter",
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
                    "include_categories": ["rpg"],
                    "enabled_filter": False,
                }
            },
        )
        assert response.status_code == 200
        row = storage.get_source_config(1, "my_games")
        assert row is not None
        assert row["config"]["user_id"] == "new_user"
        assert row["config"]["min_minutes"] == 60
        assert row["config"]["include_categories"] == ["rpg"]
        assert row["config"]["enabled_filter"] is False

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
        """Symmetric round-trip: disable then re-enable a migrated source."""
        client.post("/api/sync/sources/my_books/migrate")
        client.put("/api/sync/sources/my_books/enabled", json={"enabled": False})
        response = client.put(
            "/api/sync/sources/my_books/enabled", json={"enabled": True}
        )
        assert response.status_code == 200
        row = storage.get_source_config(1, "my_books")
        assert row is not None
        assert row["enabled"] is True
