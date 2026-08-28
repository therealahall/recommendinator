from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from src.storage.enrichment_jobs import STALE_AFTER, EnrichmentJobStore
from src.storage.manager import StorageManager


@pytest.fixture
def storage(tmp_path: Path) -> StorageManager:
    return StorageManager(sqlite_path=tmp_path / "jobs.db")


@pytest.fixture
def jobs(storage: StorageManager) -> EnrichmentJobStore:
    return storage.enrichment_jobs


@pytest.fixture
def strand(storage: StorageManager) -> Callable[[], None]:
    def age() -> None:
        gone = datetime.now(UTC) - STALE_AFTER - timedelta(seconds=1)
        with storage.connection() as conn:
            conn.execute(
                "UPDATE enrichment_job SET heartbeat_at = ?",
                (gone.isoformat(timespec="microseconds"),),
            )
            conn.commit()

    return age


class TestClaim:
    def test_an_install_that_never_enriched_reads_as_idle(
        self, jobs: EnrichmentJobStore
    ) -> None:
        record = jobs.read()

        assert record.running is False
        assert record.started_at is None

    def test_the_second_claim_is_refused_while_the_first_is_alive(
        self, jobs: EnrichmentJobStore
    ) -> None:
        assert jobs.claim("movie") is True
        assert jobs.claim(None) is False

    def test_a_claim_after_the_job_finished_is_granted(
        self, jobs: EnrichmentJobStore
    ) -> None:
        jobs.claim("movie")
        jobs.finish(completed=True, cancelled=False, errors=[])

        assert jobs.claim("book") is True
        assert jobs.read().content_type == "book"

    def test_a_run_killed_mid_flight_stops_blocking_the_next_one(
        self, jobs: EnrichmentJobStore, strand: Callable[[], None]
    ) -> None:
        jobs.claim("movie")
        strand()

        assert jobs.read().running is False
        assert jobs.claim("book") is True

    def test_a_heartbeat_keeps_a_long_run_alive(
        self, jobs: EnrichmentJobStore, strand: Callable[[], None]
    ) -> None:
        jobs.claim("movie")
        strand()
        jobs.heartbeat(
            items_processed=1,
            items_enriched=1,
            items_failed=0,
            items_not_found=0,
            total_items=9,
            current_item="Arrival",
            errors=[],
        )

        assert jobs.read().running is True
        assert jobs.claim("book") is False
        assert jobs.request_stop() is True

    def test_a_stranded_run_reads_as_cancelled_not_as_still_going(
        self, jobs: EnrichmentJobStore, strand: Callable[[], None]
    ) -> None:
        jobs.claim("movie")
        strand()

        record = jobs.read()
        assert record.running is False
        assert record.cancelled is True


class TestStop:
    def test_a_stop_with_nothing_running_says_so(
        self, jobs: EnrichmentJobStore
    ) -> None:
        assert jobs.request_stop() is False

    def test_the_running_job_is_asked_to_stop(self, jobs: EnrichmentJobStore) -> None:
        jobs.claim(None)

        assert jobs.request_stop() is True
        assert jobs.stop_requested() is True

    def test_a_stranded_job_cannot_be_stopped_because_nothing_would_read_it(
        self, jobs: EnrichmentJobStore, strand: Callable[[], None]
    ) -> None:
        jobs.claim(None)
        strand()

        assert jobs.request_stop() is False

    def test_finishing_clears_the_stop_so_the_next_run_is_not_born_cancelled(
        self, jobs: EnrichmentJobStore
    ) -> None:
        jobs.claim(None)
        jobs.request_stop()
        jobs.finish(completed=False, cancelled=True, errors=[])

        assert jobs.stop_requested() is False


class TestProgress:
    def test_the_tally_and_the_errors_survive_the_round_trip(
        self, jobs: EnrichmentJobStore
    ) -> None:
        jobs.claim("book")
        jobs.heartbeat(
            items_processed=3,
            items_enriched=2,
            items_failed=1,
            items_not_found=0,
            total_items=10,
            current_item="Dune",
            errors=["tmdb: HTTP 500"],
        )

        record = jobs.read()
        assert record.items_processed == 3
        assert record.total_items == 10
        assert record.current_item == "Dune"
        assert record.errors == ["tmdb: HTTP 500"]
        assert record.progress_percent == 30.0

    def test_a_job_that_stopped_on_an_error_is_neither_completed_nor_cancelled(
        self, jobs: EnrichmentJobStore
    ) -> None:
        jobs.claim(None)
        jobs.finish(completed=False, cancelled=False, errors=["Job error: ValueError"])

        record = jobs.read()
        assert record.running is False
        assert record.completed is False
        assert record.cancelled is False
        assert record.errors == ["Job error: ValueError"]

    def test_elapsed_is_measured_to_the_finish_not_to_now(
        self, jobs: EnrichmentJobStore
    ) -> None:
        jobs.claim(None)
        jobs.finish(completed=True, cancelled=False, errors=[])

        record = jobs.read()
        assert record.started_at is not None and record.finished_at is not None
        assert record.elapsed_seconds == pytest.approx(
            (record.finished_at - record.started_at).total_seconds()
        )
