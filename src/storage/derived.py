"""The content columns derived from an item's title and creator.

``content_items.sort_title`` and ``content_items.search_text`` exist so that
the library list can be ordered in SQL under a real LIMIT/OFFSET, and searched
over a projection of two columns, instead of building a ``ContentItem`` for
every row and slicing in Python.

Nothing reads them as input: both are recomputed from what is stored on every
write that can change either source, and ``schema.create_schema`` backfills
whatever row reaches an open without them.
"""

import sqlite3

from src.models.detail_fields import DETAIL_FIELDS, FieldKind
from src.storage.merge import detail_join
from src.utils.sorting import build_search_text, get_sort_title


def _build_creator_source() -> tuple[str, str]:
    """Build the creator expression and the joins it reads from.

    The creator is chosen by content type, the way
    ``SQLiteDB._row_to_content_item`` chooses it, so the column stores the
    name the loaded item shows. A COALESCE over every table would instead
    take whichever table happened to hold a row for that id, which is the
    same value only while nothing has left a detail row behind.
    """
    branches: list[str] = []
    joins: list[str] = []
    for content_type, spec in DETAIL_FIELDS.items():
        joins.append(detail_join(spec))
        for field in spec.fields:
            if field.kind is FieldKind.CREATOR and field.column is not None:
                branches.append(
                    f"WHEN '{content_type}' THEN {spec.table_alias}.{field.column}"
                )
    return f"CASE ci.content_type {' '.join(branches)} END", " ".join(joins)


_CREATOR_EXPRESSION, _DETAIL_JOINS = _build_creator_source()

_SOURCE_SELECT = (
    f"SELECT ci.id, ci.title, {_CREATOR_EXPRESSION} AS creator"
    f" FROM content_items ci {_DETAIL_JOINS}"
)

# The rows the backfill has anything to do. Both columns are written together,
# so either being NULL means the row came from something that does not know
# about them, and a library where none does costs one filtered read per open.
_BACKFILL_SELECT = (
    f"{_SOURCE_SELECT} WHERE ci.sort_title IS NULL OR ci.search_text IS NULL"
)


def write_derived_columns(cursor: sqlite3.Cursor, db_id: int) -> None:
    """Recompute one row's derived columns from its stored title and creator.

    Read back from the database rather than taken from the item being saved:
    the creator column is fill-only, so what a sync hands over is not always
    what ends up stored.
    """
    cursor.execute(f"{_SOURCE_SELECT} WHERE ci.id = ?", (db_id,))
    row = cursor.fetchone()
    if row is not None:
        _write_row(cursor, row)


def backfill_derived_columns(cursor: sqlite3.Cursor) -> None:
    """Fill the derived columns for every row that is missing one.

    Selected on the columns themselves rather than on the schema version,
    because a build older than they are can insert a row into a database this
    build already stamped, and no version guard would ever revisit it. Such a
    row is unreachable by search and sorts ahead of the whole library.
    """
    cursor.execute(_BACKFILL_SELECT)
    # fetchall() required: the cursor is reused for the UPDATEs in the loop
    for row in cursor.fetchall():
        _write_row(cursor, row)


def _write_row(cursor: sqlite3.Cursor, row: sqlite3.Row) -> None:
    """Write the derived columns for one row of :data:`_SOURCE_SELECT`."""
    title = row["title"] or ""
    cursor.execute(
        "UPDATE content_items SET sort_title = ?, search_text = ? WHERE id = ?",
        (get_sort_title(title), build_search_text(title, row["creator"]), row["id"]),
    )
