"""Web layer enrichment manager for background metadata enrichment.

Wraps the core EnrichmentManager to provide web-accessible status and control.
"""

from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING, Any

from src.enrichment.manager import EnrichmentJobStatus, EnrichmentManager
from src.models.content import ContentType

if TYPE_CHECKING:
    from src.storage.manager import StorageManager

logger = logging.getLogger(__name__)


class WebEnrichmentManager:
    """Web-facing manager for background enrichment jobs.

    Wraps EnrichmentManager to provide thread-safe web access
    and singleton behavior for the web application.

    Note: This is the public interface for the web layer.
    Its return types (``tuple[bool, str]``) differ from the core
    ``EnrichmentManager`` (``bool`` / ``None``) because the web layer
    needs human-readable status messages for HTTP responses.
    """

    def __init__(self) -> None:
        """Initialize the web enrichment manager."""
        self._lock = threading.Lock()
        self._manager: EnrichmentManager | None = None

    def start_enrichment(
        self,
        storage_manager: StorageManager,
        config: dict[str, Any],
        content_type: ContentType | None = None,
        user_id: int | None = None,
        include_not_found: bool = False,
    ) -> tuple[bool, str]:
        """Start a background enrichment job.

        Args:
            storage_manager: StorageManager instance
            config: Application configuration
            content_type: Optional content type filter
            user_id: Optional user ID filter
            include_not_found: Also retry items previously marked as not_found

        Returns:
            Tuple of (success, message)
        """
        with self._lock:
            # Check if already running
            if self._manager is not None:
                status = self._manager.get_status()
                if status.running:
                    return False, "Enrichment job already running"

            # Built and started without releasing the lock in between. Both
            # callers are threadpool workers, so releasing it lets the second
            # one see a manager that has not started yet, judge the server
            # idle, and start a manager of its own: two jobs against the same
            # provider key, each with its own rate limiters, and only the last
            # one stored visible to the status and stop endpoints. Nothing
            # long is held — ``start_enrichment`` spawns a daemon thread and
            # returns.
            self._manager = EnrichmentManager(storage_manager, config)
            started = self._manager.start_enrichment(
                content_type=content_type,
                user_id=user_id,
                include_not_found=include_not_found,
            )

        if started:
            type_desc = content_type.value if content_type else "all types"
            retry_msg = " (retrying not_found)" if include_not_found else ""
            return True, f"Started enrichment for {type_desc}{retry_msg}"
        else:
            return False, "Enrichment job already running"

    def stop_enrichment(self) -> tuple[bool, str]:
        """Stop the current enrichment job.

        Returns:
            Tuple of (success, message)
        """
        with self._lock:
            if self._manager is None:
                return False, "No enrichment job to stop"
            self._manager.stop_enrichment()
        return True, "Enrichment job stop requested"

    def get_status(self) -> EnrichmentJobStatus | None:
        """Get current enrichment job status.

        Returns:
            EnrichmentJobStatus or None if no job exists
        """
        with self._lock:
            if self._manager is None:
                return None
            return self._manager.get_status()

    def is_running(self) -> bool:
        """Check if enrichment is currently running.

        Returns:
            True if running, False otherwise
        """
        status = self.get_status()
        return status is not None and status.running


# Global enrichment manager instance
_enrichment_manager: WebEnrichmentManager | None = None

# Same check-then-set as ``get_sync_manager``, reachable from the API handlers
# and from the sync thread's auto-enrich callback: two builders means the job
# one of them started is invisible to the status endpoint reading the other.
_enrichment_manager_lock = threading.Lock()


def get_enrichment_manager() -> WebEnrichmentManager:
    """Get the global enrichment manager instance.

    Returns:
        The global WebEnrichmentManager instance.
    """
    global _enrichment_manager
    with _enrichment_manager_lock:
        if _enrichment_manager is None:
            _enrichment_manager = WebEnrichmentManager()
        return _enrichment_manager


def reset_enrichment_manager() -> None:
    """Reset the global enrichment manager instance.

    This is primarily used for testing to ensure a clean state between tests.
    """
    global _enrichment_manager
    _enrichment_manager = None
