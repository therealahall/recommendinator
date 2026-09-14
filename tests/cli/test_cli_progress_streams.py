from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from src.ingestion.sync import SyncResult
from src.storage.manager import SaveCounts, StorageManager

from .conftest import _invoke_with_mocks


@pytest.mark.usefixtures("registry_with_source_fakes")
class TestUpdateProgressIsOffTheDataChannel:
    def _run(self, tmp_path: Path, display_name: str | None = None) -> Any:
        def sync(progress_callback: Any, **_: Any) -> list[SyncResult]:
            progress_callback(10, 100, "Dune", "books")
            return [
                SyncResult(
                    source_name="books",
                    items_synced=3,
                    counts=SaveCounts(added=3),
                    total_items=3,
                )
            ]

        storage = StorageManager(sqlite_path=tmp_path / "sources.db")
        storage.sources.upsert(1, "books", "fake_file", {"path": "b.csv"}, enabled=True)
        storage.sources.set_display_name(1, "books", display_name)

        with patch(
            "src.cli.commands._update.execute_multi_source_sync", side_effect=sync
        ):
            return _invoke_with_mocks(CliRunner(), ["update"], storage)

    def test_the_counts_are_the_whole_of_stdout(self, tmp_path: Path) -> None:
        result = self._run(tmp_path)

        assert result.exit_code == 0
        assert "3 of 3 items saved (3 added, 0 updated, 0 unchanged)" in result.stdout
        assert "Updating data from" not in result.stdout
        assert "Processed 10/100" not in result.stdout

    def test_a_plain_run_still_shows_what_it_is_doing(self, tmp_path: Path) -> None:
        result = self._run(tmp_path)

        assert "Updating data from books (workers=4)..." in result.stderr
        assert "Processed 10/100..." in result.stderr

    def test_progress_names_a_renamed_source_by_its_name_not_its_id(
        self, tmp_path: Path
    ) -> None:
        result = self._run(tmp_path, display_name="Paperbacks")

        assert "[Paperbacks] Processed 10/100..." in result.stderr
