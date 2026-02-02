"""Dynamic sync source discovery from config.

Sources are discovered from config.inputs - any section with enabled: true
that has a registered handler is available for sync. No hardcoded source list.
"""

from dataclasses import dataclass
from typing import Any

from src.ingestion.plugin_base import SourcePlugin
from src.ingestion.sources.generic_csv import CsvImportPlugin
from src.ingestion.sources.generic_json import JsonImportPlugin
from src.ingestion.sources.goodreads import GoodreadsPlugin
from src.ingestion.sources.markdown import MarkdownImportPlugin
from src.ingestion.sources.radarr import RadarrPlugin
from src.ingestion.sources.sonarr import SonarrPlugin
from src.ingestion.sources.steam import SteamPlugin


@dataclass
class SyncSourceInfo:
    """Info about an available sync source."""

    id: str
    display_name: str
    description: str


# Registry of sync handlers: config key -> (plugin, description)
# Only sources in this dict can be synced. Config must have inputs.<key> with enabled: true.
# Each plugin owns its own config transformation via transform_config().
_SYNC_HANDLERS: dict[
    str,
    tuple[SourcePlugin, str],
] = {
    "goodreads": (GoodreadsPlugin(), "Import books from Goodreads export"),
    "steam": (SteamPlugin(), "Import games from Steam library"),
    "sonarr": (SonarrPlugin(), "Import TV series from Sonarr"),
    "radarr": (RadarrPlugin(), "Import movies from Radarr"),
    "csv_import": (CsvImportPlugin(), "Import from CSV file"),
    "json_import": (JsonImportPlugin(), "Import from JSON/JSONL file"),
    "markdown_import": (MarkdownImportPlugin(), "Import from Markdown file"),
}


def get_available_sync_sources(config: dict[str, Any]) -> list[SyncSourceInfo]:
    """Get list of sync sources that are enabled in config.

    Only returns sources defined in config.inputs with enabled: true.
    Uses the loaded config (config.yaml) - no fallback to example.

    Args:
        config: Full application config (from load_config)

    Returns:
        List of SyncSourceInfo for each enabled source we can handle
    """
    inputs_config = config.get("inputs", {})
    sources: list[SyncSourceInfo] = []

    for source_id, handler in _SYNC_HANDLERS.items():
        source_config = inputs_config.get(source_id, {})
        if not isinstance(source_config, dict):
            continue
        # Only include sources explicitly enabled in config
        if not source_config.get("enabled", False):
            continue

        plugin, description = handler

        sources.append(
            SyncSourceInfo(
                id=source_id,
                display_name=plugin.display_name,
                description=description,
            )
        )

    return sources


def get_sync_handler(
    source_id: str,
) -> tuple[SourcePlugin, str] | None:
    """Get the handler (plugin, description) for a source.

    Returns:
        (plugin, description) or None if unknown source
    """
    if source_id not in _SYNC_HANDLERS:
        return None
    return _SYNC_HANDLERS[source_id]


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
    handler = _SYNC_HANDLERS.get(source_id)
    if handler is None:
        return dict(source_config)

    plugin, _description = handler
    return type(plugin).transform_config(source_config)


def validate_source_config(source_id: str, inputs_config: dict[str, Any]) -> list[str]:
    """Validate config for a sync source.

    Returns:
        List of error messages (empty if valid)
    """
    handler = get_sync_handler(source_id)
    if handler is None:
        return [f"Unknown source: {source_id}"]

    plugin, _description = handler
    source_config = inputs_config.get(source_id, {})
    plugin_config = transform_source_config(source_id, source_config)

    return plugin.validate_config(plugin_config)
