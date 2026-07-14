"""Auto-migrate stored source labels and plugin names after a rename."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.storage.manager import StorageManager

logger = logging.getLogger(__name__)

# The default/example ingestion block was renamed from ``goodreads`` to
# ``goodreads_csv``. The value stored in ``content_items.source`` is the
# user's config-block KEY, not the plugin name, so this migration matches the
# literal historical key ``goodreads`` and rewrites only that exact value.
# Arbitrary user-chosen keys are intentionally left untouched.
_OLD_SOURCE = "goodreads"
_NEW_SOURCE = "goodreads_csv"

# The plugin itself was renamed from ``goodreads`` to ``goodreads_csv``. A
# source config moved into the database stores the PLUGIN NAME in
# ``source_configs.plugin``; the values coincide with the source labels above
# but name a different concept, so they get their own constants.
_OLD_PLUGIN = "goodreads"
_NEW_PLUGIN = "goodreads_csv"


def migrate_source_labels(
    storage: StorageManager,
    user_id: int = 1,
) -> None:
    """Relabel stored ``goodreads`` source values to ``goodreads_csv``.

    Updates every ``content_items`` row whose ``source`` is the literal
    historical key ``goodreads`` so it reflects the renamed default ingestion
    block ``goodreads_csv``. ChromaDB does not store the source, so no
    vector-store change is needed.

    This is safe to call on every startup: once the rows are relabeled the
    UPDATE matches nothing and the call is a silent no-op.

    Args:
        storage: StorageManager instance (provides SQLite access).
        user_id: User ID whose items are relabeled (default 1), matching the
            single-user scope of the credential migration.
    """
    with storage.connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE content_items SET source = ? WHERE source = ? AND user_id = ?",
            (_NEW_SOURCE, _OLD_SOURCE, user_id),
        )
        updated = cursor.rowcount
        if updated:
            conn.commit()
            logger.info(
                "Relabeled %d content item(s) from source %r to %r",
                updated,
                _OLD_SOURCE,
                _NEW_SOURCE,
            )


def migrate_source_config_plugins(
    storage: StorageManager,
    user_id: int = 1,
) -> None:
    """Relabel stored ``goodreads`` plugin values to ``goodreads_csv``.

    Updates every ``source_configs`` row whose ``plugin`` is the historical
    plugin name ``goodreads`` so a source config a user moved into the database
    keeps resolving after the plugin rename. Without this, once a
    ``plugin = 'goodreads'`` row exists ``get_plugin('goodreads')`` returns
    ``None`` and that source silently stops syncing.

    This is safe to call on every startup: once the rows are relabeled the
    UPDATE matches nothing and the call is a silent no-op.

    Args:
        storage: StorageManager instance (provides SQLite access).
        user_id: User ID whose source configs are relabeled (default 1),
            matching the single-user scope of ``migrate_source_labels`` and the
            user-scoped ``source_configs`` table.
    """
    with storage.connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE source_configs SET plugin = ? WHERE plugin = ? AND user_id = ?",
            (_NEW_PLUGIN, _OLD_PLUGIN, user_id),
        )
        updated = cursor.rowcount
        if updated:
            conn.commit()
            logger.info(
                "Relabeled %d source config(s) from plugin %r to %r",
                updated,
                _OLD_PLUGIN,
                _NEW_PLUGIN,
            )
