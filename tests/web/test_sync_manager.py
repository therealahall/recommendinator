"""Tests for background sync job manager."""

import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from src.web.sync_manager import (
    SyncInProgressError,
    SyncJob,
    SyncManager,
    SyncStatus,
    get_sync_manager,
    reset_sync_manager,
)


def _planted(manager: SyncManager, source: str = "steam") -> SyncJob:
    """Create a job entry directly so tests can drive update_progress
    without spawning the daemon thread that ``start_sync`` would launch.
    """
    job = SyncJob(source=source, status=SyncStatus.RUNNING, started_at=datetime.now())
    manager._jobs[source] = job
    return job


class TestSyncStatus:
    """Tests for SyncStatus enum."""

    def test_status_values(self) -> None:
        assert SyncStatus.IDLE.value == "idle"
        assert SyncStatus.RUNNING.value == "running"
        assert SyncStatus.COMPLETED.value == "completed"
        assert SyncStatus.FAILED.value == "failed"

    def test_status_is_string_enum(self) -> None:
        for status in SyncStatus:
            assert isinstance(status.value, str)


class TestSyncJobToDict:
    """Tests for SyncJob.to_dict() serialization."""

    def test_to_dict_defaults(self) -> None:
        job = SyncJob(source="steam")

        result = job.to_dict()

        assert result["source"] == "steam"
        assert result["status"] == "idle"
        assert result["started_at"] is None
        assert result["completed_at"] is None
        assert result["items_processed"] == 0
        assert result["total_items"] is None
        assert result["current_item"] is None
        assert result["current_source"] is None
        assert result["error_message"] is None
        assert result["progress_percent"] is None
        assert result["error_count"] == 0
        assert result["errors"] == []
        assert result["sources"] == []

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
            errors=["Error 1", "Error 2"],
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
        assert result["error_count"] == 2
        assert result["errors"] == ["Error 1", "Error 2"]

    def test_progress_percent_calculation(self) -> None:
        job = SyncJob(source="steam", items_processed=75, total_items=200)
        assert job.to_dict()["progress_percent"] == 37

    def test_progress_percent_when_total_is_zero(self) -> None:
        job = SyncJob(source="steam", items_processed=5, total_items=0)
        assert job.to_dict()["progress_percent"] is None

    def test_progress_percent_when_total_is_none(self) -> None:
        job = SyncJob(source="steam", items_processed=10)
        assert job.to_dict()["progress_percent"] is None

    def test_progress_percent_at_100(self) -> None:
        job = SyncJob(source="steam", items_processed=50, total_items=50)
        assert job.to_dict()["progress_percent"] == 100

    def test_status_serialised_as_string_value(self) -> None:
        """to_dict emits ``status.value`` (a str), not the Enum instance."""
        job = SyncJob(source="steam", status=SyncStatus.RUNNING)
        result = job.to_dict()
        assert result["status"] == "running"
        assert isinstance(result["status"], str)

    def test_datetime_fields_serialised_as_isoformat(self) -> None:
        timestamp = datetime(2026, 1, 15, 14, 30, 45)
        job = SyncJob(source="steam", started_at=timestamp, completed_at=timestamp)
        result = job.to_dict()
        assert result["started_at"] == "2026-01-15T14:30:45"
        assert result["completed_at"] == "2026-01-15T14:30:45"


class TestSyncManagerStateMachine:
    """State transitions for a single tracked job."""

    def test_initial_state_is_not_running(self) -> None:
        manager = SyncManager()
        assert manager.is_running() is False

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
        assert job["error_count"] == 0
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

    @patch("src.web.sync_manager.threading.Thread")
    def test_restart_allowed_after_previous_completes(
        self, mock_thread: MagicMock
    ) -> None:
        mock_thread.return_value = MagicMock()
        manager = SyncManager()
        sync_function = MagicMock(return_value=10)

        manager.start_sync(source="steam", sync_function=sync_function)
        manager._run_sync("steam", sync_function)

        success, _ = manager.start_sync(source="steam", sync_function=sync_function)
        assert success is True

    @patch("src.web.sync_manager.threading.Thread")
    def test_restart_allowed_after_previous_fails(self, mock_thread: MagicMock) -> None:
        mock_thread.return_value = MagicMock()
        manager = SyncManager()
        failing = MagicMock(side_effect=ValueError("parse error"))
        working = MagicMock(return_value=5)

        manager.start_sync(source="steam", sync_function=failing)
        manager._run_sync("steam", failing)

        success, _ = manager.start_sync(source="steam", sync_function=working)
        assert success is True


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
    def test_idle_when_all_jobs_completed(self, mock_thread: MagicMock) -> None:
        mock_thread.return_value = MagicMock()
        manager = SyncManager()
        sync_function = MagicMock(return_value=25)

        manager.start_sync(source="goodreads", sync_function=sync_function)
        manager._run_sync("goodreads", sync_function)

        status = manager.get_status()
        assert status["status"] == "idle"
        assert status["jobs"][0]["items_processed"] == 25
        assert status["jobs"][0]["completed_at"] is not None

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

    def test_update_items_processed(self) -> None:
        manager = SyncManager()
        _planted(manager, "steam")

        manager.update_progress(source="steam", items_processed=15)

        assert manager.get_status()["jobs"][0]["items_processed"] == 15

    def test_update_total_items(self) -> None:
        manager = SyncManager()
        _planted(manager, "steam")

        manager.update_progress(source="steam", total_items=200)

        assert manager.get_status()["jobs"][0]["total_items"] == 200

    def test_update_current_item(self) -> None:
        manager = SyncManager()
        _planted(manager, "goodreads")

        manager.update_progress(source="goodreads", current_item="The Way of Kings")

        assert manager.get_status()["jobs"][0]["current_item"] == "The Way of Kings"

    def test_update_current_source(self) -> None:
        manager = SyncManager()
        _planted(manager, "all")

        manager.update_progress(source="all", current_source="steam")

        assert manager.get_status()["jobs"][0]["current_source"] == "steam"

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

    def test_aggregate_items_processed_is_sum(self) -> None:
        manager = SyncManager()
        _planted(manager, "all")

        manager.update_progress(
            source="all", items_processed=4, current_source="goodreads"
        )
        manager.update_progress(
            source="all", items_processed=11, current_source="steam"
        )

        assert manager.get_status()["jobs"][0]["items_processed"] == 15

    def test_aggregate_total_items_is_sum(self) -> None:
        manager = SyncManager()
        _planted(manager, "all")

        manager.update_progress(
            source="all", total_items=10, current_source="goodreads"
        )
        manager.update_progress(source="all", total_items=25, current_source="steam")

        assert manager.get_status()["jobs"][0]["total_items"] == 35

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

    def test_per_source_progress_percent_over_100(self) -> None:
        """Per-source progress_percent is not clamped — over-100 is honest."""
        manager = SyncManager()
        _planted(manager, "all")

        manager.update_progress(
            source="all",
            items_processed=15,
            total_items=10,
            current_source="estimated",
        )

        sources = {
            entry["source"]: entry
            for entry in manager.get_status()["jobs"][0]["sources"]
        }
        assert sources["estimated"]["progress_percent"] == 150

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

        manager.add_error("steam", "Failed to fetch game: Portal 2")

        job = manager.get_status()["jobs"][0]
        assert job["error_count"] == 1
        assert job["errors"] == ["Failed to fetch game: Portal 2"]

    def test_add_multiple_errors(self) -> None:
        manager = SyncManager()
        _planted(manager, "steam")

        manager.add_error("steam", "Failed to fetch game: Portal 2")
        manager.add_error("steam", "Rate limit exceeded")
        manager.add_error("steam", "Invalid response for game: Half-Life")

        job = manager.get_status()["jobs"][0]
        assert job["error_count"] == 3
        assert "Rate limit exceeded" in job["errors"]

    def test_add_error_when_no_job(self) -> None:
        manager = SyncManager()
        manager.add_error("steam", "Some error")
        assert manager.get_status()["jobs"] == []

    def test_errors_are_per_job(self) -> None:
        manager = SyncManager()
        _planted(manager, "steam")
        _planted(manager, "goodreads")

        manager.add_error("steam", "Steam error")
        manager.add_error("goodreads", "Goodreads error")

        jobs = {j["source"]: j for j in manager.get_status()["jobs"]}
        assert jobs["steam"]["errors"] == ["Steam error"]
        assert jobs["goodreads"]["errors"] == ["Goodreads error"]


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

    @patch("src.web.sync_manager.threading.Thread")
    def test_sync_without_on_complete(self, mock_thread: MagicMock) -> None:
        mock_thread.return_value = MagicMock()
        manager = SyncManager()
        sync_function = MagicMock(return_value=10)

        manager.start_sync(source="steam", sync_function=sync_function)
        manager._run_sync("steam", sync_function, on_complete=None)

        assert manager.get_status()["jobs"][0]["status"] == "completed"


class TestSyncManagerRunSync:
    """_run_sync internal behaviour."""

    def test_returns_early_when_no_job(self) -> None:
        manager = SyncManager()
        sync_function = MagicMock(return_value=10)

        manager._run_sync("steam", sync_function)

        sync_function.assert_not_called()

    @patch("src.web.sync_manager.threading.Thread")
    def test_sets_completed_at_on_success(self, mock_thread: MagicMock) -> None:
        mock_thread.return_value = MagicMock()
        manager = SyncManager()
        sync_function = MagicMock(return_value=5)

        manager.start_sync(source="steam", sync_function=sync_function)
        manager._run_sync("steam", sync_function)

        assert manager.get_status()["jobs"][0]["completed_at"] is not None

    @patch("src.web.sync_manager.threading.Thread")
    def test_sets_completed_at_on_failure(self, mock_thread: MagicMock) -> None:
        mock_thread.return_value = MagicMock()
        manager = SyncManager()
        sync_function = MagicMock(side_effect=Exception("failure"))

        manager.start_sync(source="steam", sync_function=sync_function)
        manager._run_sync("steam", sync_function)

        assert manager.get_status()["jobs"][0]["completed_at"] is not None

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
    def test_items_processed_set_from_return_value(
        self, mock_thread: MagicMock
    ) -> None:
        mock_thread.return_value = MagicMock()
        manager = SyncManager()
        sync_function = MagicMock(return_value=99)

        manager.start_sync(source="steam", sync_function=sync_function)
        manager._run_sync("steam", sync_function)

        assert manager.get_status()["jobs"][0]["items_processed"] == 99


class TestSyncManagerRunImport:
    """run_import inline execution and failure reporting."""

    def test_failure_without_per_item_errors_sets_error_message(self) -> None:
        """A raise before any add_error still reports a non-null error_message.

        run_import re-raises so the web handler can map the failure, but the
        tracked job must still expose a message via get_status — otherwise a
        polling client sees status=failed with error_message=None.
        """
        manager = SyncManager()

        def boom(_job: SyncJob) -> int:
            raise RuntimeError("import blew up")

        with pytest.raises(RuntimeError, match="import blew up"):
            manager.run_import("Import: Goodreads", boom)

        job = manager.get_status()["jobs"][0]
        assert job["status"] == "failed"
        assert job["error_message"] == "import blew up"

    def test_per_item_error_preferred_over_exception_message(self) -> None:
        """When the job recorded a per-item error, it wins over the exception."""
        manager = SyncManager()

        def boom(job: SyncJob) -> int:
            manager.add_error(job.source, "row 3 failed validation")
            raise RuntimeError("downstream blew up")

        with pytest.raises(RuntimeError):
            manager.run_import("Import: Goodreads", boom)

        job = manager.get_status()["jobs"][0]
        assert job["status"] == "failed"
        assert job["error_message"] == "row 3 failed validation"

    def test_duplicate_running_label_raises(self) -> None:
        """A second run_import for a label already running raises."""
        manager = SyncManager()
        manager._jobs["Import: Goodreads"] = SyncJob(
            source="Import: Goodreads",
            status=SyncStatus.RUNNING,
            started_at=datetime.now(),
        )

        with pytest.raises(SyncInProgressError):
            manager.run_import("Import: Goodreads", lambda _job: 0)


class TestSyncManagerThreadCreation:
    """start_sync spawns a daemon thread."""

    @patch("src.web.sync_manager.threading.Thread")
    def test_creates_daemon_thread(self, mock_thread: MagicMock) -> None:
        mock_thread.return_value = MagicMock()
        manager = SyncManager()
        manager.start_sync(source="steam", sync_function=MagicMock(return_value=10))

        mock_thread.assert_called_once()
        assert mock_thread.call_args.kwargs["daemon"] is True

    @patch("src.web.sync_manager.threading.Thread")
    def test_starts_thread(self, mock_thread: MagicMock) -> None:
        instance = MagicMock()
        mock_thread.return_value = instance
        manager = SyncManager()
        manager.start_sync(source="steam", sync_function=MagicMock(return_value=10))

        instance.start.assert_called_once()

    @patch("src.web.sync_manager.threading.Thread")
    def test_target_is_run_sync(self, mock_thread: MagicMock) -> None:
        mock_thread.return_value = MagicMock()
        manager = SyncManager()
        manager.start_sync(source="steam", sync_function=MagicMock(return_value=10))

        assert mock_thread.call_args.kwargs["target"] == manager._run_sync


class TestSingletonGetterAndReset:
    def setup_method(self) -> None:
        reset_sync_manager()

    def teardown_method(self) -> None:
        reset_sync_manager()

    def test_returns_sync_manager_instance(self) -> None:
        assert isinstance(get_sync_manager(), SyncManager)

    def test_returns_same_instance(self) -> None:
        assert get_sync_manager() is get_sync_manager()

    def test_reset_clears_instance(self) -> None:
        first = get_sync_manager()
        reset_sync_manager()
        assert get_sync_manager() is not first

    def test_reset_safe_when_no_instance(self) -> None:
        reset_sync_manager()
        reset_sync_manager()


class TestSyncJobDefaults:
    def test_default_status_is_idle(self) -> None:
        assert SyncJob(source="test").status == SyncStatus.IDLE

    def test_default_errors_list_is_empty(self) -> None:
        assert SyncJob(source="test").errors == []

    def test_errors_list_is_not_shared_between_instances(self) -> None:
        a = SyncJob(source="a")
        b = SyncJob(source="b")
        a.errors.append("error in a")
        assert b.errors == []

    def test_default_items_processed_is_zero(self) -> None:
        assert SyncJob(source="test").items_processed == 0

    def test_default_optional_fields_are_none(self) -> None:
        job = SyncJob(source="test")
        assert job.started_at is None
        assert job.completed_at is None
        assert job.total_items is None
        assert job.current_item is None
        assert job.current_source is None
        assert job.error_message is None


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

    def test_no_eviction_below_cap(self) -> None:
        manager = SyncManager()
        cap = manager._MAX_TERMINAL_HISTORY
        # Plant cap-1 terminal jobs directly so we don't depend on
        # start_sync's thread spawn.
        for index in range(cap - 1):
            manager._jobs[f"src_{index}"] = self._make_terminal_job(
                f"src_{index}", datetime(2026, 1, 1, 0, index)
            )

        with manager._lock:
            manager._evict_history_locked()

        assert len(manager._jobs) == cap - 1

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

    def test_eviction_handles_completed_at_none(self) -> None:
        """Terminal jobs without completed_at sort to the start (oldest)."""
        manager = SyncManager()
        cap = manager._MAX_TERMINAL_HISTORY
        # One terminal job with completed_at=None plus cap others with
        # known timestamps. The None job must be the one evicted because
        # it sorts to the front under the ``or datetime.min`` fallback.
        manager._jobs["no_timestamp"] = SyncJob(
            source="no_timestamp",
            status=SyncStatus.COMPLETED,
            started_at=datetime(2026, 1, 1),
            completed_at=None,
        )
        for index in range(cap):
            manager._jobs[f"src_{index}"] = self._make_terminal_job(
                f"src_{index}", datetime(2026, 1, 1, 0, index + 10)
            )

        with manager._lock:
            manager._evict_history_locked()

        assert "no_timestamp" not in manager._jobs
        assert len(manager._jobs) == cap

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
            manager.add_error("Epic Games", "Epic Games API returned 401")
            return 0

        manager.start_sync(source="Epic Games", sync_function=sync_function)
        manager._run_sync("Epic Games", sync_function)

        job = manager.get_status()["jobs"][0]
        assert job["status"] == "failed"
        assert job["items_processed"] == 0
        assert job["error_count"] == 1
        assert job["error_message"] == "Epic Games API returned 401"

    @patch("src.web.sync_manager.threading.Thread")
    def test_partial_success_with_errors_stays_completed(
        self, mock_thread: MagicMock
    ) -> None:
        mock_thread.return_value = MagicMock()
        manager = SyncManager()

        def sync_function(job: SyncJob) -> int:
            manager.add_error("steam", "Item 3 failed to parse")
            return 5

        manager.start_sync(source="steam", sync_function=sync_function)
        manager._run_sync("steam", sync_function)

        job = manager.get_status()["jobs"][0]
        assert job["status"] == "completed"
        assert job["items_processed"] == 5
        assert job["error_count"] == 1

    @patch("src.web.sync_manager.threading.Thread")
    def test_on_complete_not_called_when_zero_items_with_errors(
        self, mock_thread: MagicMock
    ) -> None:
        mock_thread.return_value = MagicMock()
        manager = SyncManager()
        on_complete = MagicMock()

        def sync_function(job: SyncJob) -> int:
            manager.add_error("Epic Games", "Epic Games API returned 401")
            return 0

        manager.start_sync(
            source="Epic Games",
            sync_function=sync_function,
            on_complete=on_complete,
        )
        manager._run_sync("Epic Games", sync_function, on_complete=on_complete)

        assert manager.get_status()["jobs"][0]["status"] == "failed"
        on_complete.assert_not_called()
