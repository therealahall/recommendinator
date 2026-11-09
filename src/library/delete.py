from __future__ import annotations

from typing import TYPE_CHECKING, Any

from src.config.service import cover_cache_dir
from src.covers import cache

if TYPE_CHECKING:
    from src.storage.manager import StorageManager


def delete_item(
    storage: StorageManager, config: dict[str, Any], db_id: int, user_id: int
) -> list[int]:
    """Every id deleted, *db_id* first, their cached covers with them. Nothing
    marks the item gone, so a source that still has it brings it back."""
    deleted = storage.delete_content_item(db_id, user_id)
    for gone in deleted:
        cache.remove(cover_cache_dir(config), gone)
    return deleted
