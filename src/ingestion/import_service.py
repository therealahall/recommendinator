"""One-shot file-import service.

Imports a single user-supplied file (a temp file written from a web upload, or
a real path passed to the CLI ``import --file`` flag) through the existing
ingestion pipeline and returns the same :class:`~src.ingestion.sync.SyncResult`
that :func:`~src.ingestion.sync.execute_sync` produces.

Unlike the syncable API sources, file-import plugins have no persistent
configuration: the file is supplied at invocation time, validated, run through
the pipeline once, and forgotten.

File lifecycle is the caller's responsibility. This service never creates or
deletes the file. A web handler that wrote an upload to a temp path must remove
that path after the call (on success or failure); the CLI passes a real user
path that must be left in place.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from src.ingestion.plugin_base import SourceError
from src.ingestion.registry import get_registry
from src.ingestion.sync import SyncProgressCallback, SyncResult, execute_sync

if TYPE_CHECKING:
    from pathlib import Path

    from src.llm.embeddings import EmbeddingGenerator
    from src.storage.manager import StorageManager

logger = logging.getLogger(__name__)

# Prefix of the FileImportError message raised when the file is missing or
# unreadable. The web handler matches on this to mask the (temp) path from the
# HTTP client while the CLI keeps the full message (a real user path it can fix).
FILE_NOT_READABLE_MESSAGE = "File not found or not readable"


class FileImportError(Exception):
    """Raised when a one-shot file import cannot be completed.

    Covers an unknown or non-file-import plugin, a missing or unreadable file,
    invalid import options, and a corrupt or unparseable file (wrapping the
    plugin's :class:`~src.ingestion.plugin_base.SourceError`).
    """


def import_file(
    plugin_name: str,
    file_path: Path,
    options: dict[str, Any],
    storage_manager: StorageManager,
    embedding_generator: EmbeddingGenerator | None = None,
    use_embeddings: bool = False,
    progress_callback: SyncProgressCallback | None = None,
    mark_for_enrichment: bool = False,
    user_id: int = 1,
) -> SyncResult:
    """Run a single file through a file-import plugin and the ingestion pipeline.

    Args:
        plugin_name: Registered name of a file-import plugin (e.g. ``goodreads``,
            ``csv_import``, ``json_import``, ``markdown_import``).
        file_path: Path to the file to import. The caller owns its lifecycle —
            this function neither creates nor deletes it.
        options: The non-path import options the user supplied, keyed by the
            plugin's ``get_config_schema()`` field names (e.g. ``content_type``).
            The file path is injected by this service, so callers must not set
            ``path`` here.
        storage_manager: Storage manager used to persist imported items.
        embedding_generator: Optional embedding generator.
        use_embeddings: Whether to generate embeddings for each item.
        progress_callback: Optional progress callback forwarded to the pipeline.
        mark_for_enrichment: Whether to mark imported items for enrichment.
        user_id: User ID for credential storage (default 1).

    Returns:
        The :class:`~src.ingestion.sync.SyncResult` from the ingestion pipeline.

    Raises:
        FileImportError: If the plugin is unknown or not a file-import plugin,
            the file is missing/unreadable, the options fail validation, or the
            file is corrupt/unparseable.
    """
    plugin = get_registry().get_plugin(plugin_name)
    if plugin is None:
        raise FileImportError(f"Unknown plugin: {plugin_name}")
    if not plugin.is_file_import:
        raise FileImportError(f"Plugin '{plugin_name}' does not support file import")

    if not file_path.is_file():
        raise FileImportError(f"{FILE_NOT_READABLE_MESSAGE}: {file_path}")

    plugin_config: dict[str, Any] = {**options, "path": str(file_path)}

    validation_errors = plugin.validate_config(
        plugin_config, storage=storage_manager, user_id=user_id
    )
    if validation_errors:
        raise FileImportError("; ".join(validation_errors))

    try:
        return execute_sync(
            plugin=plugin,
            plugin_config=plugin_config,
            storage_manager=storage_manager,
            embedding_generator=embedding_generator,
            use_embeddings=use_embeddings,
            progress_callback=progress_callback,
            mark_for_enrichment=mark_for_enrichment,
            user_id=user_id,
        )
    except SourceError as error:
        raise FileImportError(
            f"Failed to import file with '{plugin_name}': {error.message}"
        ) from error
