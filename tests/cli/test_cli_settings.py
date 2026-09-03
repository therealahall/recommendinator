from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from src.cli.commands._settings import RESTART_ADVISORY
from src.settings.metadata import default_of
from src.storage.manager import StorageManager
from tests.cli.conftest import _invoke_with_mocks

_INT_KEY = "recommendations.default_count"
_BOOL_KEY = "enrichment.enabled"
_LIST_KEY = "web.allowed_origins"
_ADVANCED_KEY = "logging.file"
_SECRET_KEY = "enrichment.providers.tmdb.api_key"


@pytest.fixture()
def storage(tmp_path: Path) -> StorageManager:
    return StorageManager(sqlite_path=tmp_path / "cli.db")


class TestSettingsList:
    def test_list_groups_by_section_and_hides_advanced(
        self, cli_runner: CliRunner, storage: StorageManager
    ) -> None:
        result = _invoke_with_mocks(cli_runner, ["settings", "list"], storage)

        assert result.exit_code == 0
        assert "recommendations" in result.output
        assert "Default count" in result.output
        assert _ADVANCED_KEY not in result.output

    def test_list_section_filter_limits_output(
        self, cli_runner: CliRunner, storage: StorageManager
    ) -> None:
        result = _invoke_with_mocks(
            cli_runner,
            ["settings", "list", "--section", "recommendations"],
            storage,
        )

        assert result.exit_code == 0
        assert _INT_KEY in result.output
        assert "enrichment.enabled" not in result.output

    def test_list_flags_a_stored_row_whose_value_equals_the_default(
        self, cli_runner: CliRunner, storage: StorageManager
    ) -> None:
        storage.settings.set(_INT_KEY, default_of(_INT_KEY))

        result = _invoke_with_mocks(cli_runner, ["settings", "list"], storage)

        assert result.exit_code == 0
        assert "stored" in result.output
        assert "overridden" not in result.output

    def test_list_json_matches_service_view_shape(
        self, cli_runner: CliRunner, storage: StorageManager
    ) -> None:
        result = _invoke_with_mocks(
            cli_runner, ["settings", "list", "--format", "json"], storage
        )

        assert result.exit_code == 0
        payload = json.loads(result.output)
        assert set(payload.keys()) == {"sections"}
        setting = _find_json(payload, _INT_KEY)
        assert set(setting.keys()) == {
            "key",
            "section",
            "label",
            "help",
            "type",
            "widget",
            "choices",
            "validation",
            "advanced",
            "restart_required",
            "sensitive",
            "value",
            "db_overridden",
            "has_stored_value",
        }
        assert setting["value"] == default_of(_INT_KEY)
        assert setting["db_overridden"] is False

    def test_list_json_masks_secret(
        self, cli_runner: CliRunner, storage: StorageManager
    ) -> None:
        storage.secrets.set(_SECRET_KEY, "SECRETPLAIN")

        result = _invoke_with_mocks(
            cli_runner, ["settings", "list", "--format", "json"], storage
        )

        assert result.exit_code == 0
        secret = _find_json(json.loads(result.output), _SECRET_KEY)
        assert set(secret.keys()) == {
            "key",
            "section",
            "label",
            "help",
            "type",
            "widget",
            "choices",
            "validation",
            "advanced",
            "restart_required",
            "sensitive",
            "has_secret",
        }
        assert secret["has_secret"] is True
        assert "SECRETPLAIN" not in result.output


class TestSettingsGet:
    def test_get_scalar_json(
        self, cli_runner: CliRunner, storage: StorageManager
    ) -> None:
        result = _invoke_with_mocks(
            cli_runner, ["settings", "get", _INT_KEY, "--format", "json"], storage
        )

        assert result.exit_code == 0
        body = json.loads(result.output)
        assert body["key"] == _INT_KEY
        assert body["value"] == default_of(_INT_KEY)
        assert body["db_overridden"] is False

    def test_get_flags_a_stored_row_whose_value_equals_the_default(
        self, cli_runner: CliRunner, storage: StorageManager
    ) -> None:
        storage.settings.set(_INT_KEY, default_of(_INT_KEY))

        result = _invoke_with_mocks(cli_runner, ["settings", "get", _INT_KEY], storage)

        assert result.exit_code == 0
        assert "stored" in result.output
        assert "overridden" not in result.output

    def test_get_flags_a_differing_row_as_both_stored_and_overridden(
        self, cli_runner: CliRunner, storage: StorageManager
    ) -> None:
        storage.settings.set(_INT_KEY, default_of(_INT_KEY) + 1)

        result = _invoke_with_mocks(cli_runner, ["settings", "get", _INT_KEY], storage)

        assert result.exit_code == 0
        assert "stored" in result.output
        assert "overridden" in result.output

    def test_get_secret_shows_presence_only(
        self, cli_runner: CliRunner, storage: StorageManager
    ) -> None:
        storage.secrets.set(_SECRET_KEY, "SECRETPLAIN")

        result = _invoke_with_mocks(
            cli_runner, ["settings", "get", _SECRET_KEY, "--format", "json"], storage
        )

        assert result.exit_code == 0
        body = json.loads(result.output)
        assert body["has_secret"] is True
        assert "value" not in body
        assert "SECRETPLAIN" not in result.output


class TestSettingsSet:
    def test_set_takes_effect_on_next_invocation(
        self, cli_runner: CliRunner, storage: StorageManager
    ) -> None:
        set_result = _invoke_with_mocks(
            cli_runner, ["settings", "set", _INT_KEY, "9"], storage
        )
        assert set_result.exit_code == 0

        get_result = _invoke_with_mocks(
            cli_runner, ["settings", "get", _INT_KEY, "--format", "json"], storage
        )
        body = json.loads(get_result.output)
        assert body["value"] == 9
        assert body["db_overridden"] is True

    def test_set_bool_coerces_off_to_false(
        self, cli_runner: CliRunner, storage: StorageManager
    ) -> None:
        result = _invoke_with_mocks(
            cli_runner, ["settings", "set", _BOOL_KEY, "off"], storage
        )

        assert result.exit_code == 0
        assert storage.settings.get(_BOOL_KEY) is False

    def test_set_list_splits_on_commas(
        self, cli_runner: CliRunner, storage: StorageManager
    ) -> None:
        result = _invoke_with_mocks(
            cli_runner,
            ["settings", "set", _LIST_KEY, "https://a.example, https://b.example"],
            storage,
        )

        assert result.exit_code == 0
        assert storage.settings.get(_LIST_KEY) == [
            "https://a.example",
            "https://b.example",
        ]

    def test_set_below_min_is_rejected(
        self, cli_runner: CliRunner, storage: StorageManager
    ) -> None:
        result = _invoke_with_mocks(
            cli_runner, ["settings", "set", _INT_KEY, "0"], storage
        )

        assert result.exit_code != 0
        assert "Error" in result.output
        assert ">= 1" in result.output
        assert storage.settings.get(_INT_KEY) is None

    def test_set_rejects_sensitive_key(
        self, cli_runner: CliRunner, storage: StorageManager
    ) -> None:
        result = _invoke_with_mocks(
            cli_runner, ["settings", "set", _SECRET_KEY, "leak"], storage
        )

        assert result.exit_code != 0
        assert "set-secret" in result.output
        assert "leak" not in result.output
        assert storage.settings.get(_SECRET_KEY) is None
        assert storage.secrets.has(_SECRET_KEY) is False

    @pytest.mark.parametrize(
        ("key", "written", "running", "restarts"),
        [
            pytest.param(
                _LIST_KEY,
                "https://a.example",
                str(default_of(_LIST_KEY)[0]),
                True,
                id="restart",
            ),
            pytest.param(_INT_KEY, "9", str(default_of(_INT_KEY)), False, id="live"),
        ],
    )
    def test_set_confirms_the_written_value_not_the_running_one(
        self,
        cli_runner: CliRunner,
        storage: StorageManager,
        key: str,
        written: str,
        running: str,
        restarts: bool,
    ) -> None:
        result = _invoke_with_mocks(
            cli_runner, ["settings", "set", key, written], storage
        )

        assert result.exit_code == 0
        assert written in result.output
        assert running not in result.output
        assert ("restart" in result.output) is restarts

    def test_set_unknown_key_errors(
        self, cli_runner: CliRunner, storage: StorageManager
    ) -> None:
        result = _invoke_with_mocks(
            cli_runner, ["settings", "set", "web.nonsense", "1"], storage
        )

        assert result.exit_code != 0
        assert "Error" in result.output


class TestSettingsReset:
    def test_reset_removes_override(
        self, cli_runner: CliRunner, storage: StorageManager
    ) -> None:
        storage.settings.set(_INT_KEY, 9)

        result = _invoke_with_mocks(
            cli_runner, ["settings", "reset", _INT_KEY], storage
        )

        assert result.exit_code == 0
        assert storage.settings.get(_INT_KEY) is None

    def test_reset_rejects_sensitive_key(
        self, cli_runner: CliRunner, storage: StorageManager
    ) -> None:
        result = _invoke_with_mocks(
            cli_runner, ["settings", "reset", _SECRET_KEY], storage
        )

        assert result.exit_code != 0


class TestSettingsSecrets:
    def test_set_secret_via_env_stores_and_hides_value(
        self,
        cli_runner: CliRunner,
        storage: StorageManager,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("RECOMMENDINATOR_SECRET_VALUE", "env_secret")

        result = _invoke_with_mocks(
            cli_runner, ["settings", "set-secret", _SECRET_KEY], storage
        )

        assert result.exit_code == 0
        assert storage.secrets.has(_SECRET_KEY) is True
        assert "env_secret" not in result.output

    def test_set_secret_via_hidden_prompt(
        self, cli_runner: CliRunner, storage: StorageManager
    ) -> None:
        result = _invoke_with_mocks(
            cli_runner,
            ["settings", "set-secret", _SECRET_KEY],
            storage,
            input_text="prompt_secret\n",
        )

        assert result.exit_code == 0
        assert storage.secrets.has(_SECRET_KEY) is True
        assert "prompt_secret" not in result.output

    def test_set_secret_rejects_non_sensitive_key(
        self, cli_runner: CliRunner, storage: StorageManager
    ) -> None:
        result = _invoke_with_mocks(
            cli_runner,
            ["settings", "set-secret", _INT_KEY],
            storage,
            input_text="x\n",
        )

        assert result.exit_code != 0
        assert storage.secrets.has(_INT_KEY) is False

    def test_clear_secret_removes_it_with_yes_and_no_prompt(
        self, cli_runner: CliRunner, storage: StorageManager
    ) -> None:
        storage.secrets.set(_SECRET_KEY, "SECRETPLAIN")

        result = _invoke_with_mocks(
            cli_runner, ["settings", "clear-secret", _SECRET_KEY, "--yes"], storage
        )

        assert result.exit_code == 0
        assert storage.secrets.has(_SECRET_KEY) is False
        assert "SECRETPLAIN" not in result.output

    @pytest.mark.parametrize("answer", ["n\n", "\n"])
    def test_neither_no_nor_a_bare_enter_clears_the_secret(
        self, cli_runner: CliRunner, storage: StorageManager, answer: str
    ) -> None:
        storage.secrets.set(_SECRET_KEY, "SECRETPLAIN")

        result = _invoke_with_mocks(
            cli_runner,
            ["settings", "clear-secret", _SECRET_KEY],
            storage,
            input_text=answer,
        )

        assert result.exit_code == 0
        assert _SECRET_KEY in result.output
        assert storage.secrets.has(_SECRET_KEY) is True

    def test_clear_secret_reports_when_none_set(
        self, cli_runner: CliRunner, storage: StorageManager
    ) -> None:
        result = _invoke_with_mocks(
            cli_runner, ["settings", "clear-secret", _SECRET_KEY, "--yes"], storage
        )

        assert result.exit_code == 0
        assert "No secret" in result.output


class TestSettingsApply:
    def test_apply_persists_batch_from_stdin(
        self, cli_runner: CliRunner, storage: StorageManager
    ) -> None:
        payload = json.dumps({_INT_KEY: 9, "recommendations.max_count": 30})

        result = _invoke_with_mocks(
            cli_runner,
            ["settings", "apply", "--from-json", "-"],
            storage,
            input_text=payload,
        )

        assert result.exit_code == 0
        assert storage.settings.get(_INT_KEY) == 9
        assert storage.settings.get("recommendations.max_count") == 30

    def test_apply_is_all_or_nothing_on_invalid_key(
        self, cli_runner: CliRunner, storage: StorageManager
    ) -> None:
        payload = json.dumps({_INT_KEY: 9, "recommendations.max_count": 0})

        result = _invoke_with_mocks(
            cli_runner,
            ["settings", "apply", "--from-json", "-"],
            storage,
            input_text=payload,
        )

        assert result.exit_code != 0
        assert "recommendations.max_count" in result.output
        assert ">= 1" in result.output
        assert storage.settings.list() == {}

    def test_apply_rejects_sensitive_key_in_batch(
        self, cli_runner: CliRunner, storage: StorageManager
    ) -> None:
        payload = json.dumps({_SECRET_KEY: "leak", _INT_KEY: 9})

        result = _invoke_with_mocks(
            cli_runner,
            ["settings", "apply", "--from-json", "-"],
            storage,
            input_text=payload,
        )

        assert result.exit_code != 0
        assert "leak" not in result.output
        assert storage.settings.list() == {}
        assert storage.secrets.has(_SECRET_KEY) is False


class TestEveryWriteNamesTheLeavesItCouldNotApply:
    _MIXED = json.dumps({_INT_KEY: 9, _LIST_KEY: ["https://a.example"]})
    _LIVE = json.dumps({_INT_KEY: 9})

    @pytest.mark.parametrize(
        ("args", "payload", "deferred"),
        [
            pytest.param(["reset", _LIST_KEY], None, [_LIST_KEY], id="reset-deferred"),
            pytest.param(["reset", _INT_KEY], None, [], id="reset-live"),
            pytest.param(
                ["apply", "--from-json", "-"], _MIXED, [_LIST_KEY], id="apply-mixed"
            ),
            pytest.param(["apply", "--from-json", "-"], _LIVE, [], id="apply-live"),
        ],
    )
    def test_the_restart_advisory_names_them_and_nothing_else(
        self,
        cli_runner: CliRunner,
        storage: StorageManager,
        args: list[str],
        payload: str | None,
        deferred: list[str],
    ) -> None:
        result = _invoke_with_mocks(
            cli_runner, ["settings", *args], storage, input_text=payload
        )

        assert result.exit_code == 0, result.output
        advisory = "".join(
            line for line in result.output.splitlines() if RESTART_ADVISORY in line
        )
        assert [key for key in (_INT_KEY, _LIST_KEY) if key in advisory] == deferred

    @pytest.mark.parametrize(
        ("args", "payload"),
        [
            pytest.param(["set", _LIST_KEY, "https://a.example"], None, id="set"),
            pytest.param(["apply", "--from-json", "-"], _MIXED, id="apply"),
            pytest.param(["reset", _LIST_KEY], None, id="reset"),
        ],
    )
    def test_the_advisory_stays_out_of_the_json_a_caller_pipes(
        self,
        cli_runner: CliRunner,
        storage: StorageManager,
        args: list[str],
        payload: str | None,
    ) -> None:
        result = _invoke_with_mocks(
            cli_runner,
            ["settings", *args, "--format", "json"],
            storage,
            input_text=payload,
        )

        assert result.exit_code == 0, result.output
        assert json.loads(result.output)["sections"]


class TestSettingsBootSecretMigration:
    def test_boot_migrates_config_secret_and_strips_it(
        self, cli_runner: CliRunner, storage: StorageManager
    ) -> None:
        config = {"enrichment": {"providers": {"tmdb": {"api_key": "tmdb-secret"}}}}

        result = _invoke_with_mocks(
            cli_runner,
            ["settings", "get", _SECRET_KEY, "--format", "json"],
            storage,
            config=config,
        )

        assert result.exit_code == 0
        assert storage.secrets.has(_SECRET_KEY) is True
        providers = config.get("enrichment", {}).get("providers", {})
        assert providers.get("tmdb", {}).get("api_key") is None
        assert "tmdb-secret" not in result.output


class TestMutatingCommandsEmitTheRefreshedView:
    def test_set_emits_the_updated_view(
        self, cli_runner: CliRunner, storage: StorageManager
    ) -> None:
        result = _invoke_with_mocks(
            cli_runner, ["settings", "set", _INT_KEY, "9", "--format", "json"], storage
        )

        assert result.exit_code == 0
        payload = json.loads(result.output)
        assert set(payload.keys()) == {"sections"}
        setting = _find_json(payload, _INT_KEY)
        assert setting["value"] == 9
        assert setting["db_overridden"] is True

    def test_reset_emits_the_updated_view(
        self, cli_runner: CliRunner, storage: StorageManager
    ) -> None:
        storage.settings.set(_INT_KEY, 9)

        result = _invoke_with_mocks(
            cli_runner, ["settings", "reset", _INT_KEY, "--format", "json"], storage
        )

        assert result.exit_code == 0
        setting = _find_json(json.loads(result.output), _INT_KEY)
        assert setting["db_overridden"] is False
        assert setting["value"] == default_of(_INT_KEY)

    def test_json_output_never_carries_a_secret(
        self, cli_runner: CliRunner, storage: StorageManager
    ) -> None:
        storage.secrets.set(_SECRET_KEY, "SECRETPLAIN")

        result = _invoke_with_mocks(
            cli_runner, ["settings", "set", _INT_KEY, "9", "--format", "json"], storage
        )

        assert "SECRETPLAIN" not in result.output
        secret = _find_json(json.loads(result.output), _SECRET_KEY)
        assert secret["has_secret"] is True
        assert "value" not in secret


def _find_json(payload: dict, key: str) -> dict:
    for section in payload["sections"]:
        for setting in section["settings"]:
            if setting["key"] == key:
                return setting
    raise AssertionError(f"{key} not in settings view")
