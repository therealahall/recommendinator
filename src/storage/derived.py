import json
import sqlite3
from dataclasses import dataclass

from src.models.detail_fields import DETAIL_FIELDS, RELEASE_YEAR_FIELDS, FieldKind
from src.storage.merge import (
    StatedYear,
    creators_conflict,
    detail_join,
    regions_conflict,
    stated_region,
    stated_release_year,
    years_conflict,
)
from src.utils.series import get_series_name_from_metadata
from src.utils.sorting import build_search_text, get_sort_title


def _build_creator_source() -> tuple[str, str]:
    """By content type, as ``SQLiteDB._row_to_content_item`` chooses it: a
    COALESCE over every table would take whichever table held a row for that
    id, once anything had left a detail row behind.
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


def _build_year_expression() -> str:
    """Books have no branch, so theirs reads NULL: see ``RELEASE_YEAR_FIELDS``."""
    branches = [
        f"WHEN '{content_type}' THEN {spec.table_alias}.{field.column}"
        for content_type, spec in DETAIL_FIELDS.items()
        if (field := RELEASE_YEAR_FIELDS.get(content_type)) is not None
    ]
    return f"CASE ci.content_type {' '.join(branches)} END"


def _build_metadata_expression() -> str:
    branches = [
        f"WHEN '{content_type}' THEN {spec.table_alias}.metadata"
        for content_type, spec in DETAIL_FIELDS.items()
    ]
    return f"CASE ci.content_type {' '.join(branches)} END"


_CREATOR_EXPRESSION, _DETAIL_JOINS = _build_creator_source()
_YEAR_EXPRESSION = _build_year_expression()
_METADATA_EXPRESSION = _build_metadata_expression()

_SOURCE_SELECT = (
    f"SELECT ci.id, ci.title, {_CREATOR_EXPRESSION} AS creator,"
    f" {_METADATA_EXPRESSION} AS metadata"
    f" FROM content_items ci {_DETAIL_JOINS}"
)

_MATCH_ROW_COLUMNS = (
    "ci.id, ci.content_type, ci.title, ci.normalized_title, ci.source,"
    f" {_CREATOR_EXPRESSION} AS creator, {_YEAR_EXPRESSION} AS release_year"
)

_MATCH_SIGNAL_SELECT = (
    f"SELECT {_MATCH_ROW_COLUMNS} FROM content_items ci {_DETAIL_JOINS}"
    " WHERE ci.id = ?"
)

_LIVE_MATCH_ROWS_SELECT = (
    f"SELECT {_MATCH_ROW_COLUMNS} FROM content_items ci {_DETAIL_JOINS}"
    " WHERE ci.user_id = ? AND ci.merged_into IS NULL ORDER BY ci.id"
)

# Both columns are written together, so either being NULL means the row came
# from something that does not know about them, and a library where none does
# costs one filtered read per open.
_BACKFILL_SELECT = (
    f"{_SOURCE_SELECT} WHERE ci.sort_title IS NULL OR ci.search_text IS NULL"
)


def write_derived_columns(cursor: sqlite3.Cursor, db_id: int) -> None:
    """Read back from the database rather than taken from the item being saved:
    the creator column is fill-only, so what a sync hands over is not always
    what ends up stored.
    """
    cursor.execute(f"{_SOURCE_SELECT} WHERE ci.id = ?", (db_id,))
    row = cursor.fetchone()
    if row is not None:
        _write_row(cursor, row)


@dataclass(frozen=True)
class MatchSignals:
    creator: str | None = None
    release_year: StatedYear = StatedYear()
    region: str | None = None


def signals_conflict(one: MatchSignals, other: MatchSignals) -> bool:
    """Shared with the save door, which refuses more besides: its key keeps a
    numbered edition apart, and where one key names two rows it takes neither.
    """
    return (
        creators_conflict(one.creator, other.creator)
        or years_conflict(one.release_year, other.release_year)
        or regions_conflict(one.region, other.region)
    )


@dataclass(frozen=True)
class MatchRow:
    db_id: int
    content_type: str
    title: str
    normalized_title: str
    source: str | None
    signals: MatchSignals


def read_match_signals(cursor: sqlite3.Cursor, db_id: int) -> MatchSignals:
    cursor.execute(_MATCH_SIGNAL_SELECT, (db_id,))
    row = cursor.fetchone()
    return MatchSignals() if row is None else _read_signals(row)


def read_live_match_rows(cursor: sqlite3.Cursor, user_id: int) -> list[MatchRow]:
    cursor.execute(_LIVE_MATCH_ROWS_SELECT, (user_id,))
    return [
        MatchRow(
            db_id=int(row["id"]),
            content_type=row["content_type"],
            title=row["title"],
            normalized_title=row["normalized_title"] or "",
            source=row["source"],
            signals=_read_signals(row),
        )
        for row in cursor.fetchall()
    ]


def _read_signals(row: sqlite3.Row) -> MatchSignals:
    return MatchSignals(
        creator=str(row["creator"]) if row["creator"] else None,
        release_year=stated_release_year(
            row["content_type"], row["release_year"], row["title"]
        ),
        region=stated_region(row["title"]),
    )


def backfill_derived_columns(cursor: sqlite3.Cursor) -> None:
    """Selected on the columns themselves rather than on the schema version,
    because a build older than they are can insert a row into a database this
    build already stamped, and no version guard would ever revisit it.
    """
    cursor.execute(_BACKFILL_SELECT)
    # fetchall() required: the cursor is reused for the UPDATEs in the loop
    for row in cursor.fetchall():
        _write_row(cursor, row)


def _stated_series(blob: str | None) -> str | None:
    if not blob:
        return None
    try:
        metadata = json.loads(blob)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(metadata, dict):
        return None
    return get_series_name_from_metadata(metadata)


def _write_row(cursor: sqlite3.Cursor, row: sqlite3.Row) -> None:
    title = row["title"] or ""
    cursor.execute(
        "UPDATE content_items SET sort_title = ?, search_text = ? WHERE id = ?",
        (
            get_sort_title(title),
            build_search_text(title, row["creator"], _stated_series(row["metadata"])),
            row["id"],
        ),
    )
