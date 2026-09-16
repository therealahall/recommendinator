"""The fill-only rules discard most of what a source offers, so the offer is
recorded here: a later rebuild decides a field by rank, not by which write
reached an empty column first.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum
from typing import Any

from src.utils.dates import utc_now

_TABLE = "content_item_field_writes"


class WriterBand(str, Enum):
    """The rank a writer's word carries, weakest first as ``SeriesAuthority``
    is. Only ``SOURCE`` is recorded so far.
    """

    #: A value already in a column when the ledger arrived, claimed by nobody.
    LEGACY = "legacy"
    SOURCE = "source"
    PROVIDER = "provider"
    PINNED = "pinned"
    MANUAL = "manual"


@dataclass(frozen=True)
class FieldWriter:
    """Band and identity in one value: a door taking the identity alone leaves
    the band to a default, and a provider recorded in the source band outranks
    itself at the rebuild.
    """

    kind: WriterBand
    name: str


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


def record_field_writes(
    cursor: sqlite3.Cursor,
    db_id: int,
    writer: FieldWriter,
    writes: Iterable[FieldWrite],
) -> None:
    """Runs on the caller's cursor and does not commit. Restating a value is not
    a write: ``written_at`` moves only where the value or its authority changed.
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
