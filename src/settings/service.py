from __future__ import annotations

import re
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, assert_never

from src.settings.metadata import (
    SettingMetadata,
    Validation,
    default_of,
    entries_by_section,
    get_entry,
)

# Live-apply addresses the running config by the same nested leaf paths
# ``migrate_config_settings`` overlays DB leaves at, but publishes a whole save
# at once rather than writing in place (see ``_apply_live``).
from src.utils.dotted_path import get_leaf, set_leaves_atomically
from src.utils.urls import is_bare_origin, normalize_origin

if TYPE_CHECKING:
    from src.storage.manager import StorageManager

# The CORS allowlist. Its items get an extra, leaf-specific check because the
# rule they must satisfy is CORS semantics rather than anything generic about
# lists — see :func:`_validated_cors_origins`.
_ALLOWED_ORIGINS_KEY = "web.allowed_origins"


class SettingsValidationError(Exception):
    """``key`` and ``reason`` are safe to surface — neither ever contains a
    secret value.
    """

    def __init__(self, key: str, reason: str) -> None:
        super().__init__(f"{key}: {reason}")
        self.key = key
        self.reason = reason


def build_settings_view(
    config: dict[str, Any], storage: StorageManager
) -> dict[str, Any]:
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
    """Sensitive leaves include only ``has_secret`` and never a plaintext value."""
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
        view["has_secret"] = storage.secrets.has(entry.key)
    else:
        view["value"] = _effective_value(config, entry)
        view["db_overridden"] = storage.settings.get(entry.key) is not None
    return view


def apply_settings(
    config: dict[str, Any], storage: StorageManager, updates: dict[str, Any]
) -> None:
    """All-or-nothing: if any key is unknown, sensitive, or fails validation, a
    :class:`SettingsValidationError` is raised before anything is written, so a
    bad key cannot leave a partial write.
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
        storage.settings.set(entry.key, coerced)

    _apply_live(
        config,
        [
            (entry.key, coerced)
            for entry, coerced in validated
            if not entry.restart_required
        ],
    )


def reset_setting(config: dict[str, Any], storage: StorageManager, key: str) -> None:
    """Deletes the stored leaf so it falls back to the YAML/const layers, and
    live-applies the const default to *config* for non-``restart_required``
    leaves (a full config reload re-derives any YAML value).
    """
    entry = get_entry(key)
    if entry is None:
        raise SettingsValidationError(key, "unknown setting")
    if entry.sensitive:
        raise SettingsValidationError(key, "use the secret endpoint for secrets")
    storage.settings.delete(key)
    if not entry.restart_required:
        # default_of, not entry.default: this writes into the running config, so
        # it must not be the registry's own object.
        _apply_live(config, [(key, default_of(key))])


def set_secret(storage: StorageManager, key: str, value: str) -> None:
    """The value is never persisted in plaintext."""
    _require_sensitive(key)
    storage.secrets.set(key, value)


def clear_secret(storage: StorageManager, key: str) -> bool:
    _require_sensitive(key)
    return storage.secrets.clear(key)


def coerce_and_validate(entry: SettingMetadata, value: Any) -> Any:
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
        if entry.key == _ALLOWED_ORIGINS_KEY:
            return _validated_cors_origins(entry, value)
        return value
    assert_never(setting_type)


def _validated_cors_origins(entry: SettingMetadata, origins: list[str]) -> list[str]:
    """Starlette matches with ``origin in self.allow_origins`` — an exact string
    comparison — so a malformed entry can never match any request and is a
    silently inert allowance the operator believes is working.
    """
    normalized = [normalize_origin(origin) for origin in origins]
    for origin in normalized:
        # The documented allow-all escape hatch. It reaches only the ungated
        # surface: no origin list carries credentials — see create_app.
        if origin == "*":
            continue
        if not origin:
            raise SettingsValidationError(entry.key, "must not contain an empty origin")
        if origin.lower() == "null":
            raise SettingsValidationError(
                entry.key,
                'must not contain "null" — sandboxed iframes and local file '
                "documents send that origin, so allowing it would let any page "
                "read and write your library",
            )
        if not is_bare_origin(origin):
            raise SettingsValidationError(
                entry.key,
                f"{origin!r} is not an origin a browser can send — use "
                'scheme://host[:port] (for example http://localhost:18473), or "*"',
            )
    return normalized


def _coerce_int(entry: SettingMetadata, value: Any) -> int:
    if isinstance(value, bool):
        raise SettingsValidationError(entry.key, "expected an integer")
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    raise SettingsValidationError(entry.key, "expected an integer")


def _check_numeric_bounds(entry: SettingMetadata, value: float) -> None:
    constraints = entry.validation
    if constraints is None:
        return
    if constraints.min is not None and value < constraints.min:
        raise SettingsValidationError(entry.key, f"must be >= {constraints.min}")
    if constraints.max is not None and value > constraints.max:
        raise SettingsValidationError(entry.key, f"must be <= {constraints.max}")


def _check_string_constraints(entry: SettingMetadata, value: str) -> None:
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
        # Point at the help rather than interpolating the raw regex. This lands
        # in a role="alert" live region, where a screen reader reads the
        # metacharacters aloud as a plausible-but-wrong literal path.
        raise SettingsValidationError(
            entry.key,
            "does not match the required format — see this setting's help for examples",
        )


def _validation_view(validation: Validation | None) -> dict[str, Any] | None:
    if validation is None:
        return None
    return {
        "min": validation.min,
        "max": validation.max,
        "max_length": validation.max_length,
        "pattern": validation.pattern,
    }


def _effective_value(config: dict[str, Any], entry: SettingMetadata) -> Any:
    """``default_of`` rather than ``entry.default``: this value is serialised into
    API/CLI responses, and must never be the registry's own container.
    """
    return get_leaf(config, tuple(entry.key.split(".")), default_of(entry.key))


def _apply_live(config: dict[str, Any], updates: Sequence[tuple[str, Any]]) -> None:
    set_leaves_atomically(
        config, [(tuple(key.split(".")), value) for key, value in updates]
    )


def _require_sensitive(key: str) -> None:
    entry = get_entry(key)
    if entry is None or not entry.sensitive:
        raise SettingsValidationError(key, "not a configurable secret")
