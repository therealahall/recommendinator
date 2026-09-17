from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any, ClassVar

from src.storage.sqlite_db import SQLiteDB

#: Generous: a live job wrongly called dead lets a second start beside it.
STALE_AFTER = timedelta(minutes=5)


def stamp(moment: datetime) -> str:
    return moment.isoformat(timespec="microseconds")


def parse(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def is_alive(heartbeat: datetime | None, now: datetime) -> bool:
    return heartbeat is not None and now - heartbeat <= STALE_AFTER


class SingleRowJobStore:
    table: ClassVar[str]
    fresh: ClassVar[Mapping[str, Any]]

    _CLAIMED: ClassVar[Mapping[str, Any]] = {
        "running": 1,
        "completed": 0,
        "cancelled": 0,
        "stop_requested": 0,
        "current_item": "",
        "errors_json": "[]",
    }

    def __init__(self, sqlite_db: SQLiteDB) -> None:
        self._sqlite_db = sqlite_db

    def _claim(self, **starting: Any) -> bool:
        now = datetime.now(UTC)
        columns: dict[str, Any] = {
            **self._CLAIMED,
            **self.fresh,
            **starting,
            "started_at": stamp(now),
            "heartbeat_at": stamp(now),
        }
        assignments = ", ".join(f"{column} = ?" for column in columns)
        with self._sqlite_db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                f"INSERT INTO {self.table} (id, running) VALUES (1, 0)"
                " ON CONFLICT(id) DO NOTHING"
            )
            cursor.execute(
                f"UPDATE {self.table} SET {assignments} WHERE id = 1"
                " AND (running = 0 OR heartbeat_at IS NULL OR heartbeat_at < ?)",
                [*columns.values(), stamp(now - STALE_AFTER)],
            )
            conn.commit()
            return cursor.rowcount == 1

    def _publish(self, *, running: bool, **columns: Any) -> None:
        # A heartbeat landing after `finish` would otherwise set running back
        # to 1, refusing every Start door until it goes stale.
        assignments: dict[str, Any] = {"running": 1 if running else 0, **columns}
        if not running:
            assignments["stop_requested"] = 0
        assignments["heartbeat_at"] = stamp(datetime.now(UTC))
        set_clause = ", ".join(f"{column} = ?" for column in assignments)
        guard = " AND running = 1" if running else ""
        with self._sqlite_db.connection() as conn:
            conn.execute(
                f"UPDATE {self.table} SET {set_clause} WHERE id = 1{guard}",
                list(assignments.values()),
            )
            conn.commit()

    def request_stop(self) -> bool:
        cutoff = stamp(datetime.now(UTC) - STALE_AFTER)
        with self._sqlite_db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                f"UPDATE {self.table} SET stop_requested = 1"
                " WHERE id = 1 AND running = 1 AND heartbeat_at >= ?",
                (cutoff,),
            )
            conn.commit()
            return cursor.rowcount == 1

    def stop_requested(self) -> bool:
        with self._sqlite_db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(f"SELECT stop_requested FROM {self.table} WHERE id = 1")
            row = cursor.fetchone()
        return row is not None and bool(row["stop_requested"])
