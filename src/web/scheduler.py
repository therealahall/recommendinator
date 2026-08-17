"""Cadence-driven sync dispatch: one tick a minute, no request involved."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from src.ingestion.schedule import is_due, resolve_interval
from src.ingestion.sync import ALL_SOURCES_LABEL
from src.sources.service import resolve_inputs
from src.utils.dates import parse_iso_timestamp, utc_now
from src.utils.text import humanize_source_id, sanitize_for_log
from src.web.state import get_config, get_storage
from src.web.sync_dispatch import build_sync_job
from src.web.sync_manager import get_sync_manager

if TYPE_CHECKING:
    from src.storage.manager import StorageManager

logger = logging.getLogger(__name__)

TICK_SECONDS = 60


def dispatch_due_syncs(
    storage: StorageManager, config: dict[str, Any], user_id: int = 1
) -> None:
    sync_manager = get_sync_manager()
    # ``start_sync``'s per-label refusal cannot see a source syncing under the
    # umbrella label, so that half of the overlap check is asked here. A skip
    # records nothing: it attempted nothing, so it is not an outcome.
    if sync_manager.is_running(ALL_SOURCES_LABEL):
        logger.debug("Skipping scheduler tick: a run over every source is in flight")
        return

    rows = {row["source_id"]: row for row in storage.sources.list(user_id)}
    latest_runs = storage.sync_runs.latest_per_source(user_id)
    now = utc_now()

    for entry in resolve_inputs(config, storage=storage, user_id=user_id):
        row = rows.get(entry.source_id)
        interval = resolve_interval(
            row["sync_interval"] if row is not None else None, entry.plugin
        )
        latest_run = latest_runs.get(entry.source_id)
        failures = (
            storage.sync_runs.consecutive_failures(user_id, entry.source_id)
            if latest_run is not None and latest_run["status"] != "completed"
            else 0
        )
        last_finished_at = (
            parse_iso_timestamp(latest_run["finished_at"])
            if latest_run is not None
            else None
        )
        if not is_due(now, last_finished_at, interval, failures):
            continue

        label = humanize_source_id(entry.source_id)
        dispatch = build_sync_job(sync_manager, label, [entry], storage, config)
        started, message = sync_manager.start_sync(
            label, dispatch.run, on_complete=dispatch.on_complete
        )
        # No config validation before dispatch, unlike the single-source POST:
        # nobody is here to read a refusal, while the failed run a broken
        # source records backs it off and shows in its history.
        if started:
            logger.info("Scheduled sync started for %s", sanitize_for_log(label))
        else:
            logger.info("Scheduled sync declined: %s", sanitize_for_log(message))


class SyncScheduler:
    def __init__(self) -> None:
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self.running:
            return
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task is None:
            return
        if not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def _tick(self) -> None:
        storage, config = get_storage(), get_config()
        if storage is None or config is None:
            return
        try:
            dispatch_due_syncs(storage, config)
        except Exception:
            # Swallowed so the loop survives: the next tick is a minute away.
            logger.exception("Scheduled sync tick failed")

    async def _run(self) -> None:
        logger.info("Sync scheduler started, checking every %ds", TICK_SECONDS)
        try:
            while True:
                await asyncio.sleep(TICK_SECONDS)
                # Off the loop: the tick reads SQLite and starts sync threads.
                await asyncio.to_thread(self._tick)
        except asyncio.CancelledError:
            logger.info("Sync scheduler stopped")
            raise


#: One per process, started and stopped by the app's lifespan.
sync_scheduler = SyncScheduler()
