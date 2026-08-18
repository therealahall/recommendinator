"""Parse an uploaded file, save what parsed, report what happened.

An upload is not a sync: nothing here writes a ``source_configs`` row, takes a
cadence or records a ``sync_runs`` run.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from src.ingestion.importers.base import Importer, SkippedRow
from src.models.content import ContentType
from src.storage.manager import SaveCounts
from src.utils.text import exception_for_log, sanitize_for_log

if TYPE_CHECKING:
    from src.storage.manager import StorageManager

logger = logging.getLogger(__name__)


@dataclass
class ImportResult:
    """Five counts, and a line per row that missed.

    Every message says "line", never "row": the number is the file line a
    record ends on, which a quoted newline makes different from a
    spreadsheet's row.
    """

    importer: str
    content_type: ContentType | None
    counts: SaveCounts = field(default_factory=SaveCounts)
    skipped: int = 0
    failed: int = 0
    total_rows: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def added(self) -> int:
        return self.counts.added

    @property
    def updated(self) -> int:
        return self.counts.updated

    @property
    def unchanged(self) -> int:
        return self.counts.unchanged


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
            result.errors.append(
                f"Skipped line {row.number}: {sanitize_for_log(row.reason)}"
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
                "[IMPORT] %s: line %d failed: %s",
                importer.name,
                row.number,
                exception_for_log(error),
            )
            # Named by class rather than quoted: a storage fault's words repeat
            # the parameters it was handed, and this list reaches the browser.
            result.errors.append(
                f"Failed line {row.number}: {type(error).__name__} "
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

    # Once, not per item: the queue write is the same row for every item, so a
    # fault that hits one hits all and would otherwise report thousands.
    if enrichment_queue_failures:
        result.errors.append(
            f"Saved {enrichment_queue_failures} item(s) but could not queue them"
            " for enrichment"
        )

    logger.info(
        "[IMPORT] %s: %d line(s) read — %d added, %d updated, %d unchanged, "
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
