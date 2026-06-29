"""Tests for the CLI import command."""

import json
from unittest.mock import MagicMock

from src.storage.manager import StorageManager
from tests.cli.conftest import _invoke_with_mocks

# Goodreads CSV export header recognised by the goodreads plugin.
_GOODREADS_CSV = (
    "Title,Author,My Rating,Exclusive Shelf,Date Read\n"
    "Dune,Frank Herbert,5,read,2020/01/01\n"
    "Neuromancer,William Gibson,4,read,2020/02/01\n"
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
        names = [plugin["name"] for plugin in data]
        assert names == ["csv_import", "goodreads", "json_import", "markdown_import"]
        csv_plugin = next(p for p in data if p["name"] == "csv_import")
        assert [f["name"] for f in csv_plugin["fields"]] == ["content_type"]

    def test_goodreads_import_success(self, cli_runner, tmp_path):
        """A Goodreads CSV file imports every parsed book."""
        storage = MagicMock(spec=StorageManager)
        storage.save_content_item.return_value = 1
        data_file = tmp_path / "books.csv"
        data_file.write_text(_GOODREADS_CSV)

        result = _invoke_with_mocks(
            cli_runner,
            ["import", "--source", "goodreads", "--file", str(data_file)],
            storage,
        )
        assert result.exit_code == 0, result.output
        assert "Imported 2/2 items from goodreads." in result.output

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
