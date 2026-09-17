from __future__ import annotations

from src.models.content import ContentItem, ContentType
from src.storage.schema import (
    EnrichmentStatusDict,
    enrichment_reset_count,
    enrichment_scope_ids,
    get_enrichment_stats,
    get_enrichment_status,
    mark_enrichment_complete,
    mark_enrichment_failed,
    mark_enrichment_settled_failure,
    mark_item_needs_enrichment,
    requeue_enrichment_status,
)
from src.storage.sqlite_db import SQLiteDB


class EnrichmentStore:
    def __init__(self, sqlite_db: SQLiteDB) -> None:
        self._sqlite_db = sqlite_db

    def items_needing(
        self,
        content_type: ContentType | None = None,
        user_id: int | None = None,
        limit: int = 100,
        after_db_id: int | None = None,
        content_item_id: int | None = None,
    ) -> list[tuple[int, ContentItem]]:
        """An item with no ``enrichment_status`` row counts as queued, which is
        what puts newly ingested items in front of a provider. *content_item_id*
        narrows the queue to that one item, still only while it is queued.
        """
        return self._sqlite_db.get_items_needing_enrichment(
            content_type=content_type,
            user_id=user_id,
            limit=limit,
            after_db_id=after_db_id,
            content_item_id=content_item_id,
        )

    def count_needing(
        self,
        content_type: ContentType | None = None,
        user_id: int | None = None,
        content_item_id: int | None = None,
    ) -> int:
        return self._sqlite_db.count_items_needing_enrichment(
            content_type=content_type,
            user_id=user_id,
            content_item_id=content_item_id,
        )

    def settled_without_cover(self, user_id: int) -> int:
        return self._sqlite_db.count_settled_without_cover(user_id)

    def not_found_ids(
        self,
        content_type: ContentType | None = None,
        user_id: int | None = None,
    ) -> list[int]:
        return self._sqlite_db.get_not_found_ids(
            content_type=content_type,
            user_id=user_id,
        )

    def status(self, content_item_id: int) -> EnrichmentStatusDict | None:
        with self._sqlite_db.connection() as conn:
            return get_enrichment_status(conn, content_item_id)

    def mark_complete(self, content_item_id: int, provider: str, quality: str) -> None:
        """*quality* is "high", "medium" or "not_found"."""
        with self._sqlite_db.connection() as conn:
            mark_enrichment_complete(conn, content_item_id, provider, quality)

    def mark_failed(
        self,
        content_item_id: int,
        error: str,
        *,
        provider: str | None = None,
        quality: str | None = None,
    ) -> None:
        """A failure is an unknown outcome, not a settled miss, so the next run
        tries the item again.
        """
        with self._sqlite_db.connection() as conn:
            mark_enrichment_failed(
                conn, content_item_id, error, provider=provider, quality=quality
            )

    def mark_settled_failure(self, content_item_id: int, error: str) -> None:
        with self._sqlite_db.connection() as conn:
            mark_enrichment_settled_failure(conn, content_item_id, error)

    def mark_needed(self, content_item_id: int) -> None:
        with self._sqlite_db.connection() as conn:
            mark_item_needs_enrichment(conn, content_item_id)

    def requeue(self, content_item_id: int) -> None:
        """Back in front of a provider, every ledger row standing. ``mark_needed``
        is no substitute: it ignores an item that already has a row.
        """
        with self._sqlite_db.connection() as conn:
            requeue_enrichment_status(conn, content_item_id=content_item_id)

    def reset(
        self,
        provider: str | None = None,
        content_type: ContentType | None = None,
        user_id: int | None = None,
        content_item_id: int | None = None,
    ) -> int:
        """Each filter left as ``None`` widens the reset. What the providers in
        scope stated goes, and each item rebuilds on what is left standing.
        """
        content_type_str = content_type.value if content_type else None
        with self._sqlite_db.connection() as conn:
            scope = enrichment_scope_ids(
                conn, provider, content_type_str, user_id, content_item_id
            )
            requeued = requeue_enrichment_status(
                conn, provider, content_type_str, user_id, content_item_id
            )
        self._sqlite_db.reset_provider_writes(scope, provider)
        return requeued

    def reset_count(
        self,
        provider: str | None = None,
        content_type: ContentType | None = None,
        user_id: int | None = None,
        content_item_id: int | None = None,
    ) -> int:
        content_type_str = content_type.value if content_type else None
        with self._sqlite_db.connection() as conn:
            return enrichment_reset_count(
                conn, provider, content_type_str, user_id, content_item_id
            )

    def stats(self, user_id: int | None = None) -> dict[str, int | dict[str, int]]:
        """``pending`` and ``failed`` are both queued for retry and are reported
        apart: ``pending`` is the ones whose last attempt did not error.
        """
        with self._sqlite_db.connection() as conn:
            return get_enrichment_stats(conn, user_id)
