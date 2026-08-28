"""The config key is the user-defined source identifier, allowing multiple
instances of the same plugin (e.g. two roms sources for two libraries).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any, Literal, TypeGuard

from src.ingestion.paths import PathNotAllowed, resolve_source_path
from src.ingestion.plugin_base import SourcePlugin
from src.ingestion.registry import get_registry
from src.ingestion.schedule import (
    SYNC_INTERVAL_PRESETS,
    next_due,
    resolve_interval,
)
from src.ingestion.urls import CredentialHost, NoOrigin, UrlOrigin, url_origin
from src.models.config_field import ConfigField
from src.models.content import ContentType
from src.utils.dates import parse_iso_timestamp, utc_now
from src.utils.text import humanize_source_id, sanitize_for_log

if TYPE_CHECKING:
    from src.storage.manager import StorageManager
    from src.storage.schema import SourceConfigDict, SyncRunDict

logger = logging.getLogger(__name__)

# Closed set of error kinds. Mirrored by ``_ERROR_KIND_TO_STATUS`` and
# ``_ERROR_KIND_TO_DETAIL`` in ``src.web.api``. Keep in sync — adding a new
# kind without an HTTP mapping will type-check here but silently fall back
# to a generic 400 response on the wire.
SourceConfigErrorKind = Literal[
    "not_found",
    "not_migrated",
    "invalid_field",
    "invalid_values",
    "not_sensitive",
    "sensitive_in_config",
    "conflict",
    "invalid_id",
    "unknown_plugin",
    "credential_move",
]


@dataclass(frozen=True)
class PluginImportFailure:
    module: str
    reason: str


@dataclass(frozen=True)
class PluginNotLoaded:
    """Nothing ties a failure to *plugin*: a module that raised never declared
    the plugin name it would have provided.
    """

    plugin: str
    failures: tuple[PluginImportFailure, ...]


@dataclass
class SyncSourceInfo:
    """``enabled`` follows whichever side is authoritative, the DB row or YAML."""

    id: str
    display_name: str
    plugin_display_name: str
    enabled: bool
    plugin_not_loaded: PluginNotLoaded | None = None
    #: Resolved, never the stored ``None``: a caller must not need the default.
    sync_interval: str = "off"
    last_run_at: str | None = None
    last_run_status: str | None = None
    next_run_at: str | None = None


@dataclass
class ResolvedInput:
    """Config dict ready for ``plugin.fetch()`` / ``plugin.validate_config()``,
    with ``_source_id`` injected and ``plugin``/``enabled`` keys stripped.
    """

    source_id: str
    plugin: SourcePlugin
    config: dict[str, Any]


@dataclass
class ConfiguredSource:
    plugin: SourcePlugin
    enabled: bool
    fields: dict[str, Any]


def _declared_plugin_name(
    db_row: SourceConfigDict | None, yaml_entry: Any
) -> str | None:
    """The plugin *source_id* asks for, whether or not this build can load it."""
    if db_row is not None:
        return db_row["plugin"]
    if not isinstance(yaml_entry, dict):
        return None
    plugin_name = yaml_entry.get("plugin")
    return str(plugin_name) if plugin_name else None


def _plugin_not_loaded(plugin_name: str | None) -> PluginNotLoaded | None:
    if plugin_name is None:
        return None
    registry = get_registry()
    if registry.get_plugin(plugin_name) is not None:
        return None
    failures = registry.get_import_errors()
    if not failures:
        return None
    return PluginNotLoaded(
        plugin=plugin_name,
        failures=tuple(
            PluginImportFailure(module=module, reason=reason)
            for module, reason in sorted(failures.items())
        ),
    )


def _failed_modules(not_loaded: PluginNotLoaded) -> str:
    return "; ".join(
        f"{failure.module}: {failure.reason}" for failure in not_loaded.failures
    )


def _authoritative_source(
    source_id: str,
    db_row: SourceConfigDict | None,
    yaml_entry: Any,
) -> ConfiguredSource | None:
    plugin_name = _declared_plugin_name(db_row, yaml_entry)
    if plugin_name is None:
        # A DB row always carries a plugin, so only a YAML entry reaches here,
        # and only a malformed one is worth a word.
        if isinstance(yaml_entry, dict):
            logger.warning(
                "Input '%s' has no 'plugin' field, skipping",
                sanitize_for_log(source_id),
            )
        return None

    if db_row is not None:
        enabled = db_row["enabled"]
        fields = db_row["config"]
    else:
        enabled = bool(yaml_entry.get("enabled", False))
        fields = {
            key: value
            for key, value in yaml_entry.items()
            if key not in ("plugin", "enabled")
        }

    plugin = get_registry().get_plugin(plugin_name)
    if plugin is None:
        not_loaded = _plugin_not_loaded(plugin_name)
        logger.warning(
            "Input '%s' cannot use plugin '%s': %s",
            sanitize_for_log(source_id),
            sanitize_for_log(plugin_name),
            (
                sanitize_for_log(_failed_modules(not_loaded))
                if not_loaded
                else "no such plugin"
            ),
        )
        return None

    return ConfiguredSource(plugin=plugin, enabled=enabled, fields=fields)


def _configured_source(
    source_id: str,
    config: dict[str, Any] | None,
    storage: StorageManager | None,
    user_id: int,
) -> ConfiguredSource | None:
    db_row = storage.sources.get(user_id, source_id) if storage is not None else None
    return _authoritative_source(
        source_id, db_row, (config or {}).get("inputs", {}).get(source_id)
    )


def resolve_inputs(
    config: dict[str, Any],
    storage: StorageManager | None = None,
    user_id: int = 1,
) -> list[ResolvedInput]:
    """For any source_id present in ``source_configs`` the database row is
    authoritative — its plugin, config dict and enabled flag fully replace
    the YAML entry.
    """
    inputs_config = config.get("inputs", {})

    db_configs: dict[str, SourceConfigDict] = {}
    if storage is not None:
        for db_row in storage.sources.list(user_id):
            db_configs[db_row["source_id"]] = db_row

    resolved: list[ResolvedInput] = []

    for source_id in sorted(set(inputs_config.keys()) | set(db_configs.keys())):
        source = _authoritative_source(
            source_id, db_configs.get(source_id), inputs_config.get(source_id)
        )
        if source is None or not source.enabled:
            continue

        resolved.append(
            ResolvedInput(
                source_id=source_id,
                plugin=source.plugin,
                config=assemble_plugin_config(
                    source_id, source.plugin, source.fields, storage, user_id
                ),
            )
        )

    return resolved


def enrichment_content_type(resolved: list[ResolvedInput]) -> ContentType | None:
    """Shared so the web's auto-start and ``update`` narrow a run alike."""
    # Anything but one source enriches every type: nothing narrows a mixed run.
    if len(resolved) != 1:
        return None
    # str() at the read, not at the log call: config.yaml can put anything
    # here, and ContentType refuses a non-member either way.
    raw_content_type = resolved[0].config.get("content_type")
    content_type_str = str(raw_content_type) if raw_content_type else ""
    if not content_type_str:
        return None
    try:
        return ContentType(content_type_str)
    except ValueError:
        logger.warning(
            "Invalid content_type '%s' for source %s, enriching all types",
            sanitize_for_log(content_type_str),
            sanitize_for_log(resolved[0].source_id),
        )
        return None


def configured_source_plugins(
    config: dict[str, Any],
    storage: StorageManager,
    user_id: int,
) -> dict[str, str]:
    """A disabled source still owns items and still makes its plugin ambiguous,
    so leaving it out would let a sibling claim its rows.
    """
    sources: dict[str, str] = {}
    inputs = config.get("inputs")
    if isinstance(inputs, dict):
        for source_id, entry in inputs.items():
            if isinstance(entry, dict) and entry.get("plugin"):
                sources[str(source_id)] = str(entry["plugin"])
    # A DB row wins over YAML for the same id, as ``resolve_inputs`` has it.
    for row in storage.sources.list(user_id):
        sources[row["source_id"]] = row["plugin"]
    return sources


def _plugin_config_without_credentials(
    source_id: str, plugin: SourcePlugin, fields: dict[str, Any]
) -> dict[str, Any]:
    return type(plugin).transform_config({**fields, "_source_id": source_id})


def redact_credentials(text: str, plugin: SourcePlugin, config: dict[str, Any]) -> str:
    """A derived form (truncated, masked, encoded) still gets through."""
    for field in plugin.get_config_schema():
        if not field.sensitive:
            continue
        secret = config.get(field.name)
        if isinstance(secret, str) and secret:
            text = text.replace(secret, "[redacted]")
    return text


def assemble_plugin_config(
    source_id: str,
    plugin: SourcePlugin,
    fields: dict[str, Any],
    storage: StorageManager | None,
    user_id: int = 1,
) -> dict[str, Any]:
    """Stored credentials go on last, overriding the field values, so validation
    judges the config the sync would really run.
    """
    assembled = _plugin_config_without_credentials(source_id, plugin, fields)
    if storage is not None:
        for key, value in storage.credentials.get_for_source(
            user_id, source_id
        ).items():
            if value:
                assembled[key] = value
    return assembled


def resolve_source_interval(row: SourceConfigDict | None, plugin: SourcePlugin) -> str:
    """A cadence is a migrated source's property: nothing else auto-syncs."""
    return "off" if row is None else resolve_interval(row["sync_interval"], plugin)


@dataclass(frozen=True)
class ScheduleState:
    interval: str
    last_finished_at: datetime | None
    failures: int


def schedule_state(
    storage: StorageManager | None,
    user_id: int,
    source_id: str,
    row: SourceConfigDict | None,
    plugin: SourcePlugin,
    latest_run: SyncRunDict | None,
) -> ScheduleState:
    return ScheduleState(
        interval=resolve_source_interval(row, plugin),
        last_finished_at=(
            parse_iso_timestamp(latest_run["finished_at"])
            if latest_run is not None
            else None
        ),
        failures=(
            storage.sync_runs.consecutive_failures(user_id, source_id)
            if storage is not None
            and latest_run is not None
            and latest_run["status"] != "completed"
            else 0
        ),
    )


def get_available_sync_sources(
    config: dict[str, Any],
    storage: StorageManager | None = None,
    user_id: int = 1,
) -> list[SyncSourceInfo]:
    """``resolve_inputs`` is still the gate for sync execution — it continues
    to filter out disabled and unknown-plugin entries.
    """
    inputs_config = config.get("inputs", {})

    db_configs: dict[str, SourceConfigDict] = {}
    latest_runs: dict[str, SyncRunDict] = {}
    if storage is not None:
        for db_row in storage.sources.list(user_id):
            db_configs[db_row["source_id"]] = db_row
        # One read for the whole listing, not a query per source.
        latest_runs = storage.sync_runs.latest_per_source(user_id)

    sources: list[SyncSourceInfo] = []
    now = utc_now()
    for source_id in sorted(set(inputs_config.keys()) | set(db_configs.keys())):
        configured_row = db_configs.get(source_id)
        yaml_entry = inputs_config.get(source_id)
        source = _authoritative_source(source_id, configured_row, yaml_entry)
        if source is None:
            not_loaded = _plugin_not_loaded(
                _declared_plugin_name(configured_row, yaml_entry)
            )
            if not_loaded is None:
                continue
            sources.append(
                SyncSourceInfo(
                    id=source_id,
                    display_name=humanize_source_id(source_id),
                    # The plugin's own display name died with its module.
                    plugin_display_name=not_loaded.plugin,
                    enabled=False,
                    plugin_not_loaded=not_loaded,
                )
            )
            continue

        latest_run = latest_runs.get(source_id)
        state = schedule_state(
            storage, user_id, source_id, configured_row, source.plugin, latest_run
        )
        # ``resolve_inputs`` drops a disabled source, so its next run never comes.
        due = (
            next_due(now, state.last_finished_at, state.interval, state.failures)
            if source.enabled
            else None
        )
        sources.append(
            SyncSourceInfo(
                id=source_id,
                display_name=humanize_source_id(source_id),
                plugin_display_name=source.plugin.display_name,
                enabled=source.enabled,
                sync_interval=state.interval,
                last_run_at=(
                    latest_run["finished_at"] if latest_run is not None else None
                ),
                last_run_status=(
                    latest_run["status"] if latest_run is not None else None
                ),
                next_run_at=due.isoformat() if due is not None else None,
            )
        )
    return sources


def source_plugin_not_loaded(
    source_id: str,
    config: dict[str, Any] | None,
    storage: StorageManager | None = None,
    user_id: int = 1,
) -> PluginNotLoaded | None:
    db_row = storage.sources.get(user_id, source_id) if storage is not None else None
    yaml_entry = (config or {}).get("inputs", {}).get(source_id)
    return _plugin_not_loaded(_declared_plugin_name(db_row, yaml_entry))


def unusable_detail(not_loaded: PluginNotLoaded) -> str:
    """Said the same way wherever a source is refused for a plugin that died."""
    return (
        f"Plugin '{not_loaded.plugin}' is not loaded. "
        f"Modules that failed to import: {_failed_modules(not_loaded)}"
    )


def resolve_input_for_plugin(
    source_id: str,
    plugin_name: str,
    config: dict[str, Any] | None,
    storage: StorageManager | None = None,
    user_id: int = 1,
) -> ResolvedInput | None:
    """A client-supplied id is a credential key: unchecked, a GOG exchange files
    its token where Trakt reads one.
    """
    source = _configured_source(source_id, config, storage, user_id)
    if source is None or source.plugin.name != plugin_name:
        return None
    if not source.enabled:
        return None
    return ResolvedInput(
        source_id=source_id,
        plugin=source.plugin,
        config=assemble_plugin_config(
            source_id, source.plugin, source.fields, storage, user_id
        ),
    )


class SourceConfigError(Exception):
    """The message names the offending field, or repeats a containment
    refusal; it never carries the plugin's own words.
    """

    def __init__(self, kind: SourceConfigErrorKind, message: str) -> None:
        super().__init__(message)
        self.kind: SourceConfigErrorKind = kind
        self.message = message


def is_nonempty_secret_value(value: Any) -> TypeGuard[str]:
    """Sensitive fields are always strings on the wire."""
    if not isinstance(value, str):
        return False
    return bool(value.strip())


def field_type_name(field_type: type) -> str:
    if field_type is bool:
        return "bool"
    if field_type is int:
        return "int"
    if field_type is float:
        return "float"
    if field_type is list:
        return "list"
    if field_type is not str:
        logger.warning(
            "Unknown ConfigField.field_type=%s — falling back to 'str'", field_type
        )
    return "str"


def resolve_source_plugin(
    source_id: str,
    config: dict[str, Any] | None,
    storage: StorageManager | None,
    user_id: int = 1,
) -> SourcePlugin | None:
    """The enabled flag is not consulted."""
    source = _configured_source(source_id, config, storage, user_id)
    return source.plugin if source is not None else None


def _yaml_entry_for(source_id: str, config: dict[str, Any] | None) -> dict[str, Any]:
    if config is None:
        return {}
    entry = config.get("inputs", {}).get(source_id)
    return entry if isinstance(entry, dict) else {}


def build_sources_view(sources: list[SyncSourceInfo]) -> list[dict[str, Any]]:
    """Both CLI spellings of the listing (``source list`` and
    ``update --source list``) serialise through here.
    """
    return [
        {
            "id": entry.id,
            "display_name": entry.display_name,
            "plugin_display_name": entry.plugin_display_name,
            "enabled": entry.enabled,
            "plugin_not_loaded": (
                {
                    "plugin": entry.plugin_not_loaded.plugin,
                    "failures": [
                        {"module": failure.module, "reason": failure.reason}
                        for failure in entry.plugin_not_loaded.failures
                    ],
                }
                if entry.plugin_not_loaded is not None
                else None
            ),
            "sync_interval": entry.sync_interval,
            "last_run_at": entry.last_run_at,
            "last_run_status": entry.last_run_status,
            "next_run_at": entry.next_run_at,
        }
        for entry in sources
    ]


def build_runs_view(runs: list[SyncRunDict]) -> list[dict[str, Any]]:
    """The row id is dropped: nothing addresses a run on its own."""
    return [
        {
            "source_id": run["source_id"],
            "started_at": run["started_at"],
            "finished_at": run["finished_at"],
            "status": run["status"],
            "items_added": run["items_added"],
            "items_updated": run["items_updated"],
            "items_unchanged": run["items_unchanged"],
            "total_items": run["total_items"],
            "errors": run["errors"],
            "omitted_errors": run["omitted_errors"],
        }
        for run in runs
    ]


def build_schema_view(source_id: str, plugin: SourcePlugin) -> dict[str, Any]:
    return {
        "source_id": source_id,
        "plugin": plugin.name,
        "plugin_display_name": plugin.display_name,
        # The cadence select's options, so no interface retypes the preset list.
        "sync_intervals": [
            {"key": preset.key, "label": preset.label}
            for preset in SYNC_INTERVAL_PRESETS
        ],
        "fields": [
            {
                "name": field.name,
                "field_type": field_type_name(field.field_type),
                "required": field.required,
                "default": None if field.sensitive else field.default,
                "description": field.description,
                "sensitive": field.sensitive,
            }
            for field in plugin.get_config_schema()
        ],
    }


def build_config_view(
    source_id: str,
    plugin: SourcePlugin,
    config: dict[str, Any] | None,
    storage: StorageManager | None,
    user_id: int = 1,
) -> dict[str, Any]:
    """Sensitive field values are never included — only their presence in
    ``secret_status``.
    """
    schema = plugin.get_config_schema()
    sensitive_names = {f.name for f in schema if f.sensitive}
    non_sensitive_names = {f.name for f in schema if not f.sensitive}

    db_row = storage.sources.get(user_id, source_id) if storage is not None else None
    yaml_entry = _yaml_entry_for(source_id, config)

    migrated = db_row is not None
    migrated_at: str | None = db_row["migrated_at"] if db_row is not None else None
    # The caller resolved *plugin* through this same rule, so ``None`` here is
    # unreachable; the fallback keeps the response shape rather than raising.
    source = _authoritative_source(source_id, db_row, yaml_entry)
    source_values = source.fields if source is not None else {}
    enabled = source is not None and source.enabled

    field_values = {
        name: source_values[name]
        for name in non_sensitive_names
        if name in source_values
    }

    secret_status: dict[str, bool] = {}
    for name in sensitive_names:
        is_set = False
        if storage is not None and storage.credentials.exists(user_id, source_id, name):
            is_set = True
        elif not migrated and is_nonempty_secret_value(yaml_entry.get(name)):
            is_set = True
        secret_status[name] = is_set

    return {
        "source_id": source_id,
        "plugin": plugin.name,
        "plugin_display_name": plugin.display_name,
        "enabled": enabled,
        "migrated": migrated,
        "migrated_at": migrated_at,
        "field_values": field_values,
        "secret_status": secret_status,
        "sync_interval": resolve_source_interval(db_row, plugin),
    }


def _secret_names_with_a_stored_row(
    source_id: str,
    sensitive_names: list[str],
    storage: StorageManager,
    user_id: int,
) -> list[str]:
    return sorted(
        name
        for name in sensitive_names
        if storage.credentials.exists(user_id, source_id, name)
    )


def migrate_source(
    source_id: str,
    plugin: SourcePlugin,
    config: dict[str, Any] | None,
    storage: StorageManager,
    user_id: int = 1,
) -> dict[str, Any]:
    """The YAML entry is left in place — once the DB row exists
    ``resolve_inputs`` treats it as authoritative and ignores the YAML side.
    """
    schema = plugin.get_config_schema()
    sensitive_names = [f.name for f in schema if f.sensitive]
    non_sensitive_names = [f.name for f in schema if not f.sensitive]

    existing_row = storage.sources.get(user_id, source_id)
    if existing_row is not None:
        return {
            "source_id": source_id,
            "migrated_at": existing_row["migrated_at"],
            "fields_migrated": sorted(existing_row["config"].keys()),
            "secrets_migrated": _secret_names_with_a_stored_row(
                source_id, sensitive_names, storage, user_id
            ),
        }

    yaml_entry = _yaml_entry_for(source_id, config)
    yaml_enabled = bool(yaml_entry.get("enabled", False))

    fields_migrated: list[str] = []
    config_to_store: dict[str, Any] = {}
    for name in non_sensitive_names:
        if name in yaml_entry:
            config_to_store[name] = yaml_entry[name]
            fields_migrated.append(name)

    for name in sensitive_names:
        value = yaml_entry.get(name)
        if is_nonempty_secret_value(value):
            storage.credentials.save(user_id, source_id, name, value.strip())

    storage.sources.upsert(
        user_id,
        source_id,
        plugin.name,
        config_to_store,
        enabled=yaml_enabled,
    )

    row = storage.sources.get(user_id, source_id)
    if row is None:  # extremely unlikely (concurrent delete), but never assume
        raise SourceConfigError(
            "not_migrated",
            "Migration record missing immediately after upsert",
        )
    return {
        "source_id": source_id,
        "migrated_at": row["migrated_at"],
        "fields_migrated": sorted(fields_migrated),
        "secrets_migrated": _secret_names_with_a_stored_row(
            source_id, sensitive_names, storage, user_id
        ),
    }


def _credential_host(value: Any) -> CredentialHost | NoOrigin:
    origin = url_origin(value) if isinstance(value, str) else NoOrigin.ADDRESSES_NOBODY
    return origin.credential_host if isinstance(origin, UrlOrigin) else origin


def _moves_the_credentials_elsewhere(before: Any, after: Any) -> bool:
    before_host, after_host = _credential_host(before), _credential_host(after)
    if NoOrigin.UNREADABLE in (before_host, after_host):
        return True
    # One side names nobody — a source that has sent nothing anywhere, or one
    # that no longer can. Either way no secret is handed on.
    if NoOrigin.ADDRESSES_NOBODY in (before_host, after_host):
        return False
    return before_host != after_host


def _fields_moving_the_credentials(
    schema: dict[str, ConfigField],
    stored: dict[str, Any],
    values: dict[str, Any],
) -> list[str]:
    return sorted(
        key
        for key, value in values.items()
        if schema[key].credential_bound
        and _moves_the_credentials_elsewhere(
            stored.get(key, schema[key].default), value
        )
    )


# The plugin's own words say which path it looked for and whether it was
# there, so the caller gets this instead and the log gets the reason.
SOURCE_MISCONFIGURED_DETAIL = "Source is not properly configured — check its settings."


def misconfigured_detail(plugin: SourcePlugin, errors: list[str]) -> str:
    """Name the settings a refusal is about, not the plugin's own words."""
    reason = " ".join(errors).lower()
    named = [
        field.name
        for field in plugin.get_config_schema()
        if re.search(rf"\b{re.escape(field.name.lower())}\b", reason)
    ]
    if not named:
        return SOURCE_MISCONFIGURED_DETAIL
    quoted = ", ".join(f"'{name}'" for name in named)
    if len(named) == 1:
        return f"Source is not properly configured — check its {quoted} setting."
    return f"Source is not properly configured — check these: {quoted}."


def _quoted(names: list[str]) -> str:
    return ", ".join(f"'{name}'" for name in names)


def _invalid_values_detail(fields: list[str]) -> str:
    """The refusal the caller gets: which field, never the plugin's reason."""
    named = _quoted(fields)
    if len(fields) == 1:
        return f"The value for {named} was refused — check it and try again."
    return f"One of these values was refused — check them and try again: {named}."


def _credential_move_detail(fields: list[str], secrets: list[str]) -> str:
    return (
        f"Changing {_quoted(fields)} points this source at a different host. "
        f"Clear its stored {_quoted(secrets)} first, then save this change and "
        "enter the credential the new host expects."
    )


def _log_refusal(
    source_id: str,
    plugin: SourcePlugin,
    storage: StorageManager,
    errors: list[str],
    user_id: int,
) -> None:
    """Put the reason where only the operator reads it, as the sync door does."""
    logger.warning(
        "Source config write refused for %s: %s",
        sanitize_for_log(source_id),
        sanitize_for_log(
            redact_credentials(
                " ".join(errors),
                plugin,
                storage.credentials.get_for_source(user_id, source_id),
            )
        ),
    )


def _changed_fields(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    return sorted(
        key for key in set(before) | set(after) if before.get(key) != after.get(key)
    )


def _with_field_reverted(
    before: dict[str, Any], after: dict[str, Any], key: str
) -> dict[str, Any]:
    reverted = dict(after)
    if key in before:
        reverted[key] = before[key]
    else:
        reverted.pop(key, None)
    return reverted


def _submitted_paths(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [entry for entry in value if isinstance(entry, str)]
    return []


def _refuse_paths_outside_the_allowed_roots(
    plugin: SourcePlugin, before: dict[str, Any], after: dict[str, Any]
) -> None:
    """Refuse a containment breach here, before any plugin has spoken."""
    schema = {field.name: field for field in plugin.get_config_schema()}
    for key in _changed_fields(before, after):
        field = schema.get(key)
        if field is None or not field.reads_path:
            continue
        for path in _submitted_paths(after.get(key)):
            try:
                resolve_source_path(path)
            except PathNotAllowed as error:
                raise SourceConfigError("invalid_values", str(error)) from error


def _refuse_values_that_break_the_source(
    source_id: str,
    plugin: SourcePlugin,
    storage: StorageManager,
    before: dict[str, Any],
    after: dict[str, Any],
    user_id: int,
) -> None:
    """Only what this write broke: a source whose secret is not entered yet is
    incomplete by design, and refusing to edit one would deadlock it.
    """
    _refuse_paths_outside_the_allowed_roots(plugin, before, after)

    # Neither config carries the decrypted secrets: a plugin quoting a value
    # it was handed would answer with one. ``storage`` still answers the
    # is-it-stored question for the plugins that ask.
    def errors_in(fields: dict[str, Any]) -> list[str]:
        return plugin.validate_config(
            _plugin_config_without_credentials(source_id, plugin, fields),
            storage=storage,
            user_id=user_id,
        )

    was = set(errors_in(before))

    def introduced(candidate: dict[str, Any]) -> list[str]:
        return [error for error in errors_in(candidate) if error not in was]

    errors = introduced(after)
    if not errors:
        return

    # Reverting one field at a time asks which edit broke it. Nothing is
    # blamed when two are jointly bad, so the whole write answers for it.
    changed = _changed_fields(before, after)
    blamed = [
        key
        for key in changed
        if not introduced(_with_field_reverted(before, after, key))
    ]
    _log_refusal(source_id, plugin, storage, errors, user_id)
    raise SourceConfigError("invalid_values", _invalid_values_detail(blamed or changed))


def _refuse_to_move_stored_credentials(
    source_id: str,
    schema: dict[str, ConfigField],
    storage: StorageManager,
    stored: dict[str, Any],
    values: dict[str, Any],
    user_id: int,
) -> None:
    """Deleting the secret loses the operator a credential they may not get back,
    and the sync that fails afterwards blames a field they never touched.
    """
    moved = _fields_moving_the_credentials(schema, stored, values)
    if not moved:
        return
    secrets = _secret_names_with_a_stored_row(
        source_id,
        [field.name for field in schema.values() if field.sensitive],
        storage,
        user_id,
    )
    if not secrets:
        return
    raise SourceConfigError("credential_move", _credential_move_detail(moved, secrets))


def update_source_config_values(
    source_id: str,
    plugin: SourcePlugin,
    storage: StorageManager,
    values: dict[str, Any],
    user_id: int = 1,
) -> None:
    db_row = storage.sources.get(user_id, source_id)
    if db_row is None:
        raise SourceConfigError(
            "not_migrated",
            f"Source '{source_id}' is not migrated to the database",
        )

    schema = {f.name: f for f in plugin.get_config_schema()}
    for key in values:
        field = schema.get(key)
        if field is None:
            raise SourceConfigError("invalid_field", f"Unknown field: {key}")
        if field.sensitive:
            raise SourceConfigError(
                "sensitive_in_config",
                f"Field '{key}' is sensitive — set it via the secret API/CLI",
            )

    new_config = {**db_row["config"], **values}
    _refuse_values_that_break_the_source(
        source_id, plugin, storage, db_row["config"], new_config, user_id
    )
    _refuse_to_move_stored_credentials(
        source_id, schema, storage, db_row["config"], values, user_id
    )

    storage.sources.upsert(
        user_id, source_id, plugin.name, new_config, enabled=db_row["enabled"]
    )


def set_source_secret_value(
    source_id: str,
    plugin: SourcePlugin,
    storage: StorageManager,
    key: str,
    value: str,
    user_id: int = 1,
) -> None:
    schema = {f.name: f for f in plugin.get_config_schema()}
    field = schema.get(key)
    if field is None:
        raise SourceConfigError("not_found", f"Unknown field: {key}")
    if not field.sensitive:
        raise SourceConfigError(
            "not_sensitive",
            f"Field '{key}' is not sensitive — set it via the config API/CLI",
        )
    storage.credentials.save(user_id, source_id, key, value)


def clear_source_secret_value(
    source_id: str,
    plugin: SourcePlugin,
    storage: StorageManager,
    key: str,
    user_id: int = 1,
) -> None:
    schema = {f.name: f for f in plugin.get_config_schema()}
    field = schema.get(key)
    if field is None:
        raise SourceConfigError("not_found", f"Unknown field: {key}")
    if not field.sensitive:
        raise SourceConfigError("not_sensitive", f"Field '{key}' is not sensitive")
    storage.credentials.delete(user_id, source_id, key)


def set_source_enabled_state(
    source_id: str,
    storage: StorageManager,
    enabled: bool,
    user_id: int = 1,
) -> None:
    updated = storage.sources.set_enabled(user_id, source_id, enabled)
    if not updated:
        raise SourceConfigError(
            "not_migrated",
            f"Source '{source_id}' is not migrated to the database",
        )


def set_source_schedule(
    source_id: str,
    storage: StorageManager,
    interval: str,
    user_id: int = 1,
) -> None:
    if not storage.sources.set_schedule(user_id, source_id, interval):
        raise SourceConfigError(
            "not_migrated",
            f"Source '{source_id}' is not migrated to the database",
        )


# Safe as a URL parameter and a YAML key: the leading letter keeps an id off a
# numeric YAML key, and the trailing hyphen in the class is a literal, not a
# range.
SOURCE_ID_PATTERN = r"^[a-z][a-z0-9_-]*$"
_SOURCE_ID_RE = re.compile(SOURCE_ID_PATTERN)

#: Said the same way wherever an id is refused, routes and CLI alike.
SOURCE_ID_RULE = (
    "must start with a lowercase letter and contain only lowercase letters, "
    "digits, underscores, and hyphens"
)


def is_valid_source_id(source_id: str) -> bool:
    """``fullmatch``, not the pattern's own anchors: ``$`` matches before a
    trailing newline.
    """
    return _SOURCE_ID_RE.fullmatch(source_id) is not None


def list_available_plugins() -> list[dict[str, Any]]:
    registry = get_registry()
    plugins = []
    for name, plugin in sorted(registry.get_all_plugins().items()):
        plugins.append(
            {
                "name": name,
                "display_name": plugin.display_name,
                "description": plugin.description,
                "content_types": [str(ct.value) for ct in plugin.content_types],
                "requires_api_key": plugin.requires_api_key,
                "requires_network": plugin.requires_network,
                # Sensitive defaults are masked — a stray placeholder credential
                # in a plugin schema would otherwise leak via this endpoint.
                "fields": [
                    {
                        "name": field.name,
                        "field_type": field_type_name(field.field_type),
                        "required": field.required,
                        "default": None if field.sensitive else field.default,
                        "description": field.description,
                        "sensitive": field.sensitive,
                    }
                    for field in plugin.get_config_schema()
                ],
            }
        )
    return plugins


def build_plugins_view() -> dict[str, Any]:
    """The Add-Source picker's whole answer: what loaded, and what did not."""
    return {
        "plugins": list_available_plugins(),
        "import_errors": [
            {"module": module, "reason": reason}
            for module, reason in sorted(get_registry().get_import_errors().items())
        ],
    }


def create_source(
    source_id: str,
    plugin_name: str,
    values: dict[str, Any],
    storage: StorageManager,
    enabled: bool = True,
    user_id: int = 1,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not is_valid_source_id(source_id):
        raise SourceConfigError("invalid_id", f"Source id {SOURCE_ID_RULE}")

    if storage.sources.get(user_id, source_id) is not None:
        raise SourceConfigError("conflict", f"Source '{source_id}' already exists")

    if config is not None:
        yaml_entry = config.get("inputs", {}).get(source_id)
        if isinstance(yaml_entry, dict):
            raise SourceConfigError(
                "conflict",
                f"Source '{source_id}' is already defined in config.yaml — "
                "migrate it instead of recreating it",
            )

    plugin = get_registry().get_plugin(plugin_name)
    if plugin is None:
        raise SourceConfigError("unknown_plugin", f"Unknown plugin: {plugin_name}")

    schema = {f.name: f for f in plugin.get_config_schema()}
    for key in values:
        field = schema.get(key)
        if field is None:
            raise SourceConfigError("invalid_field", f"Unknown field: {key}")
        if field.sensitive:
            raise SourceConfigError(
                "sensitive_in_config",
                f"Field '{key}' is sensitive — set it via the secret API/CLI "
                "after creating the source",
            )

    # Diffed against an empty config, so the fields left for the secret
    # endpoint and a later edit are not required here — only the values this
    # call actually names have to hold up.
    _refuse_values_that_break_the_source(
        source_id, plugin, storage, {}, dict(values), user_id
    )

    # A new source must not inherit a secret an older one left under this id:
    # that secret would go to whatever host these values name.
    storage.credentials.delete_for_source(user_id, source_id)
    storage.sources.upsert(
        user_id, source_id, plugin.name, dict(values), enabled=enabled
    )
    return build_config_view(source_id, plugin, config, storage, user_id=user_id)


def delete_source(
    source_id: str,
    storage: StorageManager,
    config: dict[str, Any],
    user_id: int = 1,
) -> None:
    """Keyed by source id, not by the plugin's current schema: an unregistered
    plugin or a no-longer-sensitive field must not leave a row behind.
    """
    if not is_valid_source_id(source_id):
        raise SourceConfigError("invalid_id", f"Source id {SOURCE_ID_RULE}")

    db_row = storage.sources.get(user_id, source_id)
    if db_row is None:
        raise SourceConfigError(
            "not_migrated",
            f"Source '{source_id}' is not migrated to the database",
        )

    storage.credentials.delete_for_source(user_id, source_id)
    # A namesake source must not inherit this one's runs, or its backoff.
    storage.sync_runs.delete_for_source(user_id, source_id)
    storage.sources.delete(user_id, source_id)

    # Read after the row is gone, so "who is left" reads as it now is. *config*
    # is required rather than defaulted: half a source list would read a
    # YAML-only source as gone and revoke the live token it holds.
    plugin_name = db_row["plugin"]
    sources = configured_source_plugins(config, storage, user_id)
    # A namesake source reads the credential under its own id, and a sibling
    # still on the plugin may have rotated the token — nothing records which.
    if plugin_name in sources or plugin_name in sources.values():
        return

    deleted = storage.credentials.delete_for_source(user_id, plugin_name)
    if deleted:
        logger.info(
            "Deleted %d credential(s) stranded under plugin name '%s': no "
            "configured source uses that plugin any more.",
            deleted,
            sanitize_for_log(plugin_name),
        )
