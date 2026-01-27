"""Data ingestion modules.

This package provides the plugin system for ingesting data from various sources.

Key components:
- SourcePlugin: Abstract base class for all source plugins
- PluginRegistry: Discovers and manages available plugins
- ConfigField: Describes configuration options for plugins
- SourceError: Exception raised by plugins on errors

Example usage:
    from src.ingestion import get_registry, SourcePlugin

    # Get all available plugins
    registry = get_registry()
    plugins = registry.get_all_plugins()

    # Get enabled plugins from config
    enabled = registry.get_enabled_plugins(config)

    # Fetch data from a plugin
    for item in plugin.fetch(plugin_config):
        process(item)
"""

from src.ingestion.plugin_base import ConfigField, PluginInfo, SourceError, SourcePlugin
from src.ingestion.registry import PluginRegistry, get_registry

__all__ = [
    "ConfigField",
    "PluginInfo",
    "PluginRegistry",
    "SourceError",
    "SourcePlugin",
    "get_registry",
]
