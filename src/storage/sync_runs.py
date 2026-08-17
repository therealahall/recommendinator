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
    """Render *moment* as ISO 8601 text of a fixed width.

    The column is ordered as text, and ``isoformat`` drops a zero microseconds
    field — a stamp one field short sorts by its offset sign instead.
    """
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
    """Each source's recent sync history. ``StorageManager.sync_runs``."""

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
        """Store one finished run, returning its id.

        Everything past :data:`_MAX_RUNS_PER_SOURCE` for this source is pruned
        in the same transaction, so a schedule cannot grow the table for good.
        """
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
        """Return one source's runs, newest first."""
        with self._sqlite_db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                f"SELECT {_COLUMNS} FROM sync_runs "
                f"WHERE user_id = ? AND source_id = ? {_NEWEST_FIRST} LIMIT ?",
                (user_id, source_id, limit),
            )
            return [_to_dict(row) for row in cursor.fetchall()]

    def list_recent(self, user_id: int, limit: int) -> list[SyncRunDict]:
        """Return the user's runs across every source, newest first."""
        with self._sqlite_db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                f"SELECT {_COLUMNS} FROM sync_runs "
                f"WHERE user_id = ? {_NEWEST_FIRST} LIMIT ?",
                (user_id, limit),
            )
            return [_to_dict(row) for row in cursor.fetchall()]

    def latest_per_source(self, user_id: int) -> dict[str, SyncRunDict]:
        """Return each source's most recent run, keyed by source id."""
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
        """Count the failures a source has run up since it last succeeded.

        Skipped runs are left out entirely: a skip attempted nothing, so it
        neither breaks the run of failures nor extends it.
        """
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
