"""Framework-agnostic business logic for the global-settings surface.

This module is the single home for reading, validating, and writing the
in-scope global-config leaves described by :mod:`src.settings.metadata`. Both
the FastAPI endpoints (``src.web.api``) and the CLI ``settings`` group call
these functions so the two interfaces stay in lock-step (parity).

Design:

* **View** — :func:`build_settings_view` returns the grouped, JSON-ready shape
  the API/CLI render. Non-sensitive leaves expose their effective value and a
  ``db_overridden`` flag; sensitive leaves expose only ``has_secret`` (never the
  plaintext).
* **Write** — :func:`apply_settings` validates every update up front and only
  then writes, so a single bad key never leaves a partial write. For
  non-``restart_required`` leaves it also mutates the passed-in running config
  in place (live-apply) via the same nested-leaf helpers the boot assembly uses.
* **Reset** — :func:`reset_setting` drops the DB row and live-applies the const
  default so the running config immediately reflects the reset-to-default.
* **Secrets** — :func:`set_secret` / :func:`clear_secret` gate on the registry's
  ``sensitive`` flag and route through the encrypted global-secret store.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any, assert_never

from src.settings.metadata import (
    SettingMetadata,
    Validation,
    entries_by_section,
    get_entry,
)

# Live-apply mutates the running config through the same nested-leaf helpers
# ``migrate_config_settings`` uses to overlay DB leaves.
from src.utils.dotted_path import get_leaf, set_leaf

if TYPE_CHECKING:
    from src.storage.manager import StorageManager


class SettingsValidationError(Exception):
    """A user-recoverable settings error carrying the offending key + reason.

    The API maps this to ``422`` (config updates) or ``400`` (secret gating);
    the CLI maps it to a friendly message. ``key`` and ``reason`` are safe to
    surface — neither ever contains a secret value.
    """

    def __init__(self, key: str, reason: str) -> None:
        super().__init__(f"{key}: {reason}")
        self.key = key
        self.reason = reason


def build_settings_view(
    config: dict[str, Any], storage: StorageManager
) -> dict[str, Any]:
    """Return every in-scope setting grouped by section for the API/CLI.

    The shape is ``{"sections": [{"section": str, "settings": [view, ...]}]}``
    with sections and settings in registry (``example.yaml``) order.
    """
    return {
        "sections": [
            {
                "section": section,
                "settings": [setting_view(entry, config, storage) for entry in entries],
            }
            for section, entries in entries_by_section().items()
        ]
    }


def setting_view(
    entry: SettingMetadata, config: dict[str, Any], storage: StorageManager
) -> dict[str, Any]:
    """Return one setting's metadata plus its value/secret state.

    Non-sensitive leaves include ``value`` (the effective running value) and
    ``db_overridden``. Sensitive leaves include only ``has_secret`` and never a
    plaintext value.
    """
    view: dict[str, Any] = {
        "key": entry.key,
        "section": entry.section,
        "label": entry.label,
        "help": entry.help,
        "type": entry.type,
        "widget": entry.widget,
        "choices": list(entry.choices) if entry.choices is not None else None,
        "validation": _validation_view(entry.validation),
        "advanced": entry.advanced,
        "restart_required": entry.restart_required,
        "sensitive": entry.sensitive,
    }
    if entry.sensitive:
        view["has_secret"] = storage.has_global_secret(entry.key)
    else:
        view["value"] = _effective_value(config, entry)
        view["db_overridden"] = storage.get_setting(entry.key) is not None
    return view


def apply_settings(
    config: dict[str, Any], storage: StorageManager, updates: dict[str, Any]
) -> None:
    """Validate every update, then persist and live-apply them all.

    All-or-nothing: if any key is unknown, sensitive, or fails validation, a
    :class:`SettingsValidationError` is raised before anything is written, so a
    bad key cannot leave a partial write. Non-``restart_required`` leaves are
    also written into *config* in place so the change takes effect immediately;
    ``restart_required`` leaves are persisted only (they apply on next boot).
    """
    validated: list[tuple[SettingMetadata, Any]] = []
    for key, value in updates.items():
        entry = get_entry(key)
        if entry is None:
            raise SettingsValidationError(key, "unknown setting")
        if entry.sensitive:
            raise SettingsValidationError(key, "use the secret endpoint for secrets")
        validated.append((entry, coerce_and_validate(entry, value)))

    for entry, coerced in validated:
        storage.set_setting(entry.key, coerced)
        if not entry.restart_required:
            _apply_live(config, entry.key, coerced)


def reset_setting(config: dict[str, Any], storage: StorageManager, key: str) -> None:
    """Reset a setting to its default by dropping the DB override.

    Deletes the stored leaf so it falls back to the YAML/const layers, and
    live-applies the const default to *config* for non-``restart_required``
    leaves (a full config reload re-derives any YAML value). Raises for an
    unknown or sensitive key.
    """
    entry = get_entry(key)
    if entry is None:
        raise SettingsValidationError(key, "unknown setting")
    if entry.sensitive:
        raise SettingsValidationError(key, "use the secret endpoint for secrets")
    storage.delete_setting(key)
    if not entry.restart_required:
        _apply_live(config, key, entry.default)


def set_secret(storage: StorageManager, key: str, value: str) -> None:
    """Store a sensitive setting's value in the encrypted global-secret store.

    Raises :class:`SettingsValidationError` when *key* is unknown or not marked
    sensitive in the registry. The value is never persisted in plaintext.
    """
    _require_sensitive(key)
    storage.set_global_secret(key, value)


def clear_secret(storage: StorageManager, key: str) -> bool:
    """Delete a sensitive setting's stored secret.

    Returns True when a stored secret was removed. Raises
    :class:`SettingsValidationError` when *key* is unknown or not sensitive.
    """
    _require_sensitive(key)
    return storage.clear_global_secret(key)


def coerce_and_validate(entry: SettingMetadata, value: Any) -> Any:
    """Coerce *value* to *entry*'s type and validate its constraints.

    Returns the coerced value on success. Raises
    :class:`SettingsValidationError` (with the offending key + reason) on a type
    mismatch, an out-of-range number, an over-long/non-matching string, or an
    enum value outside ``choices``.
    """
    setting_type = entry.type
    if setting_type == "bool":
        if not isinstance(value, bool):
            raise SettingsValidationError(entry.key, "expected a boolean")
        return value
    if setting_type == "int":
        coerced_int = _coerce_int(entry, value)
        _check_numeric_bounds(entry, coerced_int)
        return coerced_int
    if setting_type == "float":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise SettingsValidationError(entry.key, "expected a number")
        coerced_float = float(value)
        _check_numeric_bounds(entry, coerced_float)
        return coerced_float
    if setting_type == "enum":
        if not isinstance(value, str) or (
            entry.choices is not None and value not in entry.choices
        ):
            raise SettingsValidationError(
                entry.key, f"must be one of {list(entry.choices or ())}"
            )
        return value
    if setting_type == "string":
        if not isinstance(value, str):
            raise SettingsValidationError(entry.key, "expected a string")
        _check_string_constraints(entry, value)
        return value
    if setting_type == "list":
        if not isinstance(value, list):
            raise SettingsValidationError(entry.key, "expected a list")
        if not all(isinstance(item, str) for item in value):
            raise SettingsValidationError(entry.key, "expected a list of strings")
        return value
    assert_never(setting_type)


def _coerce_int(entry: SettingMetadata, value: Any) -> int:
    """Coerce *value* to int, allowing an integral float (JSON ``5`` or ``5.0``)."""
    if isinstance(value, bool):
        raise SettingsValidationError(entry.key, "expected an integer")
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    raise SettingsValidationError(entry.key, "expected an integer")


def _check_numeric_bounds(entry: SettingMetadata, value: float) -> None:
    """Enforce ``validation.min``/``max`` on a numeric value."""
    constraints = entry.validation
    if constraints is None:
        return
    if constraints.min is not None and value < constraints.min:
        raise SettingsValidationError(entry.key, f"must be >= {constraints.min}")
    if constraints.max is not None and value > constraints.max:
        raise SettingsValidationError(entry.key, f"must be <= {constraints.max}")


def _check_string_constraints(entry: SettingMetadata, value: str) -> None:
    """Enforce ``validation.max_length``/``pattern`` on a string value."""
    constraints = entry.validation
    if constraints is None:
        return
    if constraints.max_length is not None and len(value) > constraints.max_length:
        raise SettingsValidationError(
            entry.key, f"must be at most {constraints.max_length} characters"
        )
    if (
        constraints.pattern is not None
        and re.fullmatch(constraints.pattern, value) is None
    ):
        raise SettingsValidationError(entry.key, "does not match the required pattern")


def _validation_view(validation: Validation | None) -> dict[str, Any] | None:
    """Serialize a :class:`Validation` to a JSON dict, or ``None`` when absent."""
    if validation is None:
        return None
    return {
        "min": validation.min,
        "max": validation.max,
        "max_length": validation.max_length,
        "pattern": validation.pattern,
    }


def _effective_value(config: dict[str, Any], entry: SettingMetadata) -> Any:
    """Read the running value at *entry*'s dotted path, else the const default."""
    return get_leaf(config, tuple(entry.key.split(".")), entry.default)


def _apply_live(config: dict[str, Any], key: str, value: Any) -> None:
    """Write *value* into the running *config* at *key*'s dotted path."""
    set_leaf(config, tuple(key.split(".")), value)


def _require_sensitive(key: str) -> None:
    """Raise unless *key* is a known sensitive registry leaf."""
    entry = get_entry(key)
    if entry is None or not entry.sensitive:
        raise SettingsValidationError(key, "not a configurable secret")
