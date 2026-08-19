"""Parse an uploaded file, save what parsed, report what happened.

An upload is not a sync: nothing here writes a ``source_configs`` row, takes a
cadence or records a ``sync_runs`` run.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from src.ingestion.importers.base import Importer, ImporterError, SkippedRow
from src.models.content import ContentType
from src.storage.manager import SaveCounts
from src.utils.text import exception_for_log, sanitize_for_log

if TYPE_CHECKING:
    from src.storage.manager import StorageManager

logger = logging.getLogger(__name__)

# A header the operator edited refuses every row, so an uncapped list would
# report one line per row of the export. The cap lives in the shared service,
# not the web layer, so both interfaces list the same misses.
MAX_REPORTED_ERRORS = 200


@dataclass
class ImportResult:
    """Five counts, capped per-row misses with a tally, and file-level notes.

    Each miss names its number in the importer's own unit: a file line, or an
    entry of a JSON array, which does not sit one per line.
    """

    importer: str
    content_type: ContentType | None
    counts: SaveCounts = field(default_factory=SaveCounts)
    skipped: int = 0
    failed: int = 0
    total_rows: int = 0
    errors: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    omitted_errors: int = 0

    def record_row_error(self, message: str) -> None:
        """Report a miss, or count it once the list is full."""
        if len(self.errors) < MAX_REPORTED_ERRORS:
            self.errors.append(message)
        else:
            self.omitted_errors += 1

    @property
    def added(self) -> int:
        return self.counts.added

    @property
    def updated(self) -> int:
        return self.counts.updated

    @property
    def unchanged(self) -> int:
        return self.counts.unchanged


def decode_import_text(data: bytes) -> str:
    """Decode an uploaded export, or refuse it naming the byte that is not.

    ``utf-8-sig``: a spreadsheet writes the export with a BOM, which left in
    place becomes part of the first column's name, so every row loses its title.
    """
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise ImporterError(
            f"File is not UTF-8 text (byte {error.start} is not). "
            "Re-save the export as UTF-8 and upload it again."
        ) from error


def import_file(
    storage: StorageManager,
    user_id: int,
    text: str,
    importer: Importer,
    content_type: ContentType | None = None,
    mark_for_enrichment: bool = False,
) -> ImportResult:
    """Import *text* as *importer*'s format, saving every row that parsed.

    ``ImporterError`` means the text is not that format. An empty file is not
    that: it reports zeros and no error, since "0 rows read" says it already.
    """
    result = ImportResult(
        importer=importer.name,
        # A format that handles one content type decides it; the rest ask.
        content_type=(
            importer.content_types[0] if importer.content_types else content_type
        ),
    )
    enrichment_queue_failures = 0

    for row in importer.parse(text, content_type):
        result.total_rows += 1

        if isinstance(row, SkippedRow):
            result.skipped += 1
            result.record_row_error(
                f"Skipped {row.unit} {row.number}: {sanitize_for_log(row.reason)}"
            )
            continue

        # Escaped once and shared by both sinks, as the sync loop does it: the
        # CLI writes these lines to a terminal, where a raw title off an
        # uploaded file could erase the line the operator just read (CWE-117).
        safe_title = sanitize_for_log(row.item.title)
        try:
            saved = storage.save_content_item_outcome(row.item, user_id=user_id)
        except Exception as error:
            result.failed += 1
            logger.warning(
                "[IMPORT] %s: %s %d failed: %s",
                importer.name,
                row.unit,
                row.number,
                exception_for_log(error),
            )
            # Named by class rather than quoted: a storage fault's words repeat
            # the parameters it was handed, and this list reaches the browser.
            result.record_row_error(
                f"Failed {row.unit} {row.number}: {type(error).__name__} "
                f"saving '{safe_title}'"
            )
            continue

        result.counts.record(saved.outcome)

        if mark_for_enrichment:
            try:
                storage.enrichment.mark_needed(saved.db_id)
            except Exception as error:
                enrichment_queue_failures += 1
                logger.warning(
                    "[IMPORT] Failed to mark '%s' for enrichment: %s",
                    safe_title,
                    exception_for_log(error),
                )

    if result.omitted_errors:
        result.errors.append(f"… and {result.omitted_errors} more")

    # A note, not an error: no row was refused, so listing it with the misses
    # heads a count of rows the file does not have. Once, not per item: the
    # queue write is the same row for every item.
    if enrichment_queue_failures:
        result.notes.append(
            f"Saved {enrichment_queue_failures} item(s) but could not queue them"
            " for enrichment"
        )

    logger.info(
        "[IMPORT] %s: %d row(s) read — %d added, %d updated, %d unchanged, "
        "%d skipped, %d failed",
        importer.name,
        result.total_rows,
        result.added,
        result.updated,
        result.unchanged,
        result.skipped,
        result.failed,
    )
    return result
