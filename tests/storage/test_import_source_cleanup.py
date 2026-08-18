"""Tests for the boot sweep that retires file-import sources."""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

import pytest

from src.storage.import_source_cleanup import drop_sources_replaced_by_upload
from src.storage.manager import StorageManager


@pytest.fixture()
def storage(tmp_path: Path) -> StorageManager:
    return StorageManager(sqlite_path=tmp_path / "cleanup.db")


def test_a_source_on_a_plugin_replaced_by_upload_is_dropped_and_named(
    storage: StorageManager, caplog: pytest.LogCaptureFixture
) -> None:
    """Upgrade path: the plugin is gone, so the row would refuse every sync."""
    storage.sources.upsert(
        1, "books_csv", "goodreads_csv", {"path": "inputs/goodreads.csv"}, enabled=True
    )

    with caplog.at_level(logging.WARNING):
        drop_sources_replaced_by_upload(storage)

    assert storage.sources.get(1, "books_csv") is None
    assert (
        "Removed file-import source 'books_csv' (goodreads_csv, "
        "inputs/goodreads.csv) — upload the file instead." in caplog.text
    )


def test_the_items_a_dropped_source_imported_stay_in_the_library(
    storage: StorageManager,
) -> None:
    """Only the configuration goes: nobody re-downloads a Goodreads export."""
    storage.sources.upsert(1, "books_csv", "goodreads_csv", {"path": "books.csv"})
    with storage.connection() as conn:
        conn.execute(
            "INSERT INTO content_items (user_id, title, content_type, status, "
            "source) VALUES (1, 'Dune', 'book', 'completed', 'books_csv')"
        )
        conn.commit()

    drop_sources_replaced_by_upload(storage)

    assert [item.title for item in storage.get_content_items(user_id=1)] == ["Dune"]


def test_the_dropped_sources_run_history_goes_with_it(
    storage: StorageManager,
) -> None:
    """A namesake created later would otherwise inherit its failure backoff."""
    storage.sources.upsert(1, "books_csv", "goodreads_csv", {"path": "books.csv"})
    storage.sync_runs.record(
        1,
        "books_csv",
        started_at=datetime(2026, 1, 1),
        finished_at=datetime(2026, 1, 1),
        status="failed",
    )

    drop_sources_replaced_by_upload(storage)

    assert storage.sync_runs.latest_per_source(1) == {}


@pytest.mark.parametrize("plugin", ["steam", "personal_site_games"])
def test_a_source_on_any_other_plugin_survives(
    storage: StorageManager, plugin: str
) -> None:
    """``personal_site_games`` lives in an unmounted private directory on some
    boots, so "the registry has never heard of it" cannot mean "delete it".
    """
    storage.sources.upsert(1, plugin, plugin, {"path": "/games"}, enabled=True)

    drop_sources_replaced_by_upload(storage)

    assert storage.sources.get(1, plugin) is not None
