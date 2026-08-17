from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from src.storage.manager import StorageManager
from src.storage.schema import SyncRunStatus

_START = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)


@pytest.fixture()
def storage(tmp_path: Path) -> StorageManager:
    return StorageManager(sqlite_path=tmp_path / "test.db")


def _record(
    storage: StorageManager,
    source_id: str = "steam",
    *,
    status: SyncRunStatus = "completed",
    minute: int = 0,
    errors: tuple[str, ...] = (),
) -> int:
    started_at = _START + timedelta(minutes=minute)
    return storage.sync_runs.record(
        1,
        source_id,
        started_at=started_at,
        finished_at=started_at + timedelta(seconds=30),
        status=status,
        errors=errors,
    )


def test_a_recorded_run_reads_back_whole(storage: StorageManager) -> None:
    run_id = storage.sync_runs.record(
        1,
        "steam",
        started_at=_START,
        finished_at=_START + timedelta(minutes=2),
        status="failed",
        items_added=3,
        items_updated=2,
        items_unchanged=7,
        total_items=12,
        errors=("timed out", "429 from Steam"),
    )

    (run,) = storage.sync_runs.list_for_source(1, "steam", limit=10)

    assert run["id"] == run_id
    assert run["status"] == "failed"
    assert run["started_at"].startswith("2026-03-01T12:00:00")
    assert run["finished_at"] is not None
    assert (run["items_added"], run["items_updated"], run["items_unchanged"]) == (
        3,
        2,
        7,
    )
    assert run["total_items"] == 12
    assert run["errors"] == ["timed out", "429 from Steam"]


def test_list_recent_spans_every_source_newest_first(storage: StorageManager) -> None:
    _record(storage, "steam", minute=0)
    _record(storage, "trakt", minute=5)
    _record(storage, "steam", minute=10)

    recent = storage.sync_runs.list_recent(1, limit=10)

    assert [(run["source_id"], run["started_at"][11:16]) for run in recent] == [
        ("steam", "12:10"),
        ("trakt", "12:05"),
        ("steam", "12:00"),
    ]


def test_latest_per_source_reports_each_source_newest_run(
    storage: StorageManager,
) -> None:
    _record(storage, "steam", minute=0, status="failed")
    newest_steam = _record(storage, "steam", minute=10)
    trakt = _record(storage, "trakt", minute=5)

    latest = storage.sync_runs.latest_per_source(1)

    assert {source: run["id"] for source, run in latest.items()} == {
        "steam": newest_steam,
        "trakt": trakt,
    }


def test_consecutive_failures_counts_only_the_run_since_the_last_success(
    storage: StorageManager,
) -> None:
    _record(storage, minute=0, status="failed")
    _record(storage, minute=5, status="completed")
    assert storage.sync_runs.consecutive_failures(1, "steam") == 0

    _record(storage, minute=10, status="failed")
    _record(storage, minute=15, status="failed")
    # A dropped source_id filter would back every source off because one broke.
    _record(storage, "trakt", minute=12, status="failed")
    _record(storage, "trakt", minute=17, status="failed")

    assert storage.sync_runs.consecutive_failures(1, "steam") == 2

    _record(storage, minute=25, status="completed")

    assert storage.sync_runs.consecutive_failures(1, "steam") == 0


def test_recording_prunes_to_the_newest_fifty_runs_of_a_source(
    storage: StorageManager,
) -> None:
    """The trakt row is asserted because a widened DELETE would take it too."""
    _record(storage, "trakt", minute=0)
    for minute in range(60):
        _record(storage, "steam", minute=minute)

    runs = storage.sync_runs.list_for_source(1, "steam", limit=100)

    assert len(runs) == 50
    assert runs[0]["started_at"][11:16] == "12:59"
    assert runs[-1]["started_at"][11:16] == "12:10"
    assert len(storage.sync_runs.list_for_source(1, "trakt", limit=100)) == 1
