"""Tests for the CLI ``settings`` group.

Mirrors the global-settings web endpoints in ``src/web/api.py`` — the
``--format json`` output shape must match the ``SettingsResponse``/``SettingView``
Pydantic models exactly so the two interfaces stay in lockstep. Business logic
lives in ``src.settings.service`` (shared with the API); these tests exercise
the CLI adapter against a real temp-DB ``StorageManager``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from src.settings.metadata import default_of
from src.storage.manager import StorageManager
from tests.cli.conftest import _invoke_with_mocks

# A representative leaf per behaviour, kept as constants so a registry rename
# fails loudly in one place rather than across every assertion.
_INT_KEY = "recommendations.default_count"  # int, non-restart, min 1
_BOOL_KEY = "enrichment.enabled"  # bool, non-restart
_LIST_KEY = "web.allowed_origins"  # list, restart_required, advanced
_ADVANCED_KEY = "logging.file"  # string, restart_required, advanced
_SECRET_KEY = "enrichment.providers.tmdb.api_key"  # sensitive string


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
        # Advanced infra/security leaves are hidden without --advanced.
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
        # Exact key set for a non-sensitive leaf — matches the web SettingView
        # with response_model_exclude_unset (value/db_overridden, no has_secret).
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
        }
        assert setting["value"] == 5
        assert setting["db_overridden"] is False

    def test_list_json_masks_secret(
        self, cli_runner: CliRunner, storage: StorageManager
    ) -> None:
        storage.set_global_secret(_SECRET_KEY, "SECRETPLAIN")

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
        assert body["value"] == 5
        assert body["db_overridden"] is False

    def test_get_secret_shows_presence_only(
        self, cli_runner: CliRunner, storage: StorageManager
    ) -> None:
        storage.set_global_secret(_SECRET_KEY, "SECRETPLAIN")

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
        """A set value is persisted and overlaid onto config on the next boot.

        This is the user-visible live effect: after ``set``, a fresh ``get``
        (a new process/boot) resolves the DB-overlaid value, not the default.
        """
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
        assert storage.get_setting(_BOOL_KEY) is False

    def test_set_list_splits_on_commas(
        self, cli_runner: CliRunner, storage: StorageManager
    ) -> None:
        result = _invoke_with_mocks(
            cli_runner,
            ["settings", "set", _LIST_KEY, "https://a.example, https://b.example"],
            storage,
        )

        assert result.exit_code == 0
        assert storage.get_setting(_LIST_KEY) == [
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
        assert storage.get_setting(_INT_KEY) is None

    def test_set_rejects_sensitive_key(
        self, cli_runner: CliRunner, storage: StorageManager
    ) -> None:
        result = _invoke_with_mocks(
            cli_runner, ["settings", "set", _SECRET_KEY, "leak"], storage
        )

        assert result.exit_code != 0
        assert "set-secret" in result.output
        assert "leak" not in result.output
        assert storage.get_setting(_SECRET_KEY) is None
        assert storage.has_global_secret(_SECRET_KEY) is False

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
        storage.set_setting(_INT_KEY, 9)

        result = _invoke_with_mocks(
            cli_runner, ["settings", "reset", _INT_KEY], storage
        )

        assert result.exit_code == 0
        assert storage.get_setting(_INT_KEY) is None

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
        assert storage.has_global_secret(_SECRET_KEY) is True
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
        assert storage.has_global_secret(_SECRET_KEY) is True
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
        assert storage.has_global_secret(_INT_KEY) is False

    def test_clear_secret_removes_it(
        self, cli_runner: CliRunner, storage: StorageManager
    ) -> None:
        storage.set_global_secret(_SECRET_KEY, "SECRETPLAIN")

        result = _invoke_with_mocks(
            cli_runner, ["settings", "clear-secret", _SECRET_KEY], storage
        )

        assert result.exit_code == 0
        assert storage.has_global_secret(_SECRET_KEY) is False
        assert "SECRETPLAIN" not in result.output

    def test_clear_secret_reports_when_none_set(
        self, cli_runner: CliRunner, storage: StorageManager
    ) -> None:
        result = _invoke_with_mocks(
            cli_runner, ["settings", "clear-secret", _SECRET_KEY], storage
        )

        assert result.exit_code == 0
        assert "No secret" in result.output


class TestSettingsApply:
    """``settings apply`` — the CLI equivalent of PUT /api/settings (atomic)."""

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
        assert storage.get_setting(_INT_KEY) == 9
        assert storage.get_setting("recommendations.max_count") == 30

    def test_apply_is_all_or_nothing_on_invalid_key(
        self, cli_runner: CliRunner, storage: StorageManager
    ) -> None:
        """One bad value rejects the whole batch — nothing is written."""
        payload = json.dumps({_INT_KEY: 9, "recommendations.max_count": 0})

        result = _invoke_with_mocks(
            cli_runner,
            ["settings", "apply", "--from-json", "-"],
            storage,
            input_text=payload,
        )

        assert result.exit_code != 0
        # The offending key and reason are surfaced.
        assert "recommendations.max_count" in result.output
        assert ">= 1" in result.output
        # All-or-nothing: the valid key in the same batch was not written.
        assert storage.list_settings() == {}

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
        assert storage.list_settings() == {}
        assert storage.has_global_secret(_SECRET_KEY) is False


class TestSettingsBootSecretMigration:
    """CLI group boot sweeps plaintext config secrets into encrypted storage."""

    def test_boot_migrates_config_secret_and_strips_it(
        self, cli_runner: CliRunner, storage: StorageManager
    ) -> None:
        """A YAML provider api_key is encrypted on boot and dropped from config.

        Regression: the ``migrate_config_secrets`` boot hook in the CLI group
        must actually run — asserted end-to-end against a real temp-DB. The
        config dict is mutated in place by the hook, so after invocation the
        plaintext leaf is gone and the encrypted secret is present.
        """
        config = {"enrichment": {"providers": {"tmdb": {"api_key": "tmdb-secret"}}}}

        result = _invoke_with_mocks(
            cli_runner,
            ["settings", "get", _SECRET_KEY, "--format", "json"],
            storage,
            config=config,
        )

        assert result.exit_code == 0
        assert storage.has_global_secret(_SECRET_KEY) is True
        # Stripped from the running config the CLI assembled in place.
        providers = config.get("enrichment", {}).get("providers", {})
        assert providers.get("tmdb", {}).get("api_key") is None
        assert "tmdb-secret" not in result.output


class TestMutatingCommandsEmitTheRefreshedView:
    """Parity: the web mutations return the updated body; the CLI must too.

    ``PUT /api/settings`` and ``DELETE /api/settings/{key}`` both respond with
    the full refreshed ``SettingsResponse``. Without a JSON mode on the CLI's
    equivalents, a scripted caller had to issue a second ``settings list`` to
    reconstruct what the API hands back in one round trip — a real asymmetry,
    not an interface-appropriate difference. The ``source`` group already
    establishes this pattern via the shared ``emit_view``.
    """

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
        # The NEW value, from the same call that wrote it.
        assert setting["value"] == 9
        assert setting["db_overridden"] is True

    def test_reset_emits_the_updated_view(
        self, cli_runner: CliRunner, storage: StorageManager
    ) -> None:
        storage.set_setting(_INT_KEY, 9)

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
        """The refreshed view spans every section, including sensitive leaves."""
        storage.set_global_secret(_SECRET_KEY, "SECRETPLAIN")

        result = _invoke_with_mocks(
            cli_runner, ["settings", "set", _INT_KEY, "9", "--format", "json"], storage
        )

        assert "SECRETPLAIN" not in result.output
        secret = _find_json(json.loads(result.output), _SECRET_KEY)
        assert secret["has_secret"] is True
        assert "value" not in secret


def _find_json(payload: dict, key: str) -> dict:
    """Return the setting view with ``key`` from a grouped settings response."""
    for section in payload["sections"]:
        for setting in section["settings"]:
            if setting["key"] == key:
                return setting
    raise AssertionError(f"{key} not in settings view")
