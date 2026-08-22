"""Web layer enrichment manager for background metadata enrichment.

Holds no job state of its own: the job lives in the ``enrichment_job`` record,
which is what lets the Data tab see and stop a run the CLI started.
"""

from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING, Any

from src.enrichment.manager import EnrichmentJobStatus, EnrichmentManager, job_status
from src.models.content import ContentType

if TYPE_CHECKING:
    from src.storage.manager import StorageManager

logger = logging.getLogger(__name__)


class WebEnrichmentManager:
    """Web-facing control over the enrichment job.

    Its return types (``tuple[bool, str]``) differ from the core
    ``EnrichmentManager`` because the web layer needs human-readable messages
    for HTTP responses.
    """

    def start_enrichment(
        self,
        storage_manager: StorageManager,
        config: dict[str, Any],
        content_type: ContentType | None = None,
        user_id: int | None = None,
        include_not_found: bool = False,
    ) -> tuple[bool, str]:
        """Start a background enrichment job.

        Returns:
            Tuple of (success, message)
        """
        # The claim inside is the mutual exclusion, and it holds against the
        # CLI too — which a check-then-build here never could.
        manager = EnrichmentManager(storage_manager, config)
        started = manager.start_enrichment(
            content_type=content_type,
            user_id=user_id,
            include_not_found=include_not_found,
        )
        if not started:
            return False, "Enrichment job already running"

        type_desc = content_type.value if content_type else "all types"
        retry_msg = " (retrying not_found)" if include_not_found else ""
        return True, f"Started enrichment for {type_desc}{retry_msg}"

    def stop_enrichment(self, storage_manager: StorageManager) -> tuple[bool, str]:
        """Ask the running job to stop, whichever process owns it."""
        if not storage_manager.enrichment_jobs.request_stop():
            return False, "No enrichment job is running."
        return True, "Enrichment job stop requested"

    def get_status(self, storage_manager: StorageManager) -> EnrichmentJobStatus:
        """The live job, whoever started it."""
        return job_status(storage_manager)

    def is_running(self, storage_manager: StorageManager) -> bool:
        return self.get_status(storage_manager).running


# Global enrichment manager instance
_enrichment_manager: WebEnrichmentManager | None = None
_enrichment_manager_lock = threading.Lock()


def get_enrichment_manager() -> WebEnrichmentManager:
    """Get the global enrichment manager instance."""
    global _enrichment_manager
    with _enrichment_manager_lock:
        if _enrichment_manager is None:
            _enrichment_manager = WebEnrichmentManager()
        return _enrichment_manager


def reset_enrichment_manager() -> None:
    """Reset the global instance, so tests start from a clean one."""
    global _enrichment_manager
    _enrichment_manager = None
