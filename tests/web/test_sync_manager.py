"""Tests for background sync job manager."""

from datetime import datetime
from unittest.mock import MagicMock, patch

from src.web.sync_manager import (
    SyncJob,
    SyncManager,
    SyncStatus,
    get_sync_manager,
    reset_sync_manager,
)


class TestSyncStatus:
    """Tests for SyncStatus enum."""

    def test_status_values(self) -> None:
        """Test that all expected status values exist."""
        assert SyncStatus.IDLE.value == "idle"
        assert SyncStatus.RUNNING.value == "running"
        assert SyncStatus.COMPLETED.value == "completed"
        assert SyncStatus.FAILED.value == "failed"

    def test_status_is_string_enum(self) -> None:
        """Test that SyncStatus values are strings."""
        for status in SyncStatus:
            assert isinstance(status.value, str)


class TestSyncJobToDict:
    """Tests for SyncJob.to_dict() serialization."""

    def test_to_dict_defaults(self) -> None:
        """Test to_dict with default field values."""
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

    def test_to_dict_with_all_fields_populated(self) -> None:
        """Test to_dict with all fields set."""
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

    def test_to_dict_progress_percent_calculation(self) -> None:
        """Test progress percent is calculated correctly."""
        job = SyncJob(source="steam", items_processed=75, total_items=200)

        result = job.to_dict()

        assert result["progress_percent"] == 37  # int(75 * 100 / 200) = 37

    def test_to_dict_progress_percent_when_total_is_zero(self) -> None:
        """Test progress percent is None when total_items is zero."""
        job = SyncJob(source="steam", items_processed=5, total_items=0)

        result = job.to_dict()

        assert result["progress_percent"] is None

    def test_to_dict_progress_percent_when_total_is_none(self) -> None:
        """Test progress percent is None when total_items is not set."""
        job = SyncJob(source="steam", items_processed=10)

        result = job.to_dict()

        assert result["progress_percent"] is None

    def test_to_dict_progress_percent_at_100(self) -> None:
        """Test progress percent at 100% completion."""
        job = SyncJob(source="steam", items_processed=50, total_items=50)

        result = job.to_dict()

        assert result["progress_percent"] == 100

    def test_to_dict_serializes_status_as_string_value(self) -> None:
        """Test that status is serialized as its string value, not the enum."""
        job = SyncJob(source="steam", status=SyncStatus.RUNNING)

        result = job.to_dict()

        assert result["status"] == "running"
        assert isinstance(result["status"], str)

    def test_to_dict_datetime_serialized_as_isoformat(self) -> None:
        """Test that datetime fields are serialized as ISO format strings."""
        timestamp = datetime(2026, 1, 15, 14, 30, 45)
        job = SyncJob(source="steam", started_at=timestamp)

        result = job.to_dict()

        assert result["started_at"] == "2026-01-15T14:30:45"


class TestSyncManagerStateMachine:
    """Tests for SyncManager state transitions."""

    def test_initial_state_is_not_running(self) -> None:
        """Test that a new SyncManager reports not running."""
        manager = SyncManager()

        assert manager.is_running() is False

    @patch("src.web.sync_manager.threading.Thread")
    def test_start_sync_transitions_to_running(
        self, mock_thread_class: MagicMock
    ) -> None:
        """Test that start_sync transitions state to RUNNING."""
        mock_thread_instance = MagicMock()
        mock_thread_class.return_value = mock_thread_instance
        manager = SyncManager()
        sync_function = MagicMock(return_value=10)

        success, message = manager.start_sync(
            source="steam", sync_function=sync_function
        )

        assert success is True
        assert "steam" in message
        assert manager.is_running() is True

    @patch("src.web.sync_manager.threading.Thread")
    def test_successful_sync_transitions_to_completed(
        self, mock_thread_class: MagicMock
    ) -> None:
        """Test that a successful sync transitions state to COMPLETED."""
        mock_thread_instance = MagicMock()
        mock_thread_class.return_value = mock_thread_instance
        manager = SyncManager()
        sync_function = MagicMock(return_value=42)

        manager.start_sync(source="goodreads", sync_function=sync_function)

        # Manually invoke _run_sync to simulate thread execution
        manager._run_sync(sync_function)

        status = manager.get_status()
        assert status["status"] == "completed"
        assert status["job"] is not None
        assert status["job"]["items_processed"] == 42

    @patch("src.web.sync_manager.threading.Thread")
    def test_failed_sync_transitions_to_failed(
        self, mock_thread_class: MagicMock
    ) -> None:
        """Test that a sync raising an exception transitions to FAILED."""
        mock_thread_instance = MagicMock()
        mock_thread_class.return_value = mock_thread_instance
        manager = SyncManager()
        sync_function = MagicMock(side_effect=RuntimeError("Connection timeout"))

        manager.start_sync(source="steam", sync_function=sync_function)

        # Manually invoke _run_sync to simulate thread execution
        manager._run_sync(sync_function)

        status = manager.get_status()
        assert status["status"] == "failed"
        assert status["job"] is not None
        assert status["job"]["error_message"] == "Sync failed due to an internal error"
        assert status["job"]["completed_at"] is not None


class TestSyncManagerConcurrentPrevention:
    """Tests for preventing concurrent sync operations."""

    @patch("src.web.sync_manager.threading.Thread")
    def test_start_sync_rejected_when_already_running(
        self, mock_thread_class: MagicMock
    ) -> None:
        """Test that a second sync is rejected when one is already running."""
        mock_thread_instance = MagicMock()
        mock_thread_class.return_value = mock_thread_instance
        manager = SyncManager()
        sync_function = MagicMock(return_value=10)

        first_success, _ = manager.start_sync(
            source="steam", sync_function=sync_function
        )
        second_success, second_message = manager.start_sync(
            source="goodreads", sync_function=sync_function
        )

        assert first_success is True
        assert second_success is False
        assert "already in progress" in second_message
        assert "steam" in second_message

    @patch("src.web.sync_manager.threading.Thread")
    def test_start_sync_allowed_after_previous_completes(
        self, mock_thread_class: MagicMock
    ) -> None:
        """Test that a new sync can start after the previous one completes."""
        mock_thread_instance = MagicMock()
        mock_thread_class.return_value = mock_thread_instance
        manager = SyncManager()
        sync_function = MagicMock(return_value=10)

        manager.start_sync(source="steam", sync_function=sync_function)
        # Simulate completion by running _run_sync directly
        manager._run_sync(sync_function)

        second_success, second_message = manager.start_sync(
            source="goodreads", sync_function=sync_function
        )

        assert second_success is True
        assert "goodreads" in second_message

    @patch("src.web.sync_manager.threading.Thread")
    def test_start_sync_allowed_after_previous_fails(
        self, mock_thread_class: MagicMock
    ) -> None:
        """Test that a new sync can start after the previous one fails."""
        mock_thread_instance = MagicMock()
        mock_thread_class.return_value = mock_thread_instance
        manager = SyncManager()
        failing_function = MagicMock(side_effect=ValueError("parse error"))
        working_function = MagicMock(return_value=5)

        manager.start_sync(source="steam", sync_function=failing_function)
        # Simulate failure
        manager._run_sync(failing_function)

        second_success, _ = manager.start_sync(
            source="goodreads", sync_function=working_function
        )

        assert second_success is True


class TestSyncManagerGetStatus:
    """Tests for SyncManager.get_status()."""

    def test_get_status_when_idle(self) -> None:
        """Test get_status returns idle when no job has been started."""
        manager = SyncManager()

        status = manager.get_status()

        assert status["status"] == "idle"
        assert status["job"] is None

    @patch("src.web.sync_manager.threading.Thread")
    def test_get_status_when_running(self, mock_thread_class: MagicMock) -> None:
        """Test get_status returns running job details when sync is in progress."""
        mock_thread_instance = MagicMock()
        mock_thread_class.return_value = mock_thread_instance
        manager = SyncManager()
        sync_function = MagicMock(return_value=10)

        manager.start_sync(source="steam", sync_function=sync_function)

        status = manager.get_status()

        assert status["status"] == "running"
        assert status["job"] is not None
        assert status["job"]["source"] == "steam"
        assert status["job"]["status"] == "running"
        assert status["job"]["started_at"] is not None

    @patch("src.web.sync_manager.threading.Thread")
    def test_get_status_when_completed(self, mock_thread_class: MagicMock) -> None:
        """Test get_status returns completed job details after sync finishes."""
        mock_thread_instance = MagicMock()
        mock_thread_class.return_value = mock_thread_instance
        manager = SyncManager()
        sync_function = MagicMock(return_value=25)

        manager.start_sync(source="goodreads", sync_function=sync_function)
        manager._run_sync(sync_function)

        status = manager.get_status()

        assert status["status"] == "completed"
        assert status["job"]["items_processed"] == 25
        assert status["job"]["completed_at"] is not None

    @patch("src.web.sync_manager.threading.Thread")
    def test_get_status_when_failed(self, mock_thread_class: MagicMock) -> None:
        """Test get_status returns failure details after sync fails."""
        mock_thread_instance = MagicMock()
        mock_thread_class.return_value = mock_thread_instance
        manager = SyncManager()
        sync_function = MagicMock(side_effect=Exception("network error"))

        manager.start_sync(source="steam", sync_function=sync_function)
        manager._run_sync(sync_function)

        status = manager.get_status()

        assert status["status"] == "failed"
        assert status["job"]["error_message"] is not None


class TestSyncManagerUpdateProgress:
    """Tests for SyncManager.update_progress() thread-safe updates."""

    @patch("src.web.sync_manager.threading.Thread")
    def test_update_items_processed(self, mock_thread_class: MagicMock) -> None:
        """Test updating items_processed field."""
        mock_thread_instance = MagicMock()
        mock_thread_class.return_value = mock_thread_instance
        manager = SyncManager()
        sync_function = MagicMock(return_value=10)

        manager.start_sync(source="steam", sync_function=sync_function)
        manager.update_progress(items_processed=15)

        status = manager.get_status()
        assert status["job"]["items_processed"] == 15

    @patch("src.web.sync_manager.threading.Thread")
    def test_update_total_items(self, mock_thread_class: MagicMock) -> None:
        """Test updating total_items field."""
        mock_thread_instance = MagicMock()
        mock_thread_class.return_value = mock_thread_instance
        manager = SyncManager()
        sync_function = MagicMock(return_value=10)

        manager.start_sync(source="steam", sync_function=sync_function)
        manager.update_progress(total_items=200)

        status = manager.get_status()
        assert status["job"]["total_items"] == 200

    @patch("src.web.sync_manager.threading.Thread")
    def test_update_current_item(self, mock_thread_class: MagicMock) -> None:
        """Test updating current_item field."""
        mock_thread_instance = MagicMock()
        mock_thread_class.return_value = mock_thread_instance
        manager = SyncManager()
        sync_function = MagicMock(return_value=10)

        manager.start_sync(source="goodreads", sync_function=sync_function)
        manager.update_progress(current_item="The Way of Kings")

        status = manager.get_status()
        assert status["job"]["current_item"] == "The Way of Kings"

    @patch("src.web.sync_manager.threading.Thread")
    def test_update_current_source(self, mock_thread_class: MagicMock) -> None:
        """Test updating current_source field."""
        mock_thread_instance = MagicMock()
        mock_thread_class.return_value = mock_thread_instance
        manager = SyncManager()
        sync_function = MagicMock(return_value=10)

        manager.start_sync(source="all", sync_function=sync_function)
        manager.update_progress(current_source="steam")

        status = manager.get_status()
        assert status["job"]["current_source"] == "steam"

    @patch("src.web.sync_manager.threading.Thread")
    def test_update_multiple_fields_at_once(self, mock_thread_class: MagicMock) -> None:
        """Test updating multiple progress fields in a single call."""
        mock_thread_instance = MagicMock()
        mock_thread_class.return_value = mock_thread_instance
        manager = SyncManager()
        sync_function = MagicMock(return_value=10)

        manager.start_sync(source="all", sync_function=sync_function)
        manager.update_progress(
            items_processed=30,
            total_items=100,
            current_item="Portal 2",
            current_source="steam",
        )

        status = manager.get_status()
        assert status["job"]["items_processed"] == 30
        assert status["job"]["total_items"] == 100
        assert status["job"]["current_item"] == "Portal 2"
        assert status["job"]["current_source"] == "steam"

    def test_update_progress_when_no_job(self) -> None:
        """Test that update_progress is a no-op when no job exists."""
        manager = SyncManager()

        # Should not raise any exception
        manager.update_progress(items_processed=10)

        status = manager.get_status()
        assert status["job"] is None

    @patch("src.web.sync_manager.threading.Thread")
    def test_update_only_specified_fields(self, mock_thread_class: MagicMock) -> None:
        """Test that only specified fields are updated, others remain unchanged."""
        mock_thread_instance = MagicMock()
        mock_thread_class.return_value = mock_thread_instance
        manager = SyncManager()
        sync_function = MagicMock(return_value=10)

        manager.start_sync(source="steam", sync_function=sync_function)
        manager.update_progress(items_processed=5, current_item="Game A")
        manager.update_progress(items_processed=10)

        status = manager.get_status()
        # items_processed should be updated to 10
        assert status["job"]["items_processed"] == 10
        # current_item should remain as "Game A" (not reset to None)
        assert status["job"]["current_item"] == "Game A"


class TestSyncManagerAddError:
    """Tests for SyncManager.add_error() error collection."""

    @patch("src.web.sync_manager.threading.Thread")
    def test_add_single_error(self, mock_thread_class: MagicMock) -> None:
        """Test adding a single error to the current job."""
        mock_thread_instance = MagicMock()
        mock_thread_class.return_value = mock_thread_instance
        manager = SyncManager()
        sync_function = MagicMock(return_value=10)

        manager.start_sync(source="steam", sync_function=sync_function)
        manager.add_error("Failed to fetch game: Portal 2")

        status = manager.get_status()
        assert status["job"]["error_count"] == 1

    @patch("src.web.sync_manager.threading.Thread")
    def test_add_multiple_errors(self, mock_thread_class: MagicMock) -> None:
        """Test adding multiple errors to the current job."""
        mock_thread_instance = MagicMock()
        mock_thread_class.return_value = mock_thread_instance
        manager = SyncManager()
        sync_function = MagicMock(return_value=10)

        manager.start_sync(source="steam", sync_function=sync_function)
        manager.add_error("Failed to fetch game: Portal 2")
        manager.add_error("Rate limit exceeded")
        manager.add_error("Invalid response for game: Half-Life")

        status = manager.get_status()
        assert status["job"]["error_count"] == 3

    def test_add_error_when_no_job(self) -> None:
        """Test that add_error is a no-op when no job exists."""
        manager = SyncManager()

        # Should not raise any exception
        manager.add_error("Some error")

        status = manager.get_status()
        assert status["job"] is None


class TestSyncManagerOnCompleteCallback:
    """Tests for on_complete callback invocation."""

    @patch("src.web.sync_manager.threading.Thread")
    def test_on_complete_called_on_success(self, mock_thread_class: MagicMock) -> None:
        """Test that on_complete callback is called when sync succeeds."""
        mock_thread_instance = MagicMock()
        mock_thread_class.return_value = mock_thread_instance
        manager = SyncManager()
        sync_function = MagicMock(return_value=10)
        on_complete = MagicMock()

        manager.start_sync(
            source="steam",
            sync_function=sync_function,
            on_complete=on_complete,
        )
        manager._run_sync(sync_function, on_complete=on_complete)

        on_complete.assert_called_once()

    @patch("src.web.sync_manager.threading.Thread")
    def test_on_complete_not_called_on_failure(
        self, mock_thread_class: MagicMock
    ) -> None:
        """Test that on_complete callback is not called when sync fails."""
        mock_thread_instance = MagicMock()
        mock_thread_class.return_value = mock_thread_instance
        manager = SyncManager()
        sync_function = MagicMock(side_effect=RuntimeError("sync error"))
        on_complete = MagicMock()

        manager.start_sync(
            source="steam",
            sync_function=sync_function,
            on_complete=on_complete,
        )
        manager._run_sync(sync_function, on_complete=on_complete)

        on_complete.assert_not_called()

    @patch("src.web.sync_manager.threading.Thread")
    def test_on_complete_failure_does_not_affect_job_status(
        self, mock_thread_class: MagicMock
    ) -> None:
        """Test that a failing on_complete callback does not change job status to FAILED."""
        mock_thread_instance = MagicMock()
        mock_thread_class.return_value = mock_thread_instance
        manager = SyncManager()
        sync_function = MagicMock(return_value=10)
        on_complete = MagicMock(side_effect=RuntimeError("callback error"))

        manager.start_sync(
            source="steam",
            sync_function=sync_function,
            on_complete=on_complete,
        )
        manager._run_sync(sync_function, on_complete=on_complete)

        status = manager.get_status()
        # Job should still be COMPLETED even though callback failed
        assert status["status"] == "completed"

    @patch("src.web.sync_manager.threading.Thread")
    def test_sync_without_on_complete(self, mock_thread_class: MagicMock) -> None:
        """Test that sync works fine without an on_complete callback."""
        mock_thread_instance = MagicMock()
        mock_thread_class.return_value = mock_thread_instance
        manager = SyncManager()
        sync_function = MagicMock(return_value=10)

        manager.start_sync(source="steam", sync_function=sync_function)
        manager._run_sync(sync_function, on_complete=None)

        status = manager.get_status()
        assert status["status"] == "completed"


class TestSyncManagerRunSync:
    """Tests for _run_sync internal method behavior."""

    def test_run_sync_when_no_current_job(self) -> None:
        """Test that _run_sync returns early when no current job exists."""
        manager = SyncManager()
        sync_function = MagicMock(return_value=10)

        # Should not raise an exception
        manager._run_sync(sync_function)

        sync_function.assert_not_called()

    @patch("src.web.sync_manager.threading.Thread")
    def test_run_sync_sets_completed_at_timestamp(
        self, mock_thread_class: MagicMock
    ) -> None:
        """Test that completed_at is set when sync finishes."""
        mock_thread_instance = MagicMock()
        mock_thread_class.return_value = mock_thread_instance
        manager = SyncManager()
        sync_function = MagicMock(return_value=5)

        manager.start_sync(source="steam", sync_function=sync_function)
        manager._run_sync(sync_function)

        status = manager.get_status()
        assert status["job"]["completed_at"] is not None

    @patch("src.web.sync_manager.threading.Thread")
    def test_run_sync_sets_completed_at_on_failure(
        self, mock_thread_class: MagicMock
    ) -> None:
        """Test that completed_at is set even when sync fails."""
        mock_thread_instance = MagicMock()
        mock_thread_class.return_value = mock_thread_instance
        manager = SyncManager()
        sync_function = MagicMock(side_effect=Exception("failure"))

        manager.start_sync(source="steam", sync_function=sync_function)
        manager._run_sync(sync_function)

        status = manager.get_status()
        assert status["job"]["completed_at"] is not None

    @patch("src.web.sync_manager.threading.Thread")
    def test_run_sync_passes_job_to_sync_function(
        self, mock_thread_class: MagicMock
    ) -> None:
        """Test that _run_sync passes the current SyncJob to the sync function."""
        mock_thread_instance = MagicMock()
        mock_thread_class.return_value = mock_thread_instance
        manager = SyncManager()
        sync_function = MagicMock(return_value=10)

        manager.start_sync(source="steam", sync_function=sync_function)
        manager._run_sync(sync_function)

        sync_function.assert_called_once()
        passed_job = sync_function.call_args[0][0]
        assert isinstance(passed_job, SyncJob)
        assert passed_job.source == "steam"

    @patch("src.web.sync_manager.threading.Thread")
    def test_run_sync_updates_items_processed_from_return_value(
        self, mock_thread_class: MagicMock
    ) -> None:
        """Test that the return value of sync_function sets items_processed."""
        mock_thread_instance = MagicMock()
        mock_thread_class.return_value = mock_thread_instance
        manager = SyncManager()
        sync_function = MagicMock(return_value=99)

        manager.start_sync(source="steam", sync_function=sync_function)
        manager._run_sync(sync_function)

        status = manager.get_status()
        assert status["job"]["items_processed"] == 99


class TestSyncManagerThreadCreation:
    """Tests for thread creation in start_sync."""

    @patch("src.web.sync_manager.threading.Thread")
    def test_start_sync_creates_daemon_thread(
        self, mock_thread_class: MagicMock
    ) -> None:
        """Test that start_sync creates a daemon thread."""
        mock_thread_instance = MagicMock()
        mock_thread_class.return_value = mock_thread_instance
        manager = SyncManager()
        sync_function = MagicMock(return_value=10)

        manager.start_sync(source="steam", sync_function=sync_function)

        mock_thread_class.assert_called_once()
        call_kwargs = mock_thread_class.call_args
        assert call_kwargs.kwargs["daemon"] is True

    @patch("src.web.sync_manager.threading.Thread")
    def test_start_sync_starts_thread(self, mock_thread_class: MagicMock) -> None:
        """Test that start_sync calls thread.start()."""
        mock_thread_instance = MagicMock()
        mock_thread_class.return_value = mock_thread_instance
        manager = SyncManager()
        sync_function = MagicMock(return_value=10)

        manager.start_sync(source="steam", sync_function=sync_function)

        mock_thread_instance.start.assert_called_once()

    @patch("src.web.sync_manager.threading.Thread")
    def test_start_sync_thread_target_is_run_sync(
        self, mock_thread_class: MagicMock
    ) -> None:
        """Test that the thread target is _run_sync."""
        mock_thread_instance = MagicMock()
        mock_thread_class.return_value = mock_thread_instance
        manager = SyncManager()
        sync_function = MagicMock(return_value=10)

        manager.start_sync(source="steam", sync_function=sync_function)

        call_kwargs = mock_thread_class.call_args
        assert call_kwargs.kwargs["target"] == manager._run_sync


class TestSingletonGetterAndReset:
    """Tests for get_sync_manager() and reset_sync_manager() functions."""

    def setup_method(self) -> None:
        """Reset global state before each test."""
        reset_sync_manager()

    def teardown_method(self) -> None:
        """Reset global state after each test."""
        reset_sync_manager()

    def test_get_sync_manager_returns_sync_manager_instance(self) -> None:
        """Test that get_sync_manager returns a SyncManager."""
        manager = get_sync_manager()

        assert isinstance(manager, SyncManager)

    def test_get_sync_manager_returns_same_instance(self) -> None:
        """Test that get_sync_manager returns the same instance on multiple calls."""
        first = get_sync_manager()
        second = get_sync_manager()

        assert first is second

    def test_reset_sync_manager_clears_instance(self) -> None:
        """Test that reset_sync_manager causes next call to return a new instance."""
        first = get_sync_manager()
        reset_sync_manager()
        second = get_sync_manager()

        assert first is not second

    def test_reset_sync_manager_is_safe_when_no_instance(self) -> None:
        """Test that reset_sync_manager does not raise when no instance exists."""
        reset_sync_manager()  # Already reset in setup
        reset_sync_manager()  # Should not raise


class TestSyncJobDefaults:
    """Tests for SyncJob default field values."""

    def test_default_status_is_idle(self) -> None:
        """Test that default status is IDLE."""
        job = SyncJob(source="test")

        assert job.status == SyncStatus.IDLE

    def test_default_errors_list_is_empty(self) -> None:
        """Test that default errors list is empty."""
        job = SyncJob(source="test")

        assert job.errors == []

    def test_errors_list_is_not_shared_between_instances(self) -> None:
        """Test that each SyncJob gets its own errors list."""
        job_a = SyncJob(source="a")
        job_b = SyncJob(source="b")

        job_a.errors.append("error in a")

        assert len(job_b.errors) == 0

    def test_default_items_processed_is_zero(self) -> None:
        """Test that default items_processed is zero."""
        job = SyncJob(source="test")

        assert job.items_processed == 0

    def test_default_optional_fields_are_none(self) -> None:
        """Test that optional datetime and string fields default to None."""
        job = SyncJob(source="test")

        assert job.started_at is None
        assert job.completed_at is None
        assert job.total_items is None
        assert job.current_item is None
        assert job.current_source is None
        assert job.error_message is None
