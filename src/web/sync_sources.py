"""Dynamic sync source discovery from config.

Sources are discovered from PluginRegistry - any plugin whose config section
has enabled: true is available for sync. No hardcoded source list.
"""

from dataclasses import dataclass
from typing import Any

from src.ingestion.plugin_base import SourcePlugin
from src.ingestion.registry import get_registry


@dataclass
class SyncSourceInfo:
    """Info about an available sync source."""

    id: str
    display_name: str
    description: str


def get_available_sync_sources(config: dict[str, Any]) -> list[SyncSourceInfo]:
    """Get list of sync sources that are enabled in config.

    Only returns sources defined in config.inputs with enabled: true
    that have a registered plugin in the PluginRegistry.

    Args:
        config: Full application config (from load_config)

    Returns:
        List of SyncSourceInfo for each enabled source we can handle
    """
    registry = get_registry()
    enabled_plugins = registry.get_enabled_plugins(config)

    return [
        SyncSourceInfo(
            id=plugin.name,
            display_name=plugin.display_name,
            description=plugin.description,
        )
        for plugin in enabled_plugins
    ]


def get_sync_handler(
    source_id: str,
) -> SourcePlugin | None:
    """Get the plugin for a source.

    Args:
        source_id: Source identifier (e.g. "goodreads", "steam").

    Returns:
        Plugin instance or None if unknown source
    """
    registry = get_registry()
    return registry.get_plugin(source_id)


def transform_source_config(
    source_id: str, source_config: dict[str, Any]
) -> dict[str, Any]:
    """Transform raw YAML config for a source into plugin-ready config.

    Delegates to the plugin's ``transform_config`` classmethod.

    Args:
        source_id: Source identifier (e.g. "goodreads", "steam").
        source_config: Raw ``inputs.<source_id>`` dict from YAML.

    Returns:
        Transformed config dict.
    """
    plugin = get_sync_handler(source_id)
    if plugin is None:
        return dict(source_config)

    return type(plugin).transform_config(source_config)


def validate_source_config(source_id: str, inputs_config: dict[str, Any]) -> list[str]:
    """Validate config for a sync source.

    Returns:
        List of error messages (empty if valid)
    """
    plugin = get_sync_handler(source_id)
    if plugin is None:
        return [f"Unknown source: {source_id}"]

    source_config = inputs_config.get(source_id, {})
    plugin_config = transform_source_config(source_id, source_config)

    return plugin.validate_config(plugin_config)
