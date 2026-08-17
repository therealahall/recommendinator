"""The ``sync_runs`` table: how each run of each source's sync ended."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Sequence
from datetime import datetime
from itertools import takewhile

from src.storage.schema import SyncRunDict, SyncRunStatus
from src.storage.sqlite_db import SQLiteDB

# Read as how a source has been doing lately, not as an audit trail.
_MAX_RUNS_PER_SOURCE = 50

_COLUMNS = (
    "id, source_id, started_at, finished_at, status, items_added, "
    "items_updated, items_unchanged, total_items, errors_json"
)

# The id breaks a tie on the stamp, so same-instant runs keep their order.
_NEWEST_FIRST = "ORDER BY started_at DESC, id DESC"


def _stamp(moment: datetime | None) -> str | None:
    """Fixed width: the column sorts as text, and a short stamp sorts by sign."""
    return moment.isoformat(timespec="microseconds") if moment is not None else None


def _to_dict(row: sqlite3.Row) -> SyncRunDict:
    return SyncRunDict(
        id=row["id"],
        source_id=row["source_id"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
        status=row["status"],
        items_added=row["items_added"],
        items_updated=row["items_updated"],
        items_unchanged=row["items_unchanged"],
        total_items=row["total_items"],
        errors=json.loads(row["errors_json"]),
    )


class SyncRunStore:
    def __init__(self, sqlite_db: SQLiteDB) -> None:
        self._sqlite_db = sqlite_db

    def record(
        self,
        user_id: int,
        source_id: str,
        *,
        started_at: datetime,
        finished_at: datetime | None,
        status: SyncRunStatus,
        items_added: int = 0,
        items_updated: int = 0,
        items_unchanged: int = 0,
        total_items: int = 0,
        errors: Sequence[str] = (),
    ) -> int:
        """Prunes past ``_MAX_RUNS_PER_SOURCE`` in the same transaction."""
        with self._sqlite_db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO sync_runs (
                    user_id, source_id, started_at, finished_at, status,
                    items_added, items_updated, items_unchanged, total_items,
                    errors_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    source_id,
                    _stamp(started_at),
                    _stamp(finished_at),
                    status,
                    items_added,
                    items_updated,
                    items_unchanged,
                    total_items,
                    json.dumps(list(errors)),
                ),
            )
            # Read before the prune, which leaves it meaning nothing.
            run_id: int = cursor.lastrowid  # type: ignore[assignment]
            cursor.execute(
                f"""
                DELETE FROM sync_runs WHERE id IN (
                    SELECT id FROM sync_runs
                     WHERE user_id = ? AND source_id = ?
                     {_NEWEST_FIRST} LIMIT -1 OFFSET ?
                )
                """,
                (user_id, source_id, _MAX_RUNS_PER_SOURCE),
            )
            conn.commit()
            return run_id

    def list_for_source(
        self, user_id: int, source_id: str, limit: int
    ) -> list[SyncRunDict]:
        with self._sqlite_db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                f"SELECT {_COLUMNS} FROM sync_runs "
                f"WHERE user_id = ? AND source_id = ? {_NEWEST_FIRST} LIMIT ?",
                (user_id, source_id, limit),
            )
            return [_to_dict(row) for row in cursor.fetchall()]

    def list_recent(self, user_id: int, limit: int) -> list[SyncRunDict]:
        with self._sqlite_db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                f"SELECT {_COLUMNS} FROM sync_runs "
                f"WHERE user_id = ? {_NEWEST_FIRST} LIMIT ?",
                (user_id, limit),
            )
            return [_to_dict(row) for row in cursor.fetchall()]

    def latest_per_source(self, user_id: int) -> dict[str, SyncRunDict]:
        with self._sqlite_db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                f"SELECT {_COLUMNS} FROM sync_runs "
                "WHERE user_id = ? ORDER BY started_at ASC, id ASC",
                (user_id,),
            )
            # Oldest first, so each source's newest run is the one that stands.
            return {row["source_id"]: _to_dict(row) for row in cursor.fetchall()}

    def consecutive_failures(self, user_id: int, source_id: str) -> int:
        """A skip attempted nothing, so it neither breaks nor extends the run."""
        with self._sqlite_db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT status FROM sync_runs "
                "WHERE user_id = ? AND source_id = ? AND status != 'skipped' "
                f"{_NEWEST_FIRST}",
                (user_id, source_id),
            )
            statuses = [row["status"] for row in cursor.fetchall()]
        return sum(1 for _ in takewhile(lambda status: status == "failed", statuses))
