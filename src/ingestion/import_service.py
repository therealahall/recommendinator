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

import csv
from typing import TYPE_CHECKING, Any

from src.ingestion.plugin_base import SourceError
from src.ingestion.registry import get_registry
from src.ingestion.sync import SyncProgressCallback, SyncResult, execute_sync

if TYPE_CHECKING:
    from pathlib import Path

    from src.llm.embeddings import EmbeddingGenerator
    from src.storage.manager import StorageManager

# Prefix of the FileImportError message raised when the file is missing or
# unreadable. The CLI prints that full message (a real user path it can fix);
# the client never sees it — every FileImportError carries a separate,
# path-free ``client_detail`` for that.
FILE_NOT_READABLE_MESSAGE = "File not found or not readable"

# Client-facing details. These are the only import failure strings that reach
# an HTTP response: plugin messages embed the server-side temp path and raw
# parser text, both of which fingerprint internals, so they stay in the log.
UNSUPPORTED_SOURCE_DETAIL = "Unknown or unsupported import source"
UNREADABLE_FILE_DETAIL = "The uploaded file could not be read"

# Everything a file parser can fail with. ``SourceError`` is what a
# well-behaved plugin raises; the rest is what escapes when one misses a case
# — a non-UTF-8 export or malformed JSON (both ``ValueError`` subclasses),
# deeply nested JSON (``RecursionError``), a csv module fault. Each of those
# means the supplied file is wrong, so they belong on the 4xx path; anything
# else (a storage or programming fault) stays an unhandled 500.
#
# ``OSError`` is deliberately absent. Every importer reads through
# ``src.ingestion.file_reading``, which already converts a missing, unreadable
# or directory path into a ``SourceError`` at the point of the read. What is
# left raising ``OSError`` inside ``execute_sync`` is the storage write and the
# embedding generation — a full disk or a permission fault, which must not come
# back telling the user to check their file.
_PARSE_FAILURES = (SourceError, RecursionError, ValueError, csv.Error)

# Advisory returned by import_warning for an import that parsed cleanly but
# produced nothing. Shared by both interfaces so the wording matches.
NO_ITEMS_WARNING = (
    "No items were found in the file. Check that it is the export you meant "
    "to upload, and that it is not empty."
)

# Bounds on the caller-supplied option keys echoed back in the unknown-option
# detail. Naming the offending key is what makes the error actionable, but
# Starlette accepts up to 1000 form fields whose names may each be kilobytes
# long, so an unbounded echo turns a 400 into a megabyte-scale amplifier.
_MAX_ECHOED_KEYS = 5
_MAX_ECHOED_KEY_LENGTH = 40


class FileImportError(Exception):
    """Raised when a one-shot file import cannot be completed.

    Covers an unknown or non-file-import plugin, a missing or unreadable file,
    invalid import options, and a corrupt or unparseable file (wrapping the
    plugin's :class:`~src.ingestion.plugin_base.SourceError`).

    Attributes:
        client_detail: The safe rendering of this failure. The full message
            names the file and forwards plugin text, so it belongs in the
            server log and in CLI output (where the path is the user's own);
            ``client_detail`` is what an HTTP response may carry.
    """

    def __init__(self, message: str, client_detail: str) -> None:
        """Initialize FileImportError.

        Args:
            message: Full, diagnostic message (may name paths or plugin text).
            client_detail: Path-free detail safe to return over HTTP.
        """
        super().__init__(message)
        self.client_detail = client_detail


def _echo_keys(keys: list[str]) -> str:
    """Render caller-supplied option keys for an error message, bounded.

    Both the number of keys and each key's length are capped, so the size of
    the rendering never scales with what the caller sent.
    """
    shown = [key[:_MAX_ECHOED_KEY_LENGTH] for key in keys[:_MAX_ECHOED_KEYS]]
    remaining = len(keys) - len(shown)
    if remaining > 0:
        shown.append(f"and {remaining} more")
    return ", ".join(shown)


def import_warning(result: SyncResult) -> str | None:
    """Return the advisory for an import result, or None if it needs none.

    An import that parses cleanly but yields nothing is still a success, so it
    carries a warning rather than an error — otherwise the user sees a green
    zero with no explanation.

    Args:
        result: The result of a completed :func:`import_file` call.

    Returns:
        :data:`NO_ITEMS_WARNING` when nothing was imported and no row failed;
        otherwise ``None``. An import whose rows all failed is already
        explained by ``result.errors``, so it is not warned about as well.
    """
    if result.items_synced > 0 or result.errors:
        return None
    return NO_ITEMS_WARNING


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
        plugin_name: Registered name of a file-import plugin (e.g.
            ``goodreads_csv``, ``storygraph_csv``, ``csv_import``,
            ``json_import``, ``markdown_import``).
        file_path: Path to the file to import. The caller owns its lifecycle —
            this function neither creates nor deletes it.
        options: The non-path import options the user supplied. Every key must
            be one of the plugin's ``get_config_schema()`` field names (e.g.
            ``content_type``); anything else is refused. The file path is
            injected by this service, so ``path`` is never accepted here.
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
            the file is missing/unreadable, an option is undeclared or fails
            validation, or the file is corrupt/unparseable.
    """
    plugin = get_registry().get_plugin(plugin_name)
    if plugin is None:
        raise FileImportError(
            f"Unknown plugin: {plugin_name}", UNSUPPORTED_SOURCE_DETAIL
        )
    if not plugin.is_file_import:
        raise FileImportError(
            f"Plugin '{plugin_name}' does not support file import",
            UNSUPPORTED_SOURCE_DETAIL,
        )

    if not file_path.is_file():
        raise FileImportError(
            f"{FILE_NOT_READABLE_MESSAGE}: {file_path}", UNREADABLE_FILE_DETAIL
        )

    # The plugin's schema is the only set of option keys allowed through. Both
    # interfaces gate here rather than each filtering its own way: without it,
    # ``_source_id`` would reach the pipeline and relabel every imported item.
    schema_names = {field.name for field in plugin.get_config_schema()}
    unknown = sorted(set(options) - schema_names)
    if unknown:
        detail = (
            f"Unknown import option(s) for '{plugin_name}': {_echo_keys(unknown)}. "
            f"This source accepts: {', '.join(sorted(schema_names)) or 'no options'}."
        )
        raise FileImportError(detail, detail)

    # ``path`` last: the caller's options can never redirect the import at
    # another file, even if a plugin one day declares a ``path`` field.
    plugin_config: dict[str, Any] = {**options, "path": str(file_path)}

    validation_errors = plugin.validate_config(
        plugin_config, storage=storage_manager, user_id=user_id
    )
    if validation_errors:
        # The one failure worth quoting verbatim: validation errors describe
        # the plugin's own option schema (which the client just supplied
        # values for), not runtime state.
        joined = "; ".join(validation_errors)
        raise FileImportError(joined, joined)

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
    except _PARSE_FAILURES as error:
        detail = (
            error.message
            if isinstance(error, SourceError)
            else f"{type(error).__name__}: {error}"
        )
        raise FileImportError(
            f"Failed to import file with '{plugin_name}': {detail}",
            f"Failed to import file with '{plugin_name}'. Check that the file "
            "is the export that importer expects, and that it is UTF-8 text.",
        ) from error
