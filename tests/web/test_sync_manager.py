"""Tests for background sync job manager."""

import asyncio
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import requests
from fastapi import FastAPI

from src.ingestion.sync import ALL_SOURCES_KEY, ALL_SOURCES_LABEL
from src.storage.manager import StorageManager
from src.storage.schema import SyncRunStatus
from src.utils.dates import utc_now
from src.web.app import lifespan
from src.web.scheduler import SyncScheduler, dispatch_due_syncs
from src.web.state import app_state
from src.web.sync_manager import (
    SyncError,
    SyncJob,
    SyncManager,
    SyncStatus,
)


def _planted(manager: SyncManager, source: str = "steam") -> SyncJob:
    """Create a job entry directly so tests can drive update_progress
    without spawning the daemon thread that ``start_sync`` would launch.
    """
    job = SyncJob(source=source, status=SyncStatus.RUNNING, started_at=datetime.now())
    manager._jobs[source] = job
    return job


class TestSyncJobToDict:
    """Tests for SyncJob.to_dict() serialization."""

    def test_to_dict_with_all_fields_populated(self) -> None:
        started = datetime(2026, 2, 21, 10, 0, 0)
        completed = datetime(2026, 2, 21, 10, 5, 0)
        job = SyncJob(
            source="goodreads",
            status=SyncStatus.COMPLETED,
            started_at=started,
            completed_at=completed,
            items_processed=50,
            total_items=100,
            current_item="The Name of the Wind",
            current_source="goodreads",
            error_message="Some warning",
            errors=[
                SyncError("goodreads", "Error 1"),
                SyncError("goodreads", "Error 2"),
            ],
        )

        result = job.to_dict()

        assert result["source"] == "goodreads"
        assert result["status"] == "completed"
        assert result["started_at"] == started.isoformat()
        assert result["completed_at"] == completed.isoformat()
        assert result["items_processed"] == 50
        assert result["total_items"] == 100
        assert result["current_item"] == "The Name of the Wind"
        assert result["current_source"] == "goodreads"
        assert result["error_message"] == "Some warning"
        assert result["progress_percent"] == 50
        assert result["errors"] == [
            {"source": "goodreads", "message": "Error 1"},
            {"source": "goodreads", "message": "Error 2"},
        ]

    def test_progress_percent_when_total_is_zero(self) -> None:
        job = SyncJob(source="steam", items_processed=5, total_items=0)
        assert job.to_dict()["progress_percent"] is None


class TestSyncManagerStateMachine:
    """State transitions for a single tracked job."""

    @patch("src.web.sync_manager.threading.Thread")
    def test_start_sync_marks_source_running(self, mock_thread: MagicMock) -> None:
        mock_thread.return_value = MagicMock()
        manager = SyncManager()

        success, message = manager.start_sync(
            source="steam", sync_function=MagicMock(return_value=10)
        )

        assert success is True
        assert "steam" in message
        assert manager.is_running("steam") is True
        assert manager.is_running() is True

    @patch("src.web.sync_manager.threading.Thread")
    def test_successful_sync_transitions_to_completed(
        self, mock_thread: MagicMock
    ) -> None:
        mock_thread.return_value = MagicMock()
        manager = SyncManager()
        sync_function = MagicMock(return_value=42)

        manager.start_sync(source="goodreads", sync_function=sync_function)
        manager._run_sync("goodreads", sync_function)

        status = manager.get_status()
        assert status["status"] == "idle"
        assert len(status["jobs"]) == 1
        assert status["jobs"][0]["status"] == "completed"
        assert status["jobs"][0]["items_processed"] == 42

    @patch("src.web.sync_manager.threading.Thread")
    def test_zero_items_no_errors_is_completed_not_failed(
        self, mock_thread: MagicMock
    ) -> None:
        """An empty source returning 0 items with no errors completes
        cleanly; only zero-items-WITH-errors transitions to FAILED."""
        mock_thread.return_value = MagicMock()
        manager = SyncManager()
        sync_function = MagicMock(return_value=0)

        manager.start_sync(source="empty", sync_function=sync_function)
        manager._run_sync("empty", sync_function)

        job = manager.get_status()["jobs"][0]
        assert job["status"] == "completed"
        assert job["items_processed"] == 0
        assert job["errors"] == []
        assert job["error_message"] is None

    @patch("src.web.sync_manager.threading.Thread")
    def test_failed_sync_transitions_to_failed(self, mock_thread: MagicMock) -> None:
        mock_thread.return_value = MagicMock()
        manager = SyncManager()
        sync_function = MagicMock(side_effect=RuntimeError("Connection timeout"))

        manager.start_sync(source="steam", sync_function=sync_function)
        manager._run_sync("steam", sync_function)

        status = manager.get_status()
        assert status["status"] == "idle"
        job = status["jobs"][0]
        assert job["status"] == "failed"
        assert job["error_message"] == "Sync failed due to an internal error"
        assert job["completed_at"] is not None


class TestSyncManagerConcurrentJobs:
    """Multiple jobs can run concurrently when keyed by distinct sources."""

    @patch("src.web.sync_manager.threading.Thread")
    def test_duplicate_source_rejected_while_running(
        self, mock_thread: MagicMock
    ) -> None:
        mock_thread.return_value = MagicMock()
        manager = SyncManager()
        sync_function = MagicMock(return_value=10)

        first, _ = manager.start_sync(source="steam", sync_function=sync_function)
        second, message = manager.start_sync(
            source="steam", sync_function=sync_function
        )

        assert first is True
        assert second is False
        assert "already in progress" in message
        assert "steam" in message

    @patch("src.web.sync_manager.threading.Thread")
    def test_distinct_sources_run_concurrently(self, mock_thread: MagicMock) -> None:
        mock_thread.return_value = MagicMock()
        manager = SyncManager()
        sync_function = MagicMock(return_value=10)

        first, _ = manager.start_sync(source="steam", sync_function=sync_function)
        second, _ = manager.start_sync(source="goodreads", sync_function=sync_function)

        assert first is True
        assert second is True
        assert manager.is_running("steam") is True
        assert manager.is_running("goodreads") is True
        assert manager.is_running() is True


class TestSyncManagerGetStatus:
    """get_status() shape and contents."""

    def test_idle_when_no_jobs(self) -> None:
        manager = SyncManager()
        status = manager.get_status()
        assert status["status"] == "idle"
        assert status["jobs"] == []

    @patch("src.web.sync_manager.threading.Thread")
    def test_running_when_any_job_running(self, mock_thread: MagicMock) -> None:
        mock_thread.return_value = MagicMock()
        manager = SyncManager()
        manager.start_sync(source="steam", sync_function=MagicMock(return_value=10))

        status = manager.get_status()
        assert status["status"] == "running"
        assert len(status["jobs"]) == 1
        assert status["jobs"][0]["source"] == "steam"

    @patch("src.web.sync_manager.threading.Thread")
    def test_jobs_sorted_by_source(self, mock_thread: MagicMock) -> None:
        mock_thread.return_value = MagicMock()
        manager = SyncManager()
        sync_function = MagicMock(return_value=0)

        manager.start_sync(source="zeta", sync_function=sync_function)
        manager.start_sync(source="alpha", sync_function=sync_function)
        manager.start_sync(source="middle", sync_function=sync_function)

        names = [j["source"] for j in manager.get_status()["jobs"]]
        assert names == ["alpha", "middle", "zeta"]


class TestSyncManagerUpdateProgress:
    """update_progress writes to the job keyed by ``source``."""

    def test_update_multiple_fields_at_once(self) -> None:
        manager = SyncManager()
        _planted(manager, "all")

        manager.update_progress(
            source="all",
            items_processed=30,
            total_items=100,
            current_item="Portal 2",
            current_source="steam",
        )

        job = manager.get_status()["jobs"][0]
        assert job["items_processed"] == 30
        assert job["total_items"] == 100
        assert job["current_item"] == "Portal 2"
        assert job["current_source"] == "steam"

    def test_update_progress_when_no_job(self) -> None:
        manager = SyncManager()
        manager.update_progress(source="steam", items_processed=10)
        assert manager.get_status()["jobs"] == []

    def test_only_specified_fields_overwrite(self) -> None:
        manager = SyncManager()
        _planted(manager, "steam")

        manager.update_progress(
            source="steam", items_processed=5, current_item="Game A"
        )
        manager.update_progress(source="steam", items_processed=10)

        job = manager.get_status()["jobs"][0]
        assert job["items_processed"] == 10
        assert job["current_item"] == "Game A"


class TestSyncManagerPerSourceProgress:
    """Per-source progress tracking inside one job (issue #45)."""

    def test_per_source_slot_isolated(self) -> None:
        manager = SyncManager()
        _planted(manager, "all")

        manager.update_progress(
            source="all",
            items_processed=3,
            total_items=10,
            current_item="Book A",
            current_source="goodreads",
        )
        manager.update_progress(
            source="all",
            items_processed=7,
            total_items=20,
            current_item="Game B",
            current_source="steam",
        )

        sources = manager.get_status()["jobs"][0]["sources"]
        by_source = {entry["source"]: entry for entry in sources}

        assert by_source["goodreads"]["items_processed"] == 3
        assert by_source["goodreads"]["total_items"] == 10
        assert by_source["goodreads"]["current_item"] == "Book A"
        assert by_source["steam"]["items_processed"] == 7
        assert by_source["steam"]["total_items"] == 20
        assert by_source["steam"]["current_item"] == "Game B"

    def test_progress_percent_uses_aggregate(self) -> None:
        manager = SyncManager()
        _planted(manager, "all")

        manager.update_progress(
            source="all",
            items_processed=2,
            total_items=10,
            current_source="goodreads",
        )
        manager.update_progress(
            source="all",
            items_processed=8,
            total_items=10,
            current_source="steam",
        )

        assert manager.get_status()["jobs"][0]["progress_percent"] == 50

    def test_concurrent_per_source_updates_no_loss(self) -> None:
        """Concurrent updates from many threads all land in the slot map."""
        manager = SyncManager()
        _planted(manager, "all")

        source_count = 8
        items_per_source = 25
        barrier = threading.Barrier(source_count)

        def worker(source_name: str) -> None:
            barrier.wait()
            for index in range(items_per_source):
                manager.update_progress(
                    source="all",
                    items_processed=index + 1,
                    total_items=items_per_source,
                    current_item=f"{source_name}_item_{index}",
                    current_source=source_name,
                )

        # ThreadPoolExecutor's threads bypass any patches to threading.Thread,
        # which would otherwise replace pool threads with MagicMocks and
        # deadlock the barrier.
        with ThreadPoolExecutor(max_workers=source_count) as pool:
            futures = [pool.submit(worker, f"source_{i}") for i in range(source_count)]
            for future in futures:
                future.result()

        job = manager.get_status()["jobs"][0]
        assert len(job["sources"]) == source_count
        assert job["items_processed"] == source_count * items_per_source
        assert job["total_items"] == source_count * items_per_source
        by_source = {entry["source"]: entry for entry in job["sources"]}
        for index in range(source_count):
            source_name = f"source_{index}"
            expected = f"{source_name}_item_{items_per_source - 1}"
            assert by_source[source_name]["current_item"] == expected

    def test_legacy_update_without_current_source_writes_top_level(self) -> None:
        """Updates with no ``current_source`` set top-level fields directly."""
        manager = SyncManager()
        _planted(manager, "steam")

        manager.update_progress(source="steam", items_processed=42, total_items=100)

        job = manager.get_status()["jobs"][0]
        assert job["items_processed"] == 42
        assert job["total_items"] == 100
        assert job["sources"] == []

    def test_per_source_slots_sorted_by_name(self) -> None:
        manager = SyncManager()
        _planted(manager, "all")

        manager.update_progress(source="all", items_processed=1, current_source="zeta")
        manager.update_progress(source="all", items_processed=1, current_source="alpha")
        manager.update_progress(
            source="all", items_processed=1, current_source="middle"
        )

        names = [
            entry["source"] for entry in manager.get_status()["jobs"][0]["sources"]
        ]
        assert names == ["alpha", "middle", "zeta"]


class TestSyncManagerAddError:
    """add_error appends to the job keyed by ``source``."""

    def test_add_single_error(self) -> None:
        manager = SyncManager()
        _planted(manager, "steam")

        manager.add_error("steam", "Steam", "Failed to fetch game: Portal 2")

        job = manager.get_status()["jobs"][0]
        assert job["errors"] == [
            {"source": "Steam", "message": "Failed to fetch game: Portal 2"}
        ]

    def test_add_error_when_no_job(self) -> None:
        manager = SyncManager()
        manager.add_error("steam", "Steam", "Some error")
        assert manager.get_status()["jobs"] == []

    def test_one_job_keeps_each_source_apart(self) -> None:
        """A run of every source reports into one job, keyed by the label."""
        manager = SyncManager()
        _planted(manager, "All Sources")

        manager.add_error("All Sources", "Sonarr", "TLS verification failed")
        manager.add_error("All Sources", "Steam", "Rate limit exceeded")

        job = manager.get_status()["jobs"][0]
        assert job["errors"] == [
            {"source": "Sonarr", "message": "TLS verification failed"},
            {"source": "Steam", "message": "Rate limit exceeded"},
        ]


class TestSyncManagerOnCompleteCallback:
    """on_complete fires only on successful syncs."""

    @patch("src.web.sync_manager.threading.Thread")
    def test_on_complete_called_on_success(self, mock_thread: MagicMock) -> None:
        mock_thread.return_value = MagicMock()
        manager = SyncManager()
        sync_function = MagicMock(return_value=10)
        on_complete = MagicMock()

        manager.start_sync(
            source="steam", sync_function=sync_function, on_complete=on_complete
        )
        manager._run_sync("steam", sync_function, on_complete=on_complete)

        on_complete.assert_called_once()

    @patch("src.web.sync_manager.threading.Thread")
    def test_on_complete_not_called_on_failure(self, mock_thread: MagicMock) -> None:
        mock_thread.return_value = MagicMock()
        manager = SyncManager()
        sync_function = MagicMock(side_effect=RuntimeError("sync error"))
        on_complete = MagicMock()

        manager.start_sync(
            source="steam", sync_function=sync_function, on_complete=on_complete
        )
        manager._run_sync("steam", sync_function, on_complete=on_complete)

        on_complete.assert_not_called()

    @patch("src.web.sync_manager.threading.Thread")
    def test_on_complete_failure_does_not_change_job_status(
        self, mock_thread: MagicMock
    ) -> None:
        mock_thread.return_value = MagicMock()
        manager = SyncManager()
        sync_function = MagicMock(return_value=10)
        on_complete = MagicMock(side_effect=RuntimeError("callback error"))

        manager.start_sync(
            source="steam", sync_function=sync_function, on_complete=on_complete
        )
        manager._run_sync("steam", sync_function, on_complete=on_complete)

        assert manager.get_status()["jobs"][0]["status"] == "completed"


class TestSyncManagerRunSync:
    """_run_sync internal behaviour."""

    def test_returns_early_when_no_job(self) -> None:
        manager = SyncManager()
        sync_function = MagicMock(return_value=10)

        manager._run_sync("steam", sync_function)

        sync_function.assert_not_called()

    @patch("src.web.sync_manager.threading.Thread")
    def test_passes_job_to_sync_function(self, mock_thread: MagicMock) -> None:
        mock_thread.return_value = MagicMock()
        manager = SyncManager()
        sync_function = MagicMock(return_value=10)

        manager.start_sync(source="steam", sync_function=sync_function)
        manager._run_sync("steam", sync_function)

        sync_function.assert_called_once()
        passed_job = sync_function.call_args[0][0]
        assert isinstance(passed_job, SyncJob)
        assert passed_job.source == "steam"

    @patch("src.web.sync_manager.threading.Thread")
    def test_the_umbrella_run_logs_its_label_not_its_sentinel_key(
        self, mock_thread: MagicMock, caplog: pytest.LogCaptureFixture
    ) -> None:
        mock_thread.return_value = MagicMock()
        manager = SyncManager()
        manager.start_sync(source=ALL_SOURCES_KEY, sync_function=MagicMock())

        with caplog.at_level(logging.INFO, logger=SYNC_MANAGER_LOGGER):
            manager._run_sync(ALL_SOURCES_KEY, MagicMock(return_value=3))

        assert f"Sync completed for {ALL_SOURCES_LABEL}: 3 items" in caplog.text


class TestSyncManagerHistoryEviction:
    """Cap on retained terminal jobs prevents unbounded ``_jobs`` growth.

    Without this, an unauthenticated /api/update caller could grow the
    SyncManager's job dict indefinitely by triggering syncs with arbitrary
    source labels, exhausting process memory.
    """

    def _make_terminal_job(
        self,
        source: str,
        completed_at: datetime,
        status: SyncStatus = SyncStatus.COMPLETED,
    ) -> SyncJob:
        return SyncJob(
            source=source,
            status=status,
            started_at=completed_at,
            completed_at=completed_at,
        )

    def test_eviction_drops_oldest_terminal_job(self) -> None:
        manager = SyncManager()
        cap = manager._MAX_TERMINAL_HISTORY
        # cap+1 terminal jobs: oldest at minute 0, newest at minute cap.
        for index in range(cap + 1):
            manager._jobs[f"src_{index}"] = self._make_terminal_job(
                f"src_{index}", datetime(2026, 1, 1, 0, index)
            )

        with manager._lock:
            manager._evict_history_locked()

        assert len(manager._jobs) == cap
        # The single oldest terminal job (src_0) is gone; everything
        # newer survives.
        assert "src_0" not in manager._jobs
        for index in range(1, cap + 1):
            assert f"src_{index}" in manager._jobs

    def test_eviction_only_drops_terminal_jobs(self) -> None:
        manager = SyncManager()
        cap = manager._MAX_TERMINAL_HISTORY
        # One running job (must always be retained) plus cap+1 terminal
        # jobs to push history over the cap.
        manager._jobs["running"] = SyncJob(
            source="running",
            status=SyncStatus.RUNNING,
            started_at=datetime(2026, 1, 1),
        )
        for index in range(cap + 1):
            manager._jobs[f"done_{index}"] = self._make_terminal_job(
                f"done_{index}", datetime(2026, 1, 1, 0, index)
            )

        with manager._lock:
            manager._evict_history_locked()

        # Running job preserved; one terminal job evicted to bring
        # terminals back down to the cap.
        assert "running" in manager._jobs
        terminals = [
            label
            for label, job in manager._jobs.items()
            if job.status != SyncStatus.RUNNING
        ]
        assert len(terminals) == cap

    @patch("src.web.sync_manager.threading.Thread")
    def test_start_sync_triggers_eviction(self, mock_thread: MagicMock) -> None:
        """``start_sync`` calls eviction so callers don't have to."""
        mock_thread.return_value = MagicMock()
        manager = SyncManager()
        cap = manager._MAX_TERMINAL_HISTORY
        # Pre-populate with cap+1 terminals so eviction must drop one.
        # The newly inserted RUNNING job is excluded from eviction, so
        # without these extra terminals the cap wouldn't be breached.
        for index in range(cap + 1):
            manager._jobs[f"src_{index}"] = self._make_terminal_job(
                f"src_{index}", datetime(2026, 1, 1, 0, index)
            )

        manager.start_sync(source="new", sync_function=MagicMock(return_value=0))

        assert "src_0" not in manager._jobs
        assert "new" in manager._jobs
        assert manager._jobs["new"].status == SyncStatus.RUNNING
        # Exactly one terminal evicted, so the dict is at cap + the
        # newly added running job.
        assert len(manager._jobs) == cap + 1


class TestSyncManagerZeroItemsWithErrorsRegression:
    """When a sync produces zero items but logged errors, mark it FAILED."""

    @patch("src.web.sync_manager.threading.Thread")
    def test_zero_items_with_errors_marks_failed(self, mock_thread: MagicMock) -> None:
        mock_thread.return_value = MagicMock()
        manager = SyncManager()

        def sync_function(job: SyncJob) -> int:
            manager.add_error("Epic Games", "Epic Games", "Epic Games API returned 401")
            return 0

        manager.start_sync(source="Epic Games", sync_function=sync_function)
        manager._run_sync("Epic Games", sync_function)

        job = manager.get_status()["jobs"][0]
        assert job["status"] == "failed"
        assert job["items_processed"] == 0
        assert len(job["errors"]) == 1
        assert job["error_message"] == "Epic Games API returned 401"

    @patch("src.web.sync_manager.threading.Thread")
    def test_partial_success_with_errors_stays_completed(
        self, mock_thread: MagicMock
    ) -> None:
        mock_thread.return_value = MagicMock()
        manager = SyncManager()

        def sync_function(job: SyncJob) -> int:
            manager.add_error("steam", "Steam", "Item 3 failed to parse")
            return 5

        manager.start_sync(source="steam", sync_function=sync_function)
        manager._run_sync("steam", sync_function)

        job = manager.get_status()["jobs"][0]
        assert job["status"] == "completed"
        assert job["items_processed"] == 5
        # A partial failure sets no ``error_message`` — the UI reads the text
        # off ``errors`` instead, which is the only place it survives.
        assert job["errors"] == [
            {"source": "Steam", "message": "Item 3 failed to parse"}
        ]

    @patch("src.web.sync_manager.threading.Thread")
    def test_on_complete_not_called_when_zero_items_with_errors(
        self, mock_thread: MagicMock
    ) -> None:
        mock_thread.return_value = MagicMock()
        manager = SyncManager()
        on_complete = MagicMock()

        def sync_function(job: SyncJob) -> int:
            manager.add_error("Epic Games", "Epic Games", "Epic Games API returned 401")
            return 0

        manager.start_sync(
            source="Epic Games",
            sync_function=sync_function,
            on_complete=on_complete,
        )
        manager._run_sync("Epic Games", sync_function, on_complete=on_complete)

        assert manager.get_status()["jobs"][0]["status"] == "failed"
        on_complete.assert_not_called()


SYNC_MANAGER_LOGGER = "src.web.sync_manager"

# ``source`` reaches here from POST /api/update via ``humanize_source_id``,
# which title-cases the operator's string without dropping anything.
FORGED_SOURCE = "Steam\nSync completed for Everything: 0 items processed"
ESCAPED_SOURCE = "Steam\\nSync completed for Everything: 0 items processed"


class TestSyncManagerLogInjectionRegression:
    """Regression: the job label forged log entries.

    Bug: four sinks interpolated ``source`` raw and the failure added
    ``exc_info=True``. Cause: the escaping in ``src/web/api.py`` stopped at the
    module boundary. Fix: escape once, render the exception instead.
    """

    @staticmethod
    def _messages(caplog: pytest.LogCaptureFixture) -> list[str]:
        return [
            record.getMessage()
            for record in caplog.records
            if record.name == SYNC_MANAGER_LOGGER
        ]

    @patch("src.web.sync_manager.threading.Thread")
    def test_a_newline_in_the_label_cannot_forge_a_completion(
        self, mock_thread: MagicMock, caplog: pytest.LogCaptureFixture
    ) -> None:
        mock_thread.return_value = MagicMock()
        manager = SyncManager()
        manager.start_sync(source=FORGED_SOURCE, sync_function=MagicMock())

        with caplog.at_level(logging.INFO, logger=SYNC_MANAGER_LOGGER):
            manager._run_sync(FORGED_SOURCE, MagicMock(return_value=3))

        assert self._messages(caplog) == [
            f"Sync completed for {ESCAPED_SOURCE}: 3 items processed"
        ]

    @patch("src.web.sync_manager.threading.Thread")
    def test_a_failure_logs_neither_the_raw_label_nor_a_traceback(
        self, mock_thread: MagicMock, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The traceback is the half that carried the plugin's request URL."""
        mock_thread.return_value = MagicMock()
        manager = SyncManager()
        manager.start_sync(source=FORGED_SOURCE, sync_function=MagicMock())
        api_key = "steam-web-api-key-6b1f"

        def sync_function(job: SyncJob) -> int:
            raise requests.ConnectionError(
                "HTTPSConnectionPool(host='api.steampowered.com', port=443): Max "
                f"retries exceeded with url: /GetOwnedGames/v1/?key={api_key}"
            )

        with caplog.at_level(logging.ERROR, logger=SYNC_MANAGER_LOGGER):
            manager._run_sync(FORGED_SOURCE, sync_function)

        assert self._messages(caplog) == [
            f"Sync failed for {ESCAPED_SOURCE}: ConnectionError"
        ]
        assert api_key not in caplog.text
        assert not any(record.exc_info for record in caplog.records)


SCHEDULER_LOGGER = "src.web.scheduler"
STEAM_LABEL = "Steam"

# No source_configs row, so neither interface can switch its cadence off.
_YAML_ONLY_STEAM = {"inputs": {"steam": {"plugin": "steam", "enabled": True}}}


def _steam_source(
    storage: StorageManager, interval: str, *, enabled: bool = True
) -> None:
    storage.sources.upsert(
        1, "steam", "steam", {"steam_id": "7656119"}, enabled=enabled
    )
    storage.sources.set_schedule(1, "steam", interval)


def _recorded_run(
    storage: StorageManager, ago: timedelta, status: SyncRunStatus = "completed"
) -> int:
    finished_at = utc_now() - ago
    return storage.sync_runs.record(
        1,
        "steam",
        started_at=finished_at - timedelta(seconds=10),
        finished_at=finished_at,
        status=status,
    )


def _accepting_manager() -> MagicMock:
    manager = MagicMock(spec=SyncManager)
    manager.is_running.return_value = False
    manager.start_sync.return_value = (True, f"Started sync for {STEAM_LABEL}")
    return manager


class TestScheduledSyncDispatch:
    """What one scheduler tick does, driven without waiting for the clock."""

    @pytest.fixture()
    def storage(self, tmp_path: Path) -> StorageManager:
        return StorageManager(sqlite_path=tmp_path / "test.db")

    @staticmethod
    def _tick(
        storage: StorageManager,
        manager: MagicMock,
        config: dict[str, Any] | None = None,
    ) -> None:
        with patch("src.web.scheduler.get_sync_manager", return_value=manager):
            dispatch_due_syncs(storage, config or {})

    def test_an_hourly_source_last_run_two_hours_ago_is_dispatched(
        self, storage: StorageManager
    ) -> None:
        _steam_source(storage, "hourly")
        _recorded_run(storage, timedelta(hours=2))
        manager = _accepting_manager()

        self._tick(storage, manager)

        assert manager.start_sync.call_args.args[0] == STEAM_LABEL
        with patch(
            "src.web.sync_dispatch.execute_multi_source_sync", return_value=[]
        ) as execute:
            manager.start_sync.call_args.args[1](MagicMock(spec=SyncJob))
        assert [
            source_config["_source_id"]
            for _plugin, source_config in execute.call_args.kwargs["sources"]
        ] == ["steam"]

    def test_a_run_that_finished_seconds_ago_leaves_an_hourly_source_not_due(
        self, storage: StorageManager
    ) -> None:
        _steam_source(storage, "hourly")
        _recorded_run(storage, timedelta(seconds=50))
        manager = _accepting_manager()

        self._tick(storage, manager)

        manager.start_sync.assert_not_called()

    def test_a_failed_run_backs_an_hourly_source_off_past_the_hour(
        self, storage: StorageManager
    ) -> None:
        _steam_source(storage, "hourly")
        _recorded_run(storage, timedelta(minutes=61), "failed")
        manager = _accepting_manager()

        self._tick(storage, manager)

        manager.start_sync.assert_not_called()

    def test_a_source_with_no_database_row_is_never_dispatched(
        self, storage: StorageManager
    ) -> None:
        manager = _accepting_manager()

        self._tick(storage, manager, _YAML_ONLY_STEAM)

        manager.start_sync.assert_not_called()

    def test_a_source_the_operator_switched_off_is_never_dispatched(
        self, storage: StorageManager
    ) -> None:
        _steam_source(storage, "hourly", enabled=False)
        manager = _accepting_manager()

        self._tick(storage, manager)

        manager.start_sync.assert_not_called()

    def test_a_tick_declined_for_the_umbrella_records_nothing_and_stays_due(
        self, storage: StorageManager
    ) -> None:
        _steam_source(storage, "hourly")
        run_id = _recorded_run(storage, timedelta(hours=2))
        manager = _accepting_manager()
        manager.is_running.return_value = True

        self._tick(storage, manager)

        manager.is_running.assert_called_once_with(ALL_SOURCES_KEY)
        manager.start_sync.assert_not_called()
        assert storage.sync_runs.latest_per_source(1)["steam"]["id"] == run_id

        manager.is_running.return_value = False
        self._tick(storage, manager)

        assert manager.start_sync.call_args.args[0] == STEAM_LABEL
        # The declined tick left no row of its own, here or a moment ago: a
        # skip that outranked the real run read as no history at all.
        assert storage.sync_runs.latest_per_source(1)["steam"]["id"] == run_id

    def test_the_umbrellas_own_run_leaves_the_declined_source_not_due_again(
        self, storage: StorageManager
    ) -> None:
        _steam_source(storage, "hourly")
        manager = _accepting_manager()
        manager.is_running.return_value = True

        self._tick(storage, manager)

        manager.start_sync.assert_not_called()

        # The umbrella's own Steam run lands while that tick is declining.
        run_id = _recorded_run(storage, timedelta(seconds=50))
        manager.is_running.return_value = False
        self._tick(storage, manager)

        manager.start_sync.assert_not_called()
        assert storage.sync_runs.latest_per_source(1)["steam"]["id"] == run_id


class TestSyncSchedulerLifecycle:
    def test_the_loop_keeps_ticking_after_a_tick_raises(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        scheduler = SyncScheduler()
        second_tick = threading.Event()
        ticks: list[int] = []

        def fail_once(*_args: object) -> None:
            ticks.append(1)
            if len(ticks) == 1:
                raise RuntimeError("plugin registry exploded")
            second_tick.set()

        async def drive() -> None:
            await scheduler.start()
            await asyncio.to_thread(second_tick.wait, 5)
            await scheduler.stop()

        with (
            patch("src.web.scheduler.TICK_SECONDS", 0),
            patch(
                "src.web.scheduler.get_storage",
                return_value=MagicMock(spec=StorageManager),
            ),
            patch("src.web.scheduler.get_config", return_value={}),
            patch("src.web.scheduler.dispatch_due_syncs", side_effect=fail_once),
            caplog.at_level(logging.ERROR, logger=SCHEDULER_LOGGER),
        ):
            asyncio.run(drive())

        assert second_tick.is_set()
        assert "Scheduled sync tick failed" in caplog.text

    def test_the_lifespan_starts_the_scheduler_and_stops_it_on_shutdown(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        scheduler = SyncScheduler()
        monkeypatch.setattr(app_state, "config_path", None)
        monkeypatch.setattr("src.web.app.sync_scheduler", scheduler)

        async def boot_then_shut_down() -> tuple[bool, bool, bool]:
            async with lifespan(MagicMock(spec=FastAPI)):
                booted = scheduler.running
                task = scheduler._task
            assert task is not None
            # A cancel shutdown never awaited leaves the task pending here.
            return booted, scheduler.running, task.done()

        assert asyncio.run(boot_then_shut_down()) == (True, False, True)
