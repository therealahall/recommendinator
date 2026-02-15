"""Dynamic sync source discovery from config.

Sources are discovered from PluginRegistry - each entry in config['inputs']
must have a ``plugin`` field identifying the plugin type. The config key is
the user-defined source identifier, allowing multiple instances of the same
plugin (e.g. two json_import sources for books and TV shows).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from src.ingestion.plugin_base import SourcePlugin
from src.ingestion.registry import get_registry

logger = logging.getLogger(__name__)


def _humanize_source_id(source_id: str) -> str:
    """Convert a snake_case source ID to a human-readable title.

    Examples:
        ``finished_tv_shows`` → ``Finished Tv Shows``
        ``my_books`` → ``My Books``
    """
    return source_id.replace("_", " ").title()


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


def resolve_inputs(config: dict[str, Any]) -> list[ResolvedInput]:
    """Resolve inputs config into (source_id, plugin, config) entries.

    Each entry in ``config['inputs']`` must have a ``plugin`` field identifying
    the plugin type.  The config key is the user-defined source identifier.

    Only entries with ``enabled: true`` are returned.

    Args:
        config: Full application config (from load_config).

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
            logger.warning(f"Input '{source_id}' has no 'plugin' field, skipping")
            continue

        plugin = registry.get_plugin(plugin_name)
        if plugin is None:
            logger.warning(
                f"Input '{source_id}' references unknown plugin "
                f"'{plugin_name}', skipping"
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

        resolved.append(
            ResolvedInput(
                source_id=source_id,
                plugin=plugin,
                config=transformed,
            )
        )

    return resolved


def get_available_sync_sources(config: dict[str, Any]) -> list[SyncSourceInfo]:
    """Get list of sync sources that are enabled in config.

    Returns sources defined in ``config.inputs`` with ``enabled: true``
    that have a registered plugin in the PluginRegistry.

    Args:
        config: Full application config (from load_config)

    Returns:
        List of SyncSourceInfo for each enabled source
    """
    resolved = resolve_inputs(config)

    return [
        SyncSourceInfo(
            id=entry.source_id,
            display_name=_humanize_source_id(entry.source_id),
            plugin_display_name=entry.plugin.display_name,
        )
        for entry in resolved
    ]


def get_sync_handler(
    source_id: str,
    config: dict[str, Any],
) -> ResolvedInput | None:
    """Get the resolved input for a source by its user-defined key.

    Args:
        source_id: User-defined source key (e.g. "my_books", "tv_shows").
        config: Full application config.

    Returns:
        ResolvedInput or None if not found / not enabled.
    """
    for entry in resolve_inputs(config):
        if entry.source_id == source_id:
            return entry
    return None


def transform_source_config(source_id: str, config: dict[str, Any]) -> dict[str, Any]:
    """Transform raw YAML config for a source into plugin-ready config.

    Delegates to the plugin's ``transform_config`` classmethod.

    Args:
        source_id: User-defined source key (e.g. "my_books", "tv_shows").
        config: Full application config.

    Returns:
        Transformed config dict.
    """
    resolved = get_sync_handler(source_id, config)
    if resolved is None:
        # Fall back to returning the raw input entry
        inputs_config = config.get("inputs", {})
        return dict(inputs_config.get(source_id, {}))

    return resolved.config


def validate_source_config(source_id: str, config: dict[str, Any]) -> list[str]:
    """Validate config for a sync source.

    Args:
        source_id: User-defined source key.
        config: Full application config.

    Returns:
        List of error messages (empty if valid).
    """
    resolved = get_sync_handler(source_id, config)
    if resolved is None:
        return [f"Unknown or disabled source: {source_id}"]

    return resolved.plugin.validate_config(resolved.config)
