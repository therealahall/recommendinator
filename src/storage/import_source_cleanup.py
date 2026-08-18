"""Delete configured sources whose plugin became an upload format."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from src.ingestion.importers.registry import IMPORTERS
from src.utils.text import sanitize_for_log

if TYPE_CHECKING:
    from src.storage.manager import StorageManager

logger = logging.getLogger(__name__)

# Derived, not typed out: each removed plugin's name is now an importer's, and
# a plugin merely failing to import — or a private directory that did not mount
# this boot — must never be read as removed.
_REPLACED_BY_UPLOAD = frozenset(importer.name for importer in IMPORTERS)


def drop_sources_replaced_by_upload(storage: StorageManager, user_id: int = 1) -> None:
    """Remove ``source_configs`` rows for the file-import plugins that are gone.

    Safe on every startup. The items those sources imported live in
    ``content_items`` and are untouched.
    """
    for source in storage.sources.list(user_id):
        if source["plugin"] not in _REPLACED_BY_UPLOAD:
            continue
        source_id = source["source_id"]
        if not storage.sources.delete(user_id, source_id):
            continue
        # As deleting a source by hand does: a namesake created later must not
        # inherit this one's runs, its failure backoff or its credentials.
        storage.sync_runs.delete_for_source(user_id, source_id)
        storage.credentials.delete_for_source(user_id, source_id)
        logger.warning(
            "Removed file-import source '%s' (%s, %s) — upload the file instead.",
            sanitize_for_log(source_id),
            sanitize_for_log(source["plugin"]),
            sanitize_for_log(str(source["config"].get("path") or "no path")),
        )
