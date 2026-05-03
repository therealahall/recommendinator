"""Dynamic sync source discovery from config.

Sources are discovered from PluginRegistry - each entry in config['inputs']
must have a ``plugin`` field identifying the plugin type. The config key is
the user-defined source identifier, allowing multiple instances of the same
plugin (e.g. two json_import sources for books and TV shows).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, TypeGuard

from src.ingestion.plugin_base import SourcePlugin
from src.ingestion.registry import get_registry
from src.utils.text import humanize_source_id

if TYPE_CHECKING:
    from src.storage.manager import StorageManager

logger = logging.getLogger(__name__)


@dataclass
class SyncSourceInfo:
    """Info about an available sync source."""

    id: str
    display_name: str
    plugin_display_name: str


@dataclass
class ResolvedInput:
    """A resolved input entry ready for sync.

    Attributes:
        source_id: User-defined name (the YAML key under ``inputs``).
        plugin: The plugin instance that handles this source.
        config: Config dict ready for ``plugin.fetch()`` / ``plugin.validate_config()``,
            with ``_source_id`` injected and ``plugin``/``enabled`` keys stripped.
    """

    source_id: str
    plugin: SourcePlugin
    config: dict[str, Any]


def resolve_inputs(
    config: dict[str, Any],
    storage: StorageManager | None = None,
    user_id: int = 1,
) -> list[ResolvedInput]:
    """Resolve inputs config into (source_id, plugin, config) entries.

    Resolution combines two sources of truth:

    * The ``inputs`` section of the YAML config.
    * The ``source_configs`` table (when *storage* is provided), populated
      when the user clicks "Migrate to DB" in the web UI for a given source.

    For any source_id present in ``source_configs`` the database row is
    authoritative — its plugin, config dict and enabled flag fully replace
    the YAML entry. For source_ids only present in YAML, the YAML entry is
    used as before. Sources may also exist only in the database (the YAML
    entry can be deleted post-migration); they are still resolved.

    Only enabled entries (per whichever side is authoritative) are returned.

    When *storage* is provided, encrypted credentials from the
    ``credentials`` table are merged on top of every plugin's resolved
    config, overriding both YAML and DB-config values for sensitive fields.

    Args:
        config: Full application config (from load_config).
        storage: Optional StorageManager for DB config + credential lookup.
        user_id: User ID for credential lookup (default 1).

    Returns:
        List of ResolvedInput for each enabled, valid source.
    """
    registry = get_registry()
    inputs_config = config.get("inputs", {})

    db_configs: dict[str, dict[str, Any]] = {}
    if storage is not None:
        for db_row in storage.list_source_configs(user_id):
            db_configs[db_row["source_id"]] = {
                "plugin": db_row["plugin"],
                "enabled": db_row["enabled"],
                "config": db_row["config"],
            }

    source_ids = set(inputs_config.keys()) | set(db_configs.keys())
    resolved: list[ResolvedInput] = []

    for source_id in source_ids:
        db_entry = db_configs.get(source_id)
        yaml_entry = inputs_config.get(source_id)

        if db_entry is not None:
            if not db_entry["enabled"]:
                continue
            plugin_name = db_entry["plugin"]
            raw_fields = db_entry["config"]
        else:
            if not isinstance(yaml_entry, dict):
                continue
            if not yaml_entry.get("enabled", False):
                continue
            plugin_name = yaml_entry.get("plugin")
            if not plugin_name:
                logger.warning("Input '%s' has no 'plugin' field, skipping", source_id)
                continue
            raw_fields = {
                key: value
                for key, value in yaml_entry.items()
                if key not in ("plugin", "enabled")
            }

        plugin = registry.get_plugin(plugin_name)
        if plugin is None:
            logger.warning(
                "Input '%s' references unknown plugin '%s', skipping",
                source_id,
                plugin_name,
            )
            continue

        plugin_config = dict(raw_fields)
        plugin_config["_source_id"] = source_id

        # Apply plugin-specific config transformation
        transformed = type(plugin).transform_config(plugin_config)

        # Merge DB credentials (override config-file values for sensitive fields)
        if storage is not None:
            db_creds = storage.get_credentials_for_source(user_id, source_id)
            for cred_key, cred_value in db_creds.items():
                if cred_value:
                    transformed[cred_key] = cred_value

        resolved.append(
            ResolvedInput(
                source_id=source_id,
                plugin=plugin,
                config=transformed,
            )
        )

    return resolved


def get_available_sync_sources(
    config: dict[str, Any],
    storage: StorageManager | None = None,
    user_id: int = 1,
) -> list[SyncSourceInfo]:
    """Get list of sync sources that are enabled in config.

    Returns sources defined in ``config.inputs`` with ``enabled: true``
    that have a registered plugin in the PluginRegistry.

    Args:
        config: Full application config (from load_config)
        storage: Optional StorageManager for DB credential injection.
        user_id: User ID for credential lookup (default 1).

    Returns:
        List of SyncSourceInfo for each enabled source
    """
    resolved = resolve_inputs(config, storage=storage, user_id=user_id)

    return [
        SyncSourceInfo(
            id=entry.source_id,
            display_name=humanize_source_id(entry.source_id),
            plugin_display_name=entry.plugin.display_name,
        )
        for entry in resolved
    ]


def get_sync_handler(
    source_id: str,
    config: dict[str, Any],
    storage: StorageManager | None = None,
    user_id: int = 1,
) -> ResolvedInput | None:
    """Get the resolved input for a source by its user-defined key.

    Args:
        source_id: User-defined source key (e.g. "my_books", "tv_shows").
        config: Full application config.
        storage: Optional StorageManager for DB credential injection.
        user_id: User ID for credential lookup (default 1).

    Returns:
        ResolvedInput or None if not found / not enabled.
    """
    for entry in resolve_inputs(config, storage=storage, user_id=user_id):
        if entry.source_id == source_id:
            return entry
    return None


def validate_source_config(
    source_id: str,
    config: dict[str, Any],
    storage: StorageManager | None = None,
    user_id: int = 1,
) -> list[str]:
    """Validate config for a sync source.

    Args:
        source_id: User-defined source key.
        config: Full application config.
        storage: Optional StorageManager for DB credential injection.
        user_id: User ID for credential lookup (default 1).

    Returns:
        List of error messages (empty if valid).
    """
    resolved = get_sync_handler(source_id, config, storage=storage, user_id=user_id)
    if resolved is None:
        return [f"Unknown or disabled source: {source_id}"]

    return resolved.plugin.validate_config(
        resolved.config, storage=storage, user_id=user_id
    )


# Per-source configuration helpers.
#
# Source-of-truth implementations of the per-source schema / config /
# migrate / update / secret / enabled flows. The web API in ``src.web.api``
# and the CLI ``source`` group both delegate here so the two interfaces
# stay in lockstep.


class SourceConfigError(Exception):
    """A user-recoverable per-source config error.

    Carries a ``kind`` that callers map to an HTTP status / CLI exit code:

    * ``not_found``       — source or field does not exist (404)
    * ``not_migrated``    — operation requires the source to be migrated (404)
    * ``invalid_field``   — payload references an unknown field (400)
    * ``not_sensitive``   — secret operation targeted a non-sensitive field (400)
    * ``sensitive_in_config`` — bulk update attempted to set a secret (400)
    """

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message


def _is_nonempty_secret_value(value: Any) -> TypeGuard[str]:
    """Return True when *value* should count as a stored secret.

    Sensitive fields are always strings on the wire. Any other type
    (``False``, ``0``, ``None``) means "no secret set" — checking
    ``str(value).strip()`` would otherwise mis-classify ``False`` as set
    because ``str(False) == "False"``. Acts as a ``TypeGuard`` so callers
    that pass the predicate get ``value`` narrowed to ``str``.
    """
    if not isinstance(value, str):
        return False
    return bool(value.strip())


def field_type_name(field_type: type) -> str:
    """Map a Python type used in ``ConfigField.field_type`` to a UI tag.

    Falls back to ``"str"`` for unknown types and warns so a future
    ``ConfigField(field_type=...)`` extension can't silently render as a
    plain text input.
    """
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
    """Return the plugin instance for *source_id*, or ``None`` if unknown.

    Looks up the plugin name first from the migrated DB row (when storage is
    available), then falls back to the YAML ``inputs`` entry.
    """
    plugin_name: str | None = None

    if storage is not None:
        db_row = storage.get_source_config(user_id, source_id)
        if db_row is not None:
            plugin_name = db_row["plugin"]

    if plugin_name is None and config is not None:
        yaml_entry = config.get("inputs", {}).get(source_id)
        if isinstance(yaml_entry, dict):
            plugin_name = yaml_entry.get("plugin")

    if plugin_name is None:
        return None

    return get_registry().get_plugin(plugin_name)


def _yaml_entry_for(source_id: str, config: dict[str, Any] | None) -> dict[str, Any]:
    if config is None:
        return {}
    entry = config.get("inputs", {}).get(source_id)
    return entry if isinstance(entry, dict) else {}


def build_schema_view(source_id: str, plugin: SourcePlugin) -> dict[str, Any]:
    """Return the schema response shape for *plugin*.

    Matches the ``SourceSchemaResponse`` Pydantic model exactly.
    """
    return {
        "source_id": source_id,
        "plugin": plugin.name,
        "plugin_display_name": plugin.display_name,
        "fields": [
            {
                "name": field.name,
                "field_type": field_type_name(field.field_type),
                "required": field.required,
                "default": field.default,
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
    """Return the current config response shape for *source_id*.

    Matches the ``SourceConfigResponse`` Pydantic model exactly. Sensitive
    field values are never included — only their presence in
    ``secret_status``.
    """
    schema = plugin.get_config_schema()
    sensitive_names = {f.name for f in schema if f.sensitive}
    non_sensitive_names = {f.name for f in schema if not f.sensitive}

    db_row = (
        storage.get_source_config(user_id, source_id) if storage is not None else None
    )
    yaml_entry = _yaml_entry_for(source_id, config)

    if db_row is not None:
        source_values = db_row["config"]
        enabled = db_row["enabled"]
        migrated = True
        migrated_at: str | None = db_row["migrated_at"]
    else:
        source_values = {
            k: v for k, v in yaml_entry.items() if k not in ("plugin", "enabled")
        }
        enabled = bool(yaml_entry.get("enabled", False))
        migrated = False
        migrated_at = None

    field_values = {
        name: source_values[name]
        for name in non_sensitive_names
        if name in source_values
    }

    secret_status: dict[str, bool] = {}
    for name in sensitive_names:
        is_set = False
        if storage is not None and storage.credential_row_exists(
            user_id, source_id, name
        ):
            is_set = True
        elif not migrated and _is_nonempty_secret_value(yaml_entry.get(name)):
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
    }


def migrate_source(
    source_id: str,
    plugin: SourcePlugin,
    config: dict[str, Any] | None,
    storage: StorageManager,
    user_id: int = 1,
) -> dict[str, Any]:
    """Copy the YAML entry for *source_id* into the database (idempotent).

    On first migration sensitive fields move into the encrypted credentials
    table and the rest into ``source_configs``. The YAML entry is left in
    place — once the DB row exists ``resolve_inputs`` treats it as
    authoritative and ignores the YAML side. On a re-call (when a row
    already exists) the function is a no-op and returns the current state.

    Returns a dict matching ``SourceMigrationResponse``.
    """
    schema = plugin.get_config_schema()
    sensitive_names = [f.name for f in schema if f.sensitive]
    non_sensitive_names = [f.name for f in schema if not f.sensitive]

    existing_row = storage.get_source_config(user_id, source_id)
    if existing_row is not None:
        return {
            "source_id": source_id,
            "migrated_at": existing_row["migrated_at"],
            "fields_migrated": sorted(existing_row["config"].keys()),
            "secrets_migrated": sorted(
                name
                for name in sensitive_names
                if storage.credential_row_exists(user_id, source_id, name)
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

    secrets_migrated: list[str] = []
    for name in sensitive_names:
        value = yaml_entry.get(name)
        if not _is_nonempty_secret_value(value):
            continue
        storage.save_credential(user_id, source_id, name, value.strip())
        secrets_migrated.append(name)

    storage.upsert_source_config(
        user_id,
        source_id,
        plugin.name,
        config_to_store,
        enabled=yaml_enabled,
    )

    row = storage.get_source_config(user_id, source_id)
    if row is None:  # extremely unlikely (concurrent delete), but never assume
        raise SourceConfigError(
            "not_migrated",
            "Migration record missing immediately after upsert",
        )
    return {
        "source_id": source_id,
        "migrated_at": row["migrated_at"],
        "fields_migrated": sorted(fields_migrated),
        "secrets_migrated": sorted(secrets_migrated),
    }


def update_source_config_values(
    source_id: str,
    plugin: SourcePlugin,
    storage: StorageManager,
    values: dict[str, Any],
    user_id: int = 1,
) -> None:
    """Apply non-sensitive field updates to a migrated source.

    Raises ``SourceConfigError`` for missing migration, unknown fields, or
    attempts to set a sensitive field through this path.
    """
    db_row = storage.get_source_config(user_id, source_id)
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
    storage.upsert_source_config(
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
    """Encrypt and store a sensitive field's value.

    Raises ``SourceConfigError`` if the field is unknown or non-sensitive.
    """
    schema = {f.name: f for f in plugin.get_config_schema()}
    field = schema.get(key)
    if field is None:
        raise SourceConfigError("not_found", f"Unknown field: {key}")
    if not field.sensitive:
        raise SourceConfigError(
            "not_sensitive",
            f"Field '{key}' is not sensitive — set it via the config API/CLI",
        )
    storage.save_credential(user_id, source_id, key, value)


def clear_source_secret_value(
    source_id: str,
    plugin: SourcePlugin,
    storage: StorageManager,
    key: str,
    user_id: int = 1,
) -> None:
    """Delete the stored secret for a field (no-op if missing).

    Raises ``SourceConfigError`` if *key* is not a sensitive field on the
    plugin's schema, mirroring ``set_source_secret_value`` so the two
    operations refuse the same garbage.
    """
    schema = {f.name: f for f in plugin.get_config_schema()}
    field = schema.get(key)
    if field is None:
        raise SourceConfigError("not_found", f"Unknown field: {key}")
    if not field.sensitive:
        raise SourceConfigError("not_sensitive", f"Field '{key}' is not sensitive")
    storage.delete_credential(user_id, source_id, key)


def set_source_enabled_state(
    source_id: str,
    storage: StorageManager,
    enabled: bool,
    user_id: int = 1,
) -> None:
    """Toggle the enabled flag on an already-migrated source.

    Raises ``SourceConfigError("not_migrated", …)`` if no DB row exists.
    """
    updated = storage.set_source_config_enabled(user_id, source_id, enabled)
    if not updated:
        raise SourceConfigError(
            "not_migrated",
            f"Source '{source_id}' is not migrated to the database",
        )
