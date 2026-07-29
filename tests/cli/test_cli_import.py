"""Tests for the CLI import command."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from src.ingestion.import_service import NO_ITEMS_WARNING
from src.models.content import ContentType
from src.storage.manager import StorageManager
from tests.cli.conftest import _invoke_with_mocks
from tests.import_test_data import GOODREADS_CSV

# Generic JSON array recognised by the json_import plugin.
_JSON_BOOKS = json.dumps(
    [
        {"title": "Dune", "author": "Frank Herbert", "status": "completed"},
        {"title": "Neuromancer", "author": "William Gibson", "status": "read"},
    ]
)

# Markdown export recognised by the markdown_import plugin.
_MARKDOWN_BOOKS = (
    "## Completed\n"
    "- **Dune** by Frank Herbert | Rating: 5\n"
    "- **Neuromancer** by William Gibson | Rating: 4\n"
)

# The StoryGraph library export columns the storygraph_csv plugin reads.
_STORYGRAPH_BOOKS = (
    "Title,Authors,Read Status,Star Rating\n"
    "Dune,Frank Herbert,read,5\n"
    "Neuromancer,William Gibson,read,4\n"
)


class TestImportCommand:
    """Tests for ``recommendinator import``."""

    def test_list_sources_json_matches_importable_plugins(
        self, cli_runner: CliRunner
    ) -> None:
        """--source list --format json mirrors GET /api/import/sources."""
        result = _invoke_with_mocks(
            cli_runner,
            ["import", "--source", "list", "--format", "json"],
            MagicMock(spec=StorageManager),
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        names = {plugin["name"] for plugin in data}
        # An exact set, matching the web listing test: a syncable plugin that
        # picked up ``is_file_import`` by accident would slip past a subset.
        assert names == {
            "csv_import",
            "goodreads_csv",
            "json_import",
            "markdown_import",
            "storygraph_csv",
        }
        # Syncable sources are excluded from the importable listing.
        assert "roms" not in names
        # The Goodreads RSS feed is a network source, not a file import.
        assert "goodreads_rss" not in names
        csv_plugin = next(p for p in data if p["name"] == "csv_import")
        assert [f["name"] for f in csv_plugin["fields"]] == ["content_type"]
        # Mirrors the web listing: the file types are part of the contract, so
        # a JSON consumer of either interface can filter on them.
        assert csv_plugin["accepted_extensions"] == [".csv"]
        json_plugin = next(p for p in data if p["name"] == "json_import")
        assert json_plugin["accepted_extensions"] == [".json", ".jsonl"]

    def test_source_plugins_omits_importable_plugins(
        self, cli_runner: CliRunner
    ) -> None:
        """``source plugins`` mirrors GET /api/plugins: no file-import plugins.

        The two listings partition the registry — a plugin the user can add as
        a source, or one they upload a file to. Runs against the real registry
        so it pins the actual plugin ids.
        """
        result = _invoke_with_mocks(
            cli_runner,
            ["source", "plugins", "--format", "json"],
            MagicMock(spec=StorageManager),
        )
        assert result.exit_code == 0, result.output
        names = {plugin["name"] for plugin in json.loads(result.output)}

        assert names.isdisjoint(
            {
                "goodreads_csv",
                "csv_import",
                "json_import",
                "markdown_import",
                "storygraph_csv",
            }
        )
        assert "goodreads_rss" in names
        assert "sonarr" in names

    def test_import_result_json_matches_web_fields(
        self, cli_runner: CliRunner, tmp_path: Path
    ) -> None:
        """--format json on a real import emits the web ImportResultResponse shape.

        The CLI previously honoured --format only for --source list; an actual
        import always printed prose. This pins the JSON result path so a JSON
        consumer gets the same fields (including errors) from either interface.
        """
        storage = MagicMock(spec=StorageManager)
        storage.save_content_item.return_value = 1
        data_file = tmp_path / "books.csv"
        data_file.write_text(GOODREADS_CSV)

        result = _invoke_with_mocks(
            cli_runner,
            [
                "import",
                "--source",
                "goodreads_csv",
                "--file",
                str(data_file),
                "--format",
                "json",
            ],
            storage,
        )
        assert result.exit_code == 0, result.output
        body = json.loads(result.output)
        assert set(body) == {
            "message",
            "source",
            "items_synced",
            "total_items",
            "errors",
            "warning",
        }
        assert body["source"] == "goodreads_csv"
        assert body["items_synced"] == 2
        assert body["total_items"] == 2
        assert body["errors"] == []
        assert body["warning"] is None

    def test_goodreads_import_success(
        self, cli_runner: CliRunner, tmp_path: Path
    ) -> None:
        """A Goodreads CSV file imports every parsed book."""
        storage = MagicMock(spec=StorageManager)
        storage.save_content_item.return_value = 1
        data_file = tmp_path / "books.csv"
        data_file.write_text(GOODREADS_CSV)

        result = _invoke_with_mocks(
            cli_runner,
            ["import", "--source", "goodreads_csv", "--file", str(data_file)],
            storage,
        )
        assert result.exit_code == 0, result.output
        assert "Imported 2/2 items from goodreads_csv." in result.output

    def test_per_item_error_warning_in_table_output(
        self, cli_runner: CliRunner, tmp_path: Path
    ) -> None:
        """A row that fails to save surfaces a per-item warning in table output.

        The first book's save raises and the second succeeds, so the import
        completes with one item and prints a per-item warning for the failed
        row (without leaking the raw exception text).
        """
        storage = MagicMock(spec=StorageManager)
        storage.save_content_item.side_effect = [RuntimeError("db write failed"), 1]
        data_file = tmp_path / "books.csv"
        data_file.write_text(GOODREADS_CSV)

        result = _invoke_with_mocks(
            cli_runner,
            ["import", "--source", "goodreads_csv", "--file", str(data_file)],
            storage,
        )
        assert result.exit_code == 0, result.output
        assert "Imported 1/2 items from goodreads_csv." in result.output
        assert "Warning: Failed to process 'Dune'" in result.output
        # The raw exception text must never leak to the user.
        assert "db write failed" not in result.output

    def test_per_item_error_populates_json_errors(
        self, cli_runner: CliRunner, tmp_path: Path
    ) -> None:
        """--format json surfaces failed rows in the errors array.

        Mirrors the web POST /api/import per-item error contract: a partially
        failing import still exits 0, reports the surviving count, and lists the
        safe per-item error string for the failed row.
        """
        storage = MagicMock(spec=StorageManager)
        storage.save_content_item.side_effect = [RuntimeError("db write failed"), 1]
        data_file = tmp_path / "books.csv"
        data_file.write_text(GOODREADS_CSV)

        result = _invoke_with_mocks(
            cli_runner,
            [
                "import",
                "--source",
                "goodreads_csv",
                "--file",
                str(data_file),
                "--format",
                "json",
            ],
            storage,
        )
        assert result.exit_code == 0, result.output
        body = json.loads(result.output)
        assert body["items_synced"] == 1
        assert body["total_items"] == 2
        assert body["errors"] == ["Failed to process 'Dune'"]
        # The raw exception text must never leak to the user.
        assert "db write failed" not in result.output

    def test_file_with_no_rows_warns_in_table_output(
        self, cli_runner: CliRunner, tmp_path: Path
    ) -> None:
        """An import that parses but yields nothing succeeds with a warning.

        Zero counts alone read as an unexplained success, so the table output
        says why nothing arrived — without failing the command.
        """
        storage = MagicMock(spec=StorageManager)
        data_file = tmp_path / "empty.csv"
        data_file.write_text("")

        result = _invoke_with_mocks(
            cli_runner,
            [
                "import",
                "--source",
                "csv_import",
                "--file",
                str(data_file),
                "--content-type",
                "book",
            ],
            storage,
        )
        assert result.exit_code == 0, result.output
        assert "Imported 0/0 items from csv_import." in result.output
        assert NO_ITEMS_WARNING in result.output

    def test_file_with_no_rows_warns_in_json_output(
        self, cli_runner: CliRunner, tmp_path: Path
    ) -> None:
        """--format json carries the same warning the web response returns."""
        storage = MagicMock(spec=StorageManager)
        data_file = tmp_path / "empty.csv"
        data_file.write_text("")

        result = _invoke_with_mocks(
            cli_runner,
            [
                "import",
                "--source",
                "csv_import",
                "--file",
                str(data_file),
                "--content-type",
                "book",
                "--format",
                "json",
            ],
            storage,
        )
        assert result.exit_code == 0, result.output
        body = json.loads(result.output)
        assert body["items_synced"] == 0
        assert body["total_items"] == 0
        assert body["errors"] == []
        assert body["warning"] == NO_ITEMS_WARNING

    def test_all_rows_failing_reports_errors_without_a_warning(
        self, cli_runner: CliRunner, tmp_path: Path
    ) -> None:
        """A file whose every row fails reports errors only, not also a warning.

        Mirrors the web contract: the per-item errors already explain why
        nothing was imported, so the zero-items warning would double-report it.
        """
        storage = MagicMock(spec=StorageManager)
        storage.save_content_item.side_effect = [
            RuntimeError("db write failed"),
            RuntimeError("db write failed"),
        ]
        data_file = tmp_path / "books.csv"
        data_file.write_text(GOODREADS_CSV)

        result = _invoke_with_mocks(
            cli_runner,
            [
                "import",
                "--source",
                "goodreads_csv",
                "--file",
                str(data_file),
                "--format",
                "json",
            ],
            storage,
        )
        assert result.exit_code == 0, result.output
        body = json.loads(result.output)
        assert body["items_synced"] == 0
        assert body["errors"] == [
            "Failed to process 'Dune'",
            "Failed to process 'Neuromancer'",
        ]
        assert body["warning"] is None

    def test_csv_import_passes_content_type_option(
        self, cli_runner: CliRunner, tmp_path: Path
    ) -> None:
        """--content-type is forwarded as the import option and drives parsing.

        The saved item's own type is the assertion: csv_import types every row
        from this option alone, so a regression that dropped it or forwarded the
        wrong value would still yield a titled item and a 1/1 count.
        """
        storage = MagicMock(spec=StorageManager)
        storage.save_content_item.return_value = 1
        data_file = tmp_path / "books.csv"
        data_file.write_text("title,author,status,rating\nDune,Frank Herbert,read,5\n")

        result = _invoke_with_mocks(
            cli_runner,
            [
                "import",
                "--source",
                "csv_import",
                "--file",
                str(data_file),
                "--content-type",
                "book",
            ],
            storage,
        )
        assert result.exit_code == 0, result.output
        assert "Imported 1/1 items from csv_import." in result.output
        saved_item = storage.save_content_item.call_args.args[0]
        assert saved_item.title == "Dune"
        assert saved_item.content_type == ContentType.BOOK

    def test_option_flag_passes_through_to_service(
        self, cli_runner: CliRunner, tmp_path: Path
    ) -> None:
        """A repeatable --option KEY=VALUE pair reaches the import service."""
        storage = MagicMock(spec=StorageManager)
        storage.save_content_item.return_value = 1
        data_file = tmp_path / "books.csv"
        data_file.write_text("title,author,status,rating\nDune,Frank Herbert,read,5\n")

        result = _invoke_with_mocks(
            cli_runner,
            [
                "import",
                "--source",
                "csv_import",
                "--file",
                str(data_file),
                "--option",
                "content_type=book",
            ],
            storage,
        )
        assert result.exit_code == 0, result.output
        assert "Imported 1/1 items from csv_import." in result.output
        saved_item = storage.save_content_item.call_args.args[0]
        assert saved_item.content_type == ContentType.BOOK

    def test_storygraph_import_success(
        self, cli_runner: CliRunner, tmp_path: Path
    ) -> None:
        """The StoryGraph export imports as a file, no longer as a source.

        It used to be created with ``source create`` plus a ``path`` field; the
        export is a point-in-time snapshot, so it is now a one-shot import like
        every other single-file export.
        """
        storage = MagicMock(spec=StorageManager)
        storage.save_content_item.return_value = 1
        data_file = tmp_path / "library.csv"
        data_file.write_text(_STORYGRAPH_BOOKS)

        result = _invoke_with_mocks(
            cli_runner,
            ["import", "--source", "storygraph_csv", "--file", str(data_file)],
            storage,
        )

        assert result.exit_code == 0, result.output
        assert "Imported 2/2 items from storygraph_csv." in result.output

    def test_option_not_in_the_plugin_schema_is_refused(
        self, cli_runner: CliRunner, tmp_path: Path
    ) -> None:
        """An option the source does not declare aborts before importing.

        The web handler filters the multipart form to the plugin's schema
        names. Without the same gate, ``--option`` was a way to push arbitrary
        keys (including internal pipeline keys like ``_source_id``) into the
        plugin config.
        """
        storage = MagicMock(spec=StorageManager)
        data_file = tmp_path / "library.csv"
        data_file.write_text(_STORYGRAPH_BOOKS)

        result = _invoke_with_mocks(
            cli_runner,
            [
                "import",
                "--source",
                "storygraph_csv",
                "--file",
                str(data_file),
                "--option",
                "content_type=book",
            ],
            storage,
        )

        assert result.exit_code != 0
        assert (
            "Unknown import option(s) for 'storygraph_csv': content_type. "
            "This source accepts: no options." in result.output
        )
        assert "Traceback" not in result.output
        storage.save_content_item.assert_not_called()

    def test_json_import_success(self, cli_runner: CliRunner, tmp_path: Path) -> None:
        """A JSON file imports every parsed entry (criterion 2: JSON format)."""
        storage = MagicMock(spec=StorageManager)
        storage.save_content_item.return_value = 1
        data_file = tmp_path / "books.json"
        data_file.write_text(_JSON_BOOKS)

        result = _invoke_with_mocks(
            cli_runner,
            [
                "import",
                "--source",
                "json_import",
                "--file",
                str(data_file),
                "--content-type",
                "book",
            ],
            storage,
        )
        assert result.exit_code == 0, result.output
        assert "Imported 2/2 items from json_import." in result.output

    def test_markdown_import_success(
        self, cli_runner: CliRunner, tmp_path: Path
    ) -> None:
        """A markdown file imports every parsed entry (criterion 2: markdown)."""
        storage = MagicMock(spec=StorageManager)
        storage.save_content_item.return_value = 1
        data_file = tmp_path / "books.md"
        data_file.write_text(_MARKDOWN_BOOKS)

        result = _invoke_with_mocks(
            cli_runner,
            [
                "import",
                "--source",
                "markdown_import",
                "--file",
                str(data_file),
                "--content-type",
                "book",
            ],
            storage,
        )
        assert result.exit_code == 0, result.output
        assert "Imported 2/2 items from markdown_import." in result.output

    def test_missing_required_option_errors(
        self, cli_runner: CliRunner, tmp_path: Path
    ) -> None:
        """Omitting content_type for a generic format exits non-zero, no traceback.

        csv_import requires content_type; without it the service raises
        FileImportError and the CLI must abort with a readable message.
        """
        storage = MagicMock(spec=StorageManager)
        data_file = tmp_path / "books.csv"
        data_file.write_text("title\nDune\n")

        result = _invoke_with_mocks(
            cli_runner,
            ["import", "--source", "csv_import", "--file", str(data_file)],
            storage,
        )
        assert result.exit_code != 0
        assert "content_type" in result.output

    def test_invalid_option_format_errors(
        self, cli_runner: CliRunner, tmp_path: Path
    ) -> None:
        """An --option without '=' is rejected before importing."""
        storage = MagicMock(spec=StorageManager)
        data_file = tmp_path / "books.csv"
        data_file.write_text("title\nDune\n")

        result = _invoke_with_mocks(
            cli_runner,
            [
                "import",
                "--source",
                "csv_import",
                "--file",
                str(data_file),
                "--option",
                "bogus",
            ],
            storage,
        )
        assert result.exit_code != 0
        assert "Invalid --option" in result.output

    def test_missing_file_errors(self, cli_runner: CliRunner, tmp_path: Path) -> None:
        """A non-existent --file path exits non-zero with a readable message."""
        storage = MagicMock(spec=StorageManager)
        result = _invoke_with_mocks(
            cli_runner,
            [
                "import",
                "--source",
                "goodreads_csv",
                "--file",
                str(tmp_path / "nope.csv"),
            ],
            storage,
        )
        assert result.exit_code != 0
        assert "File not found" in result.output

    def test_unknown_source_errors(self, cli_runner: CliRunner, tmp_path: Path) -> None:
        """An unregistered source exits non-zero with a clear message."""
        storage = MagicMock(spec=StorageManager)
        data_file = tmp_path / "x.csv"
        data_file.write_text("ignored\n")

        result = _invoke_with_mocks(
            cli_runner,
            ["import", "--source", "does_not_exist", "--file", str(data_file)],
            storage,
        )
        assert result.exit_code != 0
        assert "Unknown plugin" in result.output

    def test_non_file_import_source_errors(
        self, cli_runner: CliRunner, tmp_path: Path
    ) -> None:
        """A syncable (non-file-import) source exits non-zero with a clear message."""
        storage = MagicMock(spec=StorageManager)
        data_file = tmp_path / "x.csv"
        data_file.write_text("ignored\n")

        result = _invoke_with_mocks(
            cli_runner,
            ["import", "--source", "sonarr", "--file", str(data_file)],
            storage,
        )
        assert result.exit_code != 0
        assert "does not support file import" in result.output

    def test_file_required_without_list(self, cli_runner: CliRunner) -> None:
        """Omitting --file (without --source list) is an error."""
        storage = MagicMock(spec=StorageManager)
        result = _invoke_with_mocks(
            cli_runner,
            ["import", "--source", "goodreads_csv"],
            storage,
        )
        assert result.exit_code != 0
        assert "--file is required" in result.output

    def test_wrong_format_file_errors_cleanly(
        self, cli_runner: CliRunner, tmp_path: Path
    ) -> None:
        """A JSON file handed to csv_import aborts cleanly, with no traceback.

        Mirrors the web contract (``POST /api/import`` -> 400): the CSV parser
        finds no ``title`` column, raises SourceError, and the service wraps it
        into a FileImportError the CLI renders as a plain error line.
        """
        storage = MagicMock(spec=StorageManager)
        data_file = tmp_path / "books.json"
        data_file.write_text(
            json.dumps([{"name": "Dune"}, {"name": "Neuromancer"}], indent=2)
        )

        result = _invoke_with_mocks(
            cli_runner,
            [
                "import",
                "--source",
                "csv_import",
                "--file",
                str(data_file),
                "--content-type",
                "book",
            ],
            storage,
        )
        assert result.exit_code != 0
        assert "Failed to import file with 'csv_import'" in result.output
        assert "Traceback" not in result.output
        storage.save_content_item.assert_not_called()

    def test_directory_as_file_errors_cleanly(
        self, cli_runner: CliRunner, tmp_path: Path
    ) -> None:
        """--file pointing at a directory is rejected, not crashed on.

        ``Path.is_file()`` is the gate; without it the plugin's ``open`` would
        raise IsADirectoryError and surface as a traceback.
        """
        storage = MagicMock(spec=StorageManager)
        directory = tmp_path / "a_directory"
        directory.mkdir()

        result = _invoke_with_mocks(
            cli_runner,
            ["import", "--source", "goodreads_csv", "--file", str(directory)],
            storage,
        )
        assert result.exit_code != 0
        assert "File not found or not readable" in result.output
        assert "Traceback" not in result.output

    def test_blank_option_value_is_rejected_by_validation(
        self, cli_runner: CliRunner, tmp_path: Path
    ) -> None:
        """``--option content_type=`` is treated as unset, not as a valid value."""
        storage = MagicMock(spec=StorageManager)
        data_file = tmp_path / "books.csv"
        data_file.write_text("title\nDune\n")

        result = _invoke_with_mocks(
            cli_runner,
            [
                "import",
                "--source",
                "csv_import",
                "--file",
                str(data_file),
                "--option",
                "content_type=",
            ],
            storage,
        )
        assert result.exit_code != 0
        assert "'content_type' is required" in result.output
        assert "Traceback" not in result.output

    def test_unknown_content_type_option_errors_cleanly(
        self, cli_runner: CliRunner, tmp_path: Path
    ) -> None:
        """An out-of-range content_type is refused by the plugin's validation."""
        storage = MagicMock(spec=StorageManager)
        data_file = tmp_path / "books.csv"
        data_file.write_text("title\nDune\n")

        result = _invoke_with_mocks(
            cli_runner,
            [
                "import",
                "--source",
                "csv_import",
                "--file",
                str(data_file),
                "--option",
                "content_type=comic_book",
            ],
            storage,
        )
        assert result.exit_code != 0
        assert "Invalid content_type 'comic_book'" in result.output
        assert "Traceback" not in result.output

    def test_goodreads_rss_is_not_importable(
        self, cli_runner: CliRunner, tmp_path: Path
    ) -> None:
        """The RSS half of the Goodreads split cannot be file-imported.

        Control for the split: only ``goodreads_csv`` is a one-shot import, so
        pointing ``import`` at the feed source must fail the same way any other
        syncable source does.
        """
        storage = MagicMock(spec=StorageManager)
        data_file = tmp_path / "books.csv"
        data_file.write_text(GOODREADS_CSV)

        result = _invoke_with_mocks(
            cli_runner,
            ["import", "--source", "goodreads_rss", "--file", str(data_file)],
            storage,
        )
        assert result.exit_code != 0
        assert "does not support file import" in result.output
        storage.save_content_item.assert_not_called()

    def test_list_table_output_names_every_importable_plugin(
        self, cli_runner: CliRunner
    ) -> None:
        """``--source list`` (default table format) lists the five importers."""
        result = _invoke_with_mocks(
            cli_runner,
            ["import", "--source", "list"],
            MagicMock(spec=StorageManager),
        )
        assert result.exit_code == 0, result.output
        for name in (
            "goodreads_csv",
            "csv_import",
            "json_import",
            "markdown_import",
            "storygraph_csv",
        ):
            assert name in result.output
        assert "goodreads_rss" not in result.output
        # The human-readable listing names the file types too, so a CLI user
        # learns what to export without reading the JSON form.
        assert "File Types" in result.output
        assert ".json, .jsonl" in result.output

    def test_list_with_no_importable_plugins(self, cli_runner: CliRunner) -> None:
        """An empty importer set says so in table form and is ``[]`` in JSON.

        Unreachable against the real registry, which always carries the five
        importers, so the branch is driven by stubbing the listing.
        """
        storage = MagicMock(spec=StorageManager)
        with patch("src.cli.commands.list_importable_plugins", return_value=[]):
            table = _invoke_with_mocks(
                cli_runner, ["import", "--source", "list"], storage
            )
            as_json = _invoke_with_mocks(
                cli_runner,
                ["import", "--source", "list", "--format", "json"],
                storage,
            )

        assert table.exit_code == 0, table.output
        assert table.output.strip() == "No importable sources available."
        assert as_json.exit_code == 0, as_json.output
        # Parity: the JSON form of an empty listing is empty JSON, never prose.
        assert json.loads(as_json.output) == []

    def test_progress_reports_each_milestone_once(
        self, cli_runner: CliRunner, tmp_path: Path
    ) -> None:
        """A 20-row import prints one line per ten items and nothing in between.

        The reporter only fires on multiples of ten, so every other test in this
        file (three rows at most) left both of its branches dead: the
        "Processed n/total" line itself and the ``last_reported`` guard.

        csv_import reports progress twice over the same file — once while
        parsing rows, once while saving them — so ten is reached twice and only
        the guard keeps the line from being printed twice. Inverting the guard
        shows "Processed 10/20" a second time.
        """
        storage = MagicMock(spec=StorageManager)
        storage.save_content_item.return_value = 1
        rows = "".join(f"Book {n},Author {n},read,4\n" for n in range(1, 21))
        data_file = tmp_path / "books.csv"
        data_file.write_text(f"title,author,status,rating\n{rows}")

        result = _invoke_with_mocks(
            cli_runner,
            [
                "import",
                "--source",
                "csv_import",
                "--file",
                str(data_file),
                "--content-type",
                "book",
            ],
            storage,
        )

        assert result.exit_code == 0, result.output
        assert "Imported 20/20 items from csv_import." in result.output
        assert result.output.count("    Processed 10/20...") == 1
        assert result.output.count("    Processed 20/20...") == 1
        for processed in range(1, 21):
            if processed % 10:
                assert f"Processed {processed}/20" not in result.output
