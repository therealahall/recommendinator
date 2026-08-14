"""The tabular template shape of each content type.

Not in the CSV plugin: the JSON plugin and the export path read the same
tables, and a column round-trips only while one declaration names it.
"""

from __future__ import annotations

from src.models.content import ConsumptionStatus
from src.models.detail_fields import DETAIL_FIELDS, FieldKind

COMMON_COLUMNS: frozenset[str] = frozenset(
    {
        "title",
        "rating",
        "status",
        "date_completed",
        "review",
        "notes",
        "ignored",
    }
)

# The metadata key each template column stores its value under: a template
# says "year", the library says "release_year", and import and export have to
# agree. The creator column becomes ContentItem.author, never metadata.
CONTENT_TYPE_COLUMNS: dict[str, dict[str, str]] = {
    content_type: spec.template_columns for content_type, spec in DETAIL_FIELDS.items()
}

# A template cell holds one value, so an import wraps it and an export writes
# the first entry back out. Every reader downstream sees only the list form.
LIST_VALUED_COLUMNS: frozenset[str] = frozenset(
    detail_field.template_column
    for spec in DETAIL_FIELDS.values()
    for detail_field in spec.fields
    if detail_field.template_column is not None
    and detail_field.kind is FieldKind.STRING_LIST
)

STATUS_MAP: dict[str, ConsumptionStatus] = {
    "completed": ConsumptionStatus.COMPLETED,
    "read": ConsumptionStatus.COMPLETED,
    "watched": ConsumptionStatus.COMPLETED,
    "played": ConsumptionStatus.COMPLETED,
    "finished": ConsumptionStatus.COMPLETED,
    "in_progress": ConsumptionStatus.CURRENTLY_CONSUMING,
    "currently_consuming": ConsumptionStatus.CURRENTLY_CONSUMING,
    "reading": ConsumptionStatus.CURRENTLY_CONSUMING,
    "watching": ConsumptionStatus.CURRENTLY_CONSUMING,
    "playing": ConsumptionStatus.CURRENTLY_CONSUMING,
    "unread": ConsumptionStatus.UNREAD,
    "unwatched": ConsumptionStatus.UNREAD,
    "unplayed": ConsumptionStatus.UNREAD,
    "to_read": ConsumptionStatus.UNREAD,
    "to_watch": ConsumptionStatus.UNREAD,
    "to_play": ConsumptionStatus.UNREAD,
    "wishlist": ConsumptionStatus.UNREAD,
}

STATUS_DISPLAY: dict[str, dict[str, str]] = {
    "book": {
        "completed": "read",
        "currently_consuming": "reading",
        "unread": "unread",
    },
    "movie": {
        "completed": "watched",
        "currently_consuming": "watching",
        "unread": "unwatched",
    },
    "tv_show": {
        "completed": "watched",
        "currently_consuming": "watching",
        "unread": "unwatched",
    },
    "video_game": {
        "completed": "played",
        "currently_consuming": "playing",
        "unread": "unplayed",
    },
}

CREATOR_FIELD: dict[str, str] = {
    content_type: spec.creator_column for content_type, spec in DETAIL_FIELDS.items()
}

# These become ContentItem.author rather than metadata, so import and export
# both skip them when walking a type's own columns.
CREATOR_COLUMNS: frozenset[str] = frozenset(CREATOR_FIELD.values())
