"""The Goodreads library CSV export importer."""

from __future__ import annotations

from collections.abc import Iterator

from src.ingestion.importers.base import (
    ImportedRow,
    Importer,
    ParsedRow,
    SkippedRow,
)
from src.ingestion.importers.rows import csv_field, parse_slashed_date, read_csv_rows
from src.models.content import ConsumptionStatus, ContentItem, ContentType
from src.utils.series import split_series_from_title

SHELF_STATUS: dict[str, ConsumptionStatus] = {
    "read": ConsumptionStatus.COMPLETED,
    "currently-reading": ConsumptionStatus.CURRENTLY_CONSUMING,
}


class GoodreadsCsvImporter(Importer):
    """The CSV Goodreads emails you from Import/Export."""

    name = "goodreads_csv"
    display_name = "Goodreads (CSV Export)"
    description = "Import books from a Goodreads library CSV export"
    content_types = (ContentType.BOOK,)

    def parse(
        self, text: str, content_type: ContentType | None = None
    ) -> Iterator[ParsedRow]:
        _columns, rows = read_csv_rows(text)

        for row in rows:
            if row.mismatch:
                yield SkippedRow(row.number, row.mismatch)
                continue

            raw_title = csv_field(row.fields, "Title")
            if not raw_title:
                yield SkippedRow(row.number, "no title")
                continue

            title, series = split_series_from_title(raw_title)
            book_id = csv_field(row.fields, "Book Id")
            metadata = {
                "book_id": book_id,
                "isbn": csv_field(row.fields, "ISBN") or None,
                "isbn13": csv_field(row.fields, "ISBN13") or None,
                "pages": csv_field(row.fields, "Number of Pages") or None,
                "year_published": csv_field(row.fields, "Year Published") or None,
                "publisher": csv_field(row.fields, "Publisher") or None,
                **series,
            }

            yield ImportedRow(
                row.number,
                ContentItem(
                    id=book_id,
                    title=title,
                    author=csv_field(row.fields, "Author") or None,
                    content_type=ContentType.BOOK,
                    rating=_parse_rating(csv_field(row.fields, "My Rating")),
                    review=csv_field(row.fields, "My Review") or None,
                    status=SHELF_STATUS.get(
                        csv_field(row.fields, "Exclusive Shelf").lower(),
                        ConsumptionStatus.UNREAD,
                    ),
                    date_completed=parse_slashed_date(
                        csv_field(row.fields, "Date Read")
                    ),
                    metadata=metadata,
                    source=self.name,
                ),
            )


def _parse_rating(raw_rating: str) -> int | None:
    """Goodreads writes 0 for a book you never rated."""
    if not raw_rating or raw_rating == "0":
        return None
    try:
        return int(raw_rating)
    except ValueError:
        return None
