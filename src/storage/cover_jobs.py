"""A backfill the server started was invisible to the CLI, so both walked."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from src.storage.enrichment_jobs import STALE_AFTER
from src.storage.sqlite_db import SQLiteDB

_COLUMNS = (
    "running, completed, cancelled, total_items, items_processed, items_cached, "
    "items_cleared, items_failed, items_without_cover, current_item, errors_json, "
    "started_at, heartbeat_at"
)

_CLAIM = (
    "UPDATE cover_backfill_job"
    " SET running = 1, completed = 0, cancelled = 0, total_items = 0,"
    " items_processed = 0,"
    " items_cached = 0, items_cleared = 0, items_failed = 0, stop_requested = 0,"
    " items_without_cover = 0,"
    " current_item = '', errors_json = '[]', started_at = ?, heartbeat_at = ?"
    " WHERE id = 1"
    " AND (running = 0 OR heartbeat_at IS NULL OR heartbeat_at < ?)"
)


def _stamp(moment: datetime) -> str:
    return moment.isoformat(timespec="microseconds")


@dataclass
class CoverBackfillRecord:
    running: bool = False
    completed: bool = False
    #: A stop the user asked for, which neither interface reports as a failure.
    cancelled: bool = False
    total: int = 0
    processed: int = 0
    cached: int = 0
    cleared: int = 0
    failed: int = 0
    without_cover: int = 0
    current_item: str = ""
    errors: list[str] = field(default_factory=list)
    started_at: datetime | None = None

    def payload(self) -> dict[str, Any]:
        return {
            "running": self.running,
            "completed": self.completed,
            "cancelled": self.cancelled,
            "total_items": self.total,
            "items_processed": self.processed,
            "items_cached": self.cached,
            "items_cleared": self.cleared,
            "items_failed": self.failed,
            "items_without_cover": self.without_cover,
            "current_item": self.current_item,
            "errors": list(self.errors),
        }


def _moment(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _to_record(row: sqlite3.Row, *, alive: bool) -> CoverBackfillRecord:
    return CoverBackfillRecord(
        running=bool(row["running"]) and alive,
        completed=bool(row["completed"]),
        cancelled=bool(row["cancelled"]),
        total=row["total_items"],
        processed=row["items_processed"],
        cached=row["items_cached"],
        cleared=row["items_cleared"],
        failed=row["items_failed"],
        without_cover=row["items_without_cover"],
        current_item=row["current_item"],
        errors=json.loads(row["errors_json"]),
        started_at=_moment(row["started_at"]),
    )


class CoverBackfillStore:
    def __init__(self, sqlite_db: SQLiteDB) -> None:
        self._sqlite_db = sqlite_db

    def read(self) -> CoverBackfillRecord:
        now = datetime.now(UTC)
        with self._sqlite_db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(f"SELECT {_COLUMNS} FROM cover_backfill_job WHERE id = 1")
            row = cursor.fetchone()
        if row is None:
            return CoverBackfillRecord()
        heartbeat = _moment(row["heartbeat_at"])
        alive = heartbeat is not None and now - heartbeat <= STALE_AFTER
        return _to_record(row, alive=alive)

    def claim(self) -> bool:
        """One statement, so two processes racing cannot both win it."""
        now = datetime.now(UTC)
        with self._sqlite_db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO cover_backfill_job (id, running) VALUES (1, 0) "
                "ON CONFLICT(id) DO NOTHING"
            )
            cursor.execute(
                _CLAIM, (_stamp(now), _stamp(now), _stamp(now - STALE_AFTER))
            )
            conn.commit()
            return cursor.rowcount == 1

    def request_stop(self) -> bool:
        """False when there is nothing to stop."""
        cutoff = _stamp(datetime.now(UTC) - STALE_AFTER)
        with self._sqlite_db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE cover_backfill_job SET stop_requested = 1"
                " WHERE id = 1 AND running = 1 AND heartbeat_at >= ?",
                (cutoff,),
            )
            conn.commit()
            return cursor.rowcount == 1

    def stop_requested(self) -> bool:
        with self._sqlite_db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT stop_requested FROM cover_backfill_job WHERE id = 1")
            row = cursor.fetchone()
        return row is not None and bool(row["stop_requested"])

    def heartbeat(self, record: CoverBackfillRecord) -> None:
        self._publish(record, running=True)

    def finish(self, record: CoverBackfillRecord) -> None:
        self._publish(record, running=False)

    def _publish(self, record: CoverBackfillRecord, *, running: bool) -> None:
        # A walk that heartbeats after Ctrl-C published `finish` would otherwise
        # set running back to 1, refusing both Start doors until it goes stale.
        guard = " AND running = 1" if running else ""
        cleared = "" if running else "stop_requested = 0, "
        with self._sqlite_db.connection() as conn:
            conn.execute(
                "UPDATE cover_backfill_job SET running = ?, completed = ?, "
                "cancelled = ?, total_items = ?, items_processed = ?, "
                "items_cached = ?, items_cleared = ?, items_failed = ?, "
                "items_without_cover = ?, "
                f"current_item = ?, errors_json = ?, {cleared}"
                f"heartbeat_at = ? WHERE id = 1{guard}",
                (
                    1 if running else 0,
                    1 if record.completed else 0,
                    1 if record.cancelled else 0,
                    record.total,
                    record.processed,
                    record.cached,
                    record.cleared,
                    record.failed,
                    record.without_cover,
                    record.current_item,
                    json.dumps(list(record.errors)),
                    _stamp(datetime.now(UTC)),
                ),
            )
            conn.commit()
