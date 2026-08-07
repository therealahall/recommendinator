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
  non-``restart_required`` leaves it also publishes the new values into the
  passed-in running config (live-apply). Unlike the boot assembly, which owns
  the config it is building and writes it with :func:`set_leaf`, live-apply
  writes a config the engine is reading from another thread, so it goes
  through :func:`set_leaves_atomically`. The two are not interchangeable.
  Swapping this one back for the in-place helper reopens the window described
  on :func:`_apply_live`.
* **Reset** — :func:`reset_setting` drops the DB row and live-applies the const
  default so the running config immediately reflects the reset-to-default.
* **Secrets** — :func:`set_secret` / :func:`clear_secret` gate on the registry's
  ``sensitive`` flag and route through the encrypted global-secret store.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, assert_never
from urllib.parse import urlsplit

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

if TYPE_CHECKING:
    from src.storage.manager import StorageManager

# The CORS allowlist. Its items get an extra, leaf-specific check because the
# rule they must satisfy is CORS semantics rather than anything generic about
# lists — see :func:`_validated_cors_origins`.
_ALLOWED_ORIGINS_KEY = "web.allowed_origins"

# The only schemes a browser puts in an Origin header for a page that could
# reach this app.
_ORIGIN_SCHEMES = frozenset({"http", "https"})


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
    with sections and settings in registry declaration order.
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
    then published into *config* and take effect immediately, each top-level
    section in one store, so a reader cannot rank a request on half of a
    section. ``restart_required`` leaves are persisted only, applying on next
    boot.
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

    _apply_live(
        config,
        [
            (entry.key, coerced)
            for entry, coerced in validated
            if not entry.restart_required
        ],
    )


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
        # default_of, not entry.default: this writes into the running config, so
        # it must not be the registry's own object.
        _apply_live(config, [(key, default_of(key))])


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
    mismatch, an out-of-range number, an over-long/non-matching string, an enum
    value outside ``choices``, or a CORS origin a browser could never send.
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
        if entry.key == _ALLOWED_ORIGINS_KEY:
            return _validated_cors_origins(entry, value)
        return value
    assert_never(setting_type)


def _validated_cors_origins(entry: SettingMetadata, origins: list[str]) -> list[str]:
    """Return the CORS allowlist with each entry trimmed and checked.

    Starlette matches with ``origin in self.allow_origins`` — an exact string
    comparison — so a malformed entry can never match any request and is a
    silently inert allowance the operator believes is working. ``"null"`` is the
    dangerous one: a sandboxed iframe and a ``data:``/``file:`` document both
    send ``Origin: null``, and because ``"null"`` is not ``"*"`` the app keeps
    ``allow_credentials`` on, so allowing it hands any page the user visits full
    read/write of a library that ships no authentication.

    Items are trimmed before checking, and the trimmed list is what gets
    persisted: a pasted origin with a stray space would otherwise validate on
    its trimmed form and then never match.
    """
    trimmed = [origin.strip() for origin in origins]
    for origin in trimmed:
        # The documented allow-all escape hatch. create_app turns
        # allow_credentials off whenever it is present, so it stays supported.
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
        if not _is_browser_origin(origin):
            raise SettingsValidationError(
                entry.key,
                f"{origin!r} is not an origin a browser can send — use "
                'scheme://host[:port] (for example http://localhost:18473), or "*"',
            )
    return trimmed


def _is_browser_origin(origin: str) -> bool:
    """Return True when *origin* has the bare ``scheme://host[:port]`` shape.

    Anything else — a trailing path, a wildcard subdomain, embedded credentials,
    an unparseable port — never appears in an ``Origin`` header, so it could
    only ever sit in the allowlist doing nothing.
    """
    parsed = urlsplit(origin)
    try:
        port_is_usable = parsed.port is None or parsed.port > 0
    except ValueError:
        return False
    return bool(
        port_is_usable
        and parsed.scheme in _ORIGIN_SCHEMES
        and parsed.hostname
        and not parsed.path
        and not parsed.query
        and not parsed.fragment
        and not parsed.username
        and not parsed.password
    )


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
        # Point at the help rather than interpolating the raw regex. This lands
        # in a role="alert" live region, where a screen reader reads the
        # metacharacters aloud as a plausible-but-wrong literal path. Both
        # pattern leaves (logging.file, tmdb.language) carry the required shape
        # AND a worked example in their help text, which is where the user can
        # actually recover from.
        raise SettingsValidationError(
            entry.key,
            "does not match the required format — see this setting's help for examples",
        )


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
    """Read the running value at *entry*'s dotted path, else the const default.

    ``default_of`` rather than ``entry.default``: this value is serialised into
    API/CLI responses, and must never be the registry's own container.
    """
    return get_leaf(config, tuple(entry.key.split(".")), default_of(entry.key))


def _apply_live(config: dict[str, Any], updates: Sequence[tuple[str, Any]]) -> None:
    """Publish *updates* into the running *config* at their dotted paths.

    All of them together, because the engine reads the running config from a
    threadpool worker while this runs on the event loop. Writing them one at a
    time would leave a window where a request reads the first key of a save and
    the baseline for the rest, and writing any of them in place would break the
    engine's iteration of ``recommendations.scorer_weights`` outright, since
    inserting a key into a dict under an iterator raises.
    """
    set_leaves_atomically(
        config, [(tuple(key.split(".")), value) for key, value in updates]
    )


def _require_sensitive(key: str) -> None:
    """Raise unless *key* is a known sensitive registry leaf."""
    entry = get_entry(key)
    if entry is None or not entry.sensitive:
        raise SettingsValidationError(key, "not a configurable secret")
