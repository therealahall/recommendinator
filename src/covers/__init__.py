"""Cover art, fetched once and served from this app's own origin."""

from __future__ import annotations

from src.models.content import ContentItem

COVER_ROUTE = "/api/covers"


def cover_payload_url(item: ContentItem) -> str | None:
    if item.db_id is None or not item.cover_url:
        return None
    return f"{COVER_ROUTE}/{item.db_id}"
