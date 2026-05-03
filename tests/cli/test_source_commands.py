"""Tests for the CLI ``source`` group.

Mirrors the per-source web endpoints — JSON output shapes must match the
Pydantic responses in ``src/web/api.py`` exactly so the two interfaces
stay in lockstep.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from src.ingestion.plugin_base import ConfigField, SourcePlugin
from src.ingestion.registry import PluginRegistry
from src.models.content import ConsumptionStatus, ContentItem, ContentType
from src.storage.manager import StorageManager
from tests.cli.conftest import _invoke_with_mocks


class FakeFilePlugin(SourcePlugin):
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
            ConfigField(name="path", field_type=str, required=True),
            ConfigField(
                name="content_type",
                field_type=str,
                required=False,
                default="book",
            ),
        ]

    def validate_config(self, config: dict[str, Any], **kwargs: Any) -> list[str]:
        return [] if config.get("path") else ["'path' is required"]

    def fetch(self, config: dict[str, Any]) -> Iterator[ContentItem]:
        yield ContentItem(
            id="x",
            title="Stub",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
            source=self.get_source_identifier(config),
        )


class FakeApiPlugin(SourcePlugin):
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
            ConfigField(name="api_key", field_type=str, required=True, sensitive=True),
            ConfigField(name="user_id", field_type=str, required=False),
            ConfigField(
                name="min_minutes",
                field_type=int,
                required=False,
                default=0,
            ),
            ConfigField(
                name="tags",
                field_type=list,
                required=False,
                default=[],
            ),
            ConfigField(
                name="active",
                field_type=bool,
                required=False,
                default=False,
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
    registry = PluginRegistry.get_instance()
    registry._discovered = True
    registry._plugins.clear()
    registry.register(FakeFilePlugin())
    registry.register(FakeApiPlugin())
    yield
    PluginRegistry.reset_instance()


@pytest.fixture()
def storage(tmp_path: Path) -> StorageManager:
    return StorageManager(sqlite_path=tmp_path / "cli.db")


@pytest.fixture()
def base_config() -> dict[str, Any]:
    return {
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
                "api_key": "yaml_key",
                "user_id": "yaml_user",
                "min_minutes": 30,
                "tags": ["rpg", "indie"],
                "active": True,
            },
        }
    }


@pytest.mark.usefixtures("_registry_with_fakes")
class TestSourceList:
    def test_list_table_format_contains_source_ids(
        self,
        cli_runner: CliRunner,
        storage: StorageManager,
        base_config: dict[str, Any],
    ) -> None:
        result = _invoke_with_mocks(
            cli_runner,
            ["source", "list"],
            mock_storage=storage,
            config=base_config,
        )
        assert result.exit_code == 0
        assert "my_books" in result.output
        assert "my_games" in result.output

    def test_list_json_matches_api_shape(
        self,
        cli_runner: CliRunner,
        storage: StorageManager,
        base_config: dict[str, Any],
    ) -> None:
        result = _invoke_with_mocks(
            cli_runner,
            ["source", "list", "--format", "json"],
            mock_storage=storage,
            config=base_config,
        )
        assert result.exit_code == 0
        payload = json.loads(result.output)
        assert isinstance(payload, list)
        assert {"id", "display_name", "plugin_display_name"}.issubset(payload[0].keys())

    def test_list_json_returns_empty_array_for_empty_config(
        self, cli_runner: CliRunner, storage: StorageManager
    ) -> None:
        result = _invoke_with_mocks(
            cli_runner,
            ["source", "list", "--format", "json"],
            mock_storage=storage,
            config={"inputs": {}},
        )
        assert result.exit_code == 0
        assert json.loads(result.output) == []


@pytest.mark.usefixtures("_registry_with_fakes")
class TestSourceShow:
    def test_show_json_matches_api_response(
        self,
        cli_runner: CliRunner,
        storage: StorageManager,
        base_config: dict[str, Any],
    ) -> None:
        result = _invoke_with_mocks(
            cli_runner,
            ["source", "show", "my_games", "--format", "json"],
            mock_storage=storage,
            config=base_config,
        )
        assert result.exit_code == 0
        body = json.loads(result.output)
        assert body["source_id"] == "my_games"
        assert body["plugin"] == "fake_api"
        assert body["migrated"] is False
        assert body["enabled"] is True
        assert body["secret_status"] == {"api_key": True}
        assert "api_key" not in body["field_values"]
        assert body["field_values"]["user_id"] == "yaml_user"
        assert body["field_values"]["tags"] == ["rpg", "indie"]

    def test_show_unknown_returns_nonzero(
        self,
        cli_runner: CliRunner,
        storage: StorageManager,
        base_config: dict[str, Any],
    ) -> None:
        result = _invoke_with_mocks(
            cli_runner,
            ["source", "show", "nope"],
            mock_storage=storage,
            config=base_config,
        )
        assert result.exit_code != 0


@pytest.mark.usefixtures("_registry_with_fakes")
class TestSourceSchema:
    def test_schema_json_matches_api_response(
        self,
        cli_runner: CliRunner,
        storage: StorageManager,
        base_config: dict[str, Any],
    ) -> None:
        result = _invoke_with_mocks(
            cli_runner,
            ["source", "schema", "my_games", "--format", "json"],
            mock_storage=storage,
            config=base_config,
        )
        assert result.exit_code == 0
        body = json.loads(result.output)
        types = {f["name"]: f["field_type"] for f in body["fields"]}
        assert types == {
            "api_key": "str",
            "user_id": "str",
            "min_minutes": "int",
            "tags": "list",
            "active": "bool",
        }
        assert {f["name"]: f["sensitive"] for f in body["fields"]}["api_key"] is True


@pytest.mark.usefixtures("_registry_with_fakes")
class TestSourceMigrate:
    def test_migrate_moves_yaml_into_db(
        self,
        cli_runner: CliRunner,
        storage: StorageManager,
        base_config: dict[str, Any],
    ) -> None:
        result = _invoke_with_mocks(
            cli_runner,
            ["source", "migrate", "my_games"],
            mock_storage=storage,
            config=base_config,
        )
        assert result.exit_code == 0
        row = storage.get_source_config(1, "my_games")
        assert row is not None
        assert row["plugin"] == "fake_api"
        assert row["config"]["user_id"] == "yaml_user"
        assert "api_key" not in row["config"]
        assert storage.get_credential(1, "my_games", "api_key") == "yaml_key"

    def test_migrate_is_idempotent(
        self,
        cli_runner: CliRunner,
        storage: StorageManager,
        base_config: dict[str, Any],
    ) -> None:
        """Calling migrate twice on the same source is a no-op the second time."""
        first = _invoke_with_mocks(
            cli_runner,
            ["source", "migrate", "my_books"],
            mock_storage=storage,
            config=base_config,
        )
        second = _invoke_with_mocks(
            cli_runner,
            ["source", "migrate", "my_books"],
            mock_storage=storage,
            config=base_config,
        )
        assert first.exit_code == 0
        assert second.exit_code == 0
        rows = storage.list_source_configs(1)
        assert len([r for r in rows if r["source_id"] == "my_books"]) == 1

    def test_migrate_json_format_matches_api_response(
        self,
        cli_runner: CliRunner,
        storage: StorageManager,
        base_config: dict[str, Any],
    ) -> None:
        result = _invoke_with_mocks(
            cli_runner,
            ["source", "migrate", "my_games", "--format", "json"],
            mock_storage=storage,
            config=base_config,
        )
        assert result.exit_code == 0
        body = json.loads(result.output)
        assert body["source_id"] == "my_games"
        assert "fields_migrated" in body
        assert "secrets_migrated" in body
        assert body["secrets_migrated"] == ["api_key"]


@pytest.mark.usefixtures("_registry_with_fakes")
class TestSourceEnableDisable:
    def test_disable_after_migrate_flips_flag(
        self,
        cli_runner: CliRunner,
        storage: StorageManager,
        base_config: dict[str, Any],
    ) -> None:
        storage.upsert_source_config(
            1, "my_books", "fake_file", {"path": "/x"}, enabled=True
        )
        result = _invoke_with_mocks(
            cli_runner,
            ["source", "disable", "my_books"],
            mock_storage=storage,
            config=base_config,
        )
        assert result.exit_code == 0
        row = storage.get_source_config(1, "my_books")
        assert row is not None and row["enabled"] is False

    def test_enable_when_not_migrated_returns_error(
        self,
        cli_runner: CliRunner,
        storage: StorageManager,
        base_config: dict[str, Any],
    ) -> None:
        result = _invoke_with_mocks(
            cli_runner,
            ["source", "enable", "my_books"],
            mock_storage=storage,
            config=base_config,
        )
        assert result.exit_code != 0

    def test_disable_when_not_migrated_returns_error(
        self,
        cli_runner: CliRunner,
        storage: StorageManager,
        base_config: dict[str, Any],
    ) -> None:
        """Disabling a not-yet-migrated source aborts (no DB row to flip)."""
        result = _invoke_with_mocks(
            cli_runner,
            ["source", "disable", "my_books"],
            mock_storage=storage,
            config=base_config,
        )
        assert result.exit_code != 0

    def test_enable_re_enables_disabled_source(
        self,
        cli_runner: CliRunner,
        storage: StorageManager,
        base_config: dict[str, Any],
    ) -> None:
        """Enabling a previously-disabled migrated source flips the flag back."""
        storage.upsert_source_config(
            1, "my_books", "fake_file", {"path": "/x"}, enabled=False
        )
        result = _invoke_with_mocks(
            cli_runner,
            ["source", "enable", "my_books"],
            mock_storage=storage,
            config=base_config,
        )
        assert result.exit_code == 0
        row = storage.get_source_config(1, "my_books")
        assert row is not None and row["enabled"] is True


@pytest.mark.usefixtures("_registry_with_fakes")
class TestSourceSet:
    def test_set_coerces_list_value_from_comma_separated_string(
        self,
        cli_runner: CliRunner,
        storage: StorageManager,
        base_config: dict[str, Any],
    ) -> None:
        """List fields accept ``"a,b,c"`` and store ``["a", "b", "c"]``."""
        storage.upsert_source_config(1, "my_games", "fake_api", {}, enabled=True)
        result = _invoke_with_mocks(
            cli_runner,
            ["source", "set", "my_games", "tags", "rpg, indie ,strategy"],
            mock_storage=storage,
            config=base_config,
        )
        assert result.exit_code == 0
        row = storage.get_source_config(1, "my_games")
        assert row is not None
        assert row["config"]["tags"] == ["rpg", "indie", "strategy"]

    def test_set_coerces_bool_truthy_keyword(
        self,
        cli_runner: CliRunner,
        storage: StorageManager,
        base_config: dict[str, Any],
    ) -> None:
        """``"yes"`` / ``"on"`` / ``"true"`` all coerce to ``True``."""
        storage.upsert_source_config(1, "my_games", "fake_api", {}, enabled=True)
        result = _invoke_with_mocks(
            cli_runner,
            ["source", "set", "my_games", "active", "yes"],
            mock_storage=storage,
            config=base_config,
        )
        assert result.exit_code == 0
        row = storage.get_source_config(1, "my_games")
        assert row is not None and row["config"]["active"] is True

    def test_set_updates_non_sensitive_field(
        self,
        cli_runner: CliRunner,
        storage: StorageManager,
        base_config: dict[str, Any],
    ) -> None:
        storage.upsert_source_config(
            1, "my_games", "fake_api", {"min_minutes": 30}, enabled=True
        )
        result = _invoke_with_mocks(
            cli_runner,
            ["source", "set", "my_games", "min_minutes", "60"],
            mock_storage=storage,
            config=base_config,
        )
        assert result.exit_code == 0
        row = storage.get_source_config(1, "my_games")
        assert row is not None and row["config"]["min_minutes"] == 60

    def test_set_rejects_sensitive_field(
        self,
        cli_runner: CliRunner,
        storage: StorageManager,
        base_config: dict[str, Any],
    ) -> None:
        storage.upsert_source_config(1, "my_games", "fake_api", {}, enabled=True)
        result = _invoke_with_mocks(
            cli_runner,
            ["source", "set", "my_games", "api_key", "leaked"],
            mock_storage=storage,
            config=base_config,
        )
        assert result.exit_code != 0
        assert storage.get_credential(1, "my_games", "api_key") is None

    def test_set_rejects_invalid_int_value(
        self,
        cli_runner: CliRunner,
        storage: StorageManager,
        base_config: dict[str, Any],
    ) -> None:
        """Non-numeric value for an int field aborts before persisting anything."""
        storage.upsert_source_config(1, "my_games", "fake_api", {}, enabled=True)
        result = _invoke_with_mocks(
            cli_runner,
            ["source", "set", "my_games", "min_minutes", "not_a_number"],
            mock_storage=storage,
            config=base_config,
        )
        assert result.exit_code != 0
        row = storage.get_source_config(1, "my_games")
        assert row is not None and "min_minutes" not in row["config"]

    def test_set_rejects_invalid_bool_value(
        self,
        cli_runner: CliRunner,
        storage: StorageManager,
        base_config: dict[str, Any],
    ) -> None:
        """Bool fields refuse anything outside the truthy/falsy keyword set."""
        storage.upsert_source_config(1, "my_games", "fake_api", {}, enabled=True)
        result = _invoke_with_mocks(
            cli_runner,
            ["source", "set", "my_games", "active", "maybe"],
            mock_storage=storage,
            config=base_config,
        )
        assert result.exit_code != 0


@pytest.mark.usefixtures("_registry_with_fakes")
class TestSourceSecrets:
    def test_set_secret_stores_via_hidden_prompt(
        self,
        cli_runner: CliRunner,
        storage: StorageManager,
        base_config: dict[str, Any],
    ) -> None:
        storage.upsert_source_config(1, "my_games", "fake_api", {}, enabled=True)
        result = _invoke_with_mocks(
            cli_runner,
            ["source", "set-secret", "my_games", "api_key"],
            mock_storage=storage,
            config=base_config,
            input_text="rotated_value\n",
        )
        assert result.exit_code == 0
        assert storage.get_credential(1, "my_games", "api_key") == "rotated_value"

    def test_set_secret_reads_value_from_env_var_non_interactively(
        self,
        cli_runner: CliRunner,
        storage: StorageManager,
        base_config: dict[str, Any],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """``RECOMMENDINATOR_SECRET_VALUE`` is the supported scripting path.

        It must skip the hidden prompt entirely so headless pipelines never
        hang on stdin, and it must store exactly the env-var value.
        """
        storage.upsert_source_config(1, "my_games", "fake_api", {}, enabled=True)
        monkeypatch.setenv("RECOMMENDINATOR_SECRET_VALUE", "env_secret")
        result = _invoke_with_mocks(
            cli_runner,
            ["source", "set-secret", "my_games", "api_key"],
            mock_storage=storage,
            config=base_config,
        )
        assert result.exit_code == 0
        assert storage.get_credential(1, "my_games", "api_key") == "env_secret"

    def test_clear_secret_removes_credential(
        self,
        cli_runner: CliRunner,
        storage: StorageManager,
        base_config: dict[str, Any],
    ) -> None:
        storage.upsert_source_config(1, "my_games", "fake_api", {}, enabled=True)
        storage.save_credential(1, "my_games", "api_key", "to_be_cleared")
        result = _invoke_with_mocks(
            cli_runner,
            ["source", "clear-secret", "my_games", "api_key"],
            mock_storage=storage,
            config=base_config,
        )
        assert result.exit_code == 0
        assert storage.get_credential(1, "my_games", "api_key") is None

    def test_clear_secret_rejects_non_sensitive_field(
        self,
        cli_runner: CliRunner,
        storage: StorageManager,
        base_config: dict[str, Any],
    ) -> None:
        """Symmetric with `set-secret`: refuse to clear a non-sensitive field."""
        storage.upsert_source_config(1, "my_games", "fake_api", {}, enabled=True)
        result = _invoke_with_mocks(
            cli_runner,
            ["source", "clear-secret", "my_games", "user_id"],
            mock_storage=storage,
            config=base_config,
        )
        assert result.exit_code != 0

    def test_set_secret_rejects_non_sensitive_field(
        self,
        cli_runner: CliRunner,
        storage: StorageManager,
        base_config: dict[str, Any],
    ) -> None:
        storage.upsert_source_config(1, "my_games", "fake_api", {}, enabled=True)
        result = _invoke_with_mocks(
            cli_runner,
            ["source", "set-secret", "my_games", "user_id"],
            mock_storage=storage,
            config=base_config,
            input_text="x\n",
        )
        assert result.exit_code != 0
