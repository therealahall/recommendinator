from __future__ import annotations

import itertools
import sqlite3
import time
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
import requests

from src.covers import cache
from src.covers.fetch import MAX_BYTES, CoverUnavailable
from src.covers.service import backfill_covers, fill_cover, start_backfill
from src.storage.cover_jobs import STALE_AFTER, CoverBackfillRecord
from src.storage.manager import StorageManager
from src.storage.schema import create_user
from src.web.api._covers import CoverBackfillResponse
from tests.factories import authenticated_client, booted_web_app, make_item

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 32

# IP literals throughout: a hostname needs DNS, which isolation refuses first.
REMOTE = "https://1.2.3.4/cover.jpg"
LAN = "http://10.0.0.5:8083/opds/cover/1"


class FakeResponse:
    def __init__(
        self,
        status: int = 200,
        body: bytes = PNG,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status_code = status
        self.headers = headers or {}
        self._body = body

    @property
    def ok(self) -> bool:
        return self.status_code < 400

    def iter_content(self, chunk_size: int) -> Iterator[bytes]:
        for start in range(0, len(self._body), chunk_size):
            yield self._body[start : start + chunk_size]

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *_: object) -> bool:
        return False


@pytest.fixture
def library(tmp_path: Path) -> tuple[StorageManager, dict[str, Any]]:
    db_path = tmp_path / "recommendations.db"
    return (
        StorageManager(sqlite_path=db_path),
        {"storage": {"database_path": str(db_path)}},
    )


def save(storage: StorageManager, cover_url: str, title: str = "Dune") -> int:
    return storage.save_content_item(
        make_item(title=title, cover_url=cover_url, source="steam", item_id=title)
    )


def stored_cover(storage: StorageManager, db_id: int) -> str | None:
    item = storage.get_content_item(db_id)
    assert item is not None
    return item.cover_url


def finished_backfill(storage: StorageManager) -> CoverBackfillRecord:
    for _ in range(500):
        record = storage.cover_jobs.read()
        if not record.running:
            return record
        time.sleep(0.01)
    raise AssertionError("the backfill never finished")


def strand_the_claim(storage: StorageManager) -> None:
    gone = datetime.now(UTC) - STALE_AFTER - timedelta(seconds=1)
    with storage.connection() as conn:
        conn.execute(
            "UPDATE cover_backfill_job SET heartbeat_at = ?",
            (gone.isoformat(timespec="microseconds"),),
        )
        conn.commit()


class TestFillOnlyInvalidation:
    def test_a_404_clears_the_cover_so_a_later_source_can_fill_it(
        self, library: tuple[StorageManager, dict[str, Any]]
    ) -> None:
        storage, config = library
        db_id = save(storage, REMOTE)

        with patch("src.covers.fetch.requests.get", return_value=FakeResponse(404)):
            outcome = fill_cover(storage, config, storage.get_content_item(db_id))

        assert isinstance(outcome, CoverUnavailable) and outcome.permanent
        assert stored_cover(storage, db_id) is None

    def test_a_503_leaves_the_cover_url_alone(
        self, library: tuple[StorageManager, dict[str, Any]]
    ) -> None:
        storage, config = library
        db_id = save(storage, REMOTE)

        with patch("src.covers.fetch.requests.get", return_value=FakeResponse(503)):
            outcome = fill_cover(storage, config, storage.get_content_item(db_id))

        assert isinstance(outcome, CoverUnavailable) and not outcome.permanent
        assert stored_cover(storage, db_id) == REMOTE

    def test_a_connection_error_leaves_the_cover_url_alone(
        self, library: tuple[StorageManager, dict[str, Any]]
    ) -> None:
        storage, config = library
        db_id = save(storage, REMOTE)

        with patch(
            "src.covers.fetch.requests.get",
            side_effect=requests.ConnectionError("down"),
        ):
            fill_cover(storage, config, storage.get_content_item(db_id))

        assert stored_cover(storage, db_id) == REMOTE

    def test_a_body_that_is_not_an_image_clears_the_cover(
        self, library: tuple[StorageManager, dict[str, Any]]
    ) -> None:
        storage, config = library
        db_id = save(storage, REMOTE)

        with patch(
            "src.covers.fetch.requests.get",
            return_value=FakeResponse(body=b"<html>Not found</html>"),
        ):
            fill_cover(storage, config, storage.get_content_item(db_id))

        assert stored_cover(storage, db_id) is None

    def test_a_clear_re_queues_that_one_item_and_not_the_rest_of_the_library(
        self, library: tuple[StorageManager, dict[str, Any]]
    ) -> None:
        storage, config = library
        db_id = save(storage, REMOTE)
        untouched = save(storage, REMOTE + "?2", title="Elsewhere")
        storage.enrichment.mark_complete(db_id, "rawg", "high")
        storage.enrichment.mark_complete(untouched, "rawg", "high")

        with patch("src.covers.fetch.requests.get", return_value=FakeResponse(404)):
            fill_cover(storage, config, storage.get_content_item(db_id))

        assert [queued for queued, _ in storage.enrichment.items_needing()] == [db_id]

    def test_a_settled_item_is_not_re_queued_and_refetched_on_every_later_sync(
        self, library: tuple[StorageManager, dict[str, Any]]
    ) -> None:
        storage, config = library
        db_id = save(storage, REMOTE)
        with patch("src.covers.fetch.requests.get", return_value=FakeResponse(404)):
            fill_cover(storage, config, storage.get_content_item(db_id))
        storage.enrichment.mark_complete(db_id, "none", "not_found")

        save(storage, REMOTE)
        with patch("src.covers.fetch.requests.get") as again:
            fill_cover(storage, config, storage.get_content_item(db_id))

        again.assert_not_called()
        settled = storage.enrichment.status(db_id)
        assert settled is not None
        assert (settled["needs_enrichment"], settled["enrichment_quality"]) == (
            False,
            "not_found",
        )

    def test_a_transient_failure_leaves_the_enrichment_status_alone(
        self, library: tuple[StorageManager, dict[str, Any]]
    ) -> None:
        storage, config = library
        db_id = save(storage, REMOTE)
        storage.enrichment.mark_complete(db_id, "rawg", "high")

        with patch("src.covers.fetch.requests.get", return_value=FakeResponse(503)):
            fill_cover(storage, config, storage.get_content_item(db_id))

        assert storage.enrichment.items_needing() == []


class TestFetcherSafety:
    def test_a_redirect_to_another_origin_is_refused(
        self, library: tuple[StorageManager, dict[str, Any]]
    ) -> None:
        storage, config = library
        db_id = save(storage, REMOTE)
        redirect = FakeResponse(302, headers={"Location": "https://5.6.7.8/cover.jpg"})

        with patch("src.covers.fetch.requests.get", return_value=redirect) as mock_get:
            outcome = fill_cover(storage, config, storage.get_content_item(db_id))

        assert isinstance(outcome, CoverUnavailable) and not outcome.permanent
        assert mock_get.call_count == 1
        assert stored_cover(storage, db_id) == REMOTE

    def test_a_same_origin_redirect_is_followed(
        self, library: tuple[StorageManager, dict[str, Any]]
    ) -> None:
        storage, config = library
        db_id = save(storage, REMOTE)
        pages = [
            FakeResponse(302, headers={"Location": "/moved/cover.jpg"}),
            FakeResponse(),
        ]

        with patch("src.covers.fetch.requests.get", side_effect=pages):
            outcome = fill_cover(storage, config, storage.get_content_item(db_id))

        assert isinstance(outcome, Path)
        assert outcome.read_bytes() == PNG

    def test_a_private_address_no_source_owns_is_refused(
        self, library: tuple[StorageManager, dict[str, Any]]
    ) -> None:
        storage, config = library
        db_id = save(storage, LAN)

        with patch("src.covers.fetch.requests.get") as mock_get:
            outcome = fill_cover(storage, config, storage.get_content_item(db_id))

        assert isinstance(outcome, CoverUnavailable) and not outcome.permanent
        mock_get.assert_not_called()

    def test_a_body_past_the_cap_is_never_cached(
        self, library: tuple[StorageManager, dict[str, Any]]
    ) -> None:
        storage, config = library
        db_id = save(storage, REMOTE)
        oversized = FakeResponse(body=PNG + b"0" * MAX_BYTES)

        with patch("src.covers.fetch.requests.get", return_value=oversized):
            outcome = fill_cover(storage, config, storage.get_content_item(db_id))

        assert isinstance(outcome, CoverUnavailable) and not outcome.permanent

    def test_a_host_trickling_bytes_is_given_up_on_rather_than_wedging_the_walk(
        self, library: tuple[StorageManager, dict[str, Any]]
    ) -> None:
        storage, config = library
        db_id = save(storage, REMOTE)

        with (
            patch("src.covers.fetch.requests.get", return_value=FakeResponse()),
            patch(
                "src.covers.fetch.time.monotonic", side_effect=itertools.count(0, 20)
            ),
        ):
            outcome = fill_cover(storage, config, storage.get_content_item(db_id))

        assert isinstance(outcome, CoverUnavailable) and not outcome.permanent
        assert stored_cover(storage, db_id) == REMOTE


def test_a_cover_on_a_configured_sources_origin_is_fetched_with_its_credentials(
    library: tuple[StorageManager, dict[str, Any]],
) -> None:
    """``/opds/*`` is behind basic auth a browser ``<img>`` never sends, so
    without the source's own credentials a Calibre-Web cover cannot load at all.
    """
    storage, config = library
    config["inputs"] = {
        "calibre_web": {
            "plugin": "calibre_web",
            "enabled": True,
            "url": "http://10.0.0.5:8083",
            "username": "reader",
            "password": "hunter2",
            "verify_ssl": False,
        }
    }
    db_id = save(storage, LAN)

    with patch(
        "src.covers.fetch.requests.get", return_value=FakeResponse()
    ) as mock_get:
        outcome = fill_cover(storage, config, storage.get_content_item(db_id))

    assert isinstance(outcome, Path)
    assert mock_get.call_args.kwargs["auth"] == ("reader", "hunter2")
    assert mock_get.call_args.kwargs["verify"] is False


def test_a_cover_is_fetched_with_the_sources_of_the_user_it_was_looked_up_for(
    library: tuple[StorageManager, dict[str, Any]],
) -> None:
    """User 1 owns no LAN source, so user 1's access refuses the address user
    2's own Calibre-Web is reachable on."""
    storage, config = library
    with storage.connection() as conn:
        second = create_user(conn, username="second")
    storage.sources.upsert(
        second, "calibre_web", "calibre_web", {"url": "http://10.0.0.5:8083"}
    )
    db_id = storage.save_content_item(
        make_item(title="Dune", cover_url=LAN, source="calibre_web", item_id="d"),
        user_id=second,
    )
    item = storage.get_content_item(db_id, user_id=second)

    with patch("src.covers.fetch.requests.get", return_value=FakeResponse()):
        as_default = fill_cover(storage, config, item)
        as_owner = fill_cover(storage, config, item, user_id=second)

    assert isinstance(as_default, CoverUnavailable)
    assert isinstance(as_owner, Path)


class TestBackfill:
    def test_it_counts_each_outcome_and_skips_what_is_already_cached(
        self, library: tuple[StorageManager, dict[str, Any]]
    ) -> None:
        storage, config = library
        save(storage, REMOTE, title="Cached")
        save(storage, REMOTE + "?2", title="Gone")
        save(storage, REMOTE + "?3", title="Later")
        responses = [FakeResponse(), FakeResponse(404), FakeResponse(503)]

        with patch("src.covers.fetch.requests.get", side_effect=responses):
            first = backfill_covers(storage, config)

        assert (first.total, first.cached, first.cleared, first.failed) == (3, 1, 1, 1)
        assert first.errors == ["Later: the cover host answered 503"]

        with patch("src.covers.fetch.requests.get", return_value=FakeResponse(503)):
            second = backfill_covers(storage, config)

        assert second.total == 1, "the cover cached by the first run was refetched"

    def test_both_interfaces_report_a_run_with_the_same_keys(self) -> None:
        assert set(CoverBackfillRecord().payload()) == set(
            CoverBackfillResponse.model_fields
        )

    def test_a_walk_another_interface_claimed_is_refused_a_second_start(
        self, library: tuple[StorageManager, dict[str, Any]]
    ) -> None:
        storage, config = library
        assert storage.cover_jobs.claim() is True

        assert start_backfill(storage, config) is None

    def test_a_run_killed_mid_walk_stops_blocking_the_next_one(
        self, library: tuple[StorageManager, dict[str, Any]]
    ) -> None:
        storage, config = library
        storage.cover_jobs.claim()
        strand_the_claim(storage)

        assert storage.cover_jobs.read().running is False
        assert start_backfill(storage, config) is not None

    def test_a_heartbeat_landing_after_a_finish_does_not_retake_the_claim(
        self, library: tuple[StorageManager, dict[str, Any]]
    ) -> None:
        storage, _ = library
        storage.cover_jobs.claim()
        record = storage.cover_jobs.read()
        storage.cover_jobs.request_stop()
        storage.cover_jobs.finish(record)

        storage.cover_jobs.heartbeat(record)

        assert storage.cover_jobs.read().running is False
        assert storage.cover_jobs.stop_requested() is False

    def test_a_walk_that_raised_reports_the_failure_rather_than_a_clean_run(
        self, library: tuple[StorageManager, dict[str, Any]]
    ) -> None:
        storage, config = library

        with patch.object(
            storage, "get_content_items", side_effect=sqlite3.OperationalError("locked")
        ):
            assert start_backfill(storage, config) is not None
            record = finished_backfill(storage)

        assert (record.completed, record.cancelled) == (False, False)
        assert record.errors == ["the backfill stopped on an error"]

    def test_a_stop_ends_the_walk_after_the_item_in_flight_and_frees_the_claim(
        self, library: tuple[StorageManager, dict[str, Any]]
    ) -> None:
        storage, config = library
        save(storage, REMOTE, title="First")
        save(storage, REMOTE + "?2", title="Second")

        def stop_once_one_is_in_flight(*_: Any, **__: Any) -> FakeResponse:
            storage.cover_jobs.request_stop()
            return FakeResponse()

        with patch(
            "src.covers.fetch.requests.get", side_effect=stop_once_one_is_in_flight
        ):
            assert start_backfill(storage, config) is not None
            record = finished_backfill(storage)

        assert (record.processed, record.total, record.cached) == (1, 2, 1)
        assert (record.completed, record.cancelled) == (False, True)
        assert storage.cover_jobs.claim() is True

    def test_a_finished_walk_reports_its_tally_to_whoever_reads_it_next(
        self, library: tuple[StorageManager, dict[str, Any]]
    ) -> None:
        storage, config = library
        save(storage, REMOTE, title="Cached")

        with patch("src.covers.fetch.requests.get", return_value=FakeResponse()):
            assert start_backfill(storage, config) is not None
            record = finished_backfill(storage)

        assert (record.completed, record.total, record.cached) == (True, 1, 1)


class TestCoverRoute:
    def test_it_serves_the_cached_image_from_this_apps_own_origin(
        self, library: tuple[StorageManager, dict[str, Any]]
    ) -> None:
        storage, config = library
        db_id = save(storage, REMOTE)

        with (
            booted_web_app(storage, config) as app,
            patch("src.covers.fetch.requests.get", return_value=FakeResponse()),
        ):
            response = authenticated_client(app).get(f"/api/covers/{db_id}")

        assert response.status_code == 200
        assert response.headers["content-type"] == "image/png"
        assert response.content == PNG

    def test_an_item_with_no_cover_says_so_rather_than_serving_a_broken_image(
        self, library: tuple[StorageManager, dict[str, Any]]
    ) -> None:
        storage, config = library
        db_id = storage.save_content_item(make_item(title="Unadorned"))

        with booted_web_app(storage, config) as app:
            response = authenticated_client(app).get(f"/api/covers/{db_id}")

        assert response.status_code == 404
        assert response.json()["detail"] == "this item has no cover art"

    def test_a_backfill_the_cli_started_is_visible_here_and_refuses_a_second(
        self, library: tuple[StorageManager, dict[str, Any]]
    ) -> None:
        storage, config = library
        storage.cover_jobs.claim()
        storage.cover_jobs.heartbeat(
            CoverBackfillRecord(total=9, processed=2, current_item="Dune")
        )

        with booted_web_app(storage, config) as app:
            client = authenticated_client(app)
            again = client.post("/api/covers/backfill")
            status = client.get("/api/covers/backfill/status")

        assert again.status_code == 409
        assert status.json()["running"] is True
        assert status.json()["total_items"] == 9

    def test_a_stop_refuses_when_idle_and_reaches_a_walk_another_process_started(
        self, library: tuple[StorageManager, dict[str, Any]]
    ) -> None:
        storage, config = library

        with booted_web_app(storage, config) as app:
            client = authenticated_client(app)
            idle = client.post("/api/covers/backfill/stop")
            storage.cover_jobs.claim()
            storage.cover_jobs.heartbeat(CoverBackfillRecord(total=9, processed=2))
            running = client.post("/api/covers/backfill/stop")

        assert idle.status_code == 400
        assert idle.json()["detail"] == "No cover backfill is running."
        assert running.status_code == 200
        assert storage.cover_jobs.stop_requested() is True


def test_a_cover_that_moves_is_a_different_cache_file(tmp_path: Path) -> None:
    assert cache.cache_path(tmp_path, 1, REMOTE) != cache.cache_path(
        tmp_path, 1, REMOTE + "?v=2"
    )
