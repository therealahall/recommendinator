"""The background sync job, built the same way for a request and for a tick."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from src.config.service import auto_enrich_enabled
from src.enrichment.manager import EnrichmentManager
from src.ingestion.sync import (
    SyncResult,
    execute_multi_source_sync,
    release_sources,
    resolve_max_workers,
    sync_run_recorder,
)
from src.models.content import ContentType
from src.utils.text import sanitize_for_log
from src.web.sync_manager import SyncJob, SyncManager

if TYPE_CHECKING:
    from src.sources.service import ResolvedInput
    from src.storage.manager import StorageManager

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SyncDispatch:
    run: Callable[[SyncJob], int]
    on_complete: Callable[[], None]


def _enrichment_content_type(resolved: list[ResolvedInput]) -> ContentType | None:
    # Anything but one source enriches every type: nothing narrows a mixed run.
    if len(resolved) != 1:
        return None
    # str() at the read, not at the log call: config.yaml can put anything
    # here, and ContentType refuses a non-member either way.
    raw_content_type = resolved[0].config.get("content_type")
    content_type_str = str(raw_content_type) if raw_content_type else ""
    if not content_type_str:
        return None
    try:
        return ContentType(content_type_str)
    except ValueError:
        logger.warning(
            "Invalid content_type '%s' for source %s, enriching all types",
            sanitize_for_log(content_type_str),
            sanitize_for_log(resolved[0].source_id),
        )
        return None


def build_sync_job(
    sync_manager: SyncManager,
    source_label: str,
    resolved: list[ResolvedInput],
    storage: StorageManager,
    config: dict[str, Any],
    max_workers: int | None = None,
) -> SyncDispatch:
    auto_enrich = auto_enrich_enabled(config)
    source_pairs = [(entry.plugin, entry.config) for entry in resolved]
    record_run = sync_run_recorder(storage)
    enrichment_content_type = _enrichment_content_type(resolved)

    def run_sync(job: SyncJob) -> int:
        def progress_callback(
            items_processed: int,
            total_items: int | None,
            current_item: str | None,
            current_source: str | None,
        ) -> None:
            sync_manager.update_progress(
                source=source_label,
                items_processed=items_processed,
                total_items=total_items,
                current_item=current_item,
                current_source=current_source,
            )

        def result_callback(result: SyncResult) -> None:
            sync_manager.record_source_result(
                source_label,
                result.source_name,
                items_added=result.items_added,
                items_updated=result.items_updated,
                items_unchanged=result.items_unchanged,
            )
            for error_message in result.errors:
                sync_manager.add_error(source_label, result.source_name, error_message)
            record_run(result)

        try:
            results = execute_multi_source_sync(
                sources=source_pairs,
                storage_manager=storage,
                progress_callback=progress_callback,
                result_callback=result_callback,
                mark_for_enrichment=auto_enrich,
                user_id=1,
                max_workers=resolve_max_workers(config, override=max_workers),
            )
        finally:
            release_sources(storage, [entry.source_id for entry in resolved])
        return sum(result.items_synced for result in results)

    def on_sync_complete() -> None:
        if not auto_enrich:
            return
        started = EnrichmentManager(storage, config).start_enrichment(
            content_type=enrichment_content_type,
        )
        if started:
            logger.info("[ENRICHMENT] Auto-started after sync")
        else:
            logger.info("[ENRICHMENT] Auto-start skipped: a job is already running")

    return SyncDispatch(run=run_sync, on_complete=on_sync_complete)
