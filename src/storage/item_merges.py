"""One content item merged into another, recorded and reversible.

``merge.py`` holds the field rules. Here the survivor is written under them,
the absorbed row is kept behind ``merged_into``, and what was overwritten is
recorded, so an undo restores both.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from enum import Enum
from typing import Any

from src.storage.derived import write_derived_columns
from src.storage.merge import (
    ALLOWED_DETAIL_TABLES,
    detail_columns,
    merge_detail_tables,
    merge_enrichment_status,
    merge_scalar_columns,
)


class MergeEvidence(Enum):
    """What a merge was made on. Only the operator makes one, so far."""

    MANUAL = "manual"


class MergeError(ValueError):
    """A merge the storage layer refuses, naming why."""


@dataclass(frozen=True)
class MergeRecord:
    id: int
    survivor_id: int
    survivor_title: str
    absorbed_id: int
    absorbed_title: str
    evidence: MergeEvidence
    evidence_detail: str | None
    merged_at: str


# Exactly what a merge can write, so an undo puts back only what it changed.
# ``ignored`` is absent because no merge writes it: restoring it could only take
# back an ignore made since.
_SURVIVOR_COLUMNS = (
    "status",
    "rating",
    "review",
    "date_completed",
    "updated_at",
)

_ENRICHMENT_COLUMNS = (
    "last_enriched_at",
    "enrichment_provider",
    "enrichment_quality",
    "needs_enrichment",
    "enrichment_error",
)

_SNAPSHOT_TABLES = ALLOWED_DETAIL_TABLES | {"enrichment_status"}

_MERGE_SELECT = """
    SELECT m.id, m.survivor_id, m.absorbed_id, m.evidence, m.evidence_detail,
           m.merged_at, m.restore_json, s.user_id AS user_id,
           s.merged_into AS survivor_merged_into,
           s.title AS survivor_title, a.title AS absorbed_title
      FROM content_item_merges m
      JOIN content_items s ON s.id = m.survivor_id
      JOIN content_items a ON a.id = m.absorbed_id
"""


def absorb_item(
    cursor: sqlite3.Cursor,
    survivor_id: int,
    absorbed_id: int,
    evidence: MergeEvidence,
    evidence_detail: str | None = None,
    user_id: int | None = None,
) -> MergeRecord:
    """Merge one item into another; *evidence_detail* is the id or title matched."""
    if survivor_id == absorbed_id:
        raise MergeError(f"Item {survivor_id} cannot absorb itself.")
    survivor = _mergeable(cursor, survivor_id, user_id)
    absorbed = _mergeable(cursor, absorbed_id, user_id)
    if survivor["user_id"] != absorbed["user_id"]:
        raise MergeError("Two users' items cannot be merged.")
    if survivor["content_type"] != absorbed["content_type"]:
        raise MergeError(
            f"A {survivor['content_type']} cannot absorb"
            f" a {absorbed['content_type']}."
        )
    restore = _survivor_state(cursor, survivor_id, absorbed_id)
    # What it absorbed comes with it: no row may hide behind a hidden row.
    restore["repointed"] = _absorbed_by(cursor, absorbed_id)
    merge_scalar_columns(cursor, survivor_id, absorbed_id)
    merge_detail_tables(cursor, survivor_id, absorbed_id)
    merge_enrichment_status(cursor, survivor_id, absorbed_id)
    write_derived_columns(cursor, survivor_id)
    cursor.execute(
        "UPDATE content_items SET merged_into = ? WHERE id = ? OR merged_into = ?",
        (survivor_id, absorbed_id, absorbed_id),
    )
    cursor.execute(
        """INSERT INTO content_item_merges
           (survivor_id, absorbed_id, evidence, evidence_detail, restore_json)
           VALUES (?, ?, ?, ?, ?)""",
        (
            survivor_id,
            absorbed_id,
            evidence.value,
            evidence_detail,
            json.dumps(restore),
        ),
    )
    merge_id = cursor.lastrowid
    if merge_id is None:
        raise RuntimeError("INSERT did not return a row ID")
    cursor.execute(
        "SELECT merged_at FROM content_item_merges WHERE id = ?", (merge_id,)
    )
    return MergeRecord(
        id=merge_id,
        survivor_id=survivor_id,
        survivor_title=survivor["title"],
        absorbed_id=absorbed_id,
        absorbed_title=absorbed["title"],
        evidence=evidence,
        evidence_detail=evidence_detail,
        merged_at=cursor.fetchone()["merged_at"],
    )


def unmerge_item(
    cursor: sqlite3.Cursor, merge_id: int, user_id: int | None = None
) -> MergeRecord | None:
    """Undo one merge, returning it, or ``None`` when there is none.

    Raises :class:`MergeError` unless this is the newest merge in force over
    its survivor's group: an undo out of that order restores state a later
    merge has moved on.
    """
    row = _merge_row(cursor, merge_id)
    if row is None or (user_id is not None and row["user_id"] != user_id):
        return None
    later = _later_merge_id(cursor, row["survivor_id"], merge_id)
    if later is not None:
        raise MergeError(
            f"Merge {merge_id} cannot be undone before merge {later}, made into"
            f" item {row['survivor_id']} after it."
        )
    if row["survivor_merged_into"] is not None:
        raise MergeError(
            f"Merge {merge_id} cannot be undone while item {row['survivor_id']}"
            f" is itself merged into {row['survivor_merged_into']}."
        )
    state = json.loads(row["restore_json"])
    _restore_survivor(cursor, row["survivor_id"], state)
    cursor.execute(
        "UPDATE content_items SET merged_into = NULL WHERE id = ?",
        (row["absorbed_id"],),
    )
    _send_back(
        cursor, row["survivor_id"], row["absorbed_id"], state.get("repointed", [])
    )
    cursor.execute("DELETE FROM content_item_merges WHERE id = ?", (merge_id,))
    return _to_record(row)


def release_merge(cursor: sqlite3.Cursor, merge_id: int) -> None:
    """Drop one merge without restoring the survivor.

    Unlike an undo: edits may have landed on the survivor since, and reverting
    them silently costs more than the absorbed row's duplicated contributions.
    No ordering rule — nothing here reads a record's snapshot.
    """
    cursor.execute(
        "UPDATE content_items SET merged_into = NULL WHERE id ="
        " (SELECT absorbed_id FROM content_item_merges WHERE id = ?)",
        (merge_id,),
    )
    cursor.execute("DELETE FROM content_item_merges WHERE id = ?", (merge_id,))


def list_merges(cursor: sqlite3.Cursor, user_id: int) -> list[MergeRecord]:
    """Every merge in force for *user_id*, newest first."""
    cursor.execute(
        f"{_MERGE_SELECT} WHERE s.user_id = ? ORDER BY m.merged_at DESC, m.id DESC",
        (user_id,),
    )
    return [_to_record(row) for row in cursor.fetchall()]


def _merge_row(cursor: sqlite3.Cursor, merge_id: int) -> sqlite3.Row | None:
    cursor.execute(f"{_MERGE_SELECT} WHERE m.id = ?", (merge_id,))
    row: sqlite3.Row | None = cursor.fetchone()
    return row


def _to_record(row: sqlite3.Row) -> MergeRecord:
    return MergeRecord(
        id=row["id"],
        survivor_id=row["survivor_id"],
        survivor_title=row["survivor_title"],
        absorbed_id=row["absorbed_id"],
        absorbed_title=row["absorbed_title"],
        evidence=MergeEvidence(row["evidence"]),
        evidence_detail=row["evidence_detail"],
        merged_at=row["merged_at"],
    )


def _mergeable(cursor: sqlite3.Cursor, db_id: int, user_id: int | None) -> sqlite3.Row:
    cursor.execute(
        "SELECT id, user_id, title, content_type, merged_into FROM content_items"
        " WHERE id = ?",
        (db_id,),
    )
    row: sqlite3.Row | None = cursor.fetchone()
    if row is None or (user_id is not None and row["user_id"] != user_id):
        raise MergeError(f"No item with id {db_id}.")
    if row["merged_into"] is not None:
        raise MergeError(f"Item {db_id} is already merged into {row['merged_into']}.")
    return row


def _later_merge_id(
    cursor: sqlite3.Cursor, survivor_id: int, merge_id: int
) -> int | None:
    """The first merge into *survivor_id* made after *merge_id*, if one is in force.

    Sequenced by id, not ``merged_at``: two merges within a second share a
    timestamp, and SQLite gives each new row a rowid above every live one.
    """
    cursor.execute(
        "SELECT id FROM content_item_merges"
        " WHERE survivor_id = ? AND id > ? ORDER BY id LIMIT 1",
        (survivor_id, merge_id),
    )
    row = cursor.fetchone()
    return int(row["id"]) if row is not None else None


def _absorbed_by(cursor: sqlite3.Cursor, db_id: int) -> list[int]:
    cursor.execute("SELECT id FROM content_items WHERE merged_into = ?", (db_id,))
    return [int(row["id"]) for row in cursor.fetchall()]


def _send_back(
    cursor: sqlite3.Cursor, survivor_id: int, absorbed_id: int, repointed: list[int]
) -> None:
    if not repointed:
        return
    placeholders = ", ".join("?" for _ in repointed)
    cursor.execute(
        "UPDATE content_items SET merged_into = ?"
        f" WHERE merged_into = ? AND id IN ({placeholders})",
        (absorbed_id, survivor_id, *repointed),
    )


def _survivor_state(
    cursor: sqlite3.Cursor, survivor_id: int, absorbed_id: int
) -> dict[str, Any]:
    columns = ", ".join(_SURVIVOR_COLUMNS)
    cursor.execute(f"SELECT {columns} FROM content_items WHERE id = ?", (survivor_id,))
    state: dict[str, Any] = {"item": dict(cursor.fetchone()), "details": {}}
    for table in sorted(ALLOWED_DETAIL_TABLES):
        if _child_row(cursor, table, absorbed_id) is not None:
            state["details"][table] = _child_row(cursor, table, survivor_id)
    if _child_row(cursor, "enrichment_status", absorbed_id) is not None:
        state["enrichment"] = _child_row(cursor, "enrichment_status", survivor_id)
    return state


def _restore_survivor(
    cursor: sqlite3.Cursor, survivor_id: int, state: dict[str, Any]
) -> None:
    assignments = ", ".join(f"{column} = ?" for column in _SURVIVOR_COLUMNS)
    cursor.execute(
        f"UPDATE content_items SET {assignments} WHERE id = ?",
        [*(state["item"][column] for column in _SURVIVOR_COLUMNS), survivor_id],
    )
    for table, row in state["details"].items():
        _restore_child(cursor, table, detail_columns(table), survivor_id, row)
    if "enrichment" in state:
        _restore_child(
            cursor,
            "enrichment_status",
            _ENRICHMENT_COLUMNS,
            survivor_id,
            state["enrichment"],
        )
    write_derived_columns(cursor, survivor_id)


def _restore_child(
    cursor: sqlite3.Cursor,
    table: str,
    columns: tuple[str, ...],
    survivor_id: int,
    row: dict[str, Any] | None,
) -> None:
    _assert_snapshot_table(table)
    if row is None:
        cursor.execute(f"DELETE FROM {table} WHERE content_item_id = ?", (survivor_id,))
        return
    placeholders = ", ".join("?" for _ in range(len(columns) + 1))
    cursor.execute(
        f"INSERT OR REPLACE INTO {table} (content_item_id, {', '.join(columns)})"
        f" VALUES ({placeholders})",
        [survivor_id, *(row.get(column) for column in columns)],
    )


def _child_row(
    cursor: sqlite3.Cursor, table: str, content_item_id: int
) -> dict[str, Any] | None:
    _assert_snapshot_table(table)
    cursor.execute(
        f"SELECT * FROM {table} WHERE content_item_id = ?", (content_item_id,)
    )
    row = cursor.fetchone()
    return dict(row) if row is not None else None


def _assert_snapshot_table(table: str) -> None:
    if table not in _SNAPSHOT_TABLES:
        raise ValueError(f"Unknown table: {table!r}")
