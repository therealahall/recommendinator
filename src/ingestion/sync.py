"""Shared sync executor for plugin-based data import.

Provides a single save-and-embed loop used by both the web API and CLI,
eliminating duplicated sync logic across callers.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from src.ingestion.plugin_base import SourcePlugin
from src.models.content import ContentItem

logger = logging.getLogger(__name__)

# Callback signature: (items_processed, total_items, current_item_title)
SyncProgressCallback = Callable[[int, int | None, str | None], None]


@dataclass
class SyncResult:
    """Result of a sync operation."""

    source_name: str
    items_synced: int = 0
    total_items: int = 0
    errors: list[str] = field(default_factory=list)


def execute_sync(
    plugin: SourcePlugin,
    plugin_config: dict[str, Any],
    storage_manager: Any,
    embedding_generator: Any | None = None,
    use_embeddings: bool = False,
    progress_callback: SyncProgressCallback | None = None,
) -> SyncResult:
    """Execute a sync for a single plugin source.

    Fetches items from the plugin, optionally generates embeddings, and saves
    each item to storage. Progress is reported via the callback.

    Args:
        plugin: The source plugin to fetch from.
        plugin_config: Plugin-ready configuration dict.
        storage_manager: Storage manager for saving items.
        embedding_generator: Optional embedding generator.
        use_embeddings: Whether to generate embeddings for each item.
        progress_callback: Optional callback(items_processed, total, current_item).

    Returns:
        SyncResult with counts and any errors.
    """
    source_name = plugin.display_name
    result = SyncResult(source_name=source_name)

    if progress_callback:
        progress_callback(0, None, f"Fetching from {source_name}...")

    # Fetch items from plugin
    def fetch_progress(
        items_processed: int, total_items: int | None, current_item: str | None
    ) -> None:
        if progress_callback:
            progress_callback(items_processed, total_items, current_item)

    items: list[ContentItem] = list(
        plugin.fetch(plugin_config, progress_callback=fetch_progress)
    )

    result.total_items = len(items)
    if progress_callback:
        progress_callback(0, result.total_items, None)

    logger.info(
        f"[SYNC] {source_name}: Found {result.total_items} items, saving..."
    )

    # Save each item
    for index, item in enumerate(items):
        try:
            if progress_callback:
                progress_callback(index, result.total_items, item.title)

            embedding = None
            if use_embeddings and embedding_generator:
                embedding = embedding_generator.generate_content_embedding(item)

            storage_manager.save_content_item(item, embedding)
            result.items_synced += 1

        except Exception as error:
            error_message = f"Failed to process '{item.title}': {error}"
            logger.warning(f"[SYNC] {source_name}: {error_message}")
            result.errors.append(error_message)

    logger.info(
        f"[SYNC] {source_name}: Completed. "
        f"{result.items_synced}/{result.total_items} items saved."
    )
    return result
