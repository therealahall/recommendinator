"""Shared sync executor for plugin-based data import.

Provides a single save-and-embed loop used by both the web API and CLI,
eliminating duplicated sync logic across callers.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from src.ingestion.plugin_base import SourcePlugin
from src.models.content import ContentItem, get_enum_value
from src.utils.text import humanize_source_id

if TYPE_CHECKING:
    from src.llm.embeddings import EmbeddingGenerator
    from src.storage.manager import StorageManager

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


# Hard ceiling on the parallel-sync worker pool. Bounds both the CLI flag
# (via Click IntRange) and the config-file path so a malicious or
# misconfigured config.yaml cannot exhaust OS thread limits.
MAX_WORKERS_CEILING = 32


def resolve_max_workers(
    config: dict[str, Any] | None,
    override: int | None = None,
    default: int = 4,
) -> int:
    """Resolve the parallel-sync worker count from override + config + default.

    Order of precedence: ``override`` (typically a CLI flag) wins; otherwise
    ``config['sync']['max_workers']`` is used; otherwise ``default``. The
    result is always clamped to ``[1, MAX_WORKERS_CEILING]``. Non-integer
    config values fall back to ``default`` rather than raising — this path
    runs on every sync invocation, so the function must not crash on a
    malformed config.
    """

    def _clamp(value: int) -> int:
        return max(1, min(MAX_WORKERS_CEILING, value))

    if override is not None:
        return _clamp(override)
    sync_config = (config or {}).get("sync") or {}
    try:
        return _clamp(int(sync_config.get("max_workers", default)))
    except (TypeError, ValueError):
        return default


def execute_sync(
    plugin: SourcePlugin,
    plugin_config: dict[str, Any],
    storage_manager: StorageManager,
    embedding_generator: EmbeddingGenerator | None = None,
    use_embeddings: bool = False,
    progress_callback: SyncProgressCallback | None = None,
    mark_for_enrichment: bool = False,
    user_id: int = 1,
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
        user_id: User ID for credential storage (default 1).

    Returns:
        SyncResult with counts and any errors.
    """
    source_id = plugin_config.get("_source_id")
    source_name = humanize_source_id(source_id) if source_id else plugin.display_name
    result = SyncResult(source_name=source_name)

    if progress_callback:
        progress_callback(0, None, "Fetching...", source_name)

    # Inject credential rotation callback so plugins can persist rotated tokens
    def on_credential_rotated(key: str, value: str) -> None:
        try:
            storage_manager.save_credential(user_id, plugin.name, key, value)
            logger.info(
                "[SYNC] %s: Persisted rotated credential '%s'", source_name, key
            )
        except Exception as error:
            logger.warning(
                "[SYNC] %s: Failed to persist rotated credential '%s': %s",
                source_name,
                key,
                type(error).__name__,
            )

    plugin_config = {**plugin_config, "_on_credential_rotated": on_credential_rotated}

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

    logger.info("[SYNC] %s: Found %d items, saving...", source_name, result.total_items)

    # Save each item
    embeddings_generated = 0
    embeddings_skipped = 0
    for index, item in enumerate(items):
        item_num = index + 1
        content_type = get_enum_value(item.content_type)
        try:
            if progress_callback:
                progress_callback(index, result.total_items, item.title, source_name)

            logger.debug(
                "[SYNC] %s: Syncing %s %d/%d - %s",
                source_name,
                content_type,
                item_num,
                result.total_items,
                item.title,
            )

            embedding = None
            if use_embeddings and embedding_generator:
                # Skip if embedding already exists (only checkable with external ID)
                if not item.id or not storage_manager.has_embedding(item.id):
                    logger.info(
                        "[SYNC] %s: Generating embedding %d/%d - %s",
                        source_name,
                        item_num,
                        result.total_items,
                        item.title,
                    )
                    embedding = embedding_generator.generate_content_embedding(item)
                    embeddings_generated += 1
                else:
                    logger.debug(
                        "[SYNC] %s: Embedding exists, skipping %d/%d - %s",
                        source_name,
                        item_num,
                        result.total_items,
                        item.title,
                    )
                    embeddings_skipped += 1

            db_id = storage_manager.save_content_item(item, embedding=embedding)
            result.items_synced += 1

            # Mark for enrichment if enabled
            if mark_for_enrichment and db_id:
                try:
                    storage_manager.mark_item_needs_enrichment(db_id)
                except Exception as enrich_error:
                    logger.warning(
                        "[SYNC] Failed to mark '%s' for enrichment: %s",
                        item.title,
                        enrich_error,
                    )

        except Exception as error:
            error_message = f"Failed to process '{item.title}': {error}"
            logger.warning("[SYNC] %s: %s", source_name, error_message)
            result.errors.append(error_message)

    embedding_summary = ""
    if use_embeddings and embedding_generator:
        embedding_summary = (
            f" Embeddings: {embeddings_generated} generated, "
            f"{embeddings_skipped} skipped."
        )
    logger.info(
        "[SYNC] %s: Completed. %d/%d items saved.%s",
        source_name,
        result.items_synced,
        result.total_items,
        embedding_summary,
    )
    return result


def execute_multi_source_sync(
    sources: list[tuple[SourcePlugin, dict[str, Any]]],
    storage_manager: StorageManager,
    embedding_generator: EmbeddingGenerator | None = None,
    use_embeddings: bool = False,
    progress_callback: SyncProgressCallback | None = None,
    error_callback: Callable[[str], None] | None = None,
    mark_for_enrichment: bool = False,
    user_id: int = 1,
    max_workers: int = 1,
) -> list[SyncResult]:
    """Execute sync for multiple plugin sources, optionally in parallel.

    With ``max_workers <= 1`` (default), sources sync sequentially.
    With ``max_workers > 1``, sources run on a ThreadPoolExecutor, capped
    at ``min(max_workers, len(sources))``. Per-source rate limiting is
    enforced inside each plugin, so cross-source parallelism is safe.

    Thread-safety contract: when ``max_workers > 1``, ``progress_callback``
    and ``error_callback`` may be invoked concurrently from multiple worker
    threads. Both callers in this codebase honour that contract — the web
    ``SyncManager`` takes a lock internally, and the CLI ``cli_progress``
    serialises ``click.echo`` via its own lock — but any future caller
    must do the same.

    Args:
        sources: List of (plugin, plugin_config) tuples to sync.
        storage_manager: Storage manager for saving items.
        embedding_generator: Optional embedding generator.
        use_embeddings: Whether to generate embeddings.
        progress_callback: Optional callback for progress updates. Must be
            thread-safe when ``max_workers > 1``.
        error_callback: Optional callback for error reporting. Must be
            thread-safe when ``max_workers > 1``.
        mark_for_enrichment: Whether to mark items as needing enrichment after save.
        user_id: User ID for credential storage (default 1).
        max_workers: Maximum sources to sync concurrently. ``1`` (default)
            preserves the legacy sequential behaviour.

    Returns:
        List of SyncResult, one per source, in the same order as ``sources``.
    """

    def _run_one(plugin: SourcePlugin, plugin_config: dict[str, Any]) -> SyncResult:
        logger.info("[SYNC] === Starting sync for source: %s ===", plugin.name)
        try:
            return execute_sync(
                plugin=plugin,
                plugin_config=plugin_config,
                storage_manager=storage_manager,
                embedding_generator=embedding_generator,
                use_embeddings=use_embeddings,
                progress_callback=progress_callback,
                mark_for_enrichment=mark_for_enrichment,
                user_id=user_id,
            )
        except Exception as error:
            error_message = f"Sync failed for {plugin.name}: {error}"
            logger.error("[SYNC] %s", error_message)
            source_id = plugin_config.get("_source_id")
            error_source_name = (
                humanize_source_id(source_id) if source_id else plugin.display_name
            )
            return SyncResult(
                source_name=error_source_name,
                errors=[error_message],
            )

    effective_workers = min(max_workers, len(sources)) if sources else 1

    if effective_workers > 1:
        with ThreadPoolExecutor(
            max_workers=effective_workers, thread_name_prefix="sync"
        ) as executor:
            futures = [
                executor.submit(_run_one, plugin, plugin_config)
                for plugin, plugin_config in sources
            ]
            results = [future.result() for future in futures]
    else:
        results = [_run_one(plugin, cfg) for plugin, cfg in sources]

    if error_callback:
        for result in results:
            for error_message in result.errors:
                error_callback(error_message)

    total_synced = sum(result.items_synced for result in results)
    logger.info("[SYNC] === Completed. Total items processed: %d ===", total_synced)
    return results
