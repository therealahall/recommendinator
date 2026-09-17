from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING

from src.ingestion.sync import MAX_REPORTED_ERRORS
from src.recommendations.profile import refresh_profile
from src.storage.rebuild_jobs import LibraryRebuildRecord

if TYPE_CHECKING:
    from src.storage.manager import StorageManager

logger = logging.getLogger(__name__)


def rebuild_library(storage: StorageManager) -> LibraryRebuildRecord:
    record = LibraryRebuildRecord(running=True)
    targets = storage.rebuildable_items()
    record.total = len(targets)

    stopped = False
    for db_id, title in targets:
        if storage.rebuild_jobs.stop_requested():
            stopped = True
            break
        record.current_item = title
        storage.rebuild_jobs.heartbeat(record)
        try:
            if storage.rebuild_item(db_id):
                record.changed += 1
        except Exception as error:
            logger.exception("Rebuilding item %s failed", db_id)
            if len(record.errors) < MAX_REPORTED_ERRORS:
                record.errors.append(f"{title}: {type(error).__name__}")
        record.processed += 1

    if record.changed:
        refresh_profile(storage)
    record.current_item = ""
    record.running = False
    record.completed = not stopped
    record.cancelled = stopped
    return record


def run_library_rebuild(storage: StorageManager) -> LibraryRebuildRecord:
    while True:
        try:
            record = rebuild_library(storage)
        except Exception:
            logger.exception("Library rebuild failed")
            record = storage.rebuild_jobs.read()
            record.errors.append("the rebuild stopped on an error")
            storage.rebuild_jobs.request_rerun()
            storage.rebuild_jobs.finish(record)
            return record
        else:
            if record.completed and storage.rebuild_jobs.take_rerun():
                continue
        storage.rebuild_jobs.finish(record)
        if record.cancelled or not storage.rebuild_jobs.rerun_requested():
            return record
        if not storage.rebuild_jobs.claim():
            return record


def start_library_rebuild(storage: StorageManager) -> LibraryRebuildRecord | None:
    if not storage.rebuild_jobs.claim():
        return None

    threading.Thread(target=run_library_rebuild, args=(storage,), daemon=True).start()
    return storage.rebuild_jobs.read()


def owe_library_rebuild(storage: StorageManager) -> None:
    storage.rebuild_jobs.request_rerun()


def start_owed_rebuild(storage: StorageManager) -> LibraryRebuildRecord | None:
    if not storage.rebuild_jobs.rerun_requested():
        return None
    return start_library_rebuild(storage)
