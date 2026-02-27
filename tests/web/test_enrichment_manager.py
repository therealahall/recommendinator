"""Tests for WebEnrichmentManager."""

from unittest.mock import Mock, patch

import pytest

from src.enrichment.manager import EnrichmentJobStatus, EnrichmentManager
from src.models.content import ContentType
from src.storage.manager import StorageManager
from src.web.enrichment_manager import (
    WebEnrichmentManager,
    get_enrichment_manager,
    reset_enrichment_manager,
)


@pytest.fixture
def manager() -> WebEnrichmentManager:
    """Create a fresh WebEnrichmentManager for each test."""
    return WebEnrichmentManager()


@pytest.fixture(autouse=True)
def reset_singleton() -> None:
    """Reset the global singleton between tests."""
    reset_enrichment_manager()


class TestStartEnrichment:
    """Tests for starting enrichment jobs."""

    def test_start_enrichment_success(self, manager: WebEnrichmentManager) -> None:
        """Starting enrichment with valid args returns success."""
        mock_storage = Mock(spec=StorageManager)
        mock_config: dict = {"enrichment": {}}

        with (
            patch.object(EnrichmentManager, "start_enrichment", return_value=True),
            patch.object(EnrichmentManager, "__init__", return_value=None),
        ):
            success, message = manager.start_enrichment(mock_storage, mock_config)

        assert success is True
        assert "all types" in message

    def test_start_enrichment_with_content_type(
        self, manager: WebEnrichmentManager
    ) -> None:
        """Starting enrichment with content type filter includes type in message."""
        mock_storage = Mock(spec=StorageManager)
        mock_config: dict = {"enrichment": {}}

        with (
            patch.object(EnrichmentManager, "start_enrichment", return_value=True),
            patch.object(EnrichmentManager, "__init__", return_value=None),
        ):
            success, message = manager.start_enrichment(
                mock_storage, mock_config, content_type=ContentType.BOOK
            )

        assert success is True
        assert "book" in message

    def test_start_enrichment_with_include_not_found(
        self, manager: WebEnrichmentManager
    ) -> None:
        """Starting enrichment with include_not_found includes retry info in message."""
        mock_storage = Mock(spec=StorageManager)
        mock_config: dict = {"enrichment": {}}

        with (
            patch.object(EnrichmentManager, "start_enrichment", return_value=True),
            patch.object(EnrichmentManager, "__init__", return_value=None),
        ):
            success, message = manager.start_enrichment(
                mock_storage, mock_config, include_not_found=True
            )

        assert success is True
        assert "not_found" in message

    def test_start_enrichment_rejected_when_already_running(
        self, manager: WebEnrichmentManager
    ) -> None:
        """Starting enrichment when a job is already running returns failure."""
        mock_storage = Mock(spec=StorageManager)
        mock_config: dict = {"enrichment": {}}

        # Start first job
        with (
            patch.object(EnrichmentManager, "start_enrichment", return_value=True),
            patch.object(EnrichmentManager, "__init__", return_value=None),
            patch.object(
                EnrichmentManager,
                "get_status",
                return_value=EnrichmentJobStatus(running=True),
            ),
        ):
            manager.start_enrichment(mock_storage, mock_config)

            # Try to start second job
            success, message = manager.start_enrichment(mock_storage, mock_config)

        assert success is False
        assert "already running" in message

    def test_start_enrichment_allowed_after_previous_completes(
        self, manager: WebEnrichmentManager
    ) -> None:
        """A new enrichment can start after the previous one completes."""
        mock_storage = Mock(spec=StorageManager)
        mock_config: dict = {"enrichment": {}}

        with (
            patch.object(EnrichmentManager, "__init__", return_value=None),
            patch.object(EnrichmentManager, "start_enrichment", return_value=True),
            patch.object(
                EnrichmentManager,
                "get_status",
                return_value=EnrichmentJobStatus(running=False, completed=True),
            ),
        ):
            # First job "completed" (get_status returns not running)
            manager.start_enrichment(mock_storage, mock_config)

            # Second start should succeed since previous is not running
            success, message = manager.start_enrichment(mock_storage, mock_config)

        assert success is True

    def test_start_enrichment_returns_false_when_inner_start_fails(
        self, manager: WebEnrichmentManager
    ) -> None:
        """When the inner EnrichmentManager.start_enrichment returns False."""
        mock_storage = Mock(spec=StorageManager)
        mock_config: dict = {"enrichment": {}}

        with (
            patch.object(EnrichmentManager, "__init__", return_value=None),
            patch.object(EnrichmentManager, "start_enrichment", return_value=False),
        ):
            success, message = manager.start_enrichment(mock_storage, mock_config)

        assert success is False
        assert "already running" in message


class TestStopEnrichment:
    """Tests for stopping enrichment jobs."""

    def test_stop_enrichment_success(self, manager: WebEnrichmentManager) -> None:
        """Stopping a running enrichment returns success."""
        mock_storage = Mock(spec=StorageManager)
        mock_config: dict = {"enrichment": {}}

        with (
            patch.object(EnrichmentManager, "__init__", return_value=None),
            patch.object(EnrichmentManager, "start_enrichment", return_value=True),
            patch.object(EnrichmentManager, "stop_enrichment") as mock_stop,
        ):
            manager.start_enrichment(mock_storage, mock_config)
            success, message = manager.stop_enrichment()

        assert success is True
        assert "stop requested" in message
        mock_stop.assert_called_once()

    def test_stop_enrichment_when_no_job(self, manager: WebEnrichmentManager) -> None:
        """Stopping when no job exists returns failure."""
        success, message = manager.stop_enrichment()

        assert success is False
        assert "No enrichment job" in message


class TestGetStatus:
    """Tests for getting enrichment status."""

    def test_get_status_when_idle(self, manager: WebEnrichmentManager) -> None:
        """Returns None when no enrichment manager exists."""
        status = manager.get_status()
        assert status is None

    def test_get_status_when_running(self, manager: WebEnrichmentManager) -> None:
        """Returns EnrichmentJobStatus when a job is active."""
        mock_storage = Mock(spec=StorageManager)
        mock_config: dict = {"enrichment": {}}
        expected_status = EnrichmentJobStatus(running=True, items_processed=5)

        with (
            patch.object(EnrichmentManager, "__init__", return_value=None),
            patch.object(EnrichmentManager, "start_enrichment", return_value=True),
            patch.object(EnrichmentManager, "get_status", return_value=expected_status),
        ):
            manager.start_enrichment(mock_storage, mock_config)
            status = manager.get_status()

        assert status is not None
        assert status.running is True
        assert status.items_processed == 5


class TestIsRunning:
    """Tests for the is_running check."""

    def test_is_running_when_idle(self, manager: WebEnrichmentManager) -> None:
        """Returns False when no job exists."""
        assert manager.is_running() is False

    def test_is_running_when_active(self, manager: WebEnrichmentManager) -> None:
        """Returns True when a job is actively running."""
        mock_storage = Mock(spec=StorageManager)
        mock_config: dict = {"enrichment": {}}

        with (
            patch.object(EnrichmentManager, "__init__", return_value=None),
            patch.object(EnrichmentManager, "start_enrichment", return_value=True),
            patch.object(
                EnrichmentManager,
                "get_status",
                return_value=EnrichmentJobStatus(running=True),
            ),
        ):
            manager.start_enrichment(mock_storage, mock_config)
            assert manager.is_running() is True

    def test_is_running_when_completed(self, manager: WebEnrichmentManager) -> None:
        """Returns False when the job has completed."""
        mock_storage = Mock(spec=StorageManager)
        mock_config: dict = {"enrichment": {}}

        with (
            patch.object(EnrichmentManager, "__init__", return_value=None),
            patch.object(EnrichmentManager, "start_enrichment", return_value=True),
            patch.object(
                EnrichmentManager,
                "get_status",
                return_value=EnrichmentJobStatus(running=False, completed=True),
            ),
        ):
            manager.start_enrichment(mock_storage, mock_config)
            assert manager.is_running() is False


class TestSingletonFunctions:
    """Tests for module-level singleton getter and reset."""

    def test_get_enrichment_manager_returns_instance(self) -> None:
        """get_enrichment_manager returns a WebEnrichmentManager."""
        instance = get_enrichment_manager()
        assert isinstance(instance, WebEnrichmentManager)

    def test_get_enrichment_manager_returns_same_instance(self) -> None:
        """get_enrichment_manager returns the same instance (singleton)."""
        first = get_enrichment_manager()
        second = get_enrichment_manager()
        assert first is second

    def test_reset_creates_new_instance(self) -> None:
        """After reset, get_enrichment_manager returns a new instance."""
        first = get_enrichment_manager()
        reset_enrichment_manager()
        second = get_enrichment_manager()
        assert first is not second

    def test_reset_is_safe_when_no_instance(self) -> None:
        """reset_enrichment_manager does not fail when no instance exists."""
        reset_enrichment_manager()
        reset_enrichment_manager()  # Should not raise
