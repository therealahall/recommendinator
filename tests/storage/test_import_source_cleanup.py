"""Tests for the boot sweep that retires file-import sources."""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from src.cli.main import cli
from src.recommendations.engine import RecommendationEngine
from src.storage.import_source_cleanup import drop_sources_replaced_by_upload
from src.storage.manager import StorageManager
from tests.factories import booted_web_app

#: Typed out, not derived: the sweep's own set comes from ``IMPORTERS``, so a
#: test reading that back would pass through a rename that stops matching the
#: plugin names the rows on disk were written with.
RETIRED_PLUGINS = (
    "csv_import",
    "json_import",
    "markdown_import",
    "goodreads_csv",
    "storygraph_csv",
)


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


@pytest.mark.parametrize("plugin", RETIRED_PLUGINS)
def test_every_retired_plugin_leaves_no_row_behind(
    storage: StorageManager, plugin: str
) -> None:
    """All five, not just the one the upgrade example names.

    A row the sweep misses refuses every sync with "plugin not loaded" and no
    interface can delete it, since the delete path resolves the plugin first.
    """
    storage.sources.upsert(1, f"my_{plugin}", plugin, {"path": "inputs/x"})

    drop_sources_replaced_by_upload(storage)

    assert storage.sources.list(1) == []


def test_only_the_dropped_sources_runs_and_credentials_go(
    storage: StorageManager,
) -> None:
    """Keyed by source id, never by plugin name or by a bare wipe: a namesake
    must not inherit the backoff, and the steam row is one the operator keeps.
    """
    for source_id, plugin in (("books_csv", "goodreads_csv"), ("steam", "steam")):
        storage.sources.upsert(1, source_id, plugin, {"path": "inputs/x"})
        storage.credentials.save(1, source_id, "api_key", f"secret-{source_id}")
        storage.sync_runs.record(
            1,
            source_id,
            started_at=datetime(2026, 1, 1),
            finished_at=datetime(2026, 1, 1),
            status="completed",
        )

    drop_sources_replaced_by_upload(storage)

    assert storage.credentials.get(1, "books_csv", "api_key") is None
    assert storage.credentials.get(1, "steam", "api_key") == "secret-steam"
    assert list(storage.sync_runs.latest_per_source(1)) == ["steam"]
    assert [row["source_id"] for row in storage.sources.list(1)] == ["steam"]


class TestBothDoorsSweepOnBoot:
    """The criterion is "on boot", and neither entry point has a second caller.

    Dropping the call from one of them leaves that interface refusing every
    sync of the stale row forever, with the unit tests above still green.
    """

    @staticmethod
    def _stale(storage: StorageManager) -> None:
        storage.sources.upsert(
            1,
            "books_csv",
            "goodreads_csv",
            {"path": "inputs/goodreads.csv"},
            enabled=True,
        )
        with storage.connection() as conn:
            conn.execute(
                "INSERT INTO content_items (user_id, title, content_type, status, "
                "source) VALUES (1, 'Dune', 'book', 'completed', 'books_csv')"
            )
            conn.commit()

    def test_the_web_boot_drops_it_and_keeps_the_items(
        self, storage: StorageManager
    ) -> None:
        self._stale(storage)
        config: dict[str, Any] = {
            "storage": {"database_path": "data/test.db"},
            "inputs": {},
        }

        with booted_web_app(storage, config):
            pass

        assert storage.sources.get(1, "books_csv") is None
        assert [item.title for item in storage.get_content_items(user_id=1)] == ["Dune"]

    def test_the_cli_boot_drops_it_and_keeps_the_items(
        self, storage: StorageManager
    ) -> None:
        self._stale(storage)
        config: dict[str, Any] = {"inputs": {}}

        with (
            patch("src.cli.main.load_config", return_value=config),
            patch("src.cli.main.create_storage_manager", return_value=storage),
            patch(
                "src.cli.main.create_recommendation_engine",
                return_value=MagicMock(spec=RecommendationEngine),
            ),
        ):
            result = CliRunner().invoke(cli, ["status"])

        assert result.exit_code == 0, result.output
        assert storage.sources.get(1, "books_csv") is None
        assert [item.title for item in storage.get_content_items(user_id=1)] == ["Dune"]
