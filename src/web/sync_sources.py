"""Dynamic sync source discovery from config.

Sources are discovered from PluginRegistry - each entry in config['inputs']
must have a ``plugin`` field identifying the plugin type. The config key is
the user-defined source identifier, allowing multiple instances of the same
plugin (e.g. two json_import sources for books and TV shows).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, TypeGuard

from src.ingestion.paths import PathNotAllowed, resolve_source_path
from src.ingestion.plugin_base import SourcePlugin
from src.ingestion.registry import get_registry
from src.models.config_field import ConfigField
from src.storage.credential_orphans import delete_orphaned_credentials
from src.utils.text import humanize_source_id, sanitize_for_log

if TYPE_CHECKING:
    from src.storage.manager import StorageManager
    from src.storage.schema import SourceConfigDict

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
]


@dataclass
class SyncSourceInfo:
    """Info about a configured sync source.

    ``enabled`` reflects whichever side is authoritative — the DB
    ``source_configs`` row when the source has been migrated, the YAML
    ``inputs.<id>.enabled`` flag otherwise. Listing endpoints surface
    disabled sources too (so the UI can render them in a muted state),
    but ``resolve_inputs`` continues to filter them out for sync
    execution.
    """

    id: str
    display_name: str
    plugin_display_name: str
    enabled: bool


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


@dataclass
class ConfiguredSource:
    """What is configured under a source id, enabled or not.

    Which plugin a source runs and whether it is enabled are separate
    questions, and a caller asking one must not be handed the other.
    """

    plugin: SourcePlugin
    enabled: bool
    fields: dict[str, Any]


def _authoritative_source(
    source_id: str,
    db_row: SourceConfigDict | None,
    yaml_entry: Any,
) -> ConfiguredSource | None:
    """The DB row for *source_id* once migrated, else its YAML ``inputs`` entry.

    ``None`` when neither declares a plugin this build ships.
    """
    plugin_name: str | None
    if db_row is not None:
        plugin_name = db_row["plugin"]
        enabled = db_row["enabled"]
        fields = db_row["config"]
    else:
        if not isinstance(yaml_entry, dict):
            return None
        plugin_name = yaml_entry.get("plugin")
        if not plugin_name:
            logger.warning("Input '%s' has no 'plugin' field, skipping", source_id)
            return None
        enabled = bool(yaml_entry.get("enabled", False))
        fields = {
            key: value
            for key, value in yaml_entry.items()
            if key not in ("plugin", "enabled")
        }

    plugin = get_registry().get_plugin(plugin_name)
    if plugin is None:
        logger.warning(
            "Input '%s' references unknown plugin '%s', skipping",
            source_id,
            plugin_name,
        )
        return None

    return ConfiguredSource(plugin=plugin, enabled=enabled, fields=fields)


def _configured_source(
    source_id: str,
    config: dict[str, Any] | None,
    storage: StorageManager | None,
    user_id: int,
) -> ConfiguredSource | None:
    """``_authoritative_source`` for one id, reading the DB row it needs."""
    db_row = (
        storage.get_source_config(user_id, source_id) if storage is not None else None
    )
    return _authoritative_source(
        source_id, db_row, (config or {}).get("inputs", {}).get(source_id)
    )


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
    inputs_config = config.get("inputs", {})

    db_configs: dict[str, SourceConfigDict] = {}
    if storage is not None:
        for db_row in storage.list_source_configs(user_id):
            db_configs[db_row["source_id"]] = db_row

    source_ids = set(inputs_config.keys()) | set(db_configs.keys())
    resolved: list[ResolvedInput] = []

    for source_id in source_ids:
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


def _plugin_config_without_credentials(
    source_id: str, plugin: SourcePlugin, fields: dict[str, Any]
) -> dict[str, Any]:
    """The plugin's own view of *fields*, with no stored secret layered on."""
    return type(plugin).transform_config({**fields, "_source_id": source_id})


def redact_credentials(text: str, plugin: SourcePlugin, config: dict[str, Any]) -> str:
    """Replace every secret in *config* wherever it appears verbatim in *text*.

    A derived form (truncated, masked, encoded) still gets through. The rule
    plugins are held to is the guarantee; this backstops the obvious breach.
    """
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
    """Build the config a sync validates and then fetches with.

    Stored credentials go on last, overriding the field values, so validation
    judges the config the sync would really run.
    """
    assembled = _plugin_config_without_credentials(source_id, plugin, fields)
    if storage is not None:
        for key, value in storage.get_credentials_for_source(
            user_id, source_id
        ).items():
            if value:
                assembled[key] = value
    return assembled


def get_available_sync_sources(
    config: dict[str, Any],
    storage: StorageManager | None = None,
    user_id: int = 1,
) -> list[SyncSourceInfo]:
    """List every configured sync source, including disabled ones.

    The UI uses this list to render the data accordions and visually
    distinguishes disabled sources via the ``enabled`` flag. ``resolve_inputs``
    is still the gate for sync execution — it continues to filter out
    disabled and unknown-plugin entries.

    Sources may exist in YAML, the database (post-migration), or both.
    DB rows are authoritative when present; YAML provides the bootstrap.

    Args:
        config: Full application config (from load_config).
        storage: Optional StorageManager for DB lookup.
        user_id: User ID for DB lookup (default 1).

    Returns:
        Every known source as a ``SyncSourceInfo`` (with its current
        ``enabled`` flag), sorted by ID.
    """
    inputs_config = config.get("inputs", {})

    db_configs: dict[str, SourceConfigDict] = {}
    if storage is not None:
        for db_row in storage.list_source_configs(user_id):
            db_configs[db_row["source_id"]] = db_row

    sources: list[SyncSourceInfo] = []
    for source_id in sorted(set(inputs_config.keys()) | set(db_configs.keys())):
        source = _authoritative_source(
            source_id, db_configs.get(source_id), inputs_config.get(source_id)
        )
        if source is None:
            continue

        sources.append(
            SyncSourceInfo(
                id=source_id,
                display_name=humanize_source_id(source_id),
                plugin_display_name=source.plugin.display_name,
                enabled=source.enabled,
            )
        )
    return sources


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


def resolve_input_for_plugin(
    source_id: str,
    plugin_name: str,
    config: dict[str, Any] | None,
    storage: StorageManager | None = None,
    user_id: int = 1,
) -> ResolvedInput | None:
    """The enabled source *source_id*, ``None`` unless it runs *plugin_name*.

    A client-supplied id is a credential key: unchecked, a GOG exchange files
    its token where Trakt reads one. Revocation asks ``may_revoke``, a disabled
    source's token being the one to revoke.
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


class SourceConfigError(Exception):
    """A user-recoverable per-source config error.

    Carries a ``kind`` that callers map to an HTTP status / CLI exit code:

    * ``not_found``       — source or field does not exist (404)
    * ``not_migrated``    — operation requires the source to be migrated (404)
    * ``invalid_field``   — payload references an unknown field (400)
    * ``invalid_values``  — the plugin refused the values written (400).
      The message names the offending field, or repeats a containment
      refusal; it never carries the plugin's own words.
    * ``not_sensitive``   — secret operation targeted a non-sensitive field (400)
    * ``sensitive_in_config`` — bulk update attempted to set a secret (400)
    * ``conflict``        — create attempted on an existing source id (409)
    * ``invalid_id``      — source id violates the allowed character set (400)
    * ``unknown_plugin``  — create / migrate referenced an unregistered plugin (400)
    """

    def __init__(self, kind: SourceConfigErrorKind, message: str) -> None:
        super().__init__(message)
        self.kind: SourceConfigErrorKind = kind
        self.message = message


def is_nonempty_secret_value(value: Any) -> TypeGuard[str]:
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

    Reads the migrated DB row first (when storage is available), then falls
    back to the YAML ``inputs`` entry. The enabled flag is not consulted.
    """
    source = _configured_source(source_id, config, storage, user_id)
    return source.plugin if source is not None else None


def _yaml_entry_for(source_id: str, config: dict[str, Any] | None) -> dict[str, Any]:
    if config is None:
        return {}
    entry = config.get("inputs", {}).get(source_id)
    return entry if isinstance(entry, dict) else {}


def build_schema_view(source_id: str, plugin: SourcePlugin) -> dict[str, Any]:
    """Return the schema response shape for *plugin*.

    Matches the ``SourceSchemaResponse`` Pydantic model exactly.

    ``default`` is masked to ``None`` for sensitive fields so a future
    plugin that mistakenly hard-codes a placeholder credential as the
    default never serialises it onto the wire.
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
        if storage is not None and storage.credential_row_exists(
            user_id, source_id, name
        ):
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
        if not is_nonempty_secret_value(value):
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


def _moves_credential_binding(
    schema: dict[str, ConfigField],
    stored: dict[str, Any],
    values: dict[str, Any],
) -> bool:
    """True when *values* changes a field the stored credentials are bound to.

    Compared verbatim, so a cosmetic rewrite counts too: over-clearing costs
    one re-entry, under-clearing sends the secret somewhere new.
    """
    return any(
        schema[key].credential_bound and value != stored.get(key)
        for key, value in values.items()
    )


def _invalid_values_detail(fields: list[str]) -> str:
    """The refusal the caller gets: which field, never the plugin's reason."""
    named = ", ".join(f"'{name}'" for name in fields)
    if len(fields) == 1:
        return f"The value for {named} was refused — check it and try again."
    return f"One of these values was refused — check them and try again: {named}."


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
                storage.get_credentials_for_source(user_id, source_id),
            )
        ),
    )


def _changed_fields(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    """The keys this write gave a new value, in a stable order."""
    return sorted(
        key for key in set(before) | set(after) if before.get(key) != after.get(key)
    )


def _with_field_reverted(
    before: dict[str, Any], after: dict[str, Any], key: str
) -> dict[str, Any]:
    """*after* as it would be had this write left *key* alone."""
    reverted = dict(after)
    if key in before:
        reverted[key] = before[key]
    else:
        reverted.pop(key, None)
    return reverted


def _submitted_paths(value: Any) -> list[str]:
    """The path strings in a ``reads_path`` field, which may hold a list."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [entry for entry in value if isinstance(entry, str)]
    return []


def _refuse_paths_outside_the_allowed_roots(
    plugin: SourcePlugin, before: dict[str, Any], after: dict[str, Any]
) -> None:
    """Refuse a containment breach here, before any plugin has spoken.

    Containment reads no disk, so its reason repeats only what was submitted.
    Past this point the caller gets a field name instead.
    """
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
    """Raise ``invalid_values`` naming the field a validation error blames.

    Only what this write broke: a source whose secret is not entered yet is
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


def update_source_config_values(
    source_id: str,
    plugin: SourcePlugin,
    storage: StorageManager,
    values: dict[str, Any],
    user_id: int = 1,
) -> None:
    """Apply non-sensitive field updates to a migrated source.

    Moving a ``credential_bound`` field first clears the source's stored
    credentials, so repointing one cannot make the next sync hand its secret
    to the new host.

    Raises ``SourceConfigError`` — ``not_migrated``, ``invalid_field``,
    ``invalid_values``, ``sensitive_in_config``.
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
    # Before the clear: validating afterwards would judge a config whose
    # secret this call has just removed.
    _refuse_values_that_break_the_source(
        source_id, plugin, storage, db_row["config"], new_config, user_id
    )

    # Before the write, never after: a failure between the two must leave the
    # secret gone rather than the source repointed with the secret intact.
    if _moves_credential_binding(schema, db_row["config"], values):
        storage.delete_credentials_for_source(user_id, source_id)

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


# Safe as a URL parameter and a YAML key: the leading letter keeps an id off a
# numeric YAML key, and the trailing hyphen in the class is a literal, not a
# range.
SOURCE_ID_PATTERN = r"^[a-z][a-z0-9_-]*$"
_SOURCE_ID_RE = re.compile(SOURCE_ID_PATTERN)


def list_available_plugins() -> list[dict[str, Any]]:
    """Return every registered source plugin's metadata.

    Used by the "Add data source" UI/CLI to populate the plugin picker.
    Includes the same field schema returned by ``build_schema_view`` so
    the frontend can preview required fields before the user commits.
    """
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


def create_source(
    source_id: str,
    plugin_name: str,
    values: dict[str, Any],
    storage: StorageManager,
    enabled: bool = True,
    user_id: int = 1,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create a new DB-backed source.

    Mirrors ``POST /api/sync/sources``. Validates the source_id format,
    rejects collisions with existing DB rows AND existing YAML entries,
    looks up the plugin, validates every key in *values* against the
    plugin schema (and rejects sensitive fields — those go through the
    secret endpoint after creation), then inserts the row.

    Returns the freshly-built ``SourceConfigResponse``-shaped dict.

    Raises ``SourceConfigError`` for any of:
        - ``invalid_id`` — bad source_id format
        - ``conflict`` — the source_id is already in use
        - ``unknown_plugin`` — plugin_name is not registered
        - ``invalid_field`` — values has a key not in the plugin schema
        - ``invalid_values`` — the plugin refused one of the values
        - ``sensitive_in_config`` — values has a sensitive-flagged field
    """
    if not _SOURCE_ID_RE.fullmatch(source_id):
        raise SourceConfigError(
            "invalid_id",
            "Source id must start with a lowercase letter and contain only "
            "lowercase letters, digits, underscores, and hyphens",
        )

    if storage.get_source_config(user_id, source_id) is not None:
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
    storage.delete_credentials_for_source(user_id, source_id)
    storage.upsert_source_config(
        user_id, source_id, plugin.name, dict(values), enabled=enabled
    )
    return build_config_view(source_id, plugin, config, storage, user_id=user_id)


def delete_source(
    source_id: str,
    storage: StorageManager,
    user_id: int = 1,
    config: dict[str, Any] | None = None,
) -> None:
    """Remove a DB-backed source and every credential stored for it.

    Keyed by source id, not by the plugin's current schema: an unregistered
    plugin or a no-longer-sensitive field must not leave a row behind.

    Raises ``SourceConfigError`` — ``invalid_id`` or ``not_migrated``.
    """
    if not _SOURCE_ID_RE.fullmatch(source_id):
        raise SourceConfigError(
            "invalid_id",
            "Source id must start with a lowercase letter and contain only "
            "lowercase letters, digits, underscores, and hyphens",
        )

    db_row = storage.get_source_config(user_id, source_id)
    if db_row is None:
        raise SourceConfigError(
            "not_migrated",
            f"Source '{source_id}' is not migrated to the database",
        )

    storage.delete_credentials_for_source(user_id, source_id)
    storage.delete_source_config(user_id, source_id)

    # Swept after the row is gone, so "who is left" reads as it now is. A
    # caller without *config* sees half the source list, and would read a
    # YAML-only source as gone and its live token as an orphan.
    if config is not None:
        delete_orphaned_credentials(storage, db_row["plugin"], config, user_id)
