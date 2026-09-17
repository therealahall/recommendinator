from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from src.storage.job_claim import SingleRowJobStore, is_alive, parse

_COLUMNS = (
    "running, completed, cancelled, total_items, items_processed, items_changed, "
    "current_item, errors_json, started_at, heartbeat_at, rerun_requested"
)


@dataclass
class LibraryRebuildRecord:
    running: bool = False
    completed: bool = False
    cancelled: bool = False
    total: int = 0
    processed: int = 0
    changed: int = 0
    current_item: str = ""
    errors: list[str] = field(default_factory=list)
    started_at: datetime | None = None
    rerun_requested: bool = False

    def payload(self) -> dict[str, Any]:
        return {
            "running": self.running,
            "completed": self.completed,
            "cancelled": self.cancelled,
            "total_items": self.total,
            "items_processed": self.processed,
            "items_changed": self.changed,
            "current_item": self.current_item,
            "errors": list(self.errors),
        }


def _to_record(row: sqlite3.Row, *, alive: bool) -> LibraryRebuildRecord:
    return LibraryRebuildRecord(
        running=bool(row["running"]) and alive,
        completed=bool(row["completed"]),
        cancelled=bool(row["cancelled"]),
        total=row["total_items"],
        processed=row["items_processed"],
        changed=row["items_changed"],
        current_item=row["current_item"],
        errors=json.loads(row["errors_json"]),
        started_at=parse(row["started_at"]),
        rerun_requested=bool(row["rerun_requested"]),
    )


class LibraryRebuildStore(SingleRowJobStore):
    table = "library_rebuild_job"
    fresh = {"total_items": 0, "items_processed": 0, "items_changed": 0}

    def read(self) -> LibraryRebuildRecord:
        now = datetime.now(UTC)
        with self._sqlite_db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(f"SELECT {_COLUMNS} FROM {self.table} WHERE id = 1")
            row = cursor.fetchone()
        if row is None:
            return LibraryRebuildRecord()
        return _to_record(row, alive=is_alive(parse(row["heartbeat_at"]), now))

    def claim(self) -> bool:
        return self._claim(rerun_requested=0)

    def request_rerun(self) -> None:
        with self._sqlite_db.connection() as conn:
            conn.execute(
                f"INSERT INTO {self.table} (id, running) VALUES (1, 0)"
                " ON CONFLICT(id) DO NOTHING"
            )
            conn.execute(f"UPDATE {self.table} SET rerun_requested = 1 WHERE id = 1")
            conn.commit()

    def rerun_requested(self) -> bool:
        with self._sqlite_db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(f"SELECT rerun_requested FROM {self.table} WHERE id = 1")
            row = cursor.fetchone()
        return row is not None and bool(row["rerun_requested"])

    def take_rerun(self) -> bool:
        with self._sqlite_db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                f"UPDATE {self.table} SET rerun_requested = 0"
                " WHERE id = 1 AND rerun_requested = 1"
            )
            conn.commit()
            return cursor.rowcount == 1

    def heartbeat(self, record: LibraryRebuildRecord) -> None:
        self._publish_record(record, running=True)

    def finish(self, record: LibraryRebuildRecord) -> None:
        self._publish_record(record, running=False)

    def _publish_record(self, record: LibraryRebuildRecord, *, running: bool) -> None:
        self._publish(
            running=running,
            completed=1 if record.completed else 0,
            cancelled=1 if record.cancelled else 0,
            total_items=record.total,
            items_processed=record.processed,
            items_changed=record.changed,
            current_item=record.current_item,
            errors_json=json.dumps(list(record.errors)),
        )
