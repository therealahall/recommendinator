"""Tests for the CLI import command."""

import json
from unittest.mock import MagicMock

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


class TestImportCommand:
    """Tests for ``recommendinator import``."""

    def test_list_sources_json_matches_importable_plugins(self, cli_runner):
        """--source list --format json mirrors GET /api/import/sources."""
        result = _invoke_with_mocks(
            cli_runner,
            ["import", "--source", "list", "--format", "json"],
            MagicMock(spec=StorageManager),
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        names = {plugin["name"] for plugin in data}
        # Subset (not equality) so adding a fifth file-import plugin later does
        # not break this test; the contract is that these four are importable.
        assert {"csv_import", "goodreads", "json_import", "markdown_import"} <= names
        # Syncable sources are excluded from the importable listing.
        assert "roms" not in names
        csv_plugin = next(p for p in data if p["name"] == "csv_import")
        assert [f["name"] for f in csv_plugin["fields"]] == ["content_type"]

    def test_import_result_json_matches_web_fields(self, cli_runner, tmp_path):
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
                "goodreads",
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
        }
        assert body["source"] == "goodreads"
        assert body["items_synced"] == 2
        assert body["total_items"] == 2
        assert body["errors"] == []

    def test_goodreads_import_success(self, cli_runner, tmp_path):
        """A Goodreads CSV file imports every parsed book."""
        storage = MagicMock(spec=StorageManager)
        storage.save_content_item.return_value = 1
        data_file = tmp_path / "books.csv"
        data_file.write_text(GOODREADS_CSV)

        result = _invoke_with_mocks(
            cli_runner,
            ["import", "--source", "goodreads", "--file", str(data_file)],
            storage,
        )
        assert result.exit_code == 0, result.output
        assert "Imported 2/2 items from goodreads." in result.output

    def test_per_item_error_warning_in_table_output(self, cli_runner, tmp_path):
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
            ["import", "--source", "goodreads", "--file", str(data_file)],
            storage,
        )
        assert result.exit_code == 0, result.output
        assert "Imported 1/2 items from goodreads." in result.output
        assert "Warning: Failed to process 'Dune'" in result.output
        # The raw exception text must never leak to the user.
        assert "db write failed" not in result.output

    def test_per_item_error_populates_json_errors(self, cli_runner, tmp_path):
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
                "goodreads",
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

    def test_csv_import_passes_content_type_option(self, cli_runner, tmp_path):
        """--content-type is forwarded as the import option and drives parsing."""
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

    def test_option_flag_passes_through_to_service(self, cli_runner, tmp_path):
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

    def test_json_import_success(self, cli_runner, tmp_path):
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

    def test_markdown_import_success(self, cli_runner, tmp_path):
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

    def test_missing_required_option_errors(self, cli_runner, tmp_path):
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

    def test_invalid_option_format_errors(self, cli_runner, tmp_path):
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

    def test_missing_file_errors(self, cli_runner, tmp_path):
        """A non-existent --file path exits non-zero with a readable message."""
        storage = MagicMock(spec=StorageManager)
        result = _invoke_with_mocks(
            cli_runner,
            ["import", "--source", "goodreads", "--file", str(tmp_path / "nope.csv")],
            storage,
        )
        assert result.exit_code != 0
        assert "File not found" in result.output

    def test_unknown_source_errors(self, cli_runner, tmp_path):
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

    def test_non_file_import_source_errors(self, cli_runner, tmp_path):
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

    def test_file_required_without_list(self, cli_runner):
        """Omitting --file (without --source list) is an error."""
        storage = MagicMock(spec=StorageManager)
        result = _invoke_with_mocks(
            cli_runner,
            ["import", "--source", "goodreads"],
            storage,
        )
        assert result.exit_code != 0
        assert "--file is required" in result.output
