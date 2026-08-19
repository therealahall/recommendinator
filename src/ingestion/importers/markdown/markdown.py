"""The Markdown importer: a list per status section."""

from __future__ import annotations

import re
from collections.abc import Iterator

from src.ingestion.importers.base import (
    ImportedRow,
    Importer,
    ParsedRow,
    SkippedRow,
)
from src.ingestion.importers.rows import normalize_rating, parse_completion_date
from src.models.content import ConsumptionStatus, ContentItem, ContentType

SECTION_STATUS_MAP: dict[str, ConsumptionStatus] = {
    "completed": ConsumptionStatus.COMPLETED,
    "in progress": ConsumptionStatus.CURRENTLY_CONSUMING,
    "currently reading": ConsumptionStatus.CURRENTLY_CONSUMING,
    "currently watching": ConsumptionStatus.CURRENTLY_CONSUMING,
    "currently playing": ConsumptionStatus.CURRENTLY_CONSUMING,
    "to read": ConsumptionStatus.UNREAD,
    "to watch": ConsumptionStatus.UNREAD,
    "to play": ConsumptionStatus.UNREAD,
    "wishlist": ConsumptionStatus.UNREAD,
    "backlog": ConsumptionStatus.UNREAD,
}

# - **Title** by Creator | Rating: N | Date: YYYY-MM-DD
_ITEM_PATTERN = re.compile(
    r"^[-*]\s+"  # List marker (- or *)
    r"\*\*(.+?)\*\*"  # **Title** (required)
    r"(?:\s+by\s+(.+?))??"  # by Creator (optional, lazy)
    r"(?:\s*\|\s*(.+))?"  # | metadata tail (optional)
    r"\s*$"
)

_METADATA_PAIR_PATTERN = re.compile(r"(\w+)\s*:\s*(.+)")


class MarkdownImporter(Importer):
    """``## Status`` headings over ``- **Title** by Creator`` list items."""

    name = "markdown_import"
    display_name = "Markdown"
    description = "Import from Markdown file"
    content_types = ()

    def parse(
        self, text: str, content_type: ContentType | None = None
    ) -> Iterator[ParsedRow]:
        resolved = self.required_content_type(content_type)
        status = ConsumptionStatus.UNREAD

        for number, line in enumerate(text.splitlines(), start=1):
            stripped = line.strip()

            if stripped.startswith("## "):
                section_status = _match_section_status(stripped[3:].strip().lower())
                if section_status is not None:
                    status = section_status
                continue

            # Prose and headings are not rows. Reporting each one as a skip
            # would bury the list items that really did fail to parse.
            if not stripped.startswith(("- ", "* ")):
                continue

            match = _ITEM_PATTERN.match(stripped)
            if match is None:
                yield SkippedRow(number, "list item has no **Title**")
                continue

            title = match.group(1).strip()
            if not title:
                yield SkippedRow(number, "no title")
                continue

            creator = match.group(2)
            metadata = _parse_metadata_tail(match.group(3) or "")
            rating = normalize_rating(metadata.pop("rating", None))
            date_completed = parse_completion_date(metadata.pop("date", ""), title)

            yield ImportedRow(
                number,
                ContentItem(
                    title=title,
                    author=creator.strip() if creator else None,
                    content_type=resolved,
                    rating=rating,
                    status=status,
                    date_completed=date_completed,
                    metadata=metadata,
                    source=self.name,
                ),
            )


def _match_section_status(heading_text: str) -> ConsumptionStatus | None:
    """A heading is matched by keyword, so "## Books to Read" still lands."""
    for keyword, status in SECTION_STATUS_MAP.items():
        if keyword in heading_text:
            return status
    return None


def _parse_metadata_tail(tail: str) -> dict[str, str]:
    """Read ``Rating: 5 | Date: 2024-06-15 | Key: Value`` into lowercased keys."""
    result: dict[str, str] = {}

    for raw_part in tail.split("|"):
        match = _METADATA_PAIR_PATTERN.match(raw_part.strip())
        if match:
            result[match.group(1).strip().lower()] = match.group(2).strip()

    return result
