"""The fill-only rules discard most of what a source offers, so the offer is
recorded here: a later rebuild decides a field by rank, not by which write
reached an empty column first.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any

from src.models.detail_fields import (
    WRITER_STATED_BASE_FIELDS,
    ContentTypeFields,
    FieldKind,
    FieldOwner,
    interface_field,
)
from src.storage.merge import stated_creator
from src.utils.dates import utc_now
from src.utils.series import (
    SERIES_AUTHORITY_KEY,
    SERIES_POSITION_KEY,
    stored_series_authority,
)

_TABLE = "content_item_field_writes"


class WriterBand(str, Enum):
    """The rank a writer's word carries, weakest first as ``SeriesAuthority``
    is. ``PINNED`` is a rank the rebuild assigns a pinned provider's rows, never
    a band a door writes: a pin is a preference, not a statement.
    """

    #: A value already in a column when the ledger arrived, claimed by nobody.
    LEGACY = "legacy"
    SOURCE = "source"
    PROVIDER = "provider"
    PINNED = "pinned"
    #: The operator's own word, and the only band a user-owned field reaches:
    #: the sync door reads these rows to leave what they state alone.
    MANUAL = "manual"


@dataclass(frozen=True)
class FieldWriter:
    """Band and identity in one value: a door taking the identity alone leaves
    the band to a default, and a provider recorded in the source band outranks
    itself at the rebuild.
    """

    kind: WriterBand
    name: str


#: The one identity in the manual band: every door the operator edits through
#: is the same hand, so a second edit replaces the first rather than joining it.
MANUAL_WRITER = FieldWriter(WriterBand.MANUAL, "operator")

#: The one identity in the legacy band, nameless because the band exists for
#: values whose writer nobody recorded.
LEGACY_WRITER = FieldWriter(WriterBand.LEGACY, "")


@dataclass(frozen=True)
class FieldWrite:
    """One field as a writer stated it, canonical per src.models.detail_fields."""

    field: str
    value: Any
    #: Only a series ordinal carries one, as ``SeriesAuthority``'s own value.
    authority: str | None = None


@dataclass(frozen=True)
class StoredFieldWrite:
    field: str
    writer_kind: WriterBand
    writer: str
    value: Any
    authority: str | None
    written_at: str


def _stated_base_writes(stated: Mapping[str, Any]) -> list[FieldWrite]:
    return [
        FieldWrite(field, value)
        for field in WRITER_STATED_BASE_FIELDS
        if (value := stated.get(field)) is not None
    ]


def _stated_column_writes(
    spec: ContentTypeFields, stated: Mapping[str, Any], author: str | None
) -> list[FieldWrite]:
    writes: list[FieldWrite] = []
    for detail_field in spec.fields:
        if detail_field.column is None or detail_field.owner is not FieldOwner.WRITER:
            continue
        raw = detail_field.value_from(stated)
        if detail_field.kind is FieldKind.CREATOR:
            value = stated_creator(detail_field.store(author or raw))
        else:
            value = detail_field.store(raw)
        if value is not None:
            writes.append(
                FieldWrite(detail_field.metadata_key, detail_field.codec.load(value))
            )
    return writes


def _stated_free_form_writes(
    spec: ContentTypeFields, metadata: Mapping[str, Any]
) -> list[FieldWrite]:
    """The series authority is no row of its own: it rides the ordinal it grades."""
    authority = stored_series_authority(metadata)
    writes: list[FieldWrite] = []
    for detail_field in spec.fields:
        key = detail_field.metadata_key
        if (
            detail_field.column is not None
            or detail_field.owner is not FieldOwner.WRITER
            or key == SERIES_AUTHORITY_KEY
        ):
            continue
        value = detail_field.canonical(detail_field.value_from(metadata))
        if value is None:
            continue
        ordinal_authority = (
            authority.value if authority and key == SERIES_POSITION_KEY else None
        )
        writes.append(FieldWrite(key, value, ordinal_authority))
    return writes


def stated_writes(
    spec: ContentTypeFields, stated: Mapping[str, Any], author: str | None = None
) -> list[FieldWrite]:
    """The writer-owned fields a mapping states, which is every band's field set:
    a user-owned field reaches the ledger only as a manual row.
    """
    writes = _stated_base_writes(stated)
    writes.extend(_stated_column_writes(spec, stated, author))
    writes.extend(_stated_free_form_writes(spec, stated))
    return writes


def record_field_writes(
    cursor: sqlite3.Cursor,
    db_id: int,
    writer: FieldWriter,
    writes: Iterable[FieldWrite],
) -> None:
    """Runs on the caller's cursor, no commit. Nothing is deleted implicitly: a
    door short-circuits on its own terms, so "did not state it" cannot be told
    apart from "was never asked".
    """
    # Fixed width, for the reason sync_runs stamps its runs that way: the column
    # sorts as text, and a short stamp sorts out of order.
    now = utc_now().isoformat(timespec="microseconds")
    cursor.executemany(
        f"INSERT INTO {_TABLE} (content_item_id, field, writer_kind, writer,"
        " value_json, authority, written_at) VALUES (?, ?, ?, ?, ?, ?, ?)"
        " ON CONFLICT (content_item_id, field, writer_kind, writer) DO UPDATE SET"
        " value_json = excluded.value_json, authority = excluded.authority,"
        " written_at = excluded.written_at"
        " WHERE value_json IS NOT excluded.value_json"
        " OR authority IS NOT excluded.authority",
        [
            (
                db_id,
                write.field,
                writer.kind.value,
                writer.name,
                # Sorted: the text is compared to decide whether written_at
                # moves, so a dict restated in another key order is no write.
                json.dumps(write.value, sort_keys=True),
                write.authority,
                now,
            )
            for write in writes
        ],
    )


def read_manual_fields(cursor: sqlite3.Cursor, db_id: int) -> set[str]:
    cursor.execute(
        f"SELECT field FROM {_TABLE} WHERE content_item_id = ? AND writer_kind = ?",
        (db_id, WriterBand.MANUAL.value),
    )
    return {row["field"] for row in cursor.fetchall()}


def drop_manual_field(cursor: sqlite3.Cursor, db_id: int, field: str) -> bool:
    """The operator releasing a field, which is the one deletion this table
    takes: every other row persists until its writer states something else.
    """
    cursor.execute(
        f"DELETE FROM {_TABLE} WHERE content_item_id = ? AND field = ?"
        " AND writer_kind = ?",
        (db_id, field, WriterBand.MANUAL.value),
    )
    return cursor.rowcount > 0


def parse_manual_fields(payload: str | None, content_type: str) -> list[str]:
    """Ordered by name, so the report reads the same twice, and in the names both
    interfaces speak rather than the ones the ledger files them under.
    """
    fields: list[str] = json.loads(payload) if payload else []
    return sorted(interface_field(content_type, field) for field in fields)


def read_field_writes(cursor: sqlite3.Cursor, db_id: int) -> list[StoredFieldWrite]:
    cursor.execute(
        "SELECT field, writer_kind, writer, value_json, authority, written_at"
        f" FROM {_TABLE} WHERE content_item_id = ?"
        " ORDER BY field, writer_kind, writer",
        (db_id,),
    )
    return [
        StoredFieldWrite(
            field=row["field"],
            writer_kind=WriterBand(row["writer_kind"]),
            writer=row["writer"],
            value=json.loads(row["value_json"]),
            authority=row["authority"],
            written_at=row["written_at"],
        )
        for row in cursor.fetchall()
    ]
