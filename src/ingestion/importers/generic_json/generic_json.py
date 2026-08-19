"""The JSON and JSONL importer, sharing the CSV templates' field names."""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

from src.ingestion.importers.base import (
    ImportedRow,
    Importer,
    ImporterError,
    ParsedRow,
    RowUnit,
    SkippedRow,
)
from src.ingestion.importers.rows import (
    normalize_rating,
    parse_completion_date,
    parse_ignored_field,
    parse_seasons_watched,
)
from src.models.content import ConsumptionStatus, ContentItem, ContentType
from src.models.templates import (
    CONTENT_TYPE_COLUMNS,
    CREATOR_COLUMNS,
    CREATOR_FIELD,
    LIST_VALUED_COLUMNS,
    STATUS_MAP,
)


class JsonImporter(Importer):
    """A JSON array of objects, or one object per line."""

    name = "json_import"
    display_name = "JSON"
    description = "Import from JSON/JSONL file"
    content_types = ()

    def parse(
        self, text: str, content_type: ContentType | None = None
    ) -> Iterator[ParsedRow]:
        resolved = self.required_content_type(content_type)
        creator_field = CREATOR_FIELD[resolved.value]

        unit, entries = _load_entries(text)

        for number, entry in entries:
            if not isinstance(entry, dict):
                yield SkippedRow(number, "not a JSON object", unit)
                continue

            title = str(entry.get("title", "")).strip()
            if not title:
                yield SkippedRow(number, "no title", unit)
                continue

            notes = str(entry.get("notes", "")).strip() or None
            metadata = _build_metadata(entry, resolved)
            if notes:
                metadata["notes"] = notes
            if resolved is ContentType.TV_SHOW and "seasons_watched" in metadata:
                metadata["seasons_watched"] = parse_seasons_watched(
                    metadata["seasons_watched"]
                )

            yield ImportedRow(
                number,
                ContentItem(
                    title=title,
                    author=str(entry.get(creator_field, "")).strip() or None,
                    content_type=resolved,
                    rating=normalize_rating(entry.get("rating")),
                    review=str(entry.get("review", "")).strip() or None,
                    status=STATUS_MAP.get(
                        str(entry.get("status", "")).strip().lower(),
                        ConsumptionStatus.UNREAD,
                    ),
                    date_completed=parse_completion_date(
                        str(entry.get("date_completed", "")), title
                    ),
                    ignored=parse_ignored_field(entry),
                    metadata=metadata,
                    source=self.name,
                ),
                unit,
            )


def _load_entries(text: str) -> tuple[RowUnit, list[tuple[int, Any]]]:
    """Numbered by position in an array, by line in a JSONL file."""
    content = text.strip()
    if not content:
        return "line", []

    try:
        if content.startswith("["):
            data = json.loads(content)
            if not isinstance(data, list):
                raise ValueError("JSON file must contain an array of objects")
            return "entry", list(enumerate(data, start=1))

        entries: list[tuple[int, Any]] = []
        for number, line in enumerate(content.splitlines(), start=1):
            line = line.strip()
            if not line:
                continue
            try:
                entries.append((number, json.loads(line)))
            except json.JSONDecodeError as error:
                raise ValueError(f"Invalid JSON on line {number}: {error}") from error
        return "line", entries
    except (json.JSONDecodeError, ValueError) as error:
        raise ImporterError(f"Failed to parse JSON: {error}") from error


def _build_metadata(entry: dict[str, Any], content_type: ContentType) -> dict[str, Any]:
    """Unlike a CSV cell, a JSON field can already hold the list a
    list-valued column wants, so only a single value is wrapped. An absent or
    empty field says nothing and is left out entirely.
    """
    metadata: dict[str, Any] = {}

    for column, metadata_key in CONTENT_TYPE_COLUMNS[content_type.value].items():
        if column in CREATOR_COLUMNS:
            continue
        value = entry.get(column)
        if value is not None and str(value).strip():
            if column in LIST_VALUED_COLUMNS and not isinstance(value, list):
                value = [value]
            metadata[metadata_key] = value

    return metadata
