"""A row exists only while a field is held, so one query tells the sync door
which columns the operator has taken off it.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping
from typing import Any

from src.models.content import ContentItem, ManualField, get_enum_value
from src.models.detail_fields import (
    CREATOR_FIELDS,
    DETAIL_FIELDS,
    RELEASE_YEAR_FIELDS,
    DetailField,
    to_int,
)

#: ``seasons_watched`` is absent on purpose: ``reconcile_seasons`` unions the
#: operator's check-offs with the source's, losing neither side, where a hold
#: would refuse a season genuinely watched since.
MANUAL_FIELDS: tuple[str, ...] = (
    "title",
    "status",
    "rating",
    "review",
    "genres",
    "tags",
    "description",
    "release_year",
    "creator",
)

#: The held fields living on ``content_items`` rather than in a detail table.
BASE_ITEM_FIELDS: frozenset[str] = frozenset({"title", "status", "rating", "review"})

_TABLE = "content_item_manual_fields"


def manual_detail_field(content_type: str, field: str) -> DetailField | None:
    """``None`` for a field this content type does not state — a book declares
    no release year, so it has none to hold.
    """
    if field == "creator":
        return CREATOR_FIELDS.get(content_type)
    if field == "release_year":
        return RELEASE_YEAR_FIELDS.get(content_type)
    spec = DETAIL_FIELDS.get(content_type)
    if spec is None:
        return None
    return next(
        (
            candidate
            for candidate in spec.fields
            if candidate.metadata_key == field and candidate.column is not None
        ),
        None,
    )


def stated_value(item: ContentItem, field: str) -> Any:
    """What *item* currently says about *field*, in the shape a hold stores."""
    if field == "title":
        return item.title
    if field == "status":
        return get_enum_value(item.status)
    if field == "rating":
        return item.rating
    if field == "review":
        return item.review
    if field == "creator":
        return item.author
    if field == "release_year":
        return to_int(item.metadata.get("release_year"))
    return item.metadata.get(field)


def render_value(value: Any) -> str | None:
    """Provenance is reported to a person, and the item's own fields already
    carry the typed value.
    """
    if value is None:
        return None
    if isinstance(value, list):
        return ", ".join(str(entry) for entry in value) or None
    return str(value)


def read_holds(cursor: sqlite3.Cursor, db_id: int) -> dict[str, Any]:
    cursor.execute(
        f"SELECT field, manual_value FROM {_TABLE} WHERE content_item_id = ?",
        (db_id,),
    )
    return {row["field"]: json.loads(row["manual_value"]) for row in cursor.fetchall()}


def held_detail_columns(content_type: str, held: Mapping[str, Any]) -> dict[str, str]:
    """Column to field name, for the held fields this type keeps in a column."""
    columns = {}
    for field in held:
        detail_field = manual_detail_field(content_type, field)
        if detail_field is not None and detail_field.column is not None:
            columns[detail_field.column] = field
    return columns


def record_hold(
    cursor: sqlite3.Cursor, db_id: int, field: str, value: Any, replaced: Any
) -> None:
    """*replaced* is the last thing the source stated. A second correction
    leaves it alone: what that one displaces is the operator's own value.
    """
    cursor.execute(
        f"INSERT INTO {_TABLE} (content_item_id, field, manual_value, source_value)"
        " VALUES (?, ?, ?, ?)"
        " ON CONFLICT(content_item_id, field)"
        " DO UPDATE SET manual_value = excluded.manual_value",
        (db_id, field, json.dumps(value), json.dumps(replaced)),
    )


def record_source_values(
    cursor: sqlite3.Cursor, db_id: int, stated: Mapping[str, Any]
) -> None:
    """A ``None`` states nothing — most syncs carry no genres, and recording
    that would read as the source having cleared them.
    """
    for field, value in stated.items():
        if value is None or value == []:
            continue
        cursor.execute(
            f"UPDATE {_TABLE} SET source_value = ?"
            " WHERE content_item_id = ? AND field = ?",
            (json.dumps(value), db_id, field),
        )


def drop_hold(cursor: sqlite3.Cursor, db_id: int, field: str) -> None:
    cursor.execute(
        f"DELETE FROM {_TABLE} WHERE content_item_id = ? AND field = ?",
        (db_id, field),
    )


def take_hold(cursor: sqlite3.Cursor, db_id: int, field: str) -> tuple[bool, Any]:
    """Drop the hold on *field*, answering whether there was one and what the
    source last stated, which the caller applies.
    """
    cursor.execute(
        f"SELECT source_value FROM {_TABLE} WHERE content_item_id = ? AND field = ?",
        (db_id, field),
    )
    row = cursor.fetchone()
    if row is None:
        return False, None
    drop_hold(cursor, db_id, field)
    return True, json.loads(row["source_value"])


def parse_manual_fields(payload: str | None) -> list[ManualField]:
    """Ordered by name, so the report reads the same twice."""
    entries = json.loads(payload) if payload else []
    return sorted(
        (
            ManualField(
                field=entry["field"],
                value=render_value(entry["value"]),
                source_value=render_value(entry["source_value"]),
            )
            for entry in entries
        ),
        key=lambda held: held.field,
    )
