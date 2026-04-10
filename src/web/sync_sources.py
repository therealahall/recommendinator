"""Dynamic sync source discovery from config.

Sources are discovered from PluginRegistry - each entry in config['inputs']
must have a ``plugin`` field identifying the plugin type. The config key is
the user-defined source identifier, allowing multiple instances of the same
plugin (e.g. two json_import sources for books and TV shows).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

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

    Each entry in ``config['inputs']`` must have a ``plugin`` field identifying
    the plugin type.  The config key is the user-defined source identifier.

    Only entries with ``enabled: true`` are returned.

    When *storage* is provided, DB credentials are merged into each plugin's
    config after ``transform_config``, overriding any config-file values for
    sensitive fields.

    Args:
        config: Full application config (from load_config).
        storage: Optional StorageManager for DB credential injection.
        user_id: User ID for credential lookup (default 1).

    Returns:
        List of ResolvedInput for each enabled, valid source.
    """
    registry = get_registry()
    inputs_config = config.get("inputs", {})
    resolved: list[ResolvedInput] = []

    for source_id, entry in inputs_config.items():
        if not isinstance(entry, dict):
            continue

        if not entry.get("enabled", False):
            continue

        plugin_name = entry.get("plugin")
        if not plugin_name:
            logger.warning("Input '%s' has no 'plugin' field, skipping", source_id)
            continue

        plugin = registry.get_plugin(plugin_name)
        if plugin is None:
            logger.warning(
                "Input '%s' references unknown plugin '%s', skipping",
                source_id,
                plugin_name,
            )
            continue

        # Build the config for the plugin: strip control keys, inject _source_id
        plugin_config = {
            key: value
            for key, value in entry.items()
            if key not in ("plugin", "enabled")
        }
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
