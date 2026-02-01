"""Dynamic sync source discovery from config.

Sources are discovered from config.inputs - any section with enabled: true
that has a registered handler is available for sync. No hardcoded source list.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from src.ingestion.plugin_base import SourcePlugin
from src.ingestion.sources.generic_csv import CsvImportPlugin
from src.ingestion.sources.generic_json import JsonImportPlugin
from src.ingestion.sources.goodreads import GoodreadsPlugin
from src.ingestion.sources.markdown import MarkdownImportPlugin
from src.ingestion.sources.radarr import RadarrPlugin
from src.ingestion.sources.sonarr import SonarrPlugin


@dataclass
class SyncSourceInfo:
    """Info about an available sync source."""

    id: str
    display_name: str
    description: str


def _goodreads_plugin_config(config: dict[str, Any]) -> dict[str, Any]:
    """Transform inputs.goodreads config to GoodreadsPlugin config."""
    path = config.get("path", "inputs/goodreads_library_export.csv")
    return {"csv_path": str(path)}


def _steam_plugin_config(config: dict[str, Any]) -> dict[str, Any]:
    """Extract Steam params for parse_steam_games (not plugin)."""
    return {
        "api_key": config.get("api_key", "").strip(),
        "steam_id": config.get("steam_id", "").strip() or None,
        "vanity_url": config.get("vanity_url", "").strip() or None,
        "min_playtime_minutes": config.get("min_playtime_minutes", 0),
    }


def _sonarr_plugin_config(config: dict[str, Any]) -> dict[str, Any]:
    """Transform inputs.sonarr config to SonarrPlugin config."""
    return {
        "url": (config.get("url", "http://localhost:8989") or "").rstrip("/"),
        "api_key": (config.get("api_key") or "").strip(),
    }


def _radarr_plugin_config(config: dict[str, Any]) -> dict[str, Any]:
    """Transform inputs.radarr config to RadarrPlugin config."""
    return {
        "url": (config.get("url", "http://localhost:7878") or "").rstrip("/"),
        "api_key": (config.get("api_key") or "").strip(),
    }


def _csv_plugin_config(config: dict[str, Any]) -> dict[str, Any]:
    """Transform inputs.csv_import config to CsvImportPlugin config."""
    return {
        "csv_path": config.get("csv_path", ""),
        "content_type": config.get("content_type", "book"),
    }


def _json_plugin_config(config: dict[str, Any]) -> dict[str, Any]:
    """Transform inputs.json_import config to JsonImportPlugin config."""
    return {
        "json_path": config.get("json_path", ""),
        "content_type": config.get("content_type", "book"),
    }


def _markdown_plugin_config(config: dict[str, Any]) -> dict[str, Any]:
    """Transform inputs.markdown_import config to MarkdownImportPlugin config."""
    return {
        "markdown_path": config.get("markdown_path", ""),
        "content_type": config.get("content_type", "book"),
    }


# Registry of sync handlers: config key -> (plugin or None for steam, config_transform, description)
# Only sources in this dict can be synced. Config must have inputs.<key> with enabled: true.
_SYNC_HANDLERS: dict[
    str,
    tuple[
        SourcePlugin | None,
        Callable[[dict[str, Any]], dict[str, Any]],
        str,
    ],
] = {
    "goodreads": (
        GoodreadsPlugin(),
        _goodreads_plugin_config,
        "Import books from Goodreads export",
    ),
    "steam": (None, _steam_plugin_config, "Import games from Steam library"),
    "sonarr": (SonarrPlugin(), _sonarr_plugin_config, "Import TV series from Sonarr"),
    "radarr": (RadarrPlugin(), _radarr_plugin_config, "Import movies from Radarr"),
    "csv_import": (
        CsvImportPlugin(),
        _csv_plugin_config,
        "Import from CSV file",
    ),
    "json_import": (
        JsonImportPlugin(),
        _json_plugin_config,
        "Import from JSON/JSONL file",
    ),
    "markdown_import": (
        MarkdownImportPlugin(),
        _markdown_plugin_config,
        "Import from Markdown file",
    ),
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
        # Only include sources explicitly enabled in config - flip enabled: true/false to show/hide
        if not source_config.get("enabled", False):
            continue

        plugin, _transform, description = handler
        if plugin is None:
            display_name = "Steam"
        else:
            display_name = plugin.display_name

        sources.append(
            SyncSourceInfo(
                id=source_id,
                display_name=display_name,
                description=description,
            )
        )

    return sources


def get_sync_handler(
    source_id: str,
) -> tuple[Any, Callable[[dict[str, Any]], dict[str, Any]]] | None:
    """Get the handler (plugin or steam) and config transform for a source.

    Returns:
        (plugin_or_steam_marker, config_transform) or None if unknown
    """
    if source_id not in _SYNC_HANDLERS:
        return None
    plugin, transform, _ = _SYNC_HANDLERS[source_id]
    return (plugin, transform)


def validate_source_config(source_id: str, inputs_config: dict[str, Any]) -> list[str]:
    """Validate config for a sync source.

    Returns:
        List of error messages (empty if valid)
    """
    handler = get_sync_handler(source_id)
    if handler is None:
        return [f"Unknown source: {source_id}"]

    plugin, transform = handler
    source_config = inputs_config.get(source_id, {})
    plugin_config = transform(source_config)

    if plugin is None:
        # Steam - special validation
        if not plugin_config.get("api_key"):
            return [
                "Steam API key is required. Get one from https://steamcommunity.com/dev/apikey"
            ]
        if not plugin_config.get("steam_id") and not plugin_config.get("vanity_url"):
            return ["Either steam_id or vanity_url must be provided in config"]
        return []

    errors: list[str] = plugin.validate_config(plugin_config)
    return errors
