"""The ``enrichment_status`` table: the metadata queue and each item's outcome."""

from __future__ import annotations

from src.models.content import ContentItem, ContentType
from src.storage.schema import (
    EnrichmentStatusDict,
    get_enrichment_stats,
    get_enrichment_status,
    mark_enrichment_complete,
    mark_enrichment_failed,
    mark_item_needs_enrichment,
    reset_enrichment_status,
)
from src.storage.sqlite_db import SQLiteDB


class EnrichmentStore:
    """The enrichment queue and per-item status. ``StorageManager.enrichment``."""

    def __init__(self, sqlite_db: SQLiteDB) -> None:
        self._sqlite_db = sqlite_db

    def items_needing(
        self,
        content_type: ContentType | None = None,
        user_id: int | None = None,
        limit: int = 100,
        include_not_found: bool = False,
        after_db_id: int | None = None,
    ) -> list[tuple[int, ContentItem]]:
        """Return ``(db_id, item)`` for items queued for enrichment.

        An item with no ``enrichment_status`` row counts as queued, which is
        what puts newly ingested items in front of a provider. *after_db_id*
        pages forward through the queue.
        """
        return self._sqlite_db.get_items_needing_enrichment(
            content_type=content_type,
            user_id=user_id,
            limit=limit,
            include_not_found=include_not_found,
            after_db_id=after_db_id,
        )

    def count_needing(
        self,
        content_type: ContentType | None = None,
        user_id: int | None = None,
    ) -> int:
        """Count the queued items :meth:`items_needing` would page through."""
        return self._sqlite_db.count_items_needing_enrichment(
            content_type=content_type,
            user_id=user_id,
        )

    def status(self, content_item_id: int) -> EnrichmentStatusDict | None:
        """Return one item's enrichment status, or ``None`` if it has no row."""
        with self._sqlite_db.connection() as conn:
            return get_enrichment_status(conn, content_item_id)

    def mark_complete(self, content_item_id: int, provider: str, quality: str) -> None:
        """Record a settled outcome: *quality* is "high", "medium" or "not_found"."""
        with self._sqlite_db.connection() as conn:
            mark_enrichment_complete(conn, content_item_id, provider, quality)

    def mark_failed(self, content_item_id: int, error: str) -> None:
        """Record a failed attempt, leaving the item queued for retry.

        A failure is an unknown outcome, not a settled miss, so the next run
        tries the item again.
        """
        with self._sqlite_db.connection() as conn:
            mark_enrichment_failed(conn, content_item_id, error)

    def mark_needed(self, content_item_id: int) -> None:
        """Queue an item for enrichment."""
        with self._sqlite_db.connection() as conn:
            mark_item_needs_enrichment(conn, content_item_id)

    def reset(
        self,
        provider: str | None = None,
        content_type: ContentType | None = None,
        user_id: int | None = None,
    ) -> int:
        """Re-queue enriched items, returning how many were reset.

        Each filter left as ``None`` widens the reset; all three unset resets
        every item.
        """
        with self._sqlite_db.connection() as conn:
            content_type_str = content_type.value if content_type else None
            return reset_enrichment_status(conn, provider, content_type_str, user_id)

    def stats(self, user_id: int | None = None) -> dict[str, int | dict[str, int]]:
        """Return ``total``/``enriched``/``pending``/``not_found``/``failed``
        counts plus ``by_provider`` and ``by_quality`` breakdowns.

        ``pending`` and ``failed`` are both queued for retry and are reported
        apart: ``pending`` is the ones whose last attempt did not error.
        """
        with self._sqlite_db.connection() as conn:
            return get_enrichment_stats(conn, user_id)
