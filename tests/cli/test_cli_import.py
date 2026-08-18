"""The ``import`` command: the CLI half of the upload door POST /api/import.

The JSON it emits must stay field for field identical to ``ImportResponse``,
so the two interfaces cannot drift.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from src.storage.manager import StorageManager
from src.web.api import ImportResponse
from tests.cli.conftest import _invoke_with_mocks

#: One row that imports and one with no title, byte for byte what the web's
#: upload test posts, so the two interfaces' counts can be compared literally.
_BOOKS_CSV = "title,author,status,rating\nDune,Frank Herbert,read,5\n,Nobody,read,3\n"


@pytest.fixture()
def storage(tmp_path: Path) -> StorageManager:
    return StorageManager(sqlite_path=tmp_path / "cli.db")


@pytest.fixture()
def books_csv(tmp_path: Path) -> Path:
    path = tmp_path / "library.csv"
    path.write_text(_BOOKS_CSV, encoding="utf-8")
    return path


def _import(
    storage: StorageManager,
    path: Path,
    *args: str,
    config: dict[str, Any] | None = None,
) -> Any:
    """Run ``import`` with the streams kept apart, as a shell pipe sees them."""
    return _invoke_with_mocks(
        CliRunner(mix_stderr=False),
        ["import", str(path), "--importer", "csv_import", "--content-type", "book"]
        + list(args),
        mock_storage=storage,
        config=config,
    )


def test_a_file_at_a_path_imports_and_names_the_line_it_skipped(
    storage: StorageManager, books_csv: Path
) -> None:
    result = _import(storage, books_csv)

    assert result.exit_code == 0
    assert result.stderr == (
        "Added 1, updated 0, unchanged 0, skipped 1, failed 0. 2 rows read.\n"
        "Skipped line 3: no title\n"
    )
    assert [item.title for item in storage.get_content_items(user_id=1)] == ["Dune"]


def test_the_json_carries_the_import_endpoint_key_set_and_no_other(
    storage: StorageManager, books_csv: Path
) -> None:
    """A key added to ``ImportResponse`` alone is drift the web would hide."""
    payload = json.loads(_import(storage, books_csv, "--format", "json").stdout)

    assert set(payload) == set(ImportResponse.model_fields)
    assert payload == {
        "importer": "csv_import",
        "content_type": "book",
        "filename": "library.csv",
        "added": 1,
        "updated": 0,
        "unchanged": 0,
        "skipped": 1,
        "failed": 0,
        "total_rows": 2,
        "errors": ["Skipped line 3: no title"],
    }


def test_json_mode_leaves_the_count_line_off_the_data_channel(
    storage: StorageManager, books_csv: Path
) -> None:
    """A count line ahead of the JSON breaks every piped caller."""
    result = _import(storage, books_csv, "--format", "json")

    assert "rows read" not in result.stdout
    # Round-tripped: stdout is that one document and not a byte more.
    assert result.stdout == json.dumps(json.loads(result.stdout), indent=2) + "\n"


def test_a_clean_file_still_carries_an_empty_errors_list(
    storage: StorageManager, tmp_path: Path
) -> None:
    """``errors`` is never absent and never prose: a caller may not check first."""
    path = tmp_path / "clean.csv"
    path.write_text("title,author\nDune,Frank Herbert\n", encoding="utf-8")

    payload = json.loads(_import(storage, path, "--format", "json").stdout)

    assert (payload["errors"], payload["skipped"], payload["failed"]) == ([], 0, 0)


@pytest.mark.parametrize("auto_enrich", [True, False])
def test_the_config_gate_decides_whether_imported_items_are_queued(
    storage: StorageManager, books_csv: Path, auto_enrich: bool
) -> None:
    """``enrichment.auto_enrich_on_sync``, read as the CLI's sync sibling reads
    it: an import ignoring it leaves every imported item unenriched.
    """
    config = {"enrichment": {"enabled": True, "auto_enrich_on_sync": auto_enrich}}

    assert _import(storage, books_csv, config=config).exit_code == 0

    item = storage.get_content_items(user_id=1)[0]
    assert item.db_id is not None
    assert (storage.enrichment.status(item.db_id) is not None) is auto_enrich


class TestARefusedImport:
    """Every refusal names what to fix, and none of them exits 0."""

    def test_a_path_that_is_not_there_is_refused_before_storage_is_touched(
        self, storage: StorageManager, tmp_path: Path
    ) -> None:
        result = _import(storage, tmp_path / "absent.csv")

        assert result.exit_code == 2
        assert "does not exist" in result.stderr
        assert storage.get_content_items(user_id=1) == []

    def test_a_directory_is_refused_rather_than_read(
        self, storage: StorageManager, tmp_path: Path
    ) -> None:
        result = _import(storage, tmp_path)

        assert result.exit_code == 2
        assert "is a directory" in result.stderr

    def test_a_file_the_chosen_format_cannot_parse_says_what_refused_it(
        self, storage: StorageManager, books_csv: Path
    ) -> None:
        result = _import(storage, books_csv, "--importer", "json_import")

        assert result.exit_code != 0
        assert "Failed to parse JSON" in result.stderr

    def test_a_format_needing_a_content_type_says_so_in_the_endpoint_s_words(
        self, storage: StorageManager, books_csv: Path
    ) -> None:
        result = _invoke_with_mocks(
            CliRunner(mix_stderr=False),
            ["import", str(books_csv), "--importer", "csv_import"],
            mock_storage=storage,
        )

        assert result.exit_code != 0
        assert result.stderr.startswith(
            "Error: CSV Import needs a content type. "
            "One of: book, movie, tv_show, video_game"
        )

    def test_a_format_name_that_is_not_ours_lists_the_ones_that_are(
        self, storage: StorageManager, books_csv: Path
    ) -> None:
        """The choices come off the registry, so a new importer needs no edit."""
        result = _import(storage, books_csv, "--importer", "goodreads_rss")

        assert result.exit_code == 2
        assert "goodreads_csv" in result.stderr
