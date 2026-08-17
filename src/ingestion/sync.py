"""Shared sync executor for plugin-based data import.

Provides a single save loop used by both the web API and CLI, eliminating
duplicated sync logic across callers.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from src.ingestion.plugin_base import SourceError, SourcePlugin
from src.models.content import ContentItem, get_enum_value
from src.storage.manager import SaveOutcome
from src.utils.text import exception_for_log, humanize_source_id, sanitize_for_log

if TYPE_CHECKING:
    from src.storage.manager import StorageManager

logger = logging.getLogger(__name__)

# Callback signature: (items_processed, total_items, current_item_title, current_source)
SyncProgressCallback = Callable[[int, int | None, str | None, str | None], None]


@dataclass
class SyncResult:
    """Result of a sync operation.

    ``items_synced`` counts the saves that succeeded; the three outcome
    counters split those by what the save did, so a re-sync that changed
    nothing is distinguishable from the import that added everything.
    """

    source_name: str
    items_synced: int = 0
    items_added: int = 0
    items_updated: int = 0
    items_unchanged: int = 0
    total_items: int = 0
    errors: list[str] = field(default_factory=list)


# Called with each source's result as that source finishes, rather than once
# the whole run is over: a caller polling a multi-source job otherwise sees
# nothing of the first source until the last one has finished too.
SyncResultCallback = Callable[[SyncResult], None]


#: What both interfaces call a run over every source. One spelling, because
#: the web keys its job record on it and the CLI reports under it.
ALL_SOURCES_LABEL = "All Sources"

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
    progress_callback: SyncProgressCallback | None = None,
    mark_for_enrichment: bool = False,
    user_id: int = 1,
) -> SyncResult:
    """Execute a sync for a single plugin source.

    Fetches items from the plugin and saves each to storage. Progress is
    reported via the callback.

    Args:
        plugin: The source plugin to fetch from.
        plugin_config: Plugin-ready configuration dict.
        storage_manager: Storage manager for saving items.
        progress_callback: Optional callback(items_processed, total, current_item).
        mark_for_enrichment: Whether to mark items as needing enrichment after save.
        user_id: User ID for credential storage (default 1).

    Returns:
        SyncResult with counts and any errors.
    """
    source_id = plugin_config.get("_source_id")
    source_name = humanize_source_id(source_id) if source_id else plugin.display_name
    result = SyncResult(source_name=source_name)

    # ``_source_id`` is typed into config.yaml and the web source form, so the
    # logged copy is escaped while ``SyncResult`` keeps the raw name for the
    # JSON body /api/sync/status serves.
    safe_source_name = sanitize_for_log(source_name)

    if progress_callback:
        progress_callback(0, None, "Fetching...", source_name)

    # One function decides what a source is called, so the token owner, the
    # attribution and the delete key agree for every id. That includes the
    # empty one a YAML ``inputs`` key can produce, which ``source_name`` reads
    # as absent.
    credential_owner = plugin.get_source_identifier(plugin_config)

    # Inject credential rotation callback so plugins can persist rotated tokens
    def on_credential_rotated(key: str, value: str) -> None:
        safe_key = sanitize_for_log(key)
        try:
            storage_manager.credentials.save(user_id, credential_owner, key, value)
            logger.info(
                "[SYNC] %s: Persisted rotated credential '%s'",
                safe_source_name,
                safe_key,
            )
        except Exception as error:
            # A storage fault can quote the parameters it was handed, and one
            # of them is the rotated secret, so ``exception_for_log`` is too
            # much detail here.
            logger.warning(
                "[SYNC] %s: Failed to persist rotated credential '%s': %s",
                safe_source_name,
                safe_key,
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

    logger.info(
        "[SYNC] %s: Found %d items, saving...", safe_source_name, result.total_items
    )

    # Save each item
    enrichment_queue_failures = 0
    for index, item in enumerate(items):
        item_num = index + 1
        content_type = get_enum_value(item.content_type)
        # Titles come from imported files and POST /api/complete, neither of
        # which restricts characters. Every sink shares one escaped copy: the
        # CLI writes ``result.errors`` to a terminal, where a raw title could
        # erase the line the operator just read (CWE-117).
        safe_title = sanitize_for_log(item.title)
        try:
            if progress_callback:
                # Report ``item_num`` (1-based) so the UI shows the current
                # item number rather than the count of completed items.
                # Final iteration produces ``items_processed == total_items``.
                progress_callback(item_num, result.total_items, safe_title, source_name)

            logger.debug(
                "[SYNC] %s: Syncing %s %d/%d - %s",
                safe_source_name,
                content_type,
                item_num,
                result.total_items,
                safe_title,
            )

            saved = storage_manager.save_content_item_outcome(item)
            result.items_synced += 1
            if saved.outcome is SaveOutcome.ADDED:
                result.items_added += 1
            elif saved.outcome is SaveOutcome.UPDATED:
                result.items_updated += 1
            else:
                result.items_unchanged += 1

            # Mark for enrichment if enabled
            if mark_for_enrichment:
                try:
                    storage_manager.mark_item_needs_enrichment(saved.db_id)
                except Exception as enrich_error:
                    enrichment_queue_failures += 1
                    logger.warning(
                        "[SYNC] Failed to mark '%s' for enrichment: %s",
                        safe_title,
                        exception_for_log(enrich_error),
                    )

        except Exception as error:
            # Don't append the raw exception to ``result.errors`` — that
            # list is exposed via /api/sync/status and plugin exceptions
            # can carry credential text (e.g. an HTTP 401 echoing the
            # Authorization header). Log the full detail server-side and
            # return only the safe item-identifying summary to clients.
            logger.warning(
                "[SYNC] %s: Failed to process '%s': %s",
                safe_source_name,
                safe_title,
                exception_for_log(error),
            )
            result.errors.append(f"Failed to process '{safe_title}'")

    # Reported because the web reads no log file, and once per source because
    # the queue write is the same row for every item: a fault that hits one
    # hits all, and per-item errors made one sync report thousands of them.
    if enrichment_queue_failures:
        result.errors.append(
            f"Saved {enrichment_queue_failures} item(s) but could not queue them"
            " for enrichment"
        )

    logger.info(
        "[SYNC] %s: Completed. %d/%d items saved (%d added, %d updated, %d unchanged).",
        safe_source_name,
        result.items_synced,
        result.total_items,
        result.items_added,
        result.items_updated,
        result.items_unchanged,
    )
    return result


def _error_source_name(plugin: SourcePlugin, plugin_config: dict[str, Any]) -> str:
    source_id = plugin_config.get("_source_id")
    return humanize_source_id(source_id) if source_id else plugin.display_name


def execute_multi_source_sync(
    sources: list[tuple[SourcePlugin, dict[str, Any]]],
    storage_manager: StorageManager,
    progress_callback: SyncProgressCallback | None = None,
    result_callback: SyncResultCallback | None = None,
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
    and ``result_callback`` may be invoked concurrently from multiple worker
    threads. Both callers in this codebase honour that contract — the web
    ``SyncManager`` takes a lock internally, and the CLI ``cli_progress``
    serialises ``click.echo`` via its own lock — but any future caller
    must do the same.

    Args:
        sources: List of (plugin, plugin_config) tuples to sync.
        storage_manager: Storage manager for saving items.
        progress_callback: Optional callback for progress updates. Must be
            thread-safe when ``max_workers > 1``.
        result_callback: Optional callback handed each source's result as that
            source finishes, errors and counts alike. Must be thread-safe when
            ``max_workers > 1``.
        mark_for_enrichment: Whether to mark items as needing enrichment after save.
        user_id: User ID for credential storage (default 1).
        max_workers: Maximum sources to sync concurrently. ``1`` (default)
            preserves the legacy sequential behaviour.

    Returns:
        List of SyncResult, one per source, in the same order as ``sources``.
    """

    def _run_one(plugin: SourcePlugin, plugin_config: dict[str, Any]) -> SyncResult:
        result = _sync_one(plugin, plugin_config)
        if result_callback:
            result_callback(result)
        return result

    def _sync_one(plugin: SourcePlugin, plugin_config: dict[str, Any]) -> SyncResult:
        safe_plugin_name = sanitize_for_log(plugin.name)
        logger.info("[SYNC] === Starting sync for source: %s ===", safe_plugin_name)
        try:
            return execute_sync(
                plugin=plugin,
                plugin_config=plugin_config,
                storage_manager=storage_manager,
                progress_callback=progress_callback,
                mark_for_enrichment=mark_for_enrichment,
                user_id=user_id,
            )
        except SourceError as error:
            # Ours, and written for the operator: it names the setting to
            # change. Every plugin whose credential rides in the url scrubs
            # the request fault before wording one of these.
            logger.error(
                "[SYNC] Sync failed for %s: %s",
                safe_plugin_name,
                exception_for_log(error),
            )
            # The CLI writes ``result.errors`` to a terminal and these quote a
            # ``requests`` fault, so the server's reason phrase could erase the
            # line the operator just read (CWE-117).
            return SyncResult(
                source_name=_error_source_name(plugin, plugin_config),
                errors=[sanitize_for_log(error.message)],
            )
        except Exception as error:
            # See sibling note in execute_sync: keep raw exception text
            # out of result.errors. Plugin failures can include
            # credential bytes in their messages.
            logger.error(
                "[SYNC] Sync failed for %s: %s",
                safe_plugin_name,
                exception_for_log(error),
            )
            return SyncResult(
                source_name=_error_source_name(plugin, plugin_config),
                errors=[f"Sync failed for {plugin.name}"],
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

    total_synced = sum(result.items_synced for result in results)
    logger.info("[SYNC] === Completed. Total items processed: %d ===", total_synced)
    return results
