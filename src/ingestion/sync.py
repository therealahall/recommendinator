from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Iterable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

from src.ingestion.plugin_base import SourceError, SourcePlugin
from src.models.content import ContentItem, get_enum_value
from src.storage.manager import SaveCounts
from src.storage.schema import SyncRunStatus
from src.storage.sync_runs import HEARTBEAT_EVERY
from src.utils.dates import utc_now
from src.utils.text import exception_for_log, humanize_source_id, sanitize_for_log

if TYPE_CHECKING:
    from src.storage.manager import StorageManager

logger = logging.getLogger(__name__)

# Callback signature: (items_processed, total_items, current_item_title, current_source)
SyncProgressCallback = Callable[[int, int | None, str | None, str | None], None]


# One entry per failed item, so a library refusing every one of them put
# thousands of lines into every two-second /api/sync/status poll. One cap, at
# the producer, so both interfaces list the same misses.
MAX_REPORTED_ERRORS = 200


@dataclass
class SyncResult:
    source_name: str
    source_id: str = ""
    items_synced: int = 0
    counts: SaveCounts = field(default_factory=SaveCounts)
    total_items: int = 0
    errors: list[str] = field(default_factory=list)
    started_at: datetime = field(default_factory=utc_now)
    finished_at: datetime = field(default_factory=utc_now)
    omitted_errors: int = 0

    def record_item_error(self, message: str) -> None:
        """Report a failed item, or count it in ``omitted_errors`` once full."""
        if len(self.errors) < MAX_REPORTED_ERRORS:
            self.errors.append(message)
        else:
            self.omitted_errors += 1

    @property
    def items_added(self) -> int:
        return self.counts.added

    @property
    def items_updated(self) -> int:
        return self.counts.updated

    @property
    def items_unchanged(self) -> int:
        return self.counts.unchanged


# Called with each source's result as that source finishes, rather than once
# the whole run is over: a caller polling a multi-source job otherwise sees
# nothing of the first source until the last one has finished too.
SyncResultCallback = Callable[[SyncResult], None]


#: What both interfaces call a run over every source. One spelling, because
#: the CLI reports under it and the web serves it as the umbrella job's name.
ALL_SOURCES_LABEL = "All Sources"
#: The umbrella's job key. Not the label: ``all_sources`` humanizes to that.
ALL_SOURCES_KEY = "*all*"


def job_label(key: str) -> str:
    return ALL_SOURCES_LABEL if key == ALL_SOURCES_KEY else key


# Hard ceiling on the parallel-sync worker pool. Bounds both the CLI flag
# (via Click IntRange) and the config-file path so a malicious or
# misconfigured config.yaml cannot exhaust OS thread limits.
MAX_WORKERS_CEILING = 32


def resolve_max_workers(
    config: dict[str, Any] | None,
    override: int | None = None,
    default: int = 4,
) -> int:
    """Non-integer config values fall back to ``default`` rather than raising —
    this path runs on every sync invocation, so the function must not crash on
    a malformed config.
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


def sync_run_failed(items_synced: int, errors: Sequence[object]) -> bool:
    """Saved nothing while reporting errors — the rule every door applies."""
    return items_synced == 0 and bool(errors)


def claim_sources(
    storage_manager: StorageManager,
    source_ids: Sequence[str],
    user_id: int = 1,
) -> tuple[dict[str, int], list[str]]:
    claimed: dict[str, int] = {}
    refused: list[str] = []
    for source_id in source_ids:
        claim_id = storage_manager.sync_runs.claim(user_id, source_id)
        if claim_id is None:
            refused.append(source_id)
        else:
            claimed[source_id] = claim_id
    return claimed, refused


def release_sources(
    storage_manager: StorageManager,
    claim_ids: Iterable[int],
) -> None:
    for claim_id in claim_ids:
        storage_manager.sync_runs.release(claim_id)


class _ClaimHeartbeat:
    """Beat every claim a run still owes, from the driver, not from a worker."""

    def __init__(
        self,
        storage_manager: StorageManager,
        user_id: int,
        source_ids: Iterable[str],
    ) -> None:
        self._storage_manager = storage_manager
        self._user_id = user_id
        self._outstanding = set(source_ids)
        self._lock = threading.Lock()
        self._stopped = threading.Event()
        self._thread = threading.Thread(
            target=self._beat, name="sync-heartbeat", daemon=True
        )

    def __enter__(self) -> _ClaimHeartbeat:
        self._thread.start()
        return self

    def __exit__(self, *_exc_info: object) -> None:
        self._stopped.set()
        self._thread.join()

    def done(self, source_id: str) -> None:
        """Drop a recorded source: the next claim on it belongs to another run."""
        with self._lock:
            self._outstanding.discard(source_id)

    def _beat(self) -> None:
        while not self._stopped.wait(HEARTBEAT_EVERY.total_seconds()):
            with self._lock:
                outstanding = list(self._outstanding)
            for source_id in outstanding:
                self._beat_one(source_id)

    def _beat_one(self, source_id: str) -> None:
        # A write waiting out ``busy_timeout`` raises, and an escaped one ended
        # every source's beat for the rest of the run.
        try:
            self._storage_manager.sync_runs.heartbeat(self._user_id, source_id)
        except Exception as error:
            logger.warning(
                "[SYNC] %s: claim heartbeat failed: %s",
                sanitize_for_log(source_id),
                exception_for_log(error),
            )


def already_syncing_detail(source_ids: Sequence[str]) -> str:
    named = ", ".join(humanize_source_id(source_id) for source_id in source_ids)
    return f"A sync is already in progress for {named}."


def sync_run_recorder(
    storage_manager: StorageManager, user_id: int = 1
) -> SyncResultCallback:
    def record(result: SyncResult) -> None:
        status: SyncRunStatus = (
            "failed"
            if sync_run_failed(result.items_synced, result.errors)
            else "completed"
        )
        storage_manager.sync_runs.record(
            user_id,
            result.source_id,
            started_at=result.started_at,
            finished_at=result.finished_at,
            status=status,
            items_added=result.items_added,
            items_updated=result.items_updated,
            items_unchanged=result.items_unchanged,
            total_items=result.total_items,
            errors=result.errors,
            omitted_errors=result.omitted_errors,
        )

    return record


def execute_sync(
    plugin: SourcePlugin,
    plugin_config: dict[str, Any],
    storage_manager: StorageManager,
    progress_callback: SyncProgressCallback | None = None,
    mark_for_enrichment: bool = False,
    user_id: int = 1,
) -> SyncResult:
    """Fetch one source's items and save each, reporting counts and misses."""
    source_id = _configured_source_id(plugin_config)
    source_name = humanize_source_id(source_id) if source_id else plugin.display_name
    result = SyncResult(source_name=source_name, source_id=source_id)

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
    source_identifier = plugin.get_source_identifier(plugin_config)

    def on_credential_rotated(key: str, value: str) -> None:
        safe_key = sanitize_for_log(key)
        try:
            storage_manager.credentials.save(user_id, source_identifier, key, value)
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
                progress_callback(item_num, result.total_items, safe_title, source_name)

            logger.debug(
                "[SYNC] %s: Syncing %s %d/%d - %s",
                safe_source_name,
                content_type,
                item_num,
                result.total_items,
                safe_title,
            )

            # Storage keys an external id on this, so it is stamped here rather
            # than asked of each plugin: a plugin's own answer cannot tell two
            # configured instances of it apart, and the operator named them.
            item.source = source_identifier

            saved = storage_manager.save_content_item_outcome(item)
            result.items_synced += 1
            result.counts.record(saved.outcome)

            if mark_for_enrichment:
                try:
                    storage_manager.enrichment.mark_needed(saved.db_id)
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
            result.record_item_error(f"Failed to process '{safe_title}'")

    # Once per source and past the cap: the queue write is the same row for
    # every item, so a fault that hits one hits all, and it speaks for the run
    # rather than for an item.
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


def _configured_source_id(plugin_config: dict[str, Any]) -> str:
    source_id = plugin_config.get("_source_id")
    return str(source_id) if source_id else ""


def _error_result(
    plugin: SourcePlugin, plugin_config: dict[str, Any], message: str
) -> SyncResult:
    source_id = _configured_source_id(plugin_config)
    return SyncResult(
        source_name=humanize_source_id(source_id) if source_id else plugin.display_name,
        source_id=source_id,
        errors=[message],
    )


def execute_multi_source_sync(
    sources: list[tuple[SourcePlugin, dict[str, Any]]],
    storage_manager: StorageManager,
    progress_callback: SyncProgressCallback | None = None,
    result_callback: SyncResultCallback | None = None,
    mark_for_enrichment: bool = False,
    user_id: int = 1,
    max_workers: int = 1,
) -> list[SyncResult]:
    """Above one worker the progress and result callbacks may run concurrently."""

    def _run_one(plugin: SourcePlugin, plugin_config: dict[str, Any]) -> SyncResult:
        started_at = utc_now()
        result = _sync_one(plugin, plugin_config, progress_callback)
        result.started_at = started_at
        result.finished_at = utc_now()
        if result_callback:
            result_callback(result)
        heartbeat.done(_configured_source_id(plugin_config))
        return result

    def _sync_one(
        plugin: SourcePlugin,
        plugin_config: dict[str, Any],
        report: SyncProgressCallback | None,
    ) -> SyncResult:
        safe_plugin_name = sanitize_for_log(plugin.name)
        logger.info("[SYNC] === Starting sync for source: %s ===", safe_plugin_name)
        try:
            return execute_sync(
                plugin=plugin,
                plugin_config=plugin_config,
                storage_manager=storage_manager,
                progress_callback=report,
                mark_for_enrichment=mark_for_enrichment,
                user_id=user_id,
            )
        except SourceError as error:
            # Ours, and written for the operator: it names the setting to
            # change. Every plugin whose credential rides in the url scrubs
            # the request fault before wording one of these.
            logger.exception(
                "[SYNC] Sync failed for %s: %s",
                safe_plugin_name,
                exception_for_log(error),
            )
            # The CLI writes ``result.errors`` to a terminal and these quote a
            # ``requests`` fault, so the server's reason phrase could erase the
            # line the operator just read (CWE-117).
            return _error_result(plugin, plugin_config, sanitize_for_log(error.message))
        except Exception as error:
            # See sibling note in execute_sync: keep raw exception text
            # out of result.errors. Plugin failures can include
            # credential bytes in their messages. The traceback rides the
            # record instead, since a bug here has no other diagnosable trace.
            logger.exception(
                "[SYNC] Sync failed for %s: %s",
                safe_plugin_name,
                exception_for_log(error),
            )
            return _error_result(
                plugin, plugin_config, f"Sync failed for {plugin.name}"
            )

    effective_workers = min(max_workers, len(sources)) if sources else 1
    claimed = {
        source_id
        for source_id in (_configured_source_id(cfg) for _, cfg in sources)
        if source_id
    }

    with _ClaimHeartbeat(storage_manager, user_id, claimed) as heartbeat:
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
