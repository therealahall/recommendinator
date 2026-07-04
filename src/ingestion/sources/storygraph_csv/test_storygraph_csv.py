"""Tests for The StoryGraph CSV plugin."""

import csv
from datetime import date
from pathlib import Path

import pytest

from src.ingestion.plugin_base import SourceError, SourcePlugin
from src.ingestion.registry import PluginRegistry
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


class TestStorygraphCsvPluginProperties:
    """Tests for StorygraphCsvPlugin metadata properties."""

    def test_is_source_plugin(self, plugin: StorygraphCsvPlugin) -> None:
        """Test that StorygraphCsvPlugin is a SourcePlugin subclass."""
        assert isinstance(plugin, SourcePlugin)

    def test_name(self, plugin: StorygraphCsvPlugin) -> None:
        """Test plugin name identifier."""
        assert plugin.name == "storygraph_csv"

    def test_display_name(self, plugin: StorygraphCsvPlugin) -> None:
        """Test human-readable display name."""
        assert plugin.display_name == "The StoryGraph (CSV Export)"

    def test_description(self, plugin: StorygraphCsvPlugin) -> None:
        """Test the custom description overrides the base class default."""
        assert (
            plugin.description
            == "Import books from a The StoryGraph library CSV export"
        )

    def test_content_types(self, plugin: StorygraphCsvPlugin) -> None:
        """Test that plugin provides books."""
        assert plugin.content_types == [ContentType.BOOK]

    def test_requires_api_key(self, plugin: StorygraphCsvPlugin) -> None:
        """Test that plugin does not require an API key."""
        assert plugin.requires_api_key is False

    def test_requires_network(self, plugin: StorygraphCsvPlugin) -> None:
        """Test that plugin does not require network access."""
        assert plugin.requires_network is False

    def test_config_schema(self, plugin: StorygraphCsvPlugin) -> None:
        """Test configuration schema defines a single required path."""
        schema = plugin.get_config_schema()

        assert len(schema) == 1
        assert schema[0].name == "path"
        assert schema[0].field_type is str
        assert schema[0].required is True

    def test_get_source_identifier(self, plugin: StorygraphCsvPlugin) -> None:
        """Test source identifier matches plugin name by default."""
        assert plugin.get_source_identifier() == "storygraph_csv"

    def test_get_info(self, plugin: StorygraphCsvPlugin) -> None:
        """Test plugin info includes all metadata."""
        info = plugin.get_info()

        assert info.name == "storygraph_csv"
        assert info.display_name == "The StoryGraph (CSV Export)"
        assert info.content_types == [ContentType.BOOK]
        assert info.requires_api_key is False
        assert info.requires_network is False


class TestStorygraphCsvPluginValidation:
    """Tests for StorygraphCsvPlugin config validation."""

    def test_validate_valid_config(
        self, plugin: StorygraphCsvPlugin, tmp_path: Path
    ) -> None:
        """Test validation passes with valid CSV path."""
        csv_file = tmp_path / "library.csv"
        csv_file.write_text("header\n")

        errors = plugin.validate_config({"path": str(csv_file)})

        assert errors == []

    def test_validate_missing_path(self, plugin: StorygraphCsvPlugin) -> None:
        """Test validation fails when path is missing."""
        errors = plugin.validate_config({})

        assert len(errors) == 1
        assert "'path' is required" in errors[0]

    def test_validate_empty_path(self, plugin: StorygraphCsvPlugin) -> None:
        """Test validation fails when path is empty."""
        errors = plugin.validate_config({"path": ""})

        assert len(errors) == 1
        assert "'path' is required" in errors[0]

    def test_validate_nonexistent_file(self, plugin: StorygraphCsvPlugin) -> None:
        """Test validation fails when CSV file does not exist."""
        errors = plugin.validate_config({"path": "/nonexistent/library.csv"})

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

    def test_fetch_status_currently_reading(
        self, plugin: StorygraphCsvPlugin, tmp_path: Path
    ) -> None:
        """Test currently-reading maps to CURRENTLY_CONSUMING."""
        rows = "Reading Now,Some Author,,,,currently-reading," + "," * 17 + "\n"
        csv_file = self._write(tmp_path, rows)

        items = list(plugin.fetch({"path": str(csv_file)}))

        assert items[0].status == ConsumptionStatus.CURRENTLY_CONSUMING

    def test_fetch_status_to_read(
        self, plugin: StorygraphCsvPlugin, tmp_path: Path
    ) -> None:
        """Test to-read maps to UNREAD."""
        rows = "On The Pile,Some Author,,,,to-read," + "," * 17 + "\n"
        csv_file = self._write(tmp_path, rows)

        items = list(plugin.fetch({"path": str(csv_file)}))

        assert items[0].status == ConsumptionStatus.UNREAD

    def test_fetch_status_read(
        self, plugin: StorygraphCsvPlugin, tmp_path: Path
    ) -> None:
        """Test read maps to COMPLETED."""
        rows = "Finished It,Some Author,,,,read," + "," * 17 + "\n"
        csv_file = self._write(tmp_path, rows)

        items = list(plugin.fetch({"path": str(csv_file)}))

        assert items[0].status == ConsumptionStatus.COMPLETED

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

    def test_fetch_status_blank_defaults_to_unread(
        self, plugin: StorygraphCsvPlugin, tmp_path: Path
    ) -> None:
        """Test a blank read status falls back to UNREAD."""
        rows = "No Status,Some Author," + "," * 20 + "\n"
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

    def test_fetch_rating_three_quarter_rounds_up(
        self, plugin: StorygraphCsvPlugin, tmp_path: Path
    ) -> None:
        """Test a .75 rating rounds up (3.75 -> 4)."""
        rows = "Three Quarter,Some Author,,,,read," + "," * 11 + "3.75\n"
        csv_file = self._write(tmp_path, rows)

        items = list(plugin.fetch({"path": str(csv_file)}))

        assert items[0].rating == 4

    def test_fetch_rating_integer(
        self, plugin: StorygraphCsvPlugin, tmp_path: Path
    ) -> None:
        """Test an integer rating passes through unchanged."""
        rows = "Whole Star,Some Author,,,,read," + "," * 11 + "5\n"
        csv_file = self._write(tmp_path, rows)

        items = list(plugin.fetch({"path": str(csv_file)}))

        assert items[0].rating == 5

    def test_fetch_rating_zero_is_none(
        self, plugin: StorygraphCsvPlugin, tmp_path: Path
    ) -> None:
        """Test a zero rating becomes None (model requires 1-5 or None)."""
        rows = "Unrated,Some Author,,,,read," + "," * 11 + "0\n"
        csv_file = self._write(tmp_path, rows)

        items = list(plugin.fetch({"path": str(csv_file)}))

        assert items[0].rating is None

    def test_fetch_rating_blank_is_none(
        self, plugin: StorygraphCsvPlugin, tmp_path: Path
    ) -> None:
        """Test a blank rating becomes None."""
        rows = "No Rating,Some Author,,,,read," + "," * 17 + "\n"
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

    @pytest.mark.parametrize("raw_rating", ["-1", "-0.5"])
    def test_fetch_rating_negative_is_none(
        self, plugin: StorygraphCsvPlugin, tmp_path: Path, raw_rating: str
    ) -> None:
        """Test a negative rating becomes None rather than clamping up to 1."""
        rows = "Negative,Some Author,,,,read," + "," * 11 + f"{raw_rating}\n"
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

    def test_fetch_sets_source_identifier_default(
        self, plugin: StorygraphCsvPlugin, tmp_path: Path
    ) -> None:
        """Test that items use the plugin name as source by default."""
        rows = "A Book,An Author,,,,read," + "," * 17 + "\n"
        csv_file = self._write(tmp_path, rows)

        items = list(plugin.fetch({"path": str(csv_file)}))

        assert items[0].source == "storygraph_csv"

    def test_fetch_sets_source_identifier_override(
        self, plugin: StorygraphCsvPlugin, tmp_path: Path
    ) -> None:
        """Test that a user-defined _source_id overrides the plugin name."""
        rows = "A Book,An Author,,,,read," + "," * 17 + "\n"
        csv_file = self._write(tmp_path, rows)

        items = list(
            plugin.fetch({"path": str(csv_file), "_source_id": "my_storygraph"})
        )

        assert items[0].source == "my_storygraph"

    def test_fetch_file_not_found_raises_source_error(
        self, plugin: StorygraphCsvPlugin
    ) -> None:
        """Test that fetching a nonexistent file raises SourceError."""
        with pytest.raises(SourceError, match="CSV file not found") as exc_info:
            list(plugin.fetch({"path": "/nonexistent/library.csv"}))

        assert exc_info.value.plugin_name == "storygraph_csv"

    def test_fetch_oversized_field_raises_source_error(
        self, plugin: StorygraphCsvPlugin, tmp_path: Path
    ) -> None:
        """Test a field exceeding csv.field_size_limit wraps as SourceError.

        Exercises the ``except csv.Error`` branch in ``fetch()``: the csv
        module raises ``_csv.Error`` when a field exceeds the process-wide
        ``field_size_limit``, and ``fetch()`` must translate that into a
        ``SourceError`` carrying the plugin name rather than letting a raw
        ``csv.Error`` escape to the caller.
        """
        oversized_title = "x" * 200
        rows = f"{oversized_title},Author,,,,read," + ("," * 17) + "\n"
        csv_file = self._write(tmp_path, rows)

        original_field_size_limit = csv.field_size_limit()
        csv.field_size_limit(100)
        try:
            with pytest.raises(SourceError, match="Failed to parse CSV") as exc_info:
                list(plugin.fetch({"path": str(csv_file)}))
        finally:
            csv.field_size_limit(original_field_size_limit)

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

    def test_fetch_unicode_title_and_author(
        self, plugin: StorygraphCsvPlugin, tmp_path: Path
    ) -> None:
        """Test unicode characters in title and author survive parsing."""
        rows = "Café Déjà Vu,Émile Zola,,,,read," + "," * 17 + "\n"
        csv_file = self._write(tmp_path, rows)

        items = list(plugin.fetch({"path": str(csv_file)}))

        assert items[0].title == "Café Déjà Vu"
        assert items[0].author == "Émile Zola"

    def test_fetch_quoted_field_with_comma(
        self, plugin: StorygraphCsvPlugin, tmp_path: Path
    ) -> None:
        """Test a quoted field containing a comma is parsed as one value."""
        rows = '"Goodbye, Columbus",Philip Roth,,,,read,' + "," * 17 + "\n"
        csv_file = self._write(tmp_path, rows)

        items = list(plugin.fetch({"path": str(csv_file)}))

        assert items[0].title == "Goodbye, Columbus"
        assert items[0].author == "Philip Roth"

    def test_fetch_empty_author_is_none(
        self, plugin: StorygraphCsvPlugin, tmp_path: Path
    ) -> None:
        """Test that a blank Authors column yields a None author."""
        rows = "No Author Book,,,,,read," + "," * 17 + "\n"
        csv_file = self._write(tmp_path, rows)

        items = list(plugin.fetch({"path": str(csv_file)}))

        assert items[0].author is None

    def test_fetch_header_only_file_yields_nothing(
        self, plugin: StorygraphCsvPlugin, tmp_path: Path
    ) -> None:
        """Test a header-only export (no data rows) yields zero items."""
        csv_file = tmp_path / "library.csv"
        csv_file.write_text(f"{HEADER}\n")

        items = list(plugin.fetch({"path": str(csv_file)}))

        assert items == []

    def test_fetch_truly_empty_file_yields_nothing(
        self, plugin: StorygraphCsvPlugin, tmp_path: Path
    ) -> None:
        """Test a completely empty file (no header, no rows) yields no items."""
        csv_file = tmp_path / "library.csv"
        csv_file.write_text("")

        items = list(plugin.fetch({"path": str(csv_file)}))

        assert items == []

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

    def test_fetch_rating_nan_is_none(
        self, plugin: StorygraphCsvPlugin, tmp_path: Path
    ) -> None:
        """Test a Star Rating of 'nan' is treated as unrated, not a crash.

        ``float('nan')`` parses successfully, so a hand-edited or corrupt
        export carrying 'nan' must round to None rather than propagate an
        exception out of fetch().
        """
        rows = "Not A Number,Author,,,,read," + "," * 11 + "nan\n"
        csv_file = self._write(tmp_path, rows)

        items = list(plugin.fetch({"path": str(csv_file)}))

        assert items[0].rating is None

    def test_fetch_rating_infinity_is_none(
        self, plugin: StorygraphCsvPlugin, tmp_path: Path
    ) -> None:
        """Test a Star Rating of 'inf' is treated as unrated, not a crash.

        ``float('inf')`` parses successfully, so an out-of-range 'inf' must
        clamp/round to None rather than propagate an exception out of fetch().
        """
        rows = "Infinite Stars,Author,,,,read," + "," * 11 + "inf\n"
        csv_file = self._write(tmp_path, rows)

        items = list(plugin.fetch({"path": str(csv_file)}))

        assert items[0].rating is None

    def test_fetch_invokes_progress_callback_per_processed_row(
        self, plugin: StorygraphCsvPlugin, tmp_path: Path
    ) -> None:
        """Test progress_callback fires once per processed row, skipping blanks.

        The callback must report (items_processed_before_this_row, total_rows,
        title) for each row that clears the blank-title check, and must not
        fire for the skipped blank-title row in between.
        """
        rows = (
            "First Book,Author One,,,,read," + "," * 17 + "\n"
            ",Ghost Author,,,,read," + "," * 17 + "\n"
            "Second Book,Author Two,,,,read," + "," * 17 + "\n"
        )
        csv_file = self._write(tmp_path, rows)
        progress_calls: list[tuple[int, int | None, str | None]] = []

        def record_progress(
            items_processed: int, total_items: int | None, current_item: str | None
        ) -> None:
            progress_calls.append((items_processed, total_items, current_item))

        items = list(
            plugin.fetch({"path": str(csv_file)}, progress_callback=record_progress)
        )

        assert len(items) == 2
        assert progress_calls == [
            (0, 3, "First Book"),
            (1, 3, "Second Book"),
        ]


class TestStorygraphCsvPluginDiscovery:
    """Tests that the plugin is auto-discovered through the real registry."""

    def test_plugin_discovered_by_registry_name(self) -> None:
        """Test the registry resolves 'storygraph_csv' to the plugin class.

        This proves auto-discovery works end to end, not just that the class
        is importable — the registry scans src/ingestion/sources/ and must
        register this plugin under its ``name``.
        """
        registry = PluginRegistry()

        plugin = registry.get_plugin("storygraph_csv")

        assert plugin is not None
        assert isinstance(plugin, StorygraphCsvPlugin)
        assert plugin.display_name == "The StoryGraph (CSV Export)"

    def test_plugin_listed_among_book_sources(self) -> None:
        """Test the plugin is discoverable via content-type filtering."""
        registry = PluginRegistry()

        book_plugins = registry.get_plugins_by_content_type(ContentType.BOOK)

        assert "storygraph_csv" in {plugin.name for plugin in book_plugins}
