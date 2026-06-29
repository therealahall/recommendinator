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

logger = logging.getLogger(__name__)


class SyncInProgressError(Exception):
    """Raised when an inline tracked job starts while one for the same key runs.

    ``run_import`` runs in the calling thread (so the caller can surface an
    error), unlike the fire-and-forget ``start_sync``; it signals a duplicate
    via this exception rather than a ``(success, message)`` tuple.
    """

    def __init__(self, source: str) -> None:
        super().__init__(f"A job for {source} is already running")
        self.source = source


class SyncStatus(str, Enum):
    """Status of a sync job."""

    IDLE = "idle"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class _SourceProgress:
    """Per-source progress slot for a multi-source sync job."""

    items_processed: int = 0
    total_items: int | None = None
    current_item: str | None = None


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
    errors: list[str] = field(default_factory=list)
    # Keyed by humanised source name so the UI can render one row per source.
    source_progress: dict[str, _SourceProgress] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert job to dictionary for API response."""
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
            }
            for name, progress in sorted(self.source_progress.items())
        ]
        return {
            "source": self.source,
            "status": self.status.value,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": (
                self.completed_at.isoformat() if self.completed_at else None
            ),
            "items_processed": self.items_processed,
            "total_items": self.total_items,
            "current_item": self.current_item,
            "current_source": self.current_source,
            "error_message": self.error_message,
            "progress_percent": (
                int(self.items_processed * 100 / self.total_items)
                if self.total_items and self.total_items > 0
                else None
            ),
            "error_count": len(self.errors),
            "errors": list(self.errors),
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
    # evicted (see ``_evict_history_locked``). Prevents an unauthenticated
    # /api/update caller from growing ``_jobs`` without bound by
    # triggering syncs with arbitrary source labels.
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

        try:
            count = sync_function(job)
            with self._lock:
                final_status = self._finalize_job_locked(job, count)
                error_count = len(job.errors)

            if final_status == SyncStatus.COMPLETED:
                logger.info("Sync completed for %s: %d items processed", source, count)
                if on_complete is not None:
                    try:
                        on_complete()
                    except Exception as callback_error:
                        logger.error(
                            "Sync on_complete callback failed: %s", callback_error
                        )
            else:
                logger.warning(
                    "Sync for %s produced no items; marking failed (%d errors)",
                    source,
                    error_count,
                )
        except Exception:
            with self._lock:
                job.status = SyncStatus.FAILED
                job.completed_at = datetime.now()
                job.error_message = "Sync failed due to an internal error"
            logger.error("Sync failed for %s", source, exc_info=True)

    def _finalize_job_locked(self, job: SyncJob, count: int) -> SyncStatus:
        """Transition a finished job to COMPLETED/FAILED. Caller holds the lock.

        A job that produced zero items but logged errors is a failure, not a
        success — plugins like Epic Games catch their own exceptions and
        report them via ``add_error``, and the UI banner branches on status.
        """
        job.completed_at = datetime.now()
        job.items_processed = count
        if count == 0 and job.errors:
            job.status = SyncStatus.FAILED
            job.error_message = job.errors[0]
        else:
            job.status = SyncStatus.COMPLETED
        return job.status

    def run_import(
        self,
        source: str,
        import_function: Callable[[SyncJob], int],
        on_complete: Callable[[], None] | None = None,
    ) -> int:
        """Run a one-shot import inline, tracked as a job for status polling.

        Unlike :meth:`start_sync` (background, fire-and-forget), this runs in
        the calling thread and re-raises any exception so the web handler can
        map a ``FileImportError`` onto an HTTP response. The job is registered
        and finalised exactly like a background sync, so :meth:`get_status`
        reports its progress and final state to a polling frontend.

        Args:
            source: Label that identifies the job (used as the dict key).
            import_function: Callable that performs the import, accepting the
                ``SyncJob`` for progress updates and returning the item count.
            on_complete: Optional callback run after a successful import.

        Returns:
            The number of items imported.

        Raises:
            SyncInProgressError: If a job for ``source`` is already running.
        """
        with self._lock:
            existing = self._jobs.get(source)
            if existing is not None and existing.status == SyncStatus.RUNNING:
                raise SyncInProgressError(source)
            job = SyncJob(
                source=source,
                status=SyncStatus.RUNNING,
                started_at=datetime.now(),
            )
            self._jobs[source] = job
            self._evict_history_locked()

        try:
            count = import_function(job)
        except Exception:
            with self._lock:
                job.status = SyncStatus.FAILED
                job.completed_at = datetime.now()
                if job.error_message is None and job.errors:
                    job.error_message = job.errors[0]
            raise

        with self._lock:
            final_status = self._finalize_job_locked(job, count)

        if final_status == SyncStatus.COMPLETED and on_complete is not None:
            try:
                on_complete()
            except Exception as callback_error:
                logger.error("Import on_complete callback failed: %s", callback_error)

        return count

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

    def add_error(self, source: str, error: str) -> None:
        """Append an error message to the job keyed by ``source``."""
        with self._lock:
            job = self._jobs.get(source)
            if job is not None:
                job.errors.append(error)


# Global sync manager instance
_sync_manager: SyncManager | None = None


def get_sync_manager() -> SyncManager:
    """Get the global sync manager instance.

    Returns:
        The global SyncManager instance.
    """
    global _sync_manager
    if _sync_manager is None:
        _sync_manager = SyncManager()
    return _sync_manager


def reset_sync_manager() -> None:
    """Reset the global sync manager instance.

    This is primarily used for testing to ensure a clean state between tests.
    """
    global _sync_manager
    _sync_manager = None
