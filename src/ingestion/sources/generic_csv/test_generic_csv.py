"""Tests for the generic CSV import plugin.

Parsing is the importer's, and is tested next to it in
``src/ingestion/importers/generic_csv/``. What is left here is the plugin's
own job: resolving a configured path and reporting what went wrong with it.
"""

from pathlib import Path

import pytest

from src.ingestion.plugin_base import SourceError
from src.ingestion.sources.generic_csv.generic_csv import CsvImportPlugin
from src.models.content import ConsumptionStatus, ContentType


@pytest.fixture()
def plugin() -> CsvImportPlugin:
    """Create a CsvImportPlugin instance."""
    return CsvImportPlugin()


class TestCsvImportPluginValidation:
    """Tests for CsvImportPlugin config validation."""

    def test_validate_missing_csv_path(self, plugin: CsvImportPlugin) -> None:
        errors = plugin.validate_config({"content_type": "book"})
        assert any("path" in error for error in errors)

    def test_validate_nonexistent_file(
        self, plugin: CsvImportPlugin, tmp_path: Path
    ) -> None:
        errors = plugin.validate_config(
            {"path": str(tmp_path / "missing.csv"), "content_type": "book"}
        )
        assert any("not found" in error for error in errors)

    def test_validate_invalid_content_type(
        self, plugin: CsvImportPlugin, tmp_path: Path
    ) -> None:
        csv_file = tmp_path / "books.csv"
        csv_file.write_text("title\n")
        errors = plugin.validate_config(
            {"path": str(csv_file), "content_type": "podcast"}
        )
        assert any("Invalid content_type" in error for error in errors)


class TestCsvImportPluginFetch:
    """Tests for the file the plugin reads and the items it attributes."""

    def test_fetch_reads_the_configured_file(
        self, plugin: CsvImportPlugin, tmp_path: Path
    ) -> None:
        csv_file = tmp_path / "books.csv"
        csv_file.write_text(
            "title,author,rating,status\nThe Name of the Wind,Patrick Rothfuss,5,completed\n"
        )

        items = list(plugin.fetch({"path": str(csv_file), "content_type": "book"}))

        assert len(items) == 1
        assert items[0].title == "The Name of the Wind"
        assert items[0].author == "Patrick Rothfuss"
        assert items[0].content_type == ContentType.BOOK.value
        assert items[0].status == ConsumptionStatus.COMPLETED.value
        assert items[0].source == "csv_import"

    def test_fetch_attributes_items_to_the_source_id(
        self, plugin: CsvImportPlugin, tmp_path: Path
    ) -> None:
        """The id the user gave the source owns the items, not the plugin name."""
        csv_file = tmp_path / "books.csv"
        csv_file.write_text("title\nDune\n")

        items = list(
            plugin.fetch(
                {
                    "path": str(csv_file),
                    "content_type": "book",
                    "_source_id": "my_shelf",
                }
            )
        )

        assert [item.source for item in items] == ["my_shelf"]


class TestCsvImportPluginErrors:
    """Tests for error handling."""

    def test_file_not_found_raises_source_error(
        self, plugin: CsvImportPlugin, tmp_path: Path
    ) -> None:
        with pytest.raises(SourceError, match="CSV file not found"):
            list(
                plugin.fetch(
                    {"path": str(tmp_path / "missing.csv"), "content_type": "book"}
                )
            )

    def test_a_refused_file_raises_source_error(
        self, plugin: CsvImportPlugin, tmp_path: Path
    ) -> None:
        """What the importer refuses whole reaches the caller as a SourceError."""
        csv_file = tmp_path / "data.csv"
        csv_file.write_text("name,rating\nTest,5\n")

        with pytest.raises(SourceError, match="missing required column"):
            list(plugin.fetch({"path": str(csv_file), "content_type": "book"}))


class TestCsvImportPathContainmentRegression:
    """Regression: source config as an arbitrary-file reader.

    Bug: ``path`` came straight from HTTP-writable source config, so any host
    file could be imported. Cause: no containment. Fix: validate and fetch
    resolve it against ``security.allowed_source_roots``.
    """

    def test_validate_refuses_a_path_outside_every_root(
        self, plugin: CsvImportPlugin
    ) -> None:
        errors = plugin.validate_config({"path": "/etc/passwd", "content_type": "book"})
        assert errors == [
            "Path is outside the allowed source roots: /etc/passwd. "
            "Add its directory to security.allowed_source_roots in config.yaml."
        ]

    def test_fetch_refuses_and_yields_nothing(
        self, plugin: CsvImportPlugin, tmp_path: Path
    ) -> None:
        outside = tmp_path.parent / f"{tmp_path.name}-outside"
        outside.mkdir()
        secret = outside / "secret.csv"
        secret.write_text("title\nLeaked\n")

        collected = []
        with pytest.raises(SourceError, match="outside the allowed source roots"):
            for item in plugin.fetch({"path": str(secret), "content_type": "book"}):
                collected.append(item)

        # list() would discard these, leaving the leak half of the name unproven.
        assert collected == []
