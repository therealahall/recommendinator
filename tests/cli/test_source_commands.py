"""Tests for the CLI ``source`` group.

Mirrors the per-source web endpoints — JSON output shapes must match the
Pydantic responses in ``src/web/api.py`` exactly so the two interfaces
stay in lockstep.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner, Result

from src.storage.manager import StorageManager
from src.storage.schema import SyncRunStatus
from tests.cli.conftest import _invoke_with_mocks

_RUN_START = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)


@pytest.fixture()
def storage(tmp_path: Path) -> StorageManager:
    return StorageManager(sqlite_path=tmp_path / "cli.db")


def _record_run(
    storage: StorageManager, *, status: SyncRunStatus = "completed"
) -> None:
    storage.sync_runs.record(
        1,
        "my_books",
        started_at=_RUN_START,
        finished_at=_RUN_START + timedelta(seconds=30),
        status=status,
    )


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
            "plugin_not_loaded",
            "sync_interval",
            "sync_interval_default",
            "last_run_at",
            "last_run_status",
            "next_run_at",
        }
        assert payload[0]["plugin_not_loaded"] is None

    def test_list_table_shows_the_cadence_and_the_last_outcome(
        self,
        cli_runner: CliRunner,
        storage: StorageManager,
        base_config: dict[str, Any],
    ) -> None:
        storage.sources.upsert(1, "my_books", "fake_file", {"path": "/x"}, enabled=True)
        storage.sources.set_schedule(1, "my_books", "weekly")
        _record_run(storage, status="failed")

        result = _invoke_with_mocks(
            cli_runner,
            ["source", "list"],
            mock_storage=storage,
            config=base_config,
        )

        assert result.exit_code == 0
        assert "weekly" in result.output
        assert "failed" in result.output


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
class TestSourceMigrate:
    def test_migrate_leaves_the_source_db_backed(
        self,
        cli_runner: CliRunner,
        storage: StorageManager,
        base_config: dict[str, Any],
    ) -> None:
        """The end state after the command, whichever pass moved which half.

        The boot migration encrypts the secret before ``migrate`` is reached,
        so only the field half below is this command's own work.
        """
        result = _invoke_with_mocks(
            cli_runner,
            ["source", "migrate", "my_games"],
            mock_storage=storage,
            config=base_config,
        )
        assert result.exit_code == 0
        assert "Migrated source 'my_games'" in result.output
        # The reported symptom: counting what this call moved printed no
        # Secrets line at all, because startup had emptied the YAML entry.
        assert "Secrets: api_key" in result.output
        row = storage.sources.get(1, "my_games")
        assert row is not None
        assert row["plugin"] == "fake_api"
        assert row["config"]["user_id"] == "yaml_user"
        assert "api_key" not in row["config"]
        assert storage.credentials.get(1, "my_games", "api_key") == "yaml_key"

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
        rows = storage.sources.list(1)
        assert len([r for r in rows if r["source_id"] == "my_books"]) == 1


@pytest.mark.usefixtures("registry_with_source_fakes")
class TestSourceEnableDisable:
    def test_disable_after_migrate_flips_flag(
        self,
        cli_runner: CliRunner,
        storage: StorageManager,
        base_config: dict[str, Any],
    ) -> None:
        storage.sources.upsert(1, "my_books", "fake_file", {"path": "/x"}, enabled=True)
        result = _invoke_with_mocks(
            cli_runner,
            ["source", "disable", "my_books"],
            mock_storage=storage,
            config=base_config,
        )
        assert result.exit_code == 0
        row = storage.sources.get(1, "my_books")
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


@pytest.mark.usefixtures("registry_with_source_fakes")
class TestSourceSchedule:
    def test_schedule_is_read_back_by_show(
        self,
        cli_runner: CliRunner,
        storage: StorageManager,
        base_config: dict[str, Any],
    ) -> None:
        storage.sources.upsert(1, "my_books", "fake_file", {"path": "/x"}, enabled=True)

        scheduled = _invoke_with_mocks(
            cli_runner,
            ["source", "schedule", "my_books", "6h", "--format", "json"],
            mock_storage=storage,
            config=base_config,
        )
        shown = _invoke_with_mocks(
            cli_runner,
            ["source", "show", "my_books"],
            mock_storage=storage,
            config=base_config,
        )

        assert scheduled.exit_code == 0
        assert json.loads(scheduled.output)["sync_interval"] == "6h"
        assert "6h" in shown.output

    def test_schedule_refuses_an_interval_outside_the_presets(
        self,
        cli_runner: CliRunner,
        storage: StorageManager,
        base_config: dict[str, Any],
    ) -> None:
        storage.sources.upsert(1, "my_books", "fake_file", {"path": "/x"}, enabled=True)

        result = _invoke_with_mocks(
            cli_runner,
            ["source", "schedule", "my_books", "fortnightly"],
            mock_storage=storage,
            config=base_config,
        )

        assert result.exit_code != 0


@pytest.mark.usefixtures("registry_with_source_fakes")
class TestSourceSet:
    def test_set_coerces_list_value_from_comma_separated_string(
        self,
        cli_runner: CliRunner,
        storage: StorageManager,
        base_config: dict[str, Any],
    ) -> None:
        """List fields accept ``"a,b,c"`` and store ``["a", "b", "c"]``."""
        storage.sources.upsert(1, "my_games", "fake_api", {}, enabled=True)
        result = _invoke_with_mocks(
            cli_runner,
            ["source", "set", "my_games", "tags", "rpg, indie ,strategy"],
            mock_storage=storage,
            config=base_config,
        )
        assert result.exit_code == 0
        row = storage.sources.get(1, "my_games")
        assert row is not None
        assert row["config"]["tags"] == ["rpg", "indie", "strategy"]

    @pytest.mark.parametrize("keyword", ["yes", "on", "true"])
    def test_set_coerces_bool_truthy_keyword(
        self,
        cli_runner: CliRunner,
        storage: StorageManager,
        base_config: dict[str, Any],
        keyword: str,
    ) -> None:
        """``"yes"`` / ``"on"`` / ``"true"`` all coerce to ``True``."""
        storage.sources.upsert(1, "my_games", "fake_api", {}, enabled=True)
        result = _invoke_with_mocks(
            cli_runner,
            ["source", "set", "my_games", "active", keyword],
            mock_storage=storage,
            config=base_config,
        )
        assert result.exit_code == 0
        row = storage.sources.get(1, "my_games")
        assert row is not None and row["config"]["active"] is True

    def test_set_updates_non_sensitive_field(
        self,
        cli_runner: CliRunner,
        storage: StorageManager,
        base_config: dict[str, Any],
    ) -> None:
        storage.sources.upsert(
            1, "my_games", "fake_api", {"min_minutes": 30}, enabled=True
        )
        result = _invoke_with_mocks(
            cli_runner,
            ["source", "set", "my_games", "min_minutes", "60"],
            mock_storage=storage,
            config=base_config,
        )
        assert result.exit_code == 0
        row = storage.sources.get(1, "my_games")
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
        assert storage.sources.get(1, "my_games") is None

    def test_set_rejects_unknown_field(
        self,
        cli_runner: CliRunner,
        storage: StorageManager,
        base_config: dict[str, Any],
    ) -> None:
        """A field not in the plugin schema aborts before any DB write."""
        storage.sources.upsert(1, "my_games", "fake_api", {}, enabled=True)
        result = _invoke_with_mocks(
            cli_runner,
            ["source", "set", "my_games", "no_such_field", "x"],
            mock_storage=storage,
            config=base_config,
        )
        assert result.exit_code != 0
        row = storage.sources.get(1, "my_games")
        assert row is not None
        assert "no_such_field" not in row["config"]

    def test_set_rejects_sensitive_field(
        self,
        cli_runner: CliRunner,
        storage: StorageManager,
        base_config: dict[str, Any],
    ) -> None:
        storage.sources.upsert(1, "my_games", "fake_api", {}, enabled=True)
        result = _invoke_with_mocks(
            cli_runner,
            ["source", "set", "my_games", "api_key", "leaked"],
            mock_storage=storage,
            config=base_config,
        )
        assert result.exit_code != 0
        assert storage.credentials.get(1, "my_games", "api_key") is None


@pytest.mark.usefixtures("registry_with_source_fakes")
class TestSourceApply:
    """Bulk-update parity with web ``PUT /api/sync/sources/<id>/config``."""

    def test_apply_updates_multiple_fields_atomically_from_stdin(
        self,
        cli_runner: CliRunner,
        storage: StorageManager,
        base_config: dict[str, Any],
    ) -> None:
        storage.sources.upsert(1, "my_games", "fake_api", {}, enabled=True)
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
        row = storage.sources.get(1, "my_games")
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
        assert storage.sources.get(1, "my_books") is None

    def test_apply_from_file(
        self,
        cli_runner: CliRunner,
        storage: StorageManager,
        base_config: dict[str, Any],
        tmp_path: Path,
    ) -> None:
        storage.sources.upsert(1, "my_games", "fake_api", {}, enabled=True)
        payload_file = tmp_path / "values.json"
        payload_file.write_text(json.dumps({"user_id": "from_file"}))
        result = _invoke_with_mocks(
            cli_runner,
            ["source", "apply", "my_games", "--from-json", str(payload_file)],
            mock_storage=storage,
            config=base_config,
        )
        assert result.exit_code == 0
        row = storage.sources.get(1, "my_games")
        assert row is not None and row["config"]["user_id"] == "from_file"

    def test_apply_rejects_sensitive_field(
        self,
        cli_runner: CliRunner,
        storage: StorageManager,
        base_config: dict[str, Any],
    ) -> None:
        storage.sources.upsert(1, "my_games", "fake_api", {}, enabled=True)
        result = _invoke_with_mocks(
            cli_runner,
            ["source", "apply", "my_games", "--from-json", "-"],
            mock_storage=storage,
            config=base_config,
            input_text=json.dumps({"api_key": "leaked"}),
        )
        assert result.exit_code != 0
        assert storage.credentials.get(1, "my_games", "api_key") is None

    def test_apply_aborts_when_file_missing(
        self,
        cli_runner: CliRunner,
        storage: StorageManager,
        base_config: dict[str, Any],
        tmp_path: Path,
    ) -> None:
        """A path that does not exist aborts cleanly via ``abort_with``.

        Regression guard: a stray ``FileNotFoundError`` would otherwise
        surface as a Python traceback instead of the friendly error
        path every other CLI failure goes through.
        """
        storage.sources.upsert(1, "my_games", "fake_api", {}, enabled=True)
        missing = tmp_path / "does_not_exist.json"
        result = _invoke_with_mocks(
            cli_runner,
            ["source", "apply", "my_games", "--from-json", str(missing)],
            mock_storage=storage,
            config=base_config,
        )
        assert result.exit_code != 0
        assert "Could not read" in result.output


@pytest.mark.usefixtures("registry_with_source_fakes")
class TestSourceSecrets:
    def test_set_secret_stores_via_hidden_prompt(
        self,
        cli_runner: CliRunner,
        storage: StorageManager,
        base_config: dict[str, Any],
    ) -> None:
        storage.sources.upsert(1, "my_games", "fake_api", {}, enabled=True)
        result = _invoke_with_mocks(
            cli_runner,
            ["source", "set-secret", "my_games", "api_key"],
            mock_storage=storage,
            config=base_config,
            input_text="rotated_value\n",
        )
        assert result.exit_code == 0
        assert storage.credentials.get(1, "my_games", "api_key") == "rotated_value"

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
        storage.sources.upsert(1, "my_games", "fake_api", {}, enabled=True)
        monkeypatch.setenv("RECOMMENDINATOR_SECRET_VALUE", "env_secret")
        result = _invoke_with_mocks(
            cli_runner,
            ["source", "set-secret", "my_games", "api_key"],
            mock_storage=storage,
            config=base_config,
        )
        assert result.exit_code == 0
        assert storage.credentials.get(1, "my_games", "api_key") == "env_secret"

    def test_clear_secret_removes_credential(
        self,
        cli_runner: CliRunner,
        storage: StorageManager,
        base_config: dict[str, Any],
    ) -> None:
        storage.sources.upsert(1, "my_games", "fake_api", {}, enabled=True)
        storage.credentials.save(1, "my_games", "api_key", "to_be_cleared")
        result = _invoke_with_mocks(
            cli_runner,
            ["source", "clear-secret", "my_games", "api_key"],
            mock_storage=storage,
            config=base_config,
        )
        assert result.exit_code == 0
        assert storage.credentials.get(1, "my_games", "api_key") is None

    def test_set_secret_rejects_non_sensitive_field(
        self,
        cli_runner: CliRunner,
        storage: StorageManager,
        base_config: dict[str, Any],
    ) -> None:
        storage.sources.upsert(1, "my_games", "fake_api", {}, enabled=True)
        result = _invoke_with_mocks(
            cli_runner,
            ["source", "set-secret", "my_games", "user_id"],
            mock_storage=storage,
            config=base_config,
            input_text="x\n",
        )
        assert result.exit_code != 0


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
        row = storage.sources.get(1, "fresh_books")
        assert row is not None
        assert row["plugin"] == "fake_file"
        assert row["enabled"] is True

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
        assert storage.sources.get(1, "no_such") is None

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
        assert storage.sources.get(1, "leaky") is None


@pytest.mark.usefixtures("registry_with_source_fakes")
class TestSourceRemove:
    def test_remove_drops_row_and_credentials(
        self,
        cli_runner: CliRunner,
        storage: StorageManager,
        base_config: dict[str, Any],
    ) -> None:
        storage.sources.upsert(
            1, "to_remove", "fake_api", {"user_id": "x"}, enabled=True
        )
        storage.credentials.save(1, "to_remove", "api_key", "secret_value")

        result = _invoke_with_mocks(
            cli_runner,
            ["source", "remove", "to_remove", "--yes"],
            mock_storage=storage,
            config=base_config,
        )
        assert result.exit_code == 0
        assert storage.sources.get(1, "to_remove") is None
        assert storage.credentials.get(1, "to_remove", "api_key") is None

    def test_remove_aborts_when_user_declines_confirmation(
        self,
        cli_runner: CliRunner,
        storage: StorageManager,
        base_config: dict[str, Any],
    ) -> None:
        storage.sources.upsert(1, "keep_me", "fake_file", {"path": "/x"}, enabled=True)
        result = _invoke_with_mocks(
            cli_runner,
            ["source", "remove", "keep_me"],
            mock_storage=storage,
            config=base_config,
            input_text="n\n",
        )
        assert result.exit_code == 0
        assert storage.sources.get(1, "keep_me") is not None


@pytest.mark.usefixtures("registry_with_source_fakes")
class TestRemovingTheLastSourceSweepsThePluginRowRegression:
    """Reported: a credential the CLI was told to delete stayed in the database.

    Cause: ``source remove`` passed no config, so the sweep never ran.
    Fix: it passes one, as ``source create`` already did.
    """

    @pytest.fixture()
    def stranded(self, storage: StorageManager) -> StorageManager:
        storage.sources.upsert(1, "games_work", "fake_api", {}, enabled=True)
        storage.credentials.save(1, "fake_api", "api_key", "stranded-by-an-upgrade")
        return storage

    def test_the_row_under_the_plugin_name_goes_with_the_last_source(
        self, cli_runner: CliRunner, stranded: StorageManager
    ) -> None:
        result = _invoke_with_mocks(
            cli_runner,
            ["source", "remove", "games_work", "--yes"],
            mock_storage=stranded,
            config={"inputs": {}},
        )

        assert result.exit_code == 0
        assert stranded.credentials.get(1, "fake_api", "api_key") is None

    def test_a_yaml_sibling_on_the_plugin_keeps_it(
        self,
        cli_runner: CliRunner,
        stranded: StorageManager,
        base_config: dict[str, Any],
    ) -> None:
        """The anchor, and why the sweep needs the config rather than the DB.

        ``my_games`` runs the same plugin from config.yaml alone.
        """
        result = _invoke_with_mocks(
            cli_runner,
            ["source", "remove", "games_work", "--yes"],
            mock_storage=stranded,
            config=base_config,
        )

        assert result.exit_code == 0
        assert (
            stranded.credentials.get(1, "fake_api", "api_key")
            == "stranded-by-an-upgrade"
        )


class TestSourceSetGuardsBoundCredentials:
    """The CLI repoints a source too, so it must answer the PUT's way.

    Runs against the real registry: the shared fakes carry no
    ``credential_bound`` field, and one that did would prove only that this
    test's own schema was honoured.
    """

    @pytest.fixture()
    def migrated(self, storage: StorageManager) -> StorageManager:
        storage.sources.upsert(
            1,
            "calibre",
            "calibre_web",
            {"url": "http://localhost:8083", "username": "reader"},
            enabled=True,
        )
        storage.credentials.save(1, "calibre", "password", "hunter2")
        return storage

    def _set(
        self, cli_runner: CliRunner, storage: StorageManager, field: str, value: str
    ) -> Result:
        return _invoke_with_mocks(
            cli_runner,
            ["source", "set", "calibre", field, value],
            mock_storage=storage,
            config={"inputs": {}},
        )

    def test_repointing_the_url_is_refused_in_the_same_words_as_the_api(
        self, cli_runner: CliRunner, migrated: StorageManager
    ) -> None:
        result = self._set(cli_runner, migrated, "url", "https://attacker.example")

        assert result.exit_code != 0
        assert "Changing 'url' points this source at a different host." in result.output
        assert "Clear its stored 'password' first" in result.output
        row = migrated.sources.get(1, "calibre")
        assert row is not None and row["config"]["url"] == "http://localhost:8083"
        assert migrated.credentials.get(1, "calibre", "password") == "hunter2"

    def test_upgrading_to_https_keeps_the_password(
        self, cli_runner: CliRunner, migrated: StorageManager
    ) -> None:
        result = self._set(cli_runner, migrated, "url", "https://localhost:8083")

        assert result.exit_code == 0
        row = migrated.sources.get(1, "calibre")
        assert row is not None and row["config"]["url"] == "https://localhost:8083"
        assert migrated.credentials.get(1, "calibre", "password") == "hunter2"
