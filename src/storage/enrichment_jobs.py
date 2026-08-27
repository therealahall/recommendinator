"""The live enrichment job, which the CLI and the server both read here.

A manager instance is per-process, so a job the server started was invisible
to the CLI and could not be stopped from it.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from src.storage.sqlite_db import SQLiteDB

#: Generous: a live job wrongly called dead lets a second start beside it, and
#: one item can be a rate-limited call with retries behind it.
STALE_AFTER = timedelta(minutes=5)

_COLUMNS = (
    "running, completed, cancelled, stop_requested, items_processed, "
    "items_enriched, items_failed, items_not_found, total_items, "
    "current_item, content_type, errors_json, started_at, finished_at, "
    "heartbeat_at"
)


def _stamp(moment: datetime) -> str:
    return moment.isoformat(timespec="microseconds")


def _parse(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


@dataclass
class EnrichmentJobRecord:
    """What the row says, with the derived fields both interfaces render."""

    running: bool = False
    completed: bool = False
    cancelled: bool = False
    stop_requested: bool = False
    items_processed: int = 0
    items_enriched: int = 0
    items_failed: int = 0
    items_not_found: int = 0
    total_items: int = 0
    current_item: str = ""
    content_type: str | None = None
    errors: list[str] = field(default_factory=list)
    started_at: datetime | None = None
    finished_at: datetime | None = None

    @property
    def elapsed_seconds(self) -> float:
        if self.started_at is None:
            return 0.0
        end = self.finished_at or datetime.now(UTC)
        return (end - self.started_at).total_seconds()

    @property
    def progress_percent(self) -> float:
        if self.total_items == 0:
            return 0.0
        return (self.items_processed / self.total_items) * 100


def _to_record(row: sqlite3.Row, *, alive: bool) -> EnrichmentJobRecord:
    stranded = bool(row["running"]) and not alive
    return EnrichmentJobRecord(
        running=bool(row["running"]) and alive,
        completed=bool(row["completed"]),
        # A stranded job reads as cancelled: it will not finish, and nothing
        # else is left to say so.
        cancelled=bool(row["cancelled"]) or stranded,
        stop_requested=bool(row["stop_requested"]),
        items_processed=row["items_processed"],
        items_enriched=row["items_enriched"],
        items_failed=row["items_failed"],
        items_not_found=row["items_not_found"],
        total_items=row["total_items"],
        current_item=row["current_item"],
        content_type=row["content_type"],
        errors=json.loads(row["errors_json"]),
        started_at=_parse(row["started_at"]),
        finished_at=_parse(row["finished_at"]),
    )


class EnrichmentJobStore:
    def __init__(self, sqlite_db: SQLiteDB) -> None:
        self._sqlite_db = sqlite_db

    def read(self) -> EnrichmentJobRecord:
        """The job as it stands. A never-run install reads as an idle job."""
        now = datetime.now(UTC)
        with self._sqlite_db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(f"SELECT {_COLUMNS} FROM enrichment_job WHERE id = 1")
            row = cursor.fetchone()
        if row is None:
            return EnrichmentJobRecord()
        return _to_record(row, alive=self._alive(_parse(row["heartbeat_at"]), now))

    @staticmethod
    def _alive(heartbeat: datetime | None, now: datetime) -> bool:
        return heartbeat is not None and now - heartbeat <= STALE_AFTER

    def claim(self, content_type: str | None) -> bool:
        """Take the job, unless a live one already holds it.

        One statement, so two processes racing cannot both win it.
        """
        now = datetime.now(UTC)
        cutoff = _stamp(now - STALE_AFTER)
        with self._sqlite_db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO enrichment_job (id, running) VALUES (1, 0) "
                "ON CONFLICT(id) DO NOTHING"
            )
            cursor.execute(
                """
                UPDATE enrichment_job
                   SET running = 1, completed = 0, cancelled = 0,
                       stop_requested = 0, items_processed = 0,
                       items_enriched = 0, items_failed = 0,
                       items_not_found = 0, total_items = 0, current_item = '',
                       content_type = ?, errors_json = '[]', started_at = ?,
                       finished_at = NULL, heartbeat_at = ?
                 WHERE id = 1
                   AND (running = 0 OR heartbeat_at IS NULL OR heartbeat_at < ?)
                """,
                (content_type, _stamp(now), _stamp(now), cutoff),
            )
            conn.commit()
            return cursor.rowcount == 1

    def heartbeat(
        self,
        *,
        items_processed: int,
        items_enriched: int,
        items_failed: int,
        items_not_found: int,
        total_items: int,
        current_item: str,
        errors: Sequence[str],
    ) -> None:
        with self._sqlite_db.connection() as conn:
            conn.execute(
                "UPDATE enrichment_job SET items_processed = ?, "
                "items_enriched = ?, items_failed = ?, items_not_found = ?, "
                "total_items = ?, current_item = ?, errors_json = ?, "
                "heartbeat_at = ? WHERE id = 1",
                (
                    items_processed,
                    items_enriched,
                    items_failed,
                    items_not_found,
                    total_items,
                    current_item,
                    json.dumps(list(errors)),
                    _stamp(datetime.now(UTC)),
                ),
            )
            conn.commit()

    def finish(
        self, *, completed: bool, cancelled: bool, errors: Sequence[str]
    ) -> None:
        """Release the claim. Neither flag set means it stopped on an error."""
        now = _stamp(datetime.now(UTC))
        with self._sqlite_db.connection() as conn:
            conn.execute(
                "UPDATE enrichment_job SET running = 0, completed = ?, "
                "cancelled = ?, stop_requested = 0, current_item = '', "
                "errors_json = ?, finished_at = ?, heartbeat_at = ? WHERE id = 1",
                (
                    1 if completed else 0,
                    1 if cancelled else 0,
                    json.dumps(list(errors)),
                    now,
                    now,
                ),
            )
            conn.commit()

    def request_stop(self) -> bool:
        """Ask the running job to stop. False when there is nothing to stop."""
        cutoff = _stamp(datetime.now(UTC) - STALE_AFTER)
        with self._sqlite_db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE enrichment_job SET stop_requested = 1 "
                "WHERE id = 1 AND running = 1 AND heartbeat_at >= ?",
                (cutoff,),
            )
            conn.commit()
            return cursor.rowcount == 1

    def stop_requested(self) -> bool:
        with self._sqlite_db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT stop_requested FROM enrichment_job WHERE id = 1")
            row = cursor.fetchone()
        return row is not None and bool(row["stop_requested"])
