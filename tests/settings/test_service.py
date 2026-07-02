"""Unit tests for the framework-agnostic settings service layer.

These exercise :mod:`src.settings.service` directly against a real temp-DB
:class:`StorageManager` (no FastAPI): the grouped view shape, effective value
vs. ``db_overridden``, secret masking, per-type coercion/validation, all-or-
nothing writes with live-apply, reset-to-default, and secret gating.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from src.settings.metadata import SettingMetadata, Validation, get_entry
from src.settings.service import (
    SettingsValidationError,
    apply_settings,
    build_settings_view,
    clear_secret,
    coerce_and_validate,
    reset_setting,
    set_secret,
    setting_view,
)
from src.storage.manager import StorageManager

# A representative sensitive leaf and a non-sensitive numeric leaf reused across
# tests. Kept as module constants so a registry rename fails loudly in one place.
_SECRET_KEY = "enrichment.providers.tmdb.api_key"
_INT_KEY = "recommendations.default_count"


@pytest.fixture()
def storage(tmp_path: Path) -> StorageManager:
    return StorageManager(sqlite_path=tmp_path / "settings.db")


@pytest.fixture()
def config() -> dict[str, Any]:
    """A partial running config; missing leaves fall back to registry defaults."""
    return {
        "recommendations": {"default_count": 5, "max_count": 20},
        "web": {"host": "127.0.0.1", "port": 18473},
    }


def _entry(key: str) -> SettingMetadata:
    entry = get_entry(key)
    assert entry is not None
    return entry


def _find(view: dict[str, Any], key: str) -> dict[str, Any]:
    for section in view["sections"]:
        for setting in section["settings"]:
            if setting["key"] == key:
                return setting
    raise AssertionError(f"{key} not in view")


class TestBuildSettingsView:
    def test_grouped_by_section_in_registry_order(
        self, storage: StorageManager, config: dict[str, Any]
    ) -> None:
        view = build_settings_view(config, storage)

        section_names = [section["section"] for section in view["sections"]]
        # First declared section is "features"; each section carries settings.
        assert section_names[0] == "features"
        assert all(section["settings"] for section in view["sections"])

    def test_non_sensitive_setting_carries_metadata_and_value(
        self, storage: StorageManager, config: dict[str, Any]
    ) -> None:
        setting = _find(build_settings_view(config, storage), _INT_KEY)

        assert setting["type"] == "int"
        assert setting["widget"] == "number"
        assert setting["label"] == "Default count"
        assert setting["validation"] == {
            "min": 1,
            "max": None,
            "max_length": None,
            "pattern": None,
        }
        assert setting["value"] == 5
        assert setting["db_overridden"] is False
        assert "has_secret" not in setting

    def test_effective_value_falls_back_to_default(
        self, storage: StorageManager
    ) -> None:
        # Empty config → the leaf is read from the registry default (5).
        setting = _find(build_settings_view({}, storage), _INT_KEY)

        assert setting["value"] == 5

    def test_db_overridden_true_after_explicit_set(
        self, storage: StorageManager, config: dict[str, Any]
    ) -> None:
        storage.set_setting(_INT_KEY, 9)

        setting = _find(build_settings_view(config, storage), _INT_KEY)

        assert setting["db_overridden"] is True

    def test_sensitive_setting_masks_value(
        self, storage: StorageManager, config: dict[str, Any]
    ) -> None:
        setting = _find(build_settings_view(config, storage), _SECRET_KEY)

        assert setting["sensitive"] is True
        assert setting["has_secret"] is False
        assert "value" not in setting
        assert "db_overridden" not in setting

    def test_sensitive_has_secret_true_after_set(
        self, storage: StorageManager, config: dict[str, Any]
    ) -> None:
        storage.set_global_secret(_SECRET_KEY, "tmdb-key")

        setting = setting_view(_entry(_SECRET_KEY), config, storage)

        assert setting["has_secret"] is True
        assert "value" not in setting


class TestCoerceAndValidate:
    def test_bool_accepts_bool_rejects_other(self) -> None:
        entry = _entry("features.ai_enabled")

        assert coerce_and_validate(entry, True) is True
        with pytest.raises(SettingsValidationError):
            coerce_and_validate(entry, "true")
        with pytest.raises(SettingsValidationError):
            coerce_and_validate(entry, 1)

    def test_int_accepts_int_and_integral_float(self) -> None:
        entry = _entry(_INT_KEY)

        assert coerce_and_validate(entry, 3) == 3
        assert coerce_and_validate(entry, 3.0) == 3

    def test_int_rejects_bool_string_and_below_min(self) -> None:
        entry = _entry(_INT_KEY)

        with pytest.raises(SettingsValidationError):
            coerce_and_validate(entry, True)
        with pytest.raises(SettingsValidationError):
            coerce_and_validate(entry, "3")
        with pytest.raises(SettingsValidationError):
            coerce_and_validate(entry, 0)  # violates min=1

    def test_float_accepts_int_and_enforces_bounds(self) -> None:
        entry = _entry("conversation.llm.temperature")  # min 0.0, max 2.0

        assert coerce_and_validate(entry, 1) == 1.0
        assert coerce_and_validate(entry, 1.5) == 1.5
        with pytest.raises(SettingsValidationError):
            coerce_and_validate(entry, 2.5)
        with pytest.raises(SettingsValidationError):
            coerce_and_validate(entry, -0.1)

    def test_numeric_bounds_are_inclusive_at_min_and_max(self) -> None:
        """The exact min and max values are accepted (bounds are inclusive).

        Locks the inclusivity of ``coerce_and_validate``'s ``<``/``>`` checks so
        a future slip to ``<=``/``>=`` (which would reject the boundary) fails
        here. Covers an int leaf (web.port: 1-65535) and a float leaf
        (conversation.llm.temperature: 0.0-2.0).
        """
        port = _entry("web.port")  # int, min 1, max 65535
        assert coerce_and_validate(port, 1) == 1
        assert coerce_and_validate(port, 65535) == 65535

        temperature = _entry("conversation.llm.temperature")  # float, 0.0-2.0
        assert coerce_and_validate(temperature, 0.0) == 0.0
        assert coerce_and_validate(temperature, 2.0) == 2.0

    def test_enum_accepts_choice_rejects_other(self) -> None:
        entry = _entry("ingestion.conflict_strategy")
        valid = entry.choices[0] if entry.choices else ""

        assert coerce_and_validate(entry, valid) == valid
        with pytest.raises(SettingsValidationError):
            coerce_and_validate(entry, "not-a-strategy")

    def test_string_max_length(self) -> None:
        entry = SettingMetadata(
            key="web.host",
            section="web",
            label="Host",
            help="",
            type="string",
            default="",
            widget="text",
            sensitive=False,
            restart_required=False,
            advanced=False,
            validation=Validation(max_length=3),
        )

        assert coerce_and_validate(entry, "abc") == "abc"
        with pytest.raises(SettingsValidationError):
            coerce_and_validate(entry, "abcd")

    def test_string_pattern(self) -> None:
        entry = SettingMetadata(
            key="web.host",
            section="web",
            label="Host",
            help="",
            type="string",
            default="",
            widget="text",
            sensitive=False,
            restart_required=False,
            advanced=False,
            validation=Validation(pattern=r"[a-z]+"),
        )

        assert coerce_and_validate(entry, "abc") == "abc"
        with pytest.raises(SettingsValidationError):
            coerce_and_validate(entry, "ABC")

    def test_list_accepts_list_rejects_scalar(self) -> None:
        entry = _entry("ingestion.source_priority")

        assert coerce_and_validate(entry, ["steam"]) == ["steam"]
        with pytest.raises(SettingsValidationError):
            coerce_and_validate(entry, "steam")

    def test_list_accepts_strings_rejects_non_string_items(self) -> None:
        """A list must contain only strings — a mixed/non-string item is rejected.

        Covers the network-settable list leaves ``ingestion.source_priority``
        and ``web.allowed_origins`` so an injected number/dict can't slip into a
        config a scorer or CORS layer later treats as a string.
        """
        assert coerce_and_validate(
            _entry("web.allowed_origins"), ["http://localhost:18473"]
        ) == ["http://localhost:18473"]

        entry = _entry("ingestion.source_priority")
        with pytest.raises(SettingsValidationError) as exc_info:
            coerce_and_validate(entry, ["steam", 3])
        assert exc_info.value.key == entry.key
        assert "list of strings" in exc_info.value.reason

    def test_logging_file_pattern_rejects_traversal_and_absolute(self) -> None:
        """logging.file only accepts a relative ``logs/*.log`` path.

        The registry pattern blocks an absolute path and a value outside the
        logs/ directory before it can reach a FileHandler over the Settings API.
        """
        entry = _entry("logging.file")

        assert coerce_and_validate(entry, "logs/app.log") == "logs/app.log"
        with pytest.raises(SettingsValidationError):
            coerce_and_validate(entry, "/etc/cron.d/evil")
        with pytest.raises(SettingsValidationError):
            coerce_and_validate(entry, "/var/log/app.log")
        with pytest.raises(SettingsValidationError):
            coerce_and_validate(entry, "secrets/app.txt")

    def test_error_carries_key_and_reason(self) -> None:
        entry = _entry(_INT_KEY)

        with pytest.raises(SettingsValidationError) as exc_info:
            coerce_and_validate(entry, 0)

        assert exc_info.value.key == _INT_KEY
        assert entry.validation is not None
        assert exc_info.value.reason == f"must be >= {entry.validation.min}"


class TestApplySettings:
    def test_persists_and_live_applies_non_restart(
        self, storage: StorageManager, config: dict[str, Any]
    ) -> None:
        apply_settings(config, storage, {_INT_KEY: 9})

        assert storage.get_setting(_INT_KEY) == 9
        assert config["recommendations"]["default_count"] == 9

    def test_restart_required_persists_without_live_apply(
        self, storage: StorageManager, config: dict[str, Any]
    ) -> None:
        apply_settings(config, storage, {"web.port": 9000})

        assert storage.get_setting("web.port") == 9000
        # web.port is restart_required — running config is untouched.
        assert config["web"]["port"] == 18473

    def test_unknown_key_rejected(
        self, storage: StorageManager, config: dict[str, Any]
    ) -> None:
        with pytest.raises(SettingsValidationError):
            apply_settings(config, storage, {"web.nonsense": 1})

    def test_sensitive_key_rejected(
        self, storage: StorageManager, config: dict[str, Any]
    ) -> None:
        with pytest.raises(SettingsValidationError):
            apply_settings(config, storage, {_SECRET_KEY: "leak"})

        assert storage.list_settings() == {}

    def test_all_or_nothing_no_partial_write(
        self, storage: StorageManager, config: dict[str, Any]
    ) -> None:
        # First key valid, second invalid (below min) → nothing is written.
        with pytest.raises(SettingsValidationError):
            apply_settings(
                config,
                storage,
                {_INT_KEY: 9, "recommendations.max_count": 0},
            )

        assert storage.list_settings() == {}
        assert config["recommendations"]["default_count"] == 5


class TestResetSetting:
    def test_reset_deletes_row_and_restores_default(
        self, storage: StorageManager, config: dict[str, Any]
    ) -> None:
        apply_settings(config, storage, {_INT_KEY: 9})

        reset_setting(config, storage, _INT_KEY)

        assert storage.get_setting(_INT_KEY) is None
        # Non-restart leaf is live-applied back to the const default.
        assert config["recommendations"]["default_count"] == 5

    def test_reset_unknown_key_raises(
        self, storage: StorageManager, config: dict[str, Any]
    ) -> None:
        with pytest.raises(SettingsValidationError):
            reset_setting(config, storage, "web.nonsense")

    def test_reset_sensitive_key_raises(
        self, storage: StorageManager, config: dict[str, Any]
    ) -> None:
        with pytest.raises(SettingsValidationError):
            reset_setting(config, storage, _SECRET_KEY)


class TestSecretGating:
    def test_set_secret_stores_encrypted_not_in_settings(
        self, storage: StorageManager
    ) -> None:
        set_secret(storage, _SECRET_KEY, "tmdb-key")

        assert storage.has_global_secret(_SECRET_KEY) is True
        # The secret never lands in the plaintext settings table.
        assert storage.list_settings() == {}

    def test_clear_secret_removes_it(self, storage: StorageManager) -> None:
        set_secret(storage, _SECRET_KEY, "tmdb-key")

        assert clear_secret(storage, _SECRET_KEY) is True
        assert storage.has_global_secret(_SECRET_KEY) is False

    def test_set_secret_rejects_non_sensitive(self, storage: StorageManager) -> None:
        with pytest.raises(SettingsValidationError):
            set_secret(storage, _INT_KEY, "nope")

    def test_clear_secret_rejects_unknown(self, storage: StorageManager) -> None:
        with pytest.raises(SettingsValidationError):
            clear_secret(storage, "web.nonsense")
