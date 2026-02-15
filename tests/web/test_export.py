"""Tests for export functionality."""

import csv
import io
import json

from src.models.content import ConsumptionStatus, ContentItem, ContentType
from src.web.export import export_items_csv, export_items_json


class TestExportSerialization:
    """Tests for export serialization functions."""

    def test_export_csv_books(self) -> None:
        """Test CSV export for books."""
        items = [
            ContentItem(
                id="1",
                title="The Name of the Wind",
                author="Patrick Rothfuss",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.COMPLETED,
                rating=5,
                metadata={"isbn": "978-0756404741", "genre": "Fantasy"},
            ),
        ]
        result = export_items_csv(items, ContentType.BOOK)

        reader = csv.DictReader(io.StringIO(result))
        rows = list(reader)
        assert len(rows) == 1
        assert rows[0]["title"] == "The Name of the Wind"
        assert rows[0]["author"] == "Patrick Rothfuss"
        assert rows[0]["rating"] == "5"
        assert rows[0]["ignored"] == "false"
        assert rows[0]["isbn"] == "978-0756404741"

    def test_export_csv_tv_show_with_seasons_watched(self) -> None:
        """Test CSV export for TV shows with seasons_watched list."""
        items = [
            ContentItem(
                id="1",
                title="Breaking Bad",
                author="Vince Gilligan",
                content_type=ContentType.TV_SHOW,
                status=ConsumptionStatus.COMPLETED,
                rating=5,
                metadata={
                    "seasons_watched": [1, 2, 5, 6],
                    "total_seasons": "6",
                    "genre": "Drama",
                },
            ),
        ]
        result = export_items_csv(items, ContentType.TV_SHOW)

        reader = csv.DictReader(io.StringIO(result))
        rows = list(reader)
        assert len(rows) == 1
        assert rows[0]["seasons_watched"] == "1,2,5,6"
        assert rows[0]["total_seasons"] == "6"

    def test_export_csv_ignored_item(self) -> None:
        """Test that ignored=True is exported correctly."""
        items = [
            ContentItem(
                id="1",
                title="Ignored Book",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.UNREAD,
                ignored=True,
                metadata={},
            ),
        ]
        result = export_items_csv(items, ContentType.BOOK)

        reader = csv.DictReader(io.StringIO(result))
        rows = list(reader)
        assert rows[0]["ignored"] == "true"

    def test_export_json_books(self) -> None:
        """Test JSON export for books."""
        items = [
            ContentItem(
                id="1",
                title="The Name of the Wind",
                author="Patrick Rothfuss",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.COMPLETED,
                rating=5,
                metadata={"isbn": "978-0756404741", "genre": "Fantasy"},
            ),
        ]
        result = export_items_json(items, ContentType.BOOK)

        entries = json.loads(result)
        assert len(entries) == 1
        assert entries[0]["title"] == "The Name of the Wind"
        assert entries[0]["author"] == "Patrick Rothfuss"
        assert entries[0]["rating"] == 5
        assert entries[0]["ignored"] is False
        assert entries[0]["isbn"] == "978-0756404741"

    def test_export_json_tv_show_with_seasons_watched(self) -> None:
        """Test JSON export for TV shows with seasons_watched as array."""
        items = [
            ContentItem(
                id="1",
                title="Breaking Bad",
                author="Vince Gilligan",
                content_type=ContentType.TV_SHOW,
                status=ConsumptionStatus.COMPLETED,
                rating=5,
                metadata={
                    "seasons_watched": [1, 2, 5, 6],
                    "total_seasons": "6",
                    "genre": "Drama",
                },
            ),
        ]
        result = export_items_json(items, ContentType.TV_SHOW)

        entries = json.loads(result)
        assert entries[0]["seasons_watched"] == [1, 2, 5, 6]

    def test_export_json_ignored_item(self) -> None:
        """Test that ignored=True is exported as boolean in JSON."""
        items = [
            ContentItem(
                id="1",
                title="Ignored Book",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.UNREAD,
                ignored=True,
                metadata={},
            ),
        ]
        result = export_items_json(items, ContentType.BOOK)

        entries = json.loads(result)
        assert entries[0]["ignored"] is True

    def test_export_csv_empty_items(self) -> None:
        """Test CSV export with no items produces header only."""
        result = export_items_csv([], ContentType.BOOK)
        lines = result.strip().split("\n")
        assert len(lines) == 1  # Header only
        assert "title" in lines[0]

    def test_export_json_empty_items(self) -> None:
        """Test JSON export with no items produces empty array."""
        result = export_items_json([], ContentType.BOOK)
        entries = json.loads(result)
        assert entries == []


class TestExportRoundtrip:
    """Tests that exported data can be re-imported identically."""

    def test_csv_roundtrip_book(self) -> None:
        """Export a book to CSV, re-import it, verify fields match."""
        from src.ingestion.sources.generic_csv import CsvImportPlugin

        original = ContentItem(
            id="rt1",
            title="Roundtrip Book",
            author="Test Author",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            rating=4,
            ignored=True,
            metadata={"genre": "Fantasy"},
        )

        csv_content = export_items_csv([original], ContentType.BOOK)

        # Write to temp file and re-import
        import tempfile
        from pathlib import Path

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".csv", delete=False
        ) as temp_file:
            temp_file.write(csv_content)
            temp_path = temp_file.name

        plugin = CsvImportPlugin()
        reimported = list(plugin.fetch({"path": temp_path, "content_type": "book"}))

        Path(temp_path).unlink()

        assert len(reimported) == 1
        assert reimported[0].title == original.title
        assert reimported[0].author == original.author
        assert reimported[0].rating == original.rating
        assert reimported[0].ignored is True

    def test_json_roundtrip_tv_show_with_seasons(self) -> None:
        """Export a TV show with seasons_watched to JSON, re-import, verify."""
        from src.ingestion.sources.generic_json import JsonImportPlugin

        original = ContentItem(
            id="rt2",
            title="Roundtrip Show",
            author="Test Creator",
            content_type=ContentType.TV_SHOW,
            status=ConsumptionStatus.COMPLETED,
            rating=5,
            ignored=False,
            metadata={
                "seasons_watched": [1, 2, 5, 6],
                "total_seasons": 8,
                "genre": "Drama",
            },
        )

        json_content = export_items_json([original], ContentType.TV_SHOW)

        import tempfile
        from pathlib import Path

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as temp_file:
            temp_file.write(json_content)
            temp_path = temp_file.name

        plugin = JsonImportPlugin()
        reimported = list(plugin.fetch({"path": temp_path, "content_type": "tv_show"}))

        Path(temp_path).unlink()

        assert len(reimported) == 1
        assert reimported[0].title == original.title
        assert reimported[0].author == original.author
        assert reimported[0].metadata["seasons_watched"] == [1, 2, 5, 6]
        assert reimported[0].ignored is False
