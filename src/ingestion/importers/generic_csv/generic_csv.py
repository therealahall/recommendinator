"""The prescriptive-template CSV importer, one content type per file."""

from __future__ import annotations

import logging
from collections.abc import Iterator, Mapping
from typing import Any

from src.ingestion.importers.base import (
    ImportedRow,
    Importer,
    ImporterError,
    ParsedRow,
    SkippedRow,
)
from src.ingestion.importers.rows import (
    csv_field,
    normalize_rating,
    parse_completion_date,
    parse_ignored_field,
    parse_seasons_watched,
    read_csv_rows,
)
from src.models.content import ConsumptionStatus, ContentItem, ContentType
from src.models.templates import (
    COMMON_COLUMNS,
    CONTENT_TYPE_COLUMNS,
    CREATOR_COLUMNS,
    CREATOR_FIELD,
    LIST_VALUED_COLUMNS,
    STATUS_MAP,
)
from src.utils.csv_formula import strip_csv_formula_guard
from src.utils.text import sanitize_for_log

logger = logging.getLogger(__name__)


class CsvImporter(Importer):
    """A CSV matching the template for one content type."""

    name = "csv_import"
    display_name = "CSV Import"
    description = "Import from CSV file"
    content_types = ()

    def parse(
        self, text: str, content_type: ContentType | None = None
    ) -> Iterator[ParsedRow]:
        resolved = self.required_content_type(content_type)
        columns, rows = read_csv_rows(text)
        if rows:
            _check_columns(columns, resolved)

        creator_column = CREATOR_FIELD[resolved.value]

        for row in rows:
            if row.missing:
                yield SkippedRow(
                    row.number, f"{row.missing} fields short of the header"
                )
                continue

            cells = {
                column: strip_csv_formula_guard(value)
                for column, value in row.fields.items()
            }
            title = csv_field(cells, "title")
            if not title:
                yield SkippedRow(row.number, "no title")
                continue

            notes = csv_field(cells, "notes") or None
            metadata = _build_metadata(cells, resolved)
            if notes:
                metadata["notes"] = notes
            if resolved is ContentType.TV_SHOW and "seasons_watched" in metadata:
                metadata["seasons_watched"] = parse_seasons_watched(
                    metadata["seasons_watched"]
                )

            yield ImportedRow(
                row.number,
                ContentItem(
                    title=title,
                    author=csv_field(cells, creator_column) or None,
                    content_type=resolved,
                    rating=normalize_rating(csv_field(cells, "rating")),
                    review=csv_field(cells, "review") or None,
                    status=STATUS_MAP.get(
                        csv_field(cells, "status").lower(), ConsumptionStatus.UNREAD
                    ),
                    date_completed=parse_completion_date(
                        csv_field(cells, "date_completed"), title
                    ),
                    ignored=parse_ignored_field(cells),
                    metadata=metadata,
                    source=self.name,
                ),
            )


def _check_columns(columns: tuple[str, ...], content_type: ContentType) -> None:
    """Without a title there is nothing to import, so that one is fatal."""
    if "title" not in columns:
        raise ImporterError("CSV missing required column: title")

    expected = COMMON_COLUMNS | set(CONTENT_TYPE_COLUMNS[content_type.value])
    unknown = set(columns) - expected
    if unknown:
        logger.warning(
            "CSV contains unknown columns that will be ignored: %s",
            sanitize_for_log(", ".join(sorted(unknown))),
        )


def _build_metadata(
    cells: Mapping[str, Any], content_type: ContentType
) -> dict[str, Any]:
    """Storing a value under the key the library uses for it is what lands it in
    its detail-table column rather than in the free-form blob. An empty cell
    says nothing about the field and is left out entirely.
    """
    metadata: dict[str, Any] = {}

    for column, metadata_key in CONTENT_TYPE_COLUMNS[content_type.value].items():
        if column in CREATOR_COLUMNS:
            continue
        value = csv_field(cells, column)
        if value:
            metadata[metadata_key] = [value] if column in LIST_VALUED_COLUMNS else value

    return metadata
