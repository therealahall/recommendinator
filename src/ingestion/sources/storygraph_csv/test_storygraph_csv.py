"""Tests for The StoryGraph CSV plugin.

Parsing is the importer's, and is tested next to it in
``src/ingestion/importers/storygraph_csv/``. What is left here is the plugin's
own job: resolving a configured path and reporting what went wrong with it.
"""

import logging
from pathlib import Path

import pytest

from src.ingestion.plugin_base import SourceError
from src.ingestion.sources.storygraph_csv.storygraph_csv import StorygraphCsvPlugin
from src.models.content import ConsumptionStatus, ContentType


@pytest.fixture()
def plugin() -> StorygraphCsvPlugin:
    """Create a StorygraphCsvPlugin instance."""
    return StorygraphCsvPlugin()


class TestStorygraphCsvPluginValidation:
    """Tests for StorygraphCsvPlugin config validation."""

    def test_validate_missing_path(self, plugin: StorygraphCsvPlugin) -> None:
        """Test validation fails when path is missing."""
        errors = plugin.validate_config({})

        assert len(errors) == 1
        assert "'path' is required" in errors[0]

    def test_validate_nonexistent_file(
        self, plugin: StorygraphCsvPlugin, tmp_path: Path
    ) -> None:
        """Test validation fails when CSV file does not exist."""
        errors = plugin.validate_config({"path": str(tmp_path / "library.csv")})

        assert len(errors) == 1
        assert "CSV file not found" in errors[0]


class TestStorygraphCsvPluginFetch:
    """Tests for the file the plugin reads and the items it attributes."""

    def test_fetch_reads_the_configured_file(
        self, plugin: StorygraphCsvPlugin, tmp_path: Path
    ) -> None:
        csv_file = tmp_path / "library.csv"
        csv_file.write_text(
            "Title,Authors,Read Status,Star Rating\n"
            "The Fifth Season,N. K. Jemisin,read,4.5\n"
        )

        items = list(plugin.fetch({"path": str(csv_file)}))

        assert len(items) == 1
        assert items[0].title == "The Fifth Season"
        assert items[0].author == "N. K. Jemisin"
        assert items[0].rating == 5
        assert items[0].status == ConsumptionStatus.COMPLETED
        assert items[0].content_type == ContentType.BOOK
        assert items[0].source == "storygraph_csv"

    def test_fetch_attributes_items_to_the_source_id(
        self, plugin: StorygraphCsvPlugin, tmp_path: Path
    ) -> None:
        """The id the user gave the source owns the items, not the plugin name."""
        csv_file = tmp_path / "library.csv"
        csv_file.write_text("Title\nDune\n")

        items = list(plugin.fetch({"path": str(csv_file), "_source_id": "my_shelf"}))

        assert [item.source for item in items] == ["my_shelf"]

    def test_fetch_file_not_found_raises_source_error(
        self, plugin: StorygraphCsvPlugin, tmp_path: Path
    ) -> None:
        """Test that fetching a nonexistent file raises SourceError."""
        with pytest.raises(SourceError, match="CSV file not found") as exc_info:
            list(plugin.fetch({"path": str(tmp_path / "library.csv")}))

        assert exc_info.value.plugin_name == "storygraph_csv"


STORYGRAPH_CSV_LOGGER = "src.ingestion.sources.storygraph_csv.storygraph_csv"


class TestStorygraphCsvLogInjectionRegression:
    """Regression: the configured file path forged log entries.

    Bug: the plugin interpolates the resolved path raw. Cause: the sanitiser
    pass covered ``csv_import`` alone. Fix: ``sanitize_for_log``.
    """

    def test_a_newline_in_the_file_name_cannot_forge_a_log_entry(
        self,
        plugin: StorygraphCsvPlugin,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        csv_file = tmp_path / "books\nImported 9999 items from StoryGraph CSV file.csv"
        csv_file.write_text("Title,Authors,Read Status\n")

        with caplog.at_level(logging.INFO, logger=STORYGRAPH_CSV_LOGGER):
            list(plugin.fetch({"path": str(csv_file)}))

        messages = [
            record.getMessage()
            for record in caplog.records
            if record.name == STORYGRAPH_CSV_LOGGER
        ]
        assert messages, "nothing was logged, so this proves nothing"
        assert "\n" not in messages[0], messages
