"""Tests for WebEnrichmentManager."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import pytest

from src.enrichment.manager import EnrichmentManager
from src.storage.manager import StorageManager
from src.web.enrichment_manager import (
    WebEnrichmentManager,
    reset_enrichment_manager,
)

# Bounded so a thread nothing releases fails the test instead of hanging the
# suite; nothing waits this long on the passing path.
_STALL_TIMEOUT_SECONDS = 5.0

_CONFIG: dict = {"enrichment": {}}


@pytest.fixture
def manager() -> WebEnrichmentManager:
    return WebEnrichmentManager()


@pytest.fixture
def storage(tmp_path: Path) -> StorageManager:
    return StorageManager(sqlite_path=tmp_path / "web-enrichment.db")


@pytest.fixture(autouse=True)
def reset_singleton() -> None:
    reset_enrichment_manager()


class TestStartEnrichment:
    def test_start_enrichment_success(
        self, manager: WebEnrichmentManager, storage: StorageManager
    ) -> None:
        with patch.object(EnrichmentManager, "_run_enrichment"):
            success, message = manager.start_enrichment(storage, _CONFIG)

        assert success is True
        assert "all types" in message

    def test_start_enrichment_rejected_when_already_running(
        self, manager: WebEnrichmentManager, storage: StorageManager
    ) -> None:
        with patch.object(EnrichmentManager, "_run_enrichment"):
            manager.start_enrichment(storage, _CONFIG)
            success, message = manager.start_enrichment(storage, _CONFIG)

        assert success is False
        assert "already running" in message


class TestTwoConcurrentStartsRunOneJobRegression:
    """Two jobs means two sets of rate limiters against one provider key.

    The guard used to be a lock on this object, holding only inside one
    process; it is the claim on the shared record now.
    """

    def test_the_second_start_is_refused_rather_than_starting_a_job(
        self, manager: WebEnrichmentManager, storage: StorageManager
    ) -> None:
        with (
            patch.object(EnrichmentManager, "_run_enrichment"),
            ThreadPoolExecutor(max_workers=2) as pool,
        ):
            futures = [
                pool.submit(manager.start_enrichment, storage, _CONFIG)
                for _ in range(2)
            ]
            outcomes = [
                future.result(timeout=_STALL_TIMEOUT_SECONDS) for future in futures
            ]

        assert sorted(started for started, _ in outcomes) == [False, True]

    def test_another_process_on_the_same_library_is_refused_too(
        self, manager: WebEnrichmentManager, storage: StorageManager, tmp_path: Path
    ) -> None:
        """What a lock on this object could never do."""
        elsewhere = StorageManager(sqlite_path=tmp_path / "web-enrichment.db")

        with patch.object(EnrichmentManager, "_run_enrichment"):
            manager.start_enrichment(storage, _CONFIG)
            started = EnrichmentManager(elsewhere, _CONFIG).start_enrichment()

        assert started is False


class TestStopEnrichment:
    def test_stop_enrichment_success(
        self, manager: WebEnrichmentManager, storage: StorageManager
    ) -> None:
        with patch.object(EnrichmentManager, "_run_enrichment"):
            manager.start_enrichment(storage, _CONFIG)

        success, message = manager.stop_enrichment(storage)

        assert success is True
        assert "stop requested" in message
        assert storage.enrichment_jobs.stop_requested() is True

    def test_stop_enrichment_when_no_job(
        self, manager: WebEnrichmentManager, storage: StorageManager
    ) -> None:
        success, message = manager.stop_enrichment(storage)

        assert success is False
        assert "No enrichment job" in message

    def test_a_job_another_process_started_is_stoppable_from_here(
        self, manager: WebEnrichmentManager, storage: StorageManager, tmp_path: Path
    ) -> None:
        """The whole point: the CLI's job was invisible and unstoppable."""
        elsewhere = StorageManager(sqlite_path=tmp_path / "web-enrichment.db")
        with patch.object(EnrichmentManager, "_run_enrichment"):
            EnrichmentManager(elsewhere, _CONFIG).start_enrichment()

        assert manager.get_status(storage).running is True
        assert manager.stop_enrichment(storage)[0] is True


class TestGetStatus:
    def test_get_status_when_idle(
        self, manager: WebEnrichmentManager, storage: StorageManager
    ) -> None:
        assert manager.get_status(storage).running is False


class TestIsRunning:
    def test_is_running_when_idle(
        self, manager: WebEnrichmentManager, storage: StorageManager
    ) -> None:
        assert manager.is_running(storage) is False

    def test_is_running_when_active(
        self, manager: WebEnrichmentManager, storage: StorageManager
    ) -> None:
        with patch.object(EnrichmentManager, "_run_enrichment"):
            manager.start_enrichment(storage, _CONFIG)

        assert manager.is_running(storage) is True
