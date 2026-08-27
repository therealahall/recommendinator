import sqlite3
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from src.storage import sync_runs
from src.storage.manager import StorageManager
from src.storage.schema import _SYNC_RUNS_TABLE, SyncRunStatus, create_schema
from src.storage.sync_runs import STALE_AFTER
from src.utils.dates import utc_now

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
        omitted_errors=48,
    )

    run = storage.sync_runs.latest_per_source(1)["steam"]

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
    # The capped list cannot be totalled by counting it.
    assert run["omitted_errors"] == 48


_OMITTED_COLUMN = "omitted_errors"


def _legacy_sync_runs_db(path: Path) -> None:
    """A database whose ``sync_runs`` predates the omitted-error column."""
    legacy_table = "\n".join(
        line for line in _SYNC_RUNS_TABLE.splitlines() if _OMITTED_COLUMN not in line
    )
    # The schema version did not move for this column, so an ALTER is all that
    # tells an operator's existing database from a fresh one.
    assert legacy_table != _SYNC_RUNS_TABLE
    conn = sqlite3.connect(path)
    try:
        create_schema(conn)
        conn.execute("DROP TABLE sync_runs")
        conn.execute(legacy_table)
        conn.execute(
            "INSERT INTO sync_runs (user_id, source_id, started_at, finished_at, "
            "status, errors_json) VALUES (1, 'steam', ?, ?, 'failed', ?)",
            (_START.isoformat(), _START.isoformat(), '["timed out"]'),
        )
        conn.commit()
    finally:
        conn.close()


def test_a_database_written_before_the_count_still_serves_its_history(
    tmp_path: Path,
) -> None:
    """Every run read names the column, so an upgrade that did not add it takes
    run history down on the databases that already had runs in them."""
    db_path = tmp_path / "legacy.db"
    _legacy_sync_runs_db(db_path)

    run = StorageManager(sqlite_path=db_path).sync_runs.latest_per_source(1)["steam"]

    assert run["errors"] == ["timed out"]
    assert run["omitted_errors"] == 0


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


def test_two_racing_claims_leave_exactly_one_holder_of_the_source(
    storage: StorageManager,
) -> None:
    barrier = threading.Barrier(2)
    lock = threading.Lock()
    outcomes: list[int | None] = []

    def take() -> None:
        barrier.wait()
        claimed = storage.sync_runs.claim(1, "steam")
        with lock:
            outcomes.append(claimed)

    racers = [threading.Thread(target=take) for _ in range(2)]
    for racer in racers:
        racer.start()
    for racer in racers:
        racer.join()

    assert sum(1 for outcome in outcomes if outcome is not None) == 1
    _record(storage)
    assert storage.sync_runs.claim(1, "steam") is not None


def test_a_claim_a_killed_run_left_open_is_taken_over_once_it_goes_stale(
    storage: StorageManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = storage.sync_runs.claim(1, "steam")
    assert first is not None
    assert storage.sync_runs.claim(1, "steam") is None

    later = utc_now() + STALE_AFTER + timedelta(seconds=1)
    monkeypatch.setattr(sync_runs, "utc_now", lambda: later)

    assert storage.sync_runs.claim(1, "steam") not in (None, first)


def test_a_run_releases_its_own_claim_and_not_the_one_that_replaced_it(
    storage: StorageManager,
) -> None:
    """Released by source id, a finished run's exit dropped the next run's claim."""
    first = storage.sync_runs.claim(1, "steam")
    assert first is not None
    _record(storage)
    second = storage.sync_runs.claim(1, "steam")
    assert second is not None

    storage.sync_runs.release(first)

    assert storage.sync_runs.claim(1, "steam") is None
    assert [run["id"] for run in storage.sync_runs.list_recent(1, 10)] == [first]


def test_an_in_flight_claim_is_not_read_as_a_finished_run(
    storage: StorageManager,
) -> None:
    failed = _record(storage, minute=0, status="failed")
    assert storage.sync_runs.claim(1, "steam") is not None

    assert storage.sync_runs.latest_per_source(1)["steam"]["status"] == "failed"
    assert storage.sync_runs.consecutive_failures(1, "steam") == 1
    assert [run["id"] for run in storage.sync_runs.list_for_source(1, "steam", 10)] == [
        failed
    ]
    assert [run["id"] for run in storage.sync_runs.list_recent(1, 10)] == [failed]


def test_list_recent_spans_every_source_newest_first(storage: StorageManager) -> None:
    oldest = _record(storage, "steam", minute=0)
    middle = _record(storage, "trakt", minute=5)
    newest = _record(storage, "steam", minute=10)

    assert [run["id"] for run in storage.sync_runs.list_recent(1, 10)] == [
        newest,
        middle,
        oldest,
    ]
    assert [run["id"] for run in storage.sync_runs.list_recent(1, 1)] == [newest]


_SYNC_RUNS_AT_VERSION_SEVENTEEN = (
    "CREATE TABLE sync_runs ("
    "id INTEGER PRIMARY KEY AUTOINCREMENT, "
    "user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE, "
    "source_id TEXT NOT NULL, "
    "started_at TIMESTAMP NOT NULL, "
    "finished_at TIMESTAMP NOT NULL, "
    "status TEXT NOT NULL, "
    "items_added INTEGER NOT NULL DEFAULT 0, "
    "items_updated INTEGER NOT NULL DEFAULT 0, "
    "items_unchanged INTEGER NOT NULL DEFAULT 0, "
    "total_items INTEGER NOT NULL DEFAULT 0, "
    "errors_json TEXT NOT NULL DEFAULT '[]'"
    ")"
)


def test_the_runs_a_version_seventeen_library_holds_survive_the_rebuild(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "test.db"
    StorageManager(sqlite_path=db_path)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("DROP TABLE sync_runs")
        conn.execute(_SYNC_RUNS_AT_VERSION_SEVENTEEN)
        conn.execute(
            "INSERT INTO sync_runs (user_id, source_id, started_at, finished_at, "
            "status, items_added, total_items, errors_json) "
            "VALUES (1, 'steam', ?, ?, 'failed', 3, 12, '[\"timed out\"]')",
            (_START.isoformat(), (_START + timedelta(minutes=1)).isoformat()),
        )
        conn.execute("PRAGMA user_version = 17")
        conn.commit()
    finally:
        conn.close()

    storage = StorageManager(sqlite_path=db_path)

    run = storage.sync_runs.latest_per_source(1)["steam"]
    assert (run["status"], run["items_added"], run["total_items"]) == ("failed", 3, 12)
    assert run["errors"] == ["timed out"]
    assert storage.sync_runs.claim(1, "steam") is not None


def test_recording_prunes_to_the_newest_fifty_runs_of_a_source(
    storage: StorageManager,
) -> None:
    """The trakt row is asserted because a widened DELETE would take it too."""
    _record(storage, "trakt", minute=0, status="failed")
    for minute in range(60):
        _record(storage, "steam", minute=minute, status="failed")

    assert storage.sync_runs.consecutive_failures(1, "steam") == 50
    assert storage.sync_runs.consecutive_failures(1, "trakt") == 1
