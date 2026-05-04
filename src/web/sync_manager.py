"""Background sync job manager for data source synchronization.

Manages sync jobs that run in background threads, tracking status and
preventing concurrent sync operations.
"""

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


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
            "sources": sources,
        }


class SyncManager:
    """Manages background sync jobs for data sources.

    Only one sync job can run at a time. The manager tracks job status
    and provides methods to start jobs and check their progress.
    """

    def __init__(self) -> None:
        """Initialize the sync manager."""
        self._lock = threading.Lock()
        self._current_job: SyncJob | None = None
        self._thread: threading.Thread | None = None

    def is_running(self) -> bool:
        """Check if a sync job is currently running.

        Returns:
            True if a job is running, False otherwise.
        """
        with self._lock:
            return (
                self._current_job is not None
                and self._current_job.status == SyncStatus.RUNNING
            )

    def get_status(self) -> dict[str, Any]:
        """Get the current sync status.

        Returns:
            Dictionary with sync status information.
        """
        with self._lock:
            if self._current_job is None:
                return {"status": SyncStatus.IDLE.value, "job": None}
            return {
                "status": self._current_job.status.value,
                "job": self._current_job.to_dict(),
            }

    def start_sync(
        self,
        source: str,
        sync_function: Callable[[SyncJob], int],
        on_complete: Callable[[], None] | None = None,
    ) -> tuple[bool, str]:
        """Start a background sync job.

        Args:
            source: Name of the source being synced (e.g., "steam", "goodreads").
            sync_function: Function that performs the sync. Should accept a SyncJob
                parameter for progress updates and return the count of items processed.
            on_complete: Optional callback to run after sync completes successfully.

        Returns:
            Tuple of (success, message). Success is False if a job is already running.
        """
        with self._lock:
            if (
                self._current_job is not None
                and self._current_job.status == SyncStatus.RUNNING
            ):
                return False, f"Sync already in progress for {self._current_job.source}"

            self._current_job = SyncJob(
                source=source,
                status=SyncStatus.RUNNING,
                started_at=datetime.now(),
            )

        # Start background thread
        self._thread = threading.Thread(
            target=self._run_sync,
            args=(sync_function, on_complete),
            daemon=True,
        )
        self._thread.start()

        return True, f"Started sync for {source}"

    def _run_sync(
        self,
        sync_function: Callable[[SyncJob], int],
        on_complete: Callable[[], None] | None = None,
    ) -> None:
        """Run the sync function in a background thread.

        Args:
            sync_function: Function that performs the sync.
            on_complete: Optional callback to run after sync completes successfully.
        """
        job = self._current_job
        if job is None:
            return

        try:
            count = sync_function(job)
            with self._lock:
                job.completed_at = datetime.now()
                job.items_processed = count
                # A sync that produced zero items but logged errors is a
                # failure, not a success — plugins like Epic Games catch
                # their own exceptions and report them via add_error, and
                # the UI banner branches on the status field.
                if count == 0 and job.errors:
                    job.status = SyncStatus.FAILED
                    job.error_message = job.errors[0]
                else:
                    job.status = SyncStatus.COMPLETED
                final_status = job.status
                error_count = len(job.errors)

            if final_status == SyncStatus.COMPLETED:
                logger.info(
                    "Sync completed for %s: %d items processed", job.source, count
                )
                # Run completion callback only on true success
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
                    job.source,
                    error_count,
                )
        except Exception:
            with self._lock:
                job.status = SyncStatus.FAILED
                job.completed_at = datetime.now()
                job.error_message = "Sync failed due to an internal error"
            logger.error("Sync failed for %s", job.source, exc_info=True)

    def update_progress(
        self,
        items_processed: int | None = None,
        total_items: int | None = None,
        current_item: str | None = None,
        current_source: str | None = None,
    ) -> None:
        """Update progress of the current job.

        Thread-safe method to update job progress from the sync function.
        When ``current_source`` is provided, the per-source slot in
        ``source_progress`` is updated and the top-level ``items_processed``
        / ``total_items`` are recomputed as the sum across all sources.
        When ``current_source`` is not provided, the top-level fields are
        written directly (legacy single-source path).

        Args:
            items_processed: Number of items processed so far.
            total_items: Total number of items to process.
            current_item: Name of the item currently being processed.
            current_source: Name of the source currently being synced.
        """
        with self._lock:
            if self._current_job is None:
                return

            if current_source is not None:
                slot = self._current_job.source_progress.setdefault(
                    current_source, _SourceProgress()
                )
                if items_processed is not None:
                    slot.items_processed = items_processed
                if total_items is not None:
                    slot.total_items = total_items
                if current_item is not None:
                    slot.current_item = current_item
                self._current_job.current_source = current_source
                # Top-level current_item is intentionally last-write-wins
                # for the single-line "Currently syncing X" status banner;
                # source_progress[*].current_item holds the per-source view.
                if current_item is not None:
                    self._current_job.current_item = current_item
                # Recompute aggregates from the per-source map so the
                # top-level counters reflect the sum across all sources
                # rather than racing on the most recent worker's update.
                self._current_job.items_processed = sum(
                    progress.items_processed
                    for progress in self._current_job.source_progress.values()
                )
                total_sum = sum(
                    progress.total_items or 0
                    for progress in self._current_job.source_progress.values()
                )
                # None until at least one source reported a known total —
                # avoids divide-by-zero in progress_percent rendering.
                self._current_job.total_items = total_sum if total_sum > 0 else None
            else:
                if items_processed is not None:
                    self._current_job.items_processed = items_processed
                if total_items is not None:
                    self._current_job.total_items = total_items
                if current_item is not None:
                    self._current_job.current_item = current_item

    def add_error(self, error: str) -> None:
        """Add an error message to the current job.

        Args:
            error: Error message to add.
        """
        with self._lock:
            if self._current_job is not None:
                self._current_job.errors.append(error)


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
