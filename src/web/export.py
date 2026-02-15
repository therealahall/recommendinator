"""Export library items to CSV and JSON formats."""

import csv
import io
import json
from typing import Any

from src.ingestion.sources.generic_csv import CONTENT_TYPE_COLUMNS, CREATOR_FIELD
from src.models.content import ContentItem, ContentType, get_enum_value

# Column order for CSV export, matching the templates
_CSV_COLUMN_ORDER: dict[str, list[str]] = {
    "book": [
        "title",
        "author",
        "rating",
        "status",
        "date_completed",
        "review",
        "notes",
        "isbn",
        "pages",
        "year_published",
        "genre",
        "ignored",
    ],
    "movie": [
        "title",
        "director",
        "rating",
        "status",
        "date_completed",
        "review",
        "notes",
        "year",
        "runtime_minutes",
        "genre",
        "ignored",
    ],
    "tv_show": [
        "title",
        "creator",
        "rating",
        "status",
        "date_completed",
        "review",
        "notes",
        "seasons_watched",
        "total_seasons",
        "year",
        "genre",
        "ignored",
    ],
    "video_game": [
        "title",
        "developer",
        "rating",
        "status",
        "date_completed",
        "review",
        "notes",
        "platform",
        "genre",
        "hours_played",
        "ignored",
    ],
}


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
    creator_field = CREATOR_FIELD.get(content_type_value, "author")

    result: dict[str, Any] = {
        "title": item.title,
        creator_field: item.author or "",
        "rating": item.rating if item.rating is not None else ("" if for_csv else None),
        "status": get_enum_value(item.status),
        "date_completed": (
            item.date_completed.isoformat() if item.date_completed else ""
        ),
        "review": item.review or "",
        "notes": item.metadata.get("notes", ""),
        "ignored": str(bool(item.ignored)).lower() if for_csv else bool(item.ignored),
    }

    # Add type-specific metadata fields
    type_columns = CONTENT_TYPE_COLUMNS.get(content_type_value, set())
    skip_fields = {"author", "director", "creator", "developer"}

    for column in type_columns:
        if column in skip_fields:
            continue
        if column in result:
            continue

        value = item.metadata.get(column)

        if column == "seasons_watched":
            if isinstance(value, list):
                result[column] = (
                    ",".join(str(season) for season in value) if for_csv else value
                )
            else:
                result[column] = (
                    value if value is not None else ("" if for_csv else None)
                )
        elif column == "genre":
            # Map genres list back to genre string
            genres = item.metadata.get("genres")
            if isinstance(genres, list) and genres:
                result[column] = genres[0] if for_csv else genres[0]
            elif value is not None:
                result[column] = value
            else:
                result[column] = "" if for_csv else None
        else:
            result[column] = value if value is not None else ("" if for_csv else None)

    return result


def export_items_csv(items: list[ContentItem], content_type: ContentType) -> str:
    """Export content items to CSV format.

    Args:
        items: List of ContentItem objects to export
        content_type: Content type for determining column layout

    Returns:
        CSV string with header and data rows
    """
    content_type_value = get_enum_value(content_type)
    columns = _CSV_COLUMN_ORDER.get(content_type_value, [])

    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()

    for item in items:
        row = _item_to_export_dict(item, content_type, for_csv=True)
        writer.writerow(row)

    return output.getvalue()


def export_items_json(items: list[ContentItem], content_type: ContentType) -> str:
    """Export content items to JSON format.

    Args:
        items: List of ContentItem objects to export
        content_type: Content type for determining field layout

    Returns:
        Pretty-printed JSON string (array of objects)
    """
    entries = [
        _item_to_export_dict(item, content_type, for_csv=False) for item in items
    ]
    return json.dumps(entries, indent=2, ensure_ascii=False)
