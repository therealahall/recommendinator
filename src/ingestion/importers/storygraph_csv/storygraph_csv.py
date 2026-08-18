"""The StoryGraph library CSV export importer."""

from __future__ import annotations

import math
from collections.abc import Iterator

from src.ingestion.importers.base import (
    ImportedRow,
    Importer,
    ParsedRow,
    SkippedRow,
)
from src.ingestion.importers.rows import csv_field, parse_slashed_date, read_csv_rows
from src.models.content import ConsumptionStatus, ContentItem, ContentType

# did-not-finish maps to completed because a rated-then-abandoned book is a real
# preference signal; the raw status is kept in metadata so no fidelity is lost.
STATUS_MAP: dict[str, ConsumptionStatus] = {
    "read": ConsumptionStatus.COMPLETED,
    "currently-reading": ConsumptionStatus.CURRENTLY_CONSUMING,
    "to-read": ConsumptionStatus.UNREAD,
    "did-not-finish": ConsumptionStatus.COMPLETED,
}


class StorygraphCsvImporter(Importer):
    """The CSV from Manage Account -> Manage Your Data -> Export Library."""

    name = "storygraph_csv"
    display_name = "The StoryGraph (CSV Export)"
    description = "Import books from a The StoryGraph library CSV export"
    content_types = (ContentType.BOOK,)

    def parse(
        self, text: str, content_type: ContentType | None = None
    ) -> Iterator[ParsedRow]:
        _columns, rows = read_csv_rows(text)

        for row in rows:
            if row.missing:
                yield SkippedRow(
                    row.number, f"{row.missing} fields short of the header"
                )
                continue

            cells = row.fields
            title = csv_field(cells, "Title")
            if not title:
                yield SkippedRow(row.number, "no title")
                continue

            read_status = csv_field(cells, "Read Status").lower()
            last_date_read = csv_field(cells, "Last Date Read")
            isbn_uid = csv_field(cells, "ISBN/UID") or None

            metadata = {
                "isbn_uid": isbn_uid,
                "contributors": csv_field(cells, "Contributors") or None,
                "format": csv_field(cells, "Format") or None,
                "read_status": read_status or None,
                "read_count": csv_field(cells, "Read Count") or None,
                "date_added": csv_field(cells, "Date Added") or None,
                "last_date_read": last_date_read or None,
                "dates_read": csv_field(cells, "Dates Read") or None,
                "moods": csv_field(cells, "Moods") or None,
                "pace": csv_field(cells, "Pace") or None,
                "character_or_plot_driven": (
                    csv_field(cells, "Character- or Plot-Driven?") or None
                ),
                "strong_character_development": (
                    csv_field(cells, "Strong Character Development?") or None
                ),
                "loveable_characters": csv_field(cells, "Loveable Characters?") or None,
                "diverse_characters": csv_field(cells, "Diverse Characters?") or None,
                "flawed_characters": csv_field(cells, "Flawed Characters?") or None,
                "content_warnings": csv_field(cells, "Content Warnings") or None,
                "content_warning_description": (
                    csv_field(cells, "Content Warning Description") or None
                ),
                "tags": csv_field(cells, "Tags") or None,
                "owned": csv_field(cells, "Owned?") or None,
            }

            yield ImportedRow(
                row.number,
                ContentItem(
                    id=isbn_uid,
                    title=title,
                    author=csv_field(cells, "Authors") or None,
                    content_type=ContentType.BOOK,
                    rating=_parse_rating(csv_field(cells, "Star Rating")),
                    review=csv_field(cells, "Review") or None,
                    status=STATUS_MAP.get(read_status, ConsumptionStatus.UNREAD),
                    date_completed=parse_slashed_date(last_date_read),
                    metadata=metadata,
                    source=self.name,
                ),
            )


def _parse_rating(raw_rating: str) -> int | None:
    """StoryGraph rates in quarter stars, so the fraction is rounded half up.

    Deliberately not the shared normalizer: that one truncates, dropping the
    fractional star, and clamps a negative up to 1 instead of unrated.
    """
    if not raw_rating:
        return None
    try:
        value = float(raw_rating)
    except ValueError:
        return None
    if not math.isfinite(value):
        return None
    rounded = math.floor(value + 0.5)
    if rounded <= 0:
        return None
    return min(5, rounded)
