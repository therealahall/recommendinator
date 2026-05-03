"""Tests for the CLI ``source`` group.

Mirrors the per-source web endpoints — JSON output shapes must match the
Pydantic responses in ``src/web/api.py`` exactly so the two interfaces
stay in lockstep.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from src.storage.manager import StorageManager
from tests.cli.conftest import _invoke_with_mocks


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


@pytest.mark.usefixtures("registry_with_source_fakes")
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
        # Exact key match (not subset) so a CLI/web drift adding a key on
        # one side without the other is caught immediately.
        assert set(payload[0].keys()) == {
            "id",
            "display_name",
            "plugin_display_name",
            "enabled",
        }

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


@pytest.mark.usefixtures("registry_with_source_fakes")
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


@pytest.mark.usefixtures("registry_with_source_fakes")
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


@pytest.mark.usefixtures("registry_with_source_fakes")
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
        # Exact set match of fields_migrated mirrors the web counterpart so
        # an empty / drift-shaped response is caught by both surfaces.
        assert set(body["fields_migrated"]) == {
            "user_id",
            "min_minutes",
            "tags",
            "active",
        }
        assert body["secrets_migrated"] == ["api_key"]


@pytest.mark.usefixtures("registry_with_source_fakes")
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
        """Enabling a not-yet-migrated source aborts (no DB row to flip)."""
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


@pytest.mark.usefixtures("registry_with_source_fakes")
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

    def test_set_coerces_bool_falsy_keyword(
        self,
        cli_runner: CliRunner,
        storage: StorageManager,
        base_config: dict[str, Any],
    ) -> None:
        """``"no"`` / ``"off"`` / ``"false"`` all coerce to ``False``."""
        storage.upsert_source_config(
            1, "my_games", "fake_api", {"active": True}, enabled=True
        )
        result = _invoke_with_mocks(
            cli_runner,
            ["source", "set", "my_games", "active", "no"],
            mock_storage=storage,
            config=base_config,
        )
        assert result.exit_code == 0
        row = storage.get_source_config(1, "my_games")
        assert row is not None and row["config"]["active"] is False

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

    def test_set_when_not_migrated_returns_error(
        self,
        cli_runner: CliRunner,
        storage: StorageManager,
        base_config: dict[str, Any],
    ) -> None:
        """``source set`` on a YAML-only source aborts (no DB row to update)."""
        result = _invoke_with_mocks(
            cli_runner,
            ["source", "set", "my_games", "min_minutes", "5"],
            mock_storage=storage,
            config=base_config,
        )
        assert result.exit_code != 0
        assert storage.get_source_config(1, "my_games") is None

    def test_set_rejects_unknown_field(
        self,
        cli_runner: CliRunner,
        storage: StorageManager,
        base_config: dict[str, Any],
    ) -> None:
        """A field not in the plugin schema aborts before any DB write."""
        storage.upsert_source_config(1, "my_games", "fake_api", {}, enabled=True)
        result = _invoke_with_mocks(
            cli_runner,
            ["source", "set", "my_games", "no_such_field", "x"],
            mock_storage=storage,
            config=base_config,
        )
        assert result.exit_code != 0
        row = storage.get_source_config(1, "my_games")
        assert row is not None
        assert "no_such_field" not in row["config"]

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


@pytest.mark.usefixtures("registry_with_source_fakes")
class TestSourceApply:
    """Bulk-update parity with web ``PUT /api/sync/sources/<id>/config``."""

    def test_apply_updates_multiple_fields_atomically_from_stdin(
        self,
        cli_runner: CliRunner,
        storage: StorageManager,
        base_config: dict[str, Any],
    ) -> None:
        storage.upsert_source_config(1, "my_games", "fake_api", {}, enabled=True)
        payload = json.dumps(
            {"user_id": "new", "min_minutes": 90, "tags": ["rpg"], "active": False}
        )
        result = _invoke_with_mocks(
            cli_runner,
            ["source", "apply", "my_games", "--from-json", "-"],
            mock_storage=storage,
            config=base_config,
            input_text=payload,
        )
        assert result.exit_code == 0
        assert "Applied" in result.output
        row = storage.get_source_config(1, "my_games")
        assert row is not None
        assert row["config"] == {
            "user_id": "new",
            "min_minutes": 90,
            "tags": ["rpg"],
            "active": False,
        }

    def test_apply_returns_error_when_not_migrated(
        self,
        cli_runner: CliRunner,
        storage: StorageManager,
        base_config: dict[str, Any],
    ) -> None:
        """Apply on a YAML-only (not-yet-migrated) source aborts with non-zero exit."""
        result = _invoke_with_mocks(
            cli_runner,
            ["source", "apply", "my_books", "--from-json", "-"],
            mock_storage=storage,
            config=base_config,
            input_text=json.dumps({"path": "/x"}),
        )
        assert result.exit_code != 0
        # Guard fired before any DB write — no source_configs row created.
        assert storage.get_source_config(1, "my_books") is None

    def test_apply_json_format_matches_api_response(
        self,
        cli_runner: CliRunner,
        storage: StorageManager,
        base_config: dict[str, Any],
    ) -> None:
        """``--format json`` emits the SourceConfigResponse-shaped view."""
        storage.upsert_source_config(1, "my_games", "fake_api", {}, enabled=True)
        result = _invoke_with_mocks(
            cli_runner,
            [
                "source",
                "apply",
                "my_games",
                "--from-json",
                "-",
                "--format",
                "json",
            ],
            mock_storage=storage,
            config=base_config,
            input_text=json.dumps({"user_id": "via_json"}),
        )
        assert result.exit_code == 0
        body = json.loads(result.output)
        assert body["source_id"] == "my_games"
        assert body["plugin"] == "fake_api"
        assert body["field_values"]["user_id"] == "via_json"
        assert "api_key" not in body["field_values"]

    def test_apply_from_file(
        self,
        cli_runner: CliRunner,
        storage: StorageManager,
        base_config: dict[str, Any],
        tmp_path: Path,
    ) -> None:
        storage.upsert_source_config(1, "my_games", "fake_api", {}, enabled=True)
        payload_file = tmp_path / "values.json"
        payload_file.write_text(json.dumps({"user_id": "from_file"}))
        result = _invoke_with_mocks(
            cli_runner,
            ["source", "apply", "my_games", "--from-json", str(payload_file)],
            mock_storage=storage,
            config=base_config,
        )
        assert result.exit_code == 0
        row = storage.get_source_config(1, "my_games")
        assert row is not None and row["config"]["user_id"] == "from_file"

    def test_apply_rejects_unknown_field(
        self,
        cli_runner: CliRunner,
        storage: StorageManager,
        base_config: dict[str, Any],
    ) -> None:
        """A field name not in the plugin schema must abort the bulk apply."""
        storage.upsert_source_config(1, "my_games", "fake_api", {}, enabled=True)
        result = _invoke_with_mocks(
            cli_runner,
            ["source", "apply", "my_games", "--from-json", "-"],
            mock_storage=storage,
            config=base_config,
            input_text=json.dumps({"no_such_field": "x"}),
        )
        assert result.exit_code != 0
        row = storage.get_source_config(1, "my_games")
        assert row is not None
        assert "no_such_field" not in row["config"]

    def test_apply_rejects_sensitive_field(
        self,
        cli_runner: CliRunner,
        storage: StorageManager,
        base_config: dict[str, Any],
    ) -> None:
        storage.upsert_source_config(1, "my_games", "fake_api", {}, enabled=True)
        result = _invoke_with_mocks(
            cli_runner,
            ["source", "apply", "my_games", "--from-json", "-"],
            mock_storage=storage,
            config=base_config,
            input_text=json.dumps({"api_key": "leaked"}),
        )
        assert result.exit_code != 0
        assert storage.get_credential(1, "my_games", "api_key") is None

    def test_apply_aborts_when_file_missing(
        self,
        cli_runner: CliRunner,
        storage: StorageManager,
        base_config: dict[str, Any],
        tmp_path: Path,
    ) -> None:
        """A path that does not exist aborts cleanly via ``_abort_with``.

        Regression guard: a stray ``FileNotFoundError`` would otherwise
        surface as a Python traceback instead of the friendly error
        path every other CLI failure goes through.
        """
        storage.upsert_source_config(1, "my_games", "fake_api", {}, enabled=True)
        missing = tmp_path / "does_not_exist.json"
        result = _invoke_with_mocks(
            cli_runner,
            ["source", "apply", "my_games", "--from-json", str(missing)],
            mock_storage=storage,
            config=base_config,
        )
        assert result.exit_code != 0
        assert "Could not read" in result.output

    def test_apply_rejects_invalid_json(
        self,
        cli_runner: CliRunner,
        storage: StorageManager,
        base_config: dict[str, Any],
    ) -> None:
        storage.upsert_source_config(1, "my_games", "fake_api", {}, enabled=True)
        result = _invoke_with_mocks(
            cli_runner,
            ["source", "apply", "my_games", "--from-json", "-"],
            mock_storage=storage,
            config=base_config,
            input_text="{not json",
        )
        assert result.exit_code != 0

    def test_apply_rejects_non_object_payload(
        self,
        cli_runner: CliRunner,
        storage: StorageManager,
        base_config: dict[str, Any],
    ) -> None:
        storage.upsert_source_config(1, "my_games", "fake_api", {}, enabled=True)
        result = _invoke_with_mocks(
            cli_runner,
            ["source", "apply", "my_games", "--from-json", "-"],
            mock_storage=storage,
            config=base_config,
            input_text="[1, 2, 3]",
        )
        assert result.exit_code != 0


@pytest.mark.usefixtures("registry_with_source_fakes")
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

    def test_set_secret_rejects_unknown_field_name(
        self,
        cli_runner: CliRunner,
        storage: StorageManager,
        base_config: dict[str, Any],
    ) -> None:
        """A field not declared in the plugin schema aborts the prompt path."""
        storage.upsert_source_config(1, "my_games", "fake_api", {}, enabled=True)
        result = _invoke_with_mocks(
            cli_runner,
            ["source", "set-secret", "my_games", "no_such_field"],
            mock_storage=storage,
            config=base_config,
            input_text="x\n",
        )
        assert result.exit_code != 0

    def test_clear_secret_rejects_unknown_field_name(
        self,
        cli_runner: CliRunner,
        storage: StorageManager,
        base_config: dict[str, Any],
    ) -> None:
        """A field not declared in the plugin schema aborts the clear path."""
        storage.upsert_source_config(1, "my_games", "fake_api", {}, enabled=True)
        result = _invoke_with_mocks(
            cli_runner,
            ["source", "clear-secret", "my_games", "no_such_field"],
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


@pytest.mark.usefixtures("registry_with_source_fakes")
class TestSourcePlugins:
    def test_plugins_json_lists_all_registered(
        self,
        cli_runner: CliRunner,
        storage: StorageManager,
        base_config: dict[str, Any],
    ) -> None:
        result = _invoke_with_mocks(
            cli_runner,
            ["source", "plugins", "--format", "json"],
            mock_storage=storage,
            config=base_config,
        )
        assert result.exit_code == 0
        body = json.loads(result.output)
        # Exact set match — the fixture pins two plugins; an extra one
        # appearing in the output would indicate a registry leak.
        assert {p["name"] for p in body} == {"fake_file", "fake_api"}
        # Every plugin entry mirrors PluginInfoResponse exactly.
        for plugin in body:
            assert set(plugin.keys()) == {
                "name",
                "display_name",
                "description",
                "content_types",
                "requires_api_key",
                "requires_network",
                "fields",
            }
            # Per-field key set mirrors SourceFieldSchema; a serialiser
            # drift dropping any of these would be a parity gap.
            for field in plugin["fields"]:
                assert set(field.keys()) == {
                    "name",
                    "field_type",
                    "required",
                    "default",
                    "description",
                    "sensitive",
                }


@pytest.mark.usefixtures("registry_with_source_fakes")
class TestSourceCreate:
    def test_create_inserts_db_row(
        self,
        cli_runner: CliRunner,
        storage: StorageManager,
        base_config: dict[str, Any],
    ) -> None:
        """JSON output mirrors the SourceConfigResponse the web endpoint returns.

        Uses ``--format json`` to confirm the CLI emits the same field set
        as ``POST /api/sync/sources`` so a future drift on either side is
        caught.
        """
        result = _invoke_with_mocks(
            cli_runner,
            [
                "source",
                "create",
                "fresh_books",
                "fake_file",
                "--from-json",
                "-",
                "--format",
                "json",
            ],
            mock_storage=storage,
            config=base_config,
            input_text=json.dumps({"path": "/data/fresh.csv", "content_type": "book"}),
        )
        assert result.exit_code == 0
        body = json.loads(result.output)
        assert body["source_id"] == "fresh_books"
        assert body["plugin"] == "fake_file"
        assert body["plugin_display_name"] == "Fake File"
        assert body["enabled"] is True
        assert body["migrated"] is True
        assert body["field_values"] == {
            "path": "/data/fresh.csv",
            "content_type": "book",
        }
        assert body["secret_status"] == {}
        row = storage.get_source_config(1, "fresh_books")
        assert row is not None
        assert row["plugin"] == "fake_file"
        assert row["enabled"] is True

    def test_create_with_initial_values_from_stdin(
        self,
        cli_runner: CliRunner,
        storage: StorageManager,
        base_config: dict[str, Any],
    ) -> None:
        result = _invoke_with_mocks(
            cli_runner,
            ["source", "create", "with_values", "fake_file", "--from-json", "-"],
            mock_storage=storage,
            config=base_config,
            input_text=json.dumps({"path": "/x", "content_type": "book"}),
        )
        assert result.exit_code == 0
        row = storage.get_source_config(1, "with_values")
        assert row is not None
        assert row["config"] == {"path": "/x", "content_type": "book"}

    def test_create_rejects_existing_yaml_id(
        self,
        cli_runner: CliRunner,
        storage: StorageManager,
        base_config: dict[str, Any],
    ) -> None:
        result = _invoke_with_mocks(
            cli_runner,
            ["source", "create", "my_books", "fake_file"],
            mock_storage=storage,
            config=base_config,
        )
        assert result.exit_code != 0

    def test_create_rejects_existing_db_id(
        self,
        cli_runner: CliRunner,
        storage: StorageManager,
        base_config: dict[str, Any],
    ) -> None:
        """Collision with an existing DB row aborts and leaves it intact."""
        storage.upsert_source_config(
            1, "already_here", "fake_file", {"path": "/x"}, enabled=True
        )
        result = _invoke_with_mocks(
            cli_runner,
            ["source", "create", "already_here", "fake_file"],
            mock_storage=storage,
            config=base_config,
        )
        assert result.exit_code != 0
        row = storage.get_source_config(1, "already_here")
        assert row is not None
        assert row["config"] == {"path": "/x"}

    def test_create_rejects_unknown_plugin(
        self,
        cli_runner: CliRunner,
        storage: StorageManager,
        base_config: dict[str, Any],
    ) -> None:
        result = _invoke_with_mocks(
            cli_runner,
            ["source", "create", "no_such", "no_such_plugin"],
            mock_storage=storage,
            config=base_config,
        )
        assert result.exit_code != 0
        assert storage.get_source_config(1, "no_such") is None

    def test_create_rejects_invalid_id(
        self,
        cli_runner: CliRunner,
        storage: StorageManager,
        base_config: dict[str, Any],
    ) -> None:
        result = _invoke_with_mocks(
            cli_runner,
            ["source", "create", "Bad-ID!", "fake_file"],
            mock_storage=storage,
            config=base_config,
        )
        assert result.exit_code != 0

    def test_create_rejects_sensitive_field(
        self,
        cli_runner: CliRunner,
        storage: StorageManager,
        base_config: dict[str, Any],
    ) -> None:
        result = _invoke_with_mocks(
            cli_runner,
            [
                "source",
                "create",
                "leaky",
                "fake_api",
                "--from-json",
                "-",
            ],
            mock_storage=storage,
            config=base_config,
            input_text=json.dumps({"api_key": "leaked"}),
        )
        assert result.exit_code != 0
        assert storage.get_source_config(1, "leaky") is None


@pytest.mark.usefixtures("registry_with_source_fakes")
class TestSourceRemove:
    def test_remove_drops_row_and_credentials(
        self,
        cli_runner: CliRunner,
        storage: StorageManager,
        base_config: dict[str, Any],
    ) -> None:
        storage.upsert_source_config(
            1, "to_remove", "fake_api", {"user_id": "x"}, enabled=True
        )
        storage.save_credential(1, "to_remove", "api_key", "secret_value")

        result = _invoke_with_mocks(
            cli_runner,
            ["source", "remove", "to_remove", "--yes"],
            mock_storage=storage,
            config=base_config,
        )
        assert result.exit_code == 0
        assert storage.get_source_config(1, "to_remove") is None
        assert storage.get_credential(1, "to_remove", "api_key") is None

    def test_remove_aborts_when_user_declines_confirmation(
        self,
        cli_runner: CliRunner,
        storage: StorageManager,
        base_config: dict[str, Any],
    ) -> None:
        storage.upsert_source_config(
            1, "keep_me", "fake_file", {"path": "/x"}, enabled=True
        )
        result = _invoke_with_mocks(
            cli_runner,
            ["source", "remove", "keep_me"],
            mock_storage=storage,
            config=base_config,
            input_text="n\n",
        )
        assert result.exit_code == 0
        assert storage.get_source_config(1, "keep_me") is not None

    def test_remove_returns_error_when_not_migrated(
        self,
        cli_runner: CliRunner,
        storage: StorageManager,
        base_config: dict[str, Any],
    ) -> None:
        result = _invoke_with_mocks(
            cli_runner,
            ["source", "remove", "my_books", "--yes"],
            mock_storage=storage,
            config=base_config,
        )
        assert result.exit_code != 0
