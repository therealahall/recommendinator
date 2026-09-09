"""A row exists only while a field is held, so one query tells the sync door
which columns the operator has taken off it.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable

from src.models.detail_fields import (
    CREATOR_FIELDS,
    DETAIL_FIELDS,
    RELEASE_YEAR_FIELDS,
    DetailField,
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

_TABLE = "content_item_manual_fields"


def _manual_detail_field(content_type: str, field: str) -> DetailField | None:
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


def read_holds(cursor: sqlite3.Cursor, db_id: int) -> set[str]:
    cursor.execute(
        f"SELECT field FROM {_TABLE} WHERE content_item_id = ?",
        (db_id,),
    )
    return {row["field"] for row in cursor.fetchall()}


def held_detail_columns(content_type: str, held: Iterable[str]) -> set[str]:
    """The columns this type keeps the held fields in."""
    columns = set()
    for field in held:
        detail_field = _manual_detail_field(content_type, field)
        if detail_field is not None and detail_field.column is not None:
            columns.add(detail_field.column)
    return columns


def record_hold(cursor: sqlite3.Cursor, db_id: int, field: str) -> None:
    cursor.execute(
        f"INSERT OR IGNORE INTO {_TABLE} (content_item_id, field) VALUES (?, ?)",
        (db_id, field),
    )


def drop_hold(cursor: sqlite3.Cursor, db_id: int, field: str) -> bool:
    cursor.execute(
        f"DELETE FROM {_TABLE} WHERE content_item_id = ? AND field = ?",
        (db_id, field),
    )
    return cursor.rowcount > 0


def parse_manual_fields(payload: str | None) -> list[str]:
    """Ordered by name, so the report reads the same twice."""
    fields: list[str] = json.loads(payload) if payload else []
    return sorted(fields)
