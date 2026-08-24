from __future__ import annotations

import json
import sqlite3
from collections.abc import Sequence
from datetime import datetime, timedelta
from itertools import takewhile

from src.storage.schema import SyncRunDict, SyncRunStatus
from src.storage.sqlite_db import SQLiteDB
from src.utils.dates import utc_now

# Read as how a source has been doing lately, not as an audit trail.
_MAX_RUNS_PER_SOURCE = 50

#: Generous: a live claim called dead lets a second sync run beside the first.
STALE_AFTER = timedelta(minutes=15)

HEARTBEAT_EVERY = timedelta(seconds=30)

_COLUMNS = (
    "id, source_id, started_at, finished_at, status, items_added, "
    "items_updated, items_unchanged, total_items, errors_json"
)

# The id breaks a tie on the stamp, so same-instant runs keep their order.
_NEWEST_FIRST = "ORDER BY started_at DESC, id DESC"


def _stamp(moment: datetime) -> str:
    """Fixed width: the column sorts as text, and a short stamp sorts by sign."""
    return moment.isoformat(timespec="microseconds")


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
        finished_at: datetime,
        status: SyncRunStatus,
        items_added: int = 0,
        items_updated: int = 0,
        items_unchanged: int = 0,
        total_items: int = 0,
        errors: Sequence[str] = (),
    ) -> int:
        outcome = (
            _stamp(started_at),
            _stamp(finished_at),
            status,
            items_added,
            items_updated,
            items_unchanged,
            total_items,
            json.dumps(list(errors)),
        )
        with self._sqlite_db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT id FROM sync_runs "
                "WHERE user_id = ? AND source_id = ? AND finished_at IS NULL",
                (user_id, source_id),
            )
            claim = cursor.fetchone()
            if claim is None:
                cursor.execute(
                    "INSERT INTO sync_runs (started_at, finished_at, status, "
                    "items_added, items_updated, items_unchanged, total_items, "
                    "errors_json, user_id, source_id) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (*outcome, user_id, source_id),
                )
                # Read before the prune, which leaves it meaning nothing.
                run_id: int = cursor.lastrowid  # type: ignore[assignment]
            else:
                run_id = claim["id"]
                cursor.execute(
                    "UPDATE sync_runs SET started_at = ?, finished_at = ?, "
                    "status = ?, items_added = ?, items_updated = ?, "
                    "items_unchanged = ?, total_items = ?, errors_json = ? "
                    "WHERE id = ?",
                    (*outcome, run_id),
                )
            cursor.execute(
                "DELETE FROM sync_runs WHERE id IN ("
                "SELECT id FROM sync_runs WHERE user_id = ? AND source_id = ? "
                f"AND finished_at IS NOT NULL {_NEWEST_FIRST} LIMIT -1 OFFSET ?)",
                (user_id, source_id, _MAX_RUNS_PER_SOURCE),
            )
            conn.commit()
            return run_id

    def claim(self, user_id: int, source_id: str) -> bool:
        """Take the source, unless a live run holds it: the partial unique index
        decides the race, and a killed run's claim goes stale rather than reaped."""
        now = utc_now()
        with self._sqlite_db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "DELETE FROM sync_runs WHERE user_id = ? AND source_id = ? "
                "AND finished_at IS NULL AND heartbeat_at < ?",
                (user_id, source_id, _stamp(now - STALE_AFTER)),
            )
            cursor.execute(
                "INSERT INTO sync_runs "
                "(user_id, source_id, started_at, status, heartbeat_at) "
                "VALUES (?, ?, ?, 'running', ?) ON CONFLICT DO NOTHING",
                (user_id, source_id, _stamp(now), _stamp(now)),
            )
            claimed = cursor.rowcount == 1
            conn.commit()
            return claimed

    def heartbeat(self, user_id: int, source_id: str) -> None:
        with self._sqlite_db.connection() as conn:
            conn.execute(
                "UPDATE sync_runs SET heartbeat_at = ? "
                "WHERE user_id = ? AND source_id = ? AND finished_at IS NULL",
                (_stamp(utc_now()), user_id, source_id),
            )
            conn.commit()

    def release(self, user_id: int, source_id: str) -> None:
        with self._sqlite_db.connection() as conn:
            conn.execute(
                "DELETE FROM sync_runs "
                "WHERE user_id = ? AND source_id = ? AND finished_at IS NULL",
                (user_id, source_id),
            )
            conn.commit()

    def delete_for_source(self, user_id: int, source_id: str) -> int:
        with self._sqlite_db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "DELETE FROM sync_runs WHERE user_id = ? AND source_id = ?",
                (user_id, source_id),
            )
            conn.commit()
            return cursor.rowcount

    def latest_per_source(self, user_id: int) -> dict[str, SyncRunDict]:
        with self._sqlite_db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                f"SELECT {_COLUMNS} FROM sync_runs "
                "WHERE user_id = ? AND finished_at IS NOT NULL "
                "ORDER BY started_at ASC, id ASC",
                (user_id,),
            )
            # Oldest first, so each source's newest run is the one that stands.
            return {row["source_id"]: _to_dict(row) for row in cursor.fetchall()}

    def consecutive_failures(self, user_id: int, source_id: str) -> int:
        with self._sqlite_db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT status FROM sync_runs WHERE user_id = ? AND source_id = ? "
                f"AND finished_at IS NOT NULL {_NEWEST_FIRST}",
                (user_id, source_id),
            )
            statuses = [row["status"] for row in cursor.fetchall()]
        return sum(1 for _ in takewhile(lambda status: status == "failed", statuses))
