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

# Callback signature: (items_processed, total_items, current_item_title, current_source)
SyncProgressCallback = Callable[[int, int | None, str | None, str | None], None]


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
    mark_for_enrichment: bool = False,
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
        mark_for_enrichment: Whether to mark items as needing enrichment after save.

    Returns:
        SyncResult with counts and any errors.
    """
    source_name = plugin.display_name
    result = SyncResult(source_name=source_name)

    if progress_callback:
        progress_callback(0, None, f"Fetching from {source_name}...", source_name)

    # Fetch items from plugin
    def fetch_progress(
        items_processed: int, total_items: int | None, current_item: str | None
    ) -> None:
        if progress_callback:
            progress_callback(items_processed, total_items, current_item, source_name)

    items: list[ContentItem] = list(
        plugin.fetch(plugin_config, progress_callback=fetch_progress)
    )

    result.total_items = len(items)
    if progress_callback:
        progress_callback(0, result.total_items, None, source_name)

    logger.info(f"[SYNC] {source_name}: Found {result.total_items} items, saving...")

    # Save each item
    for index, item in enumerate(items):
        item_num = index + 1
        content_type = (
            item.content_type.value
            if hasattr(item.content_type, "value")
            else item.content_type
        )
        try:
            if progress_callback:
                progress_callback(index, result.total_items, item.title, source_name)

            logger.debug(
                f"[SYNC] {source_name}: Syncing {content_type} {item_num}/{result.total_items} - {item.title}"
            )

            embedding = None
            if use_embeddings and embedding_generator:
                embedding = embedding_generator.generate_content_embedding(item)

            db_id = storage_manager.save_content_item(item, embedding)
            result.items_synced += 1

            # Mark for enrichment if enabled
            if mark_for_enrichment and db_id:
                try:
                    storage_manager.mark_item_needs_enrichment(db_id)
                except Exception as enrich_error:
                    logger.warning(
                        f"[SYNC] Failed to mark '{item.title}' for enrichment: "
                        f"{enrich_error}"
                    )

        except Exception as error:
            error_message = f"Failed to process '{item.title}': {error}"
            logger.warning(f"[SYNC] {source_name}: {error_message}")
            result.errors.append(error_message)

    logger.info(
        f"[SYNC] {source_name}: Completed. "
        f"{result.items_synced}/{result.total_items} items saved."
    )
    return result


def execute_multi_source_sync(
    sources: list[tuple[SourcePlugin, dict[str, Any]]],
    storage_manager: Any,
    embedding_generator: Any | None = None,
    use_embeddings: bool = False,
    progress_callback: SyncProgressCallback | None = None,
    error_callback: Callable[[str], None] | None = None,
    mark_for_enrichment: bool = False,
) -> list[SyncResult]:
    """Execute sync for multiple plugin sources sequentially.

    Args:
        sources: List of (plugin, plugin_config) tuples to sync.
        storage_manager: Storage manager for saving items.
        embedding_generator: Optional embedding generator.
        use_embeddings: Whether to generate embeddings.
        progress_callback: Optional callback for progress updates.
        error_callback: Optional callback for error reporting.
        mark_for_enrichment: Whether to mark items as needing enrichment after save.

    Returns:
        List of SyncResult, one per source.
    """
    results: list[SyncResult] = []

    for plugin, plugin_config in sources:
        logger.info(f"[SYNC] === Starting sync for source: {plugin.name} ===")

        try:
            result = execute_sync(
                plugin=plugin,
                plugin_config=plugin_config,
                storage_manager=storage_manager,
                embedding_generator=embedding_generator,
                use_embeddings=use_embeddings,
                progress_callback=progress_callback,
                mark_for_enrichment=mark_for_enrichment,
            )
            results.append(result)

            if result.errors and error_callback:
                for error_message in result.errors:
                    error_callback(error_message)

        except Exception as error:
            error_message = f"Sync failed for {plugin.name}: {error}"
            logger.error(f"[SYNC] {error_message}")
            if error_callback:
                error_callback(error_message)
            results.append(
                SyncResult(
                    source_name=plugin.display_name,
                    errors=[error_message],
                )
            )

    total_synced = sum(result.items_synced for result in results)
    logger.info(f"[SYNC] === Completed. Total items processed: {total_synced} ===")
    return results
