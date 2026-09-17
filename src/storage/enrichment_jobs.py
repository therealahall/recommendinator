"""A manager instance is per-process, so a job the server started was invisible
to the CLI and could not be stopped from it.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime

from src.storage.job_claim import SingleRowJobStore, is_alive, parse, stamp

_COLUMNS = (
    "running, completed, cancelled, stop_requested, items_processed, "
    "items_enriched, items_failed, items_not_found, total_items, "
    "current_item, content_type, errors_json, started_at, finished_at, "
    "heartbeat_at"
)


@dataclass
class EnrichmentJobRecord:
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
        started_at=parse(row["started_at"]),
        finished_at=parse(row["finished_at"]),
    )


class EnrichmentJobStore(SingleRowJobStore):
    table = "enrichment_job"
    fresh = {
        "items_processed": 0,
        "items_enriched": 0,
        "items_failed": 0,
        "items_not_found": 0,
        "total_items": 0,
        "finished_at": None,
    }

    def read(self) -> EnrichmentJobRecord:
        """A never-run install reads as an idle job."""
        now = datetime.now(UTC)
        with self._sqlite_db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(f"SELECT {_COLUMNS} FROM {self.table} WHERE id = 1")
            row = cursor.fetchone()
        if row is None:
            return EnrichmentJobRecord()
        return _to_record(row, alive=is_alive(parse(row["heartbeat_at"]), now))

    def claim(self, content_type: str | None) -> bool:
        return self._claim(content_type=content_type)

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
                f"UPDATE {self.table} SET items_processed = ?, "
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
                    stamp(datetime.now(UTC)),
                ),
            )
            conn.commit()

    def finish(
        self, *, completed: bool, cancelled: bool, errors: Sequence[str]
    ) -> None:
        """Neither flag set means it stopped on an error."""
        now = stamp(datetime.now(UTC))
        with self._sqlite_db.connection() as conn:
            conn.execute(
                f"UPDATE {self.table} SET running = 0, completed = ?, "
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
