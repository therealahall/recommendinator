"""Tests for Goodreads CSV parser."""

from datetime import date
from pathlib import Path

from src.ingestion.sources.goodreads import parse_goodreads_csv
from src.models.content import ConsumptionStatus, ContentType


def test_parse_goodreads_csv_basic(tmp_path: Path) -> None:
    """Test basic parsing of Goodreads CSV."""
    # Create a test CSV file
    csv_content = """Book Id,Title,Author,My Rating,Exclusive Shelf,Date Read,My Review
123,Test Book,Test Author,4,read,2025/01/15,Great book!
456,Another Book,Another Author,0,to-read,,
"""
    csv_file = tmp_path / "test.csv"
    csv_file.write_text(csv_content)

    # Parse the file
    items = list(parse_goodreads_csv(csv_file))

    # Check results
    assert len(items) == 2

    # First item (completed)
    assert items[0].title == "Test Book"
    assert items[0].author == "Test Author"
    assert items[0].rating == 4
    assert items[0].status == ConsumptionStatus.COMPLETED
    assert items[0].date_completed == date(2025, 1, 15)
    assert items[0].review == "Great book!"
    assert items[0].content_type == ContentType.BOOK

    # Second item (unread)
    assert items[1].title == "Another Book"
    assert items[1].author == "Another Author"
    assert items[1].rating is None
    assert items[1].status == ConsumptionStatus.UNREAD
    assert items[1].date_completed is None
    assert items[1].review is None


def test_parse_goodreads_csv_currently_reading(tmp_path: Path) -> None:
    """Test parsing of currently-reading status."""
    csv_content = """Book Id,Title,Author,My Rating,Exclusive Shelf,Date Read
789,Reading Now,Author Name,0,currently-reading,
"""
    csv_file = tmp_path / "test.csv"
    csv_file.write_text(csv_content)

    items = list(parse_goodreads_csv(csv_file))
    assert len(items) == 1
    assert items[0].status == ConsumptionStatus.CURRENTLY_CONSUMING


def test_parse_goodreads_csv_empty_title_skipped(tmp_path: Path) -> None:
    """Test that rows with empty titles are skipped."""
    csv_content = """Book Id,Title,Author,My Rating,Exclusive Shelf
123,,Test Author,4,read
456,Valid Book,Author,4,read
"""
    csv_file = tmp_path / "test.csv"
    csv_file.write_text(csv_content)

    items = list(parse_goodreads_csv(csv_file))
    assert len(items) == 1
    assert items[0].title == "Valid Book"
