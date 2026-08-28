from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from src.ingestion.import_templates import TEMPLATES_DIR
from src.ingestion.importers.registry import IMPORTERS
from src.storage.manager import StorageManager
from src.web.api import (
    ImporterResponse,
    ImportResponse,
    ImportTemplateResponse,
    list_importers,
)
from tests.cli.conftest import _invoke_with_mocks

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
    return _invoke_with_mocks(
        CliRunner(),
        ["import", str(path), "--importer", "csv_import", "--content-type", "book"]
        + list(args),
        mock_storage=storage,
        config=config,
    )


def _formats(storage: StorageManager, *args: str) -> Any:
    return _invoke_with_mocks(
        CliRunner(),
        ["import-formats", *args],
        mock_storage=storage,
    )


def _template(
    storage: StorageManager, *args: str, input_text: str | None = None
) -> Any:
    return _invoke_with_mocks(
        CliRunner(),
        ["import-template", *args],
        mock_storage=storage,
        input_text=input_text,
    )


def test_a_file_at_a_path_imports_and_names_the_line_it_skipped_on_stdout(
    storage: StorageManager, books_csv: Path
) -> None:
    result = _import(storage, books_csv)

    assert result.exit_code == 0
    assert result.stdout == (
        "Added 1, updated 0, unchanged 0, skipped 1, failed 0. 2 rows read.\n"
        "Skipped line 3: no title\n"
    )
    assert result.stderr == ""
    assert [item.title for item in storage.get_content_items(user_id=1)] == ["Dune"]


def test_the_json_carries_the_import_endpoint_key_set_and_no_other(
    storage: StorageManager, books_csv: Path
) -> None:
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
        "notes": [],
    }


def test_json_mode_leaves_the_count_line_off_the_data_channel(
    storage: StorageManager, books_csv: Path
) -> None:
    result = _import(storage, books_csv, "--format", "json")

    assert "rows read" not in result.stdout
    assert result.stdout == json.dumps(json.loads(result.stdout), indent=2) + "\n"


def test_a_clean_file_still_carries_an_empty_errors_list(
    storage: StorageManager, tmp_path: Path
) -> None:
    path = tmp_path / "clean.csv"
    path.write_text("title,author\nDune,Frank Herbert\n", encoding="utf-8")

    payload = json.loads(_import(storage, path, "--format", "json").stdout)

    assert (payload["errors"], payload["skipped"], payload["failed"]) == ([], 0, 0)


@pytest.mark.parametrize("auto_enrich", [True, False])
def test_the_config_gate_decides_whether_imported_items_are_queued(
    storage: StorageManager, books_csv: Path, auto_enrich: bool
) -> None:
    config = {"enrichment": {"enabled": True, "auto_enrich_on_sync": auto_enrich}}

    assert _import(storage, books_csv, config=config).exit_code == 0

    item = storage.get_content_items(user_id=1)[0]
    assert item.db_id is not None
    assert (storage.enrichment.status(item.db_id) is not None) is auto_enrich


def test_a_queue_fault_is_reported_as_a_note_rather_than_a_refused_row(
    storage: StorageManager, books_csv: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(db_id: int) -> None:
        raise RuntimeError("database is locked")

    monkeypatch.setattr(storage.enrichment, "mark_needed", refuse)
    config = {"enrichment": {"enabled": True, "auto_enrich_on_sync": True}}

    assert _import(storage, books_csv, config=config).stdout == (
        "Added 1, updated 0, unchanged 0, skipped 1, failed 0. 2 rows read.\n"
        "Saved 1 item(s) but could not queue them for enrichment\n"
        "Skipped line 3: no title\n"
    )

    payload = json.loads(
        _import(storage, books_csv, "--format", "json", config=config).stdout
    )
    assert (payload["notes"], payload["errors"]) == (
        ["Saved 1 item(s) but could not queue them for enrichment"],
        ["Skipped line 3: no title"],
    )


class TestARefusedImport:
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

    def test_a_file_the_operator_cannot_read_is_refused_before_it_is_parsed(
        self, storage: StorageManager, books_csv: Path
    ) -> None:
        books_csv.chmod(0o000)

        result = _import(storage, books_csv)

        assert result.exit_code == 2
        assert "is not readable" in result.stderr

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
            CliRunner(),
            ["import", str(books_csv), "--importer", "csv_import"],
            mock_storage=storage,
        )

        assert result.exit_code != 0
        assert result.stderr.startswith(
            "Error: CSV needs a content type. "
            "One of: book, movie, tv_show, video_game"
        )

    def test_a_format_name_that_is_not_ours_lists_the_ones_that_are(
        self, storage: StorageManager, books_csv: Path
    ) -> None:
        result = _import(storage, books_csv, "--importer", "goodreads_rss")

        assert result.exit_code == 2
        assert "goodreads_csv" in result.stderr


class TestTheTemplateAnOperatorFillsIn:
    def test_a_named_template_goes_to_stdout_byte_for_byte(
        self, storage: StorageManager
    ) -> None:
        result = _template(
            storage, "--importer", "csv_import", "--content-type", "book"
        )

        assert result.exit_code == 0
        assert result.stdout_bytes == (TEMPLATES_DIR / "books.csv").read_bytes()

    def test_output_writes_the_shipped_file_to_that_path(
        self, storage: StorageManager, tmp_path: Path
    ) -> None:
        destination = tmp_path / "filled_in.json"

        result = _template(
            storage,
            "--importer",
            "json_import",
            "--content-type",
            "video_game",
            "--output",
            str(destination),
        )

        assert result.exit_code == 0
        assert (
            destination.read_bytes()
            == (TEMPLATES_DIR / "video_games.json").read_bytes()
        )
        assert "video_games.json" in result.stdout

    def test_the_listing_carries_the_endpoint_key_set_and_no_other(
        self, storage: StorageManager
    ) -> None:
        payload = json.loads(_template(storage, "--format", "json").stdout)

        assert {frozenset(entry) for entry in payload} == {
            frozenset(ImportTemplateResponse.model_fields)
        }
        assert {(entry["importer"], entry["content_type"]) for entry in payload} == {
            (importer, content_type)
            for importer in ("csv_import", "json_import", "markdown_import")
            for content_type in ("book", "movie", "tv_show", "video_game")
        }

    def test_the_table_listing_names_each_template_on_stdout(
        self, storage: StorageManager
    ) -> None:
        result = _template(storage)

        assert "books.csv" in result.stdout
        assert "video_games.md" in result.stdout

    def test_naming_one_half_of_the_pair_is_refused_rather_than_guessed(
        self, storage: StorageManager
    ) -> None:
        result = _template(storage, "--importer", "csv_import")

        assert result.exit_code != 0
        assert "Name both --importer and --content-type" in result.stderr

    def test_a_missing_templates_directory_says_where_it_looked(
        self, storage: StorageManager, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        absent = tmp_path / "absent"
        monkeypatch.setattr("src.ingestion.import_templates.TEMPLATES_DIR", absent)

        result = _template(storage, "--format", "json")

        assert result.exit_code != 0
        assert str(absent) in result.stderr

    def test_an_output_path_with_neither_half_named_is_refused_not_ignored(
        self, storage: StorageManager, tmp_path: Path
    ) -> None:
        destination = tmp_path / "books.csv"

        result = _template(storage, "--output", str(destination))

        assert result.exit_code != 0
        assert not destination.exists()

    def test_a_listing_format_named_beside_a_template_is_refused_not_ignored(
        self, storage: StorageManager
    ) -> None:
        result = _template(
            storage,
            "--importer",
            "csv_import",
            "--content-type",
            "book",
            "--format",
            "json",
        )

        assert result.exit_code != 0
        assert result.stdout == ""
        assert "--format describes the template listing" in result.stderr

    def test_an_existing_output_file_is_kept_until_the_overwrite_is_confirmed(
        self, storage: StorageManager, tmp_path: Path
    ) -> None:
        destination = tmp_path / "books.csv"
        destination.write_text("title\nmy own work\n", encoding="utf-8")

        def write_template(*args: str, input_text: str | None = None) -> Any:
            return _template(
                storage,
                "--importer",
                "csv_import",
                "--content-type",
                "book",
                "--output",
                str(destination),
                *args,
                input_text=input_text,
            )

        declined = write_template(input_text="n\n")

        assert destination.read_text(encoding="utf-8") == "title\nmy own work\n"

        confirmed = write_template(input_text="y\n")

        assert declined.exit_code == 0
        assert confirmed.exit_code == 0
        assert destination.read_bytes() == (TEMPLATES_DIR / "books.csv").read_bytes()


class TestTheFormatsOnOffer:
    def test_the_json_is_the_importers_endpoint_answer_field_for_field(
        self, storage: StorageManager
    ) -> None:
        payload = json.loads(_formats(storage, "--format", "json").stdout)

        assert {frozenset(entry) for entry in payload} == {
            frozenset(ImporterResponse.model_fields)
        }
        assert payload == [entry.model_dump() for entry in list_importers()]

    def test_the_table_listing_names_every_format_and_which_needs_a_type(
        self, storage: StorageManager
    ) -> None:
        result = _formats(storage)

        assert result.exit_code == 0
        listed = {
            line.split()[0]: line
            for line in result.stdout.splitlines()
            if line.startswith("  ")
        }
        assert set(listed) == {entry.name for entry in IMPORTERS}
        assert "--content-type" in listed["csv_import"]
        assert "--content-type" not in listed["goodreads_csv"]
