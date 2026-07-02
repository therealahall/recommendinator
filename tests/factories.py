"""Shared test factories for creating model instances with sensible defaults."""

from __future__ import annotations

from typing import Any
from unittest.mock import NonCallableMock

from src.models.content import ConsumptionStatus, ContentItem, ContentType


def back_mock_settings_store(storage: Any) -> dict[str, Any]:
    """Back a mocked StorageManager's settings methods with an in-memory store.

    Lets the real ``migrate_config_settings`` boot hook run against a mocked
    StorageManager without leaking state across tests: the store starts empty,
    so the DB overlay is a no-op and config resolves from const/YAML. Returns
    the backing dict so a test can pre-set leaves or assert what was written.

    A real ``StorageManager`` (temp-DB) already isolates itself, so this is a
    no-op for non-mock storage.
    """
    store: dict[str, Any] = {}
    if not isinstance(storage, NonCallableMock):
        return store

    storage.get_setting.side_effect = lambda key: store.get(key)
    storage.set_setting.side_effect = store.__setitem__
    storage.list_settings.side_effect = store.copy
    return store


def make_item(
    title: str = "Test Item",
    content_type: ContentType = ContentType.BOOK,
    status: ConsumptionStatus = ConsumptionStatus.COMPLETED,
    item_id: str | None = None,
    rating: int | None = None,
    author: str | None = None,
    review: str | None = None,
    metadata: dict[str, Any] | None = None,
    genres: str | None = None,
    source: str | None = None,
) -> ContentItem:
    """Create a ``ContentItem`` with minimal boilerplate.

    Parameters
    ----------
    genres:
        Shorthand — sets ``metadata["genre"]`` when provided.
    """
    effective_metadata: dict[str, Any] = metadata.copy() if metadata else {}
    if genres:
        effective_metadata["genre"] = genres

    return ContentItem(
        id=item_id,
        title=title,
        content_type=content_type,
        status=status,
        rating=rating,
        author=author,
        review=review,
        metadata=effective_metadata,
        source=source,
    )
