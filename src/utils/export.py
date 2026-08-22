"""Export library items to CSV and JSON formats."""

import csv
import io
import json
from typing import Any

from src.models.content import ContentItem, ContentType, get_enum_value
from src.models.templates import (
    CONTENT_TYPE_COLUMNS,
    CREATOR_COLUMNS,
    CREATOR_FIELD,
    LIST_VALUED_COLUMNS,
    STATUS_DISPLAY,
)
from src.utils.csv_formula import guard_csv_formula

# Column order for CSV export: the common columns bracket the type-specific
# ones, which follow the templates because both come off CONTENT_TYPE_COLUMNS.
_CSV_COLUMN_ORDER: dict[str, list[str]] = {
    content_type: [
        "title",
        CREATOR_FIELD[content_type],
        "rating",
        "status",
        "date_completed",
        "review",
        "notes",
        *(column for column in columns if column not in CREATOR_COLUMNS),
        "ignored",
    ]
    for content_type, columns in CONTENT_TYPE_COLUMNS.items()
}

# Header for a whole-library CSV: every type's columns in one row, so an item
# leaves the columns its own type does not declare blank.
_ALL_CSV_COLUMNS: list[str] = [
    *dict.fromkeys(
        column
        for columns in _CSV_COLUMN_ORDER.values()
        for column in columns
        if column != "ignored"
    ),
    "ignored",
]


def _item_to_export_dict(
    item: ContentItem, content_type: ContentType, for_csv: bool = False
) -> dict[str, Any]:
    """Convert a ContentItem to a flat dict matching template fields.

    Args:
        item: ContentItem to convert
        content_type: Content type for field mapping
        for_csv: If True, format values for CSV (strings); if False, for JSON

    Returns:
        Dictionary with template-matching keys
    """
    content_type_value = get_enum_value(content_type)
    creator_field = CREATOR_FIELD[content_type_value]

    result: dict[str, Any] = {
        "title": item.title,
        creator_field: item.author or "",
        "rating": item.rating if item.rating is not None else ("" if for_csv else None),
        "status": STATUS_DISPLAY.get(content_type_value, {}).get(
            get_enum_value(item.status), get_enum_value(item.status)
        ),
        "date_completed": (
            item.date_completed.isoformat() if item.date_completed else ""
        ),
        "review": item.review or "",
        "notes": item.metadata.get("notes", ""),
        "ignored": str(bool(item.ignored)).lower() if for_csv else bool(item.ignored),
    }

    # Add type-specific metadata fields, read under the key the library
    # stores each template column as (a template says "year", the library
    # stores "release_year").
    type_columns = CONTENT_TYPE_COLUMNS[content_type_value]

    for column, metadata_key in type_columns.items():
        if column in CREATOR_COLUMNS or column in result:
            continue

        value = item.metadata.get(metadata_key)

        if column == "seasons_watched" and isinstance(value, list):
            # One cell holds the whole list.
            value = ",".join(str(season) for season in value) if for_csv else value
        elif column in LIST_VALUED_COLUMNS and isinstance(value, list):
            # One cell holds one of them.
            value = value[0] if value else None

        result[column] = value if value is not None else ("" if for_csv else None)

    return result


def export_items_csv(items: list[ContentItem], content_type: ContentType | None) -> str:
    """Export items as CSV; *content_type* None writes the whole library."""
    columns = (
        _ALL_CSV_COLUMNS
        if content_type is None
        else _CSV_COLUMN_ORDER[get_enum_value(content_type)]
    )

    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()

    # Guarding here rather than per field covers every column a future
    # content type adds, and the JSON export stays byte-for-byte raw.
    for item in items:
        row = _item_to_export_dict(
            item, content_type or item.content_type, for_csv=True
        )
        writer.writerow(
            {column: guard_csv_formula(value) for column, value in row.items()}
        )

    return output.getvalue()


def export_items_json(
    items: list[ContentItem], content_type: ContentType | None
) -> str:
    """Export items as JSON; *content_type* None writes the whole library."""
    entries = [
        _item_to_export_dict(item, content_type or item.content_type, for_csv=False)
        for item in items
    ]
    return json.dumps(entries, indent=2, ensure_ascii=False)
