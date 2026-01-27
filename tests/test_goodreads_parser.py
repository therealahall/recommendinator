"""Tests for Goodreads plugin."""

from datetime import date
from pathlib import Path

import pytest

from src.ingestion.plugin_base import SourceError, SourcePlugin
from src.ingestion.sources.goodreads import GoodreadsPlugin
from src.models.content import ConsumptionStatus, ContentType


@pytest.fixture()
def plugin() -> GoodreadsPlugin:
    """Create a GoodreadsPlugin instance."""
    return GoodreadsPlugin()


class TestGoodreadsPluginProperties:
    """Tests for GoodreadsPlugin metadata properties."""

    def test_is_source_plugin(self, plugin: GoodreadsPlugin) -> None:
        """Test that GoodreadsPlugin is a SourcePlugin subclass."""
        assert isinstance(plugin, SourcePlugin)

    def test_name(self, plugin: GoodreadsPlugin) -> None:
        """Test plugin name identifier."""
        assert plugin.name == "goodreads"

    def test_display_name(self, plugin: GoodreadsPlugin) -> None:
        """Test human-readable display name."""
        assert plugin.display_name == "Goodreads"

    def test_content_types(self, plugin: GoodreadsPlugin) -> None:
        """Test that plugin provides books."""
        assert plugin.content_types == [ContentType.BOOK]

    def test_requires_api_key(self, plugin: GoodreadsPlugin) -> None:
        """Test that plugin does not require an API key."""
        assert plugin.requires_api_key is False

    def test_requires_network(self, plugin: GoodreadsPlugin) -> None:
        """Test that plugin does not require network access."""
        assert plugin.requires_network is False

    def test_config_schema(self, plugin: GoodreadsPlugin) -> None:
        """Test configuration schema defines csv_path."""
        schema = plugin.get_config_schema()

        assert len(schema) == 1
        assert schema[0].name == "csv_path"
        assert schema[0].field_type is str
        assert schema[0].required is True

    def test_get_source_identifier(self, plugin: GoodreadsPlugin) -> None:
        """Test source identifier matches plugin name."""
        assert plugin.get_source_identifier() == "goodreads"

    def test_get_info(self, plugin: GoodreadsPlugin) -> None:
        """Test plugin info includes all metadata."""
        info = plugin.get_info()

        assert info.name == "goodreads"
        assert info.display_name == "Goodreads"
        assert info.content_types == [ContentType.BOOK]
        assert info.requires_api_key is False
        assert info.requires_network is False


class TestGoodreadsPluginValidation:
    """Tests for GoodreadsPlugin config validation."""

    def test_validate_valid_config(
        self, plugin: GoodreadsPlugin, tmp_path: Path
    ) -> None:
        """Test validation passes with valid CSV path."""
        csv_file = tmp_path / "books.csv"
        csv_file.write_text("header\n")

        errors = plugin.validate_config({"csv_path": str(csv_file)})

        assert errors == []

    def test_validate_missing_csv_path(self, plugin: GoodreadsPlugin) -> None:
        """Test validation fails when csv_path is missing."""
        errors = plugin.validate_config({})

        assert len(errors) == 1
        assert "'csv_path' is required" in errors[0]

    def test_validate_empty_csv_path(self, plugin: GoodreadsPlugin) -> None:
        """Test validation fails when csv_path is empty."""
        errors = plugin.validate_config({"csv_path": ""})

        assert len(errors) == 1
        assert "'csv_path' is required" in errors[0]

    def test_validate_nonexistent_file(self, plugin: GoodreadsPlugin) -> None:
        """Test validation fails when CSV file does not exist."""
        errors = plugin.validate_config({"csv_path": "/nonexistent/books.csv"})

        assert len(errors) == 1
        assert "CSV file not found" in errors[0]


class TestGoodreadsPluginFetch:
    """Tests for GoodreadsPlugin.fetch()."""

    def test_fetch_basic(self, plugin: GoodreadsPlugin, tmp_path: Path) -> None:
        """Test basic CSV parsing through the plugin interface."""
        csv_content = """Book Id,Title,Author,My Rating,Exclusive Shelf,Date Read,My Review
123,Test Book,Test Author,4,read,2025/01/15,Great book!
456,Another Book,Another Author,0,to-read,,
"""
        csv_file = tmp_path / "books.csv"
        csv_file.write_text(csv_content)

        items = list(plugin.fetch({"csv_path": str(csv_file)}))

        assert len(items) == 2

        # First item (completed)
        assert items[0].title == "Test Book"
        assert items[0].author == "Test Author"
        assert items[0].rating == 4
        assert items[0].status == ConsumptionStatus.COMPLETED
        assert items[0].date_completed == date(2025, 1, 15)
        assert items[0].review == "Great book!"
        assert items[0].content_type == ContentType.BOOK
        assert items[0].source == "goodreads"

        # Second item (unread)
        assert items[1].title == "Another Book"
        assert items[1].author == "Another Author"
        assert items[1].rating is None
        assert items[1].status == ConsumptionStatus.UNREAD
        assert items[1].date_completed is None
        assert items[1].review is None

    def test_fetch_currently_reading(
        self, plugin: GoodreadsPlugin, tmp_path: Path
    ) -> None:
        """Test parsing of currently-reading status."""
        csv_content = """Book Id,Title,Author,My Rating,Exclusive Shelf,Date Read
789,Reading Now,Author Name,0,currently-reading,
"""
        csv_file = tmp_path / "books.csv"
        csv_file.write_text(csv_content)

        items = list(plugin.fetch({"csv_path": str(csv_file)}))

        assert len(items) == 1
        assert items[0].status == ConsumptionStatus.CURRENTLY_CONSUMING

    def test_fetch_empty_title_skipped(
        self, plugin: GoodreadsPlugin, tmp_path: Path
    ) -> None:
        """Test that rows with empty titles are skipped."""
        csv_content = """Book Id,Title,Author,My Rating,Exclusive Shelf
123,,Test Author,4,read
456,Valid Book,Author,4,read
"""
        csv_file = tmp_path / "books.csv"
        csv_file.write_text(csv_content)

        items = list(plugin.fetch({"csv_path": str(csv_file)}))

        assert len(items) == 1
        assert items[0].title == "Valid Book"

    def test_fetch_sets_source_identifier(
        self, plugin: GoodreadsPlugin, tmp_path: Path
    ) -> None:
        """Test that fetched items have the correct source identifier."""
        csv_content = """Book Id,Title,Author,My Rating,Exclusive Shelf
123,A Book,An Author,5,read
"""
        csv_file = tmp_path / "books.csv"
        csv_file.write_text(csv_content)

        items = list(plugin.fetch({"csv_path": str(csv_file)}))

        assert items[0].source == plugin.get_source_identifier()

    def test_fetch_file_not_found_raises_source_error(
        self, plugin: GoodreadsPlugin
    ) -> None:
        """Test that fetching a nonexistent file raises SourceError."""
        with pytest.raises(SourceError) as exc_info:
            list(plugin.fetch({"csv_path": "/nonexistent/books.csv"}))

        assert exc_info.value.plugin_name == "goodreads"
        assert "CSV file not found" in exc_info.value.message

    def test_fetch_metadata(self, plugin: GoodreadsPlugin, tmp_path: Path) -> None:
        """Test that metadata fields are populated correctly."""
        csv_content = (
            "Book Id,Title,Author,My Rating,Exclusive Shelf,"
            "ISBN,ISBN13,Number of Pages,Year Published,Publisher\n"
            '123,Test Book,Test Author,4,read,"=""1234567890=""","=""9781234567890=""",'
            "350,2020,Test Publisher\n"
        )
        csv_file = tmp_path / "books.csv"
        csv_file.write_text(csv_content)

        items = list(plugin.fetch({"csv_path": str(csv_file)}))

        assert len(items) == 1
        assert items[0].metadata["book_id"] == "123"
        assert items[0].metadata["pages"] == "350"
        assert items[0].metadata["year_published"] == "2020"
        assert items[0].metadata["publisher"] == "Test Publisher"

    def test_fetch_invalid_rating(
        self, plugin: GoodreadsPlugin, tmp_path: Path
    ) -> None:
        """Test that invalid ratings are treated as None."""
        csv_content = """Book Id,Title,Author,My Rating,Exclusive Shelf
123,Test Book,Test Author,invalid,read
"""
        csv_file = tmp_path / "books.csv"
        csv_file.write_text(csv_content)

        items = list(plugin.fetch({"csv_path": str(csv_file)}))

        assert items[0].rating is None

    def test_fetch_invalid_date(self, plugin: GoodreadsPlugin, tmp_path: Path) -> None:
        """Test that invalid dates are treated as None."""
        csv_content = """Book Id,Title,Author,My Rating,Exclusive Shelf,Date Read
123,Test Book,Test Author,4,read,not-a-date
"""
        csv_file = tmp_path / "books.csv"
        csv_file.write_text(csv_content)

        items = list(plugin.fetch({"csv_path": str(csv_file)}))

        assert items[0].date_completed is None
