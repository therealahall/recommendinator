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
from tests.fakes.source_plugins import (
    BROKEN_PRIVATE_MODULE,
    BROKEN_PRIVATE_REASON,
    UNLOADED_PLUGIN,
    UNLOADED_PLUGIN_DETAIL,
)

_RUN_START = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)


@pytest.fixture()
def storage(tmp_path: Path) -> StorageManager:
    return StorageManager(sqlite_path=tmp_path / "cli.db")


def _record_run(
    storage: StorageManager,
    *,
    source_id: str = "my_books",
    status: SyncRunStatus = "completed",
    minute: int = 0,
    errors: tuple[str, ...] = (),
    omitted_errors: int = 0,
) -> None:
    started_at = _RUN_START + timedelta(minutes=minute)
    storage.sync_runs.record(
        1,
        source_id,
        started_at=started_at,
        finished_at=started_at + timedelta(seconds=30),
        status=status,
        errors=errors,
        omitted_errors=omitted_errors,
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
        assert set(payload[0].keys()) == {
            "id",
            "display_name",
            "plugin_display_name",
            "enabled",
            "plugin_not_loaded",
            "sync_interval",
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
        result = _invoke_with_mocks(
            cli_runner,
            ["source", "migrate", "my_games"],
            mock_storage=storage,
            config=base_config,
        )
        assert result.exit_code == 0
        assert "Migrated source 'my_games'" in result.output
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
class TestSourceHistory:
    def test_history_json_reports_runs_newest_first_in_the_api_key_set(
        self,
        cli_runner: CliRunner,
        storage: StorageManager,
        base_config: dict[str, Any],
    ) -> None:
        failures = ("429 from the API", "book 12 has no title", "timed out")
        _record_run(storage, minute=0)
        _record_run(
            storage, minute=10, status="failed", errors=failures, omitted_errors=4997
        )

        result = _invoke_with_mocks(
            cli_runner,
            ["source", "history", "my_books", "--format", "json"],
            mock_storage=storage,
            config=base_config,
        )

        assert result.exit_code == 0
        payload = json.loads(result.output)
        assert [run["status"] for run in payload] == ["failed", "completed"]
        assert payload[0]["errors"] == list(failures)
        assert len(payload[0]["errors"]) + payload[0]["omitted_errors"] == 5000
        assert set(payload[0].keys()) == {
            "source_id",
            "started_at",
            "finished_at",
            "status",
            "items_added",
            "items_updated",
            "items_unchanged",
            "total_items",
            "errors",
            "omitted_errors",
        }

    def test_history_table_names_the_error_total_behind_the_listed_ones(
        self,
        cli_runner: CliRunner,
        storage: StorageManager,
        base_config: dict[str, Any],
    ) -> None:
        _record_run(
            storage,
            minute=0,
            status="failed",
            errors=("429 from the API",),
            omitted_errors=4999,
        )

        result = _invoke_with_mocks(
            cli_runner,
            ["source", "history", "my_books"],
            mock_storage=storage,
            config=base_config,
        )

        assert result.exit_code == 0, result.output
        assert "5000" in result.output

    def test_history_with_nothing_recorded_is_an_empty_json_list_not_prose(
        self,
        cli_runner: CliRunner,
        storage: StorageManager,
        base_config: dict[str, Any],
    ) -> None:
        as_json = _invoke_with_mocks(
            cli_runner,
            ["source", "history", "my_books", "--format", "json"],
            mock_storage=storage,
            config=base_config,
        )
        as_table = _invoke_with_mocks(
            cli_runner,
            ["source", "history", "my_books"],
            mock_storage=storage,
            config=base_config,
        )

        assert json.loads(as_json.output) == []
        assert "No sync runs recorded." in as_table.output

    def test_history_without_a_source_id_spans_every_source_and_limit_trims_it(
        self,
        cli_runner: CliRunner,
        storage: StorageManager,
        base_config: dict[str, Any],
    ) -> None:
        _record_run(storage, source_id="my_books", minute=0)
        _record_run(storage, source_id="my_games", minute=10)

        newest = _invoke_with_mocks(
            cli_runner,
            ["source", "history", "--limit", "1", "--format", "json"],
            mock_storage=storage,
            config=base_config,
        )

        assert [run["source_id"] for run in json.loads(newest.output)] == ["my_games"]


@pytest.mark.usefixtures("registry_with_source_fakes")
class TestSourceSet:
    def test_set_coerces_list_value_from_comma_separated_string(
        self,
        cli_runner: CliRunner,
        storage: StorageManager,
        base_config: dict[str, Any],
    ) -> None:
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
        result = _invoke_with_mocks(
            cli_runner,
            ["source", "apply", "my_books", "--from-json", "-"],
            mock_storage=storage,
            config=base_config,
            input_text=json.dumps({"path": "/x"}),
        )
        assert result.exit_code != 0
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
            ["source", "clear-secret", "my_games", "api_key", "--yes"],
            mock_storage=storage,
            config=base_config,
        )
        assert result.exit_code == 0
        assert storage.credentials.get(1, "my_games", "api_key") is None

    def test_clear_secret_keeps_the_credential_when_the_prompt_is_declined(
        self,
        cli_runner: CliRunner,
        storage: StorageManager,
        base_config: dict[str, Any],
    ) -> None:
        storage.sources.upsert(1, "my_games", "fake_api", {}, enabled=True)
        storage.credentials.save(1, "my_games", "api_key", "keep_me")
        result = _invoke_with_mocks(
            cli_runner,
            ["source", "clear-secret", "my_games", "api_key"],
            mock_storage=storage,
            config=base_config,
            input_text="n\n",
        )
        assert result.exit_code == 0
        assert "Aborted." in result.output
        assert storage.credentials.get(1, "my_games", "api_key") == "keep_me"

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


@pytest.mark.usefixtures("registry_with_a_failed_import")
class TestSourceWhosePluginNeverImported:
    @pytest.fixture()
    def broken_config(self) -> dict[str, Any]:
        return {"inputs": {"my_books": {"plugin": UNLOADED_PLUGIN, "enabled": True}}}

    def test_show_says_why_it_cannot_be_used_in_the_api_s_words(
        self,
        cli_runner: CliRunner,
        storage: StorageManager,
        broken_config: dict[str, Any],
    ) -> None:
        result = _invoke_with_mocks(
            cli_runner,
            ["source", "show", "my_books"],
            mock_storage=storage,
            config=broken_config,
        )

        assert result.exit_code != 0
        assert "Unknown source" not in result.output
        assert UNLOADED_PLUGIN_DETAIL in result.output

    def test_a_source_that_really_is_absent_still_reads_as_unknown(
        self,
        cli_runner: CliRunner,
        storage: StorageManager,
        broken_config: dict[str, Any],
    ) -> None:
        result = _invoke_with_mocks(
            cli_runner,
            ["source", "show", "nothing_here"],
            mock_storage=storage,
            config=broken_config,
        )

        assert result.exit_code != 0
        assert "Unknown source: nothing_here" in result.output


class TestPrivateModuleImportFailureIsReported:
    def test_source_plugins_names_the_private_module_and_why_it_died(
        self,
        storage: StorageManager,
        registry_with_a_broken_private_module: None,
    ) -> None:
        result = _invoke_with_mocks(
            CliRunner(),
            ["source", "plugins"],
            mock_storage=storage,
            config={"inputs": {}},
        )

        assert result.exit_code == 0
        assert BROKEN_PRIVATE_MODULE in result.stderr
        assert BROKEN_PRIVATE_REASON in result.stderr
