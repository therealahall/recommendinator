"""Background sync job manager for data source synchronization.

Manages sync jobs that run in background threads. Multiple jobs can run
concurrently as long as each is keyed by a distinct ``source`` label;
the manager rejects a duplicate start request for a source whose job is
still running.
"""

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from src.ingestion.sync import ALL_SOURCES_KEY, ALL_SOURCES_LABEL, sync_run_failed
from src.utils.text import exception_for_log, sanitize_for_log

logger = logging.getLogger(__name__)


class SyncStatus(str, Enum):
    """Status of a sync job."""

    IDLE = "idle"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class SyncError:
    source: str
    message: str


@dataclass
class _SourceProgress:
    """Per-source progress slot for a multi-source sync job."""

    items_processed: int = 0
    total_items: int | None = None
    current_item: str | None = None
    items_added: int = 0
    items_updated: int = 0
    items_unchanged: int = 0


@dataclass
class SyncJob:
    """Represents a sync job with its status and progress."""

    source: str
    status: SyncStatus = SyncStatus.IDLE
    started_at: datetime | None = None
    completed_at: datetime | None = None
    items_processed: int = 0
    total_items: int | None = None
    current_item: str | None = None
    current_source: str | None = None  # Currently syncing source (for multi-source)
    error_message: str | None = None
    errors: list[SyncError] = field(default_factory=list)
    # Keyed by humanised source name so the UI can render one row per source.
    source_progress: dict[str, _SourceProgress] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        # The umbrella's key is a sentinel; clients match jobs by name.
        job_name = ALL_SOURCES_LABEL if self.source == ALL_SOURCES_KEY else self.source
        progress_slots = list(self.source_progress.values())
        sources = [
            {
                "source": name,
                "items_processed": progress.items_processed,
                "total_items": progress.total_items,
                "current_item": progress.current_item,
                "progress_percent": (
                    int(progress.items_processed * 100 / progress.total_items)
                    if progress.total_items and progress.total_items > 0
                    else None
                ),
                "items_added": progress.items_added,
                "items_updated": progress.items_updated,
                "items_unchanged": progress.items_unchanged,
            }
            for name, progress in sorted(self.source_progress.items())
        ]
        return {
            "source": job_name,
            "status": self.status.value,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": (
                self.completed_at.isoformat() if self.completed_at else None
            ),
            "items_processed": self.items_processed,
            "total_items": self.total_items,
            "items_added": sum(entry.items_added for entry in progress_slots),
            "items_updated": sum(entry.items_updated for entry in progress_slots),
            "items_unchanged": sum(entry.items_unchanged for entry in progress_slots),
            "current_item": self.current_item,
            "current_source": self.current_source,
            "error_message": self.error_message,
            "progress_percent": (
                int(self.items_processed * 100 / self.total_items)
                if self.total_items and self.total_items > 0
                else None
            ),
            "errors": [
                {"source": error.source, "message": error.message}
                for error in self.errors
            ],
            "sources": sources,
        }


class SyncManager:
    """Manages background sync jobs for data sources.

    Multiple jobs can run at the same time as long as each is keyed by a
    distinct ``source`` label. ``start_sync`` rejects a duplicate start
    request for a source whose job is still in ``RUNNING`` state. Newer
    completed/failed entries replace older ones for the same source so
    ``get_status`` always reflects the latest result per source.
    """

    # Cap on retained completed/failed jobs. Running jobs are never
    # evicted (see ``_evict_history_locked``). Prevents an /api/update caller
    # from growing ``_jobs`` without bound by triggering syncs with arbitrary
    # source labels.
    _MAX_TERMINAL_HISTORY = 50

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._jobs: dict[str, SyncJob] = {}

    def is_running(self, source: str | None = None) -> bool:
        """Check whether a sync job is running.

        Args:
            source: When provided, check only the job for this source label.
                When omitted, return ``True`` if any job is currently running.
        """
        with self._lock:
            if source is not None:
                job = self._jobs.get(source)
                return job is not None and job.status == SyncStatus.RUNNING
            return any(job.status == SyncStatus.RUNNING for job in self._jobs.values())

    def get_status(self) -> dict[str, Any]:
        """Get the aggregate sync status across every tracked job."""
        # Compute the running flag inside the lock so a concurrent
        # ``_run_sync`` cannot transition a job between this snapshot and
        # the status decision and make the response say "idle" while a
        # job is still RUNNING. ``to_dict`` runs outside the lock — it
        # reads but does not mutate, and the brief drift between snapshot
        # and serialisation is acceptable for a polling endpoint.
        with self._lock:
            jobs = list(self._jobs.values())
            any_running = any(job.status == SyncStatus.RUNNING for job in jobs)
        return {
            "status": (
                SyncStatus.RUNNING.value if any_running else SyncStatus.IDLE.value
            ),
            "jobs": [job.to_dict() for job in sorted(jobs, key=lambda j: j.source)],
        }

    def start_sync(
        self,
        source: str,
        sync_function: Callable[[SyncJob], int],
        on_complete: Callable[[], None] | None = None,
    ) -> tuple[bool, str]:
        """Start a background sync job keyed by ``source``.

        A second start with the same ``source`` while the previous job is
        still running is rejected. Different ``source`` values can run
        concurrently.

        Args:
            source: Label that identifies the job (e.g. ``"Steam"`` or
                ``"All Sources"``). Used as the dict key.
            sync_function: Function that performs the sync. Should accept a
                SyncJob parameter for progress updates and return the count
                of items processed.
            on_complete: Optional callback to run after sync completes
                successfully.

        Returns:
            Tuple of ``(success, message)``. Success is ``False`` if a job
            for the same ``source`` is still running.
        """
        with self._lock:
            existing = self._jobs.get(source)
            if existing is not None and existing.status == SyncStatus.RUNNING:
                return False, f"Sync already in progress for {source}"

            self._jobs[source] = SyncJob(
                source=source,
                status=SyncStatus.RUNNING,
                started_at=datetime.now(),
            )
            # Eviction runs AFTER the new RUNNING job is inserted. The
            # eviction filter excludes RUNNING jobs by status, so the
            # freshly inserted entry cannot be the one removed even at
            # cap. This ordering is load-bearing — moving the eviction
            # call earlier or relaxing the RUNNING filter would risk
            # evicting the job whose thread is about to read it.
            self._evict_history_locked()

        thread = threading.Thread(
            target=self._run_sync,
            args=(source, sync_function, on_complete),
            daemon=True,
        )
        thread.start()

        return True, f"Started sync for {source}"

    def _evict_history_locked(self) -> None:
        """Drop the oldest non-running jobs once history exceeds the cap.

        Caller must already hold ``self._lock``.
        """
        terminal = [
            (label, job)
            for label, job in self._jobs.items()
            if job.status != SyncStatus.RUNNING
        ]
        excess = len(terminal) - self._MAX_TERMINAL_HISTORY
        if excess <= 0:
            return
        terminal.sort(
            key=lambda pair: pair[1].completed_at or datetime.min,
        )
        for label, _ in terminal[:excess]:
            self._jobs.pop(label, None)

    def _run_sync(
        self,
        source: str,
        sync_function: Callable[[SyncJob], int],
        on_complete: Callable[[], None] | None = None,
    ) -> None:
        """Run the sync function in a background thread for ``source``."""
        with self._lock:
            job = self._jobs.get(source)
        if job is None:
            return

        # ``source`` is the humanised source id POST /api/update supplies, and
        # nothing restricts its characters, so every sink below shares one
        # escaped copy.
        safe_source = sanitize_for_log(source)

        try:
            count = sync_function(job)
            with self._lock:
                job.completed_at = datetime.now()
                job.items_processed = count
                if sync_run_failed(count, job.errors):
                    job.status = SyncStatus.FAILED
                    job.error_message = job.errors[0].message
                else:
                    job.status = SyncStatus.COMPLETED
                final_status = job.status
                error_count = len(job.errors)

            if final_status == SyncStatus.COMPLETED:
                logger.info(
                    "Sync completed for %s: %d items processed", safe_source, count
                )
                if on_complete is not None:
                    try:
                        on_complete()
                    except Exception as callback_error:
                        logger.error(
                            "Sync on_complete callback failed: %s",
                            exception_for_log(callback_error),
                        )
            else:
                logger.warning(
                    "Sync for %s produced no items; marking failed (%d errors)",
                    safe_source,
                    error_count,
                )
        except Exception as error:
            with self._lock:
                job.status = SyncStatus.FAILED
                job.completed_at = datetime.now()
                job.error_message = "Sync failed due to an internal error"
            # No traceback: a plugin fault reaching here can quote the request
            # URL it failed on, credentials and all.
            logger.error(
                "Sync failed for %s: %s", safe_source, exception_for_log(error)
            )

    def update_progress(
        self,
        source: str,
        items_processed: int | None = None,
        total_items: int | None = None,
        current_item: str | None = None,
        current_source: str | None = None,
    ) -> None:
        """Update progress on the job keyed by ``source``.

        When ``current_source`` is provided, the per-source slot in the
        job's ``source_progress`` map is updated and the top-level
        ``items_processed`` / ``total_items`` are recomputed as the sum
        across that job's sources. When ``current_source`` is not provided,
        the top-level fields are written directly (legacy single-source
        path).

        Args:
            source: The job key (matches the ``source`` passed to
                ``start_sync``).
            items_processed: Number of items processed so far.
            total_items: Total number of items to process.
            current_item: Name of the item currently being processed.
            current_source: Name of the per-source slot within this job.
        """
        with self._lock:
            job = self._jobs.get(source)
            if job is None:
                return

            if current_source is not None:
                slot = job.source_progress.setdefault(current_source, _SourceProgress())
                if items_processed is not None:
                    slot.items_processed = items_processed
                if total_items is not None:
                    slot.total_items = total_items
                if current_item is not None:
                    slot.current_item = current_item
                job.current_source = current_source
                # Top-level current_item is intentionally last-write-wins
                # for the single-line "Currently syncing X" banner;
                # source_progress[*].current_item holds the per-source view.
                if current_item is not None:
                    job.current_item = current_item
                # Recompute aggregates from the per-source map so the
                # top-level counters reflect the sum across all sources
                # rather than racing on the most recent worker's update.
                job.items_processed = sum(
                    progress.items_processed
                    for progress in job.source_progress.values()
                )
                total_sum = sum(
                    progress.total_items or 0
                    for progress in job.source_progress.values()
                )
                # None until at least one source reported a known total —
                # avoids divide-by-zero in progress_percent rendering.
                job.total_items = total_sum if total_sum > 0 else None
            else:
                if items_processed is not None:
                    job.items_processed = items_processed
                if total_items is not None:
                    job.total_items = total_items
                if current_item is not None:
                    job.current_item = current_item

    def record_source_result(
        self,
        source: str,
        synced_source: str,
        items_added: int,
        items_updated: int,
        items_unchanged: int,
    ) -> None:
        """Record what one finished source did, on the job keyed by ``source``.

        ``synced_source`` names the per-source slot, the same key
        ``update_progress`` writes progress into.
        """
        with self._lock:
            job = self._jobs.get(source)
            if job is None:
                return
            slot = job.source_progress.setdefault(synced_source, _SourceProgress())
            slot.items_added = items_added
            slot.items_updated = items_updated
            slot.items_unchanged = items_unchanged

    def add_error(self, source: str, failed_source: str, error: str) -> None:
        """Append an error to the job keyed by ``source``.

        ``failed_source`` names the source the message came from, which the UI
        matches against its own rows to show it on the right one.
        """
        with self._lock:
            job = self._jobs.get(source)
            if job is not None:
                job.errors.append(SyncError(source=failed_source, message=error))


# Global sync manager instance
_sync_manager: SyncManager | None = None

# The lazy build below is a check-then-set, and both callers of it are plain
# ``def`` handlers running in threadpool workers. Unserialised, two requests on
# a cold process each build a manager of their own, and a job started through
# one is invisible to ``GET /api/sync/status`` served off the other — sync
# progress that never moves.
_sync_manager_lock = threading.Lock()


def get_sync_manager() -> SyncManager:
    """Get the global sync manager instance.

    Returns:
        The global SyncManager instance.
    """
    global _sync_manager
    with _sync_manager_lock:
        if _sync_manager is None:
            _sync_manager = SyncManager()
        return _sync_manager


def reset_sync_manager() -> None:
    """Reset the global sync manager instance.

    This is primarily used for testing to ensure a clean state between tests.
    """
    global _sync_manager
    _sync_manager = None
