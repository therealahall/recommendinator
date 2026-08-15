"""Tests for The StoryGraph CSV plugin."""

import logging
from datetime import date
from pathlib import Path

import pytest

from src.ingestion.plugin_base import SourceError
from src.ingestion.sources.storygraph_csv.storygraph_csv import StorygraphCsvPlugin
from src.models.content import ConsumptionStatus, ContentType

# A full StoryGraph export header row, matching the columns the site emits.
HEADER = (
    "Title,Authors,Contributors,ISBN/UID,Format,Read Status,Date Added,"
    "Last Date Read,Dates Read,Read Count,Moods,Pace,"
    "Character- or Plot-Driven?,Strong Character Development?,"
    "Loveable Characters?,Diverse Characters?,Flawed Characters?,"
    "Star Rating,Review,Content Warnings,Content Warning Description,Tags,Owned?"
)


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
    """Tests for StorygraphCsvPlugin.fetch()."""

    def _write(self, tmp_path: Path, rows: str) -> Path:
        csv_file = tmp_path / "library.csv"
        csv_file.write_text(f"{HEADER}\n{rows}")
        return csv_file

    def test_fetch_basic(self, plugin: StorygraphCsvPlugin, tmp_path: Path) -> None:
        """Test basic multi-row parsing into ContentItem fields."""
        rows = (
            "The Fifth Season,N. K. Jemisin,,9780316229296,Paperback,read,"
            "2024/01/01,2024/03/15,2024/01/01-2024/03/15,1,adventurous,medium,"
            "plot,Yes,Yes,Yes,Yes,4.5,A stunning read.,,,fantasy,Yes\n"
            "The Way of Kings,Brandon Sanderson,,,Hardcover,to-read,"
            "2024/02/01,,,0,,,,,,,,,,,,,No\n"
        )
        csv_file = self._write(tmp_path, rows)

        items = list(plugin.fetch({"path": str(csv_file)}))

        assert len(items) == 2

        first = items[0]
        assert first.title == "The Fifth Season"
        assert first.author == "N. K. Jemisin"
        assert first.content_type == ContentType.BOOK
        assert first.rating == 5  # 4.5 rounds up
        assert first.status == ConsumptionStatus.COMPLETED
        assert first.date_completed == date(2024, 3, 15)
        assert first.review == "A stunning read."
        assert first.id == "9780316229296"
        assert first.source == "storygraph_csv"

        second = items[1]
        assert second.title == "The Way of Kings"
        assert second.author == "Brandon Sanderson"
        assert second.rating is None
        assert second.status == ConsumptionStatus.UNREAD
        assert second.date_completed is None
        assert second.review is None
        assert second.id is None

    def test_fetch_status_did_not_finish_maps_to_completed(
        self, plugin: StorygraphCsvPlugin, tmp_path: Path
    ) -> None:
        """Test did-not-finish maps to COMPLETED but preserves raw status.

        Product decision: a rated-then-abandoned book is a real signal, so it
        counts as completed for scoring, while the true StoryGraph status is
        retained in metadata so no fidelity is lost.
        """
        rows = "Gave Up,Some Author,,,,did-not-finish," + "," * 17 + "\n"
        csv_file = self._write(tmp_path, rows)

        items = list(plugin.fetch({"path": str(csv_file)}))

        assert items[0].status == ConsumptionStatus.COMPLETED
        assert items[0].metadata["read_status"] == "did-not-finish"

    def test_fetch_status_unknown_defaults_to_unread(
        self, plugin: StorygraphCsvPlugin, tmp_path: Path
    ) -> None:
        """Test an unrecognized read status falls back to UNREAD."""
        rows = "Mystery Status,Some Author,,,,wishlist," + "," * 17 + "\n"
        csv_file = self._write(tmp_path, rows)

        items = list(plugin.fetch({"path": str(csv_file)}))

        assert items[0].status == ConsumptionStatus.UNREAD

    def test_fetch_rating_half_rounds_up(
        self, plugin: StorygraphCsvPlugin, tmp_path: Path
    ) -> None:
        """Test a .5 rating rounds up (3.5 -> 4)."""
        rows = "Half Star,Some Author,,,,read," + "," * 11 + "3.5\n"
        csv_file = self._write(tmp_path, rows)

        items = list(plugin.fetch({"path": str(csv_file)}))

        assert items[0].rating == 4

    def test_fetch_rating_quarter_rounds_down(
        self, plugin: StorygraphCsvPlugin, tmp_path: Path
    ) -> None:
        """Test a .25 rating rounds down (3.25 -> 3)."""
        rows = "Quarter Star,Some Author,,,,read," + "," * 11 + "3.25\n"
        csv_file = self._write(tmp_path, rows)

        items = list(plugin.fetch({"path": str(csv_file)}))

        assert items[0].rating == 3

    def test_fetch_rating_zero_is_none(
        self, plugin: StorygraphCsvPlugin, tmp_path: Path
    ) -> None:
        """Test a zero rating becomes None (model requires 1-5 or None)."""
        rows = "Unrated,Some Author,,,,read," + "," * 11 + "0\n"
        csv_file = self._write(tmp_path, rows)

        items = list(plugin.fetch({"path": str(csv_file)}))

        assert items[0].rating is None

    def test_fetch_rating_out_of_range_clamped(
        self, plugin: StorygraphCsvPlugin, tmp_path: Path
    ) -> None:
        """Test an out-of-range rating clamps into 1-5."""
        rows = "Too High,Some Author,,,,read," + "," * 11 + "9\n"
        csv_file = self._write(tmp_path, rows)

        items = list(plugin.fetch({"path": str(csv_file)}))

        assert items[0].rating == 5

    def test_fetch_rating_garbage_is_none(
        self, plugin: StorygraphCsvPlugin, tmp_path: Path
    ) -> None:
        """Test an unparseable rating becomes None."""
        rows = "Weird,Some Author,,,,read," + "," * 11 + "abc\n"
        csv_file = self._write(tmp_path, rows)

        items = list(plugin.fetch({"path": str(csv_file)}))

        assert items[0].rating is None

    def test_fetch_empty_title_skipped(
        self, plugin: StorygraphCsvPlugin, tmp_path: Path
    ) -> None:
        """Test that rows with an empty title are skipped."""
        rows = (
            ",Ghost Author,,,,read," + "," * 17 + "\n"
            "Real Book,Real Author,,,,read," + "," * 17 + "\n"
        )
        csv_file = self._write(tmp_path, rows)

        items = list(plugin.fetch({"path": str(csv_file)}))

        assert len(items) == 1
        assert items[0].title == "Real Book"

    def test_fetch_file_not_found_raises_source_error(
        self, plugin: StorygraphCsvPlugin, tmp_path: Path
    ) -> None:
        """Test that fetching a nonexistent file raises SourceError."""
        with pytest.raises(SourceError, match="CSV file not found") as exc_info:
            list(plugin.fetch({"path": str(tmp_path / "library.csv")}))

        assert exc_info.value.plugin_name == "storygraph_csv"

    def test_fetch_invalid_date_is_none(
        self, plugin: StorygraphCsvPlugin, tmp_path: Path
    ) -> None:
        """Test that an unparseable date falls through to None."""
        rows = "Bad Date,Some Author,,,,read,2024/01/01,not-a-date," + "," * 15 + "\n"
        csv_file = self._write(tmp_path, rows)

        items = list(plugin.fetch({"path": str(csv_file)}))

        assert items[0].date_completed is None

    def test_fetch_metadata_rich_signals(
        self, plugin: StorygraphCsvPlugin, tmp_path: Path
    ) -> None:
        """Test the rich StoryGraph signals populate metadata."""
        rows = (
            "Rich Signals,Author Name,Narrator Person,9781234567890,Audiobook,read,"
            "2024/01/01,2024/03/15,2024/01/01-2024/03/15,2,"
            '"adventurous, dark",fast,character,Yes,Yes,No,Yes,4,Loved it,'
            '"violence, grief",Some description,"fantasy, favorites",Yes\n'
        )
        csv_file = self._write(tmp_path, rows)

        items = list(plugin.fetch({"path": str(csv_file)}))

        meta = items[0].metadata
        assert meta["isbn_uid"] == "9781234567890"
        assert meta["format"] == "Audiobook"
        assert meta["read_count"] == "2"
        assert meta["date_added"] == "2024/01/01"
        assert meta["last_date_read"] == "2024/03/15"
        assert meta["dates_read"] == "2024/01/01-2024/03/15"
        assert meta["read_status"] == "read"
        assert meta["moods"] == "adventurous, dark"
        assert meta["pace"] == "fast"
        assert meta["tags"] == "fantasy, favorites"
        assert meta["content_warnings"] == "violence, grief"
        assert meta["content_warning_description"] == "Some description"
        assert meta["character_or_plot_driven"] == "character"
        assert meta["strong_character_development"] == "Yes"
        assert meta["loveable_characters"] == "Yes"
        assert meta["diverse_characters"] == "No"
        assert meta["flawed_characters"] == "Yes"
        assert meta["contributors"] == "Narrator Person"
        assert meta["owned"] == "Yes"

    def test_fetch_missing_optional_columns_parses(
        self, plugin: StorygraphCsvPlugin, tmp_path: Path
    ) -> None:
        """Test a trimmed export (missing optional columns) still parses.

        StoryGraph tweaks its export shape over time, so a header with only a
        subset of columns must not crash the parse.
        """
        csv_file = tmp_path / "library.csv"
        csv_file.write_text(
            "Title,Authors,Read Status,Star Rating\n"
            "Slim Export,Terse Author,read,4\n"
        )

        items = list(plugin.fetch({"path": str(csv_file)}))

        assert len(items) == 1
        assert items[0].title == "Slim Export"
        assert items[0].author == "Terse Author"
        assert items[0].rating == 4
        assert items[0].status == ConsumptionStatus.COMPLETED
        assert items[0].id is None

    def test_fetch_short_row_missing_trailing_columns(
        self, plugin: StorygraphCsvPlugin, tmp_path: Path
    ) -> None:
        """Test a row with fewer fields than the header does not crash.

        Under ``csv.DictReader`` a short row leaves later columns as ``None``.
        The plugin must tolerate that (the flagged None-handling risk) rather
        than raising on ``.strip()`` of a missing value.
        """
        # Full 23-column header, but a row that stops after Read Status.
        rows = "Short Row,Terse Author,,,,read\n"
        csv_file = self._write(tmp_path, rows)

        items = list(plugin.fetch({"path": str(csv_file)}))

        assert len(items) == 1
        assert items[0].title == "Short Row"
        assert items[0].author == "Terse Author"
        assert items[0].status == ConsumptionStatus.COMPLETED
        assert items[0].rating is None
        assert items[0].metadata["format"] is None
        assert items[0].metadata["tags"] is None

    def test_fetch_read_status_is_case_insensitive(
        self, plugin: StorygraphCsvPlugin, tmp_path: Path
    ) -> None:
        """Test Read Status matching ignores case (e.g. 'READ', 'To-Read')."""
        rows = (
            "Shouty Read,Author,,,,READ," + "," * 17 + "\n"
            "Mixed Case,Author,,,,Currently-Reading," + "," * 17 + "\n"
            "Title Case,Author,,,,To-Read," + "," * 17 + "\n"
        )
        csv_file = self._write(tmp_path, rows)

        items = list(plugin.fetch({"path": str(csv_file)}))

        assert items[0].status == ConsumptionStatus.COMPLETED
        assert items[1].status == ConsumptionStatus.CURRENTLY_CONSUMING
        assert items[2].status == ConsumptionStatus.UNREAD


STORYGRAPH_CSV_LOGGER = "src.ingestion.sources.storygraph_csv.storygraph_csv"


class TestStorygraphCsvLogInjectionRegression:
    """Regression: the configured file path forged log entries.

    Bug: ``_parse_csv`` interpolates the resolved path raw. Cause: the
    sanitiser pass covered ``csv_import`` alone. Fix: ``sanitize_for_log``.
    """

    def test_a_newline_in_the_file_name_cannot_forge_a_log_entry(
        self,
        plugin: StorygraphCsvPlugin,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        csv_file = tmp_path / "books\nImported 9999 items from StoryGraph CSV file.csv"
        csv_file.write_text(f"{HEADER}\n")

        with caplog.at_level(logging.INFO, logger=STORYGRAPH_CSV_LOGGER):
            list(plugin.fetch({"path": str(csv_file)}))

        messages = [
            record.getMessage()
            for record in caplog.records
            if record.name == STORYGRAPH_CSV_LOGGER
        ]
        assert messages, "nothing was logged, so this proves nothing"
        assert "\n" not in messages[0], messages
