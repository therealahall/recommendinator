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

    def test_export_csv_empty_items_has_correct_book_headers(self) -> None:
        """Test CSV export with empty book list has all expected book columns."""
        result = export_items_csv([], ContentType.BOOK)
        reader = csv.DictReader(io.StringIO(result))
        assert reader.fieldnames is not None
        expected_columns = [
            "title",
            "author",
            "rating",
            "status",
            "date_completed",
            "review",
            "notes",
            "isbn",
            "pages",
            "year_published",
            "genre",
            "ignored",
        ]
        assert list(reader.fieldnames) == expected_columns
        rows = list(reader)
        assert len(rows) == 0

    def test_export_csv_empty_items_movie_headers(self) -> None:
        """Test CSV export with empty movie list has correct movie columns."""
        result = export_items_csv([], ContentType.MOVIE)
        reader = csv.DictReader(io.StringIO(result))
        assert reader.fieldnames is not None
        assert "director" in reader.fieldnames
        assert "runtime_minutes" in reader.fieldnames
        assert "author" not in reader.fieldnames
        rows = list(reader)
        assert len(rows) == 0

    def test_export_csv_empty_items_tv_show_headers(self) -> None:
        """Test CSV export with empty TV show list has correct TV show columns."""
        result = export_items_csv([], ContentType.TV_SHOW)
        reader = csv.DictReader(io.StringIO(result))
        assert reader.fieldnames is not None
        assert "creator" in reader.fieldnames
        assert "seasons_watched" in reader.fieldnames
        assert "total_seasons" in reader.fieldnames
        rows = list(reader)
        assert len(rows) == 0

    def test_export_csv_empty_items_video_game_headers(self) -> None:
        """Test CSV export with empty video game list has correct columns."""
        result = export_items_csv([], ContentType.VIDEO_GAME)
        reader = csv.DictReader(io.StringIO(result))
        assert reader.fieldnames is not None
        assert "developer" in reader.fieldnames
        assert "platform" in reader.fieldnames
        assert "hours_played" in reader.fieldnames
        rows = list(reader)
        assert len(rows) == 0

    def test_export_json_empty_items(self) -> None:
        """Test JSON export with no items produces empty array."""
        result = export_items_json([], ContentType.BOOK)
        entries = json.loads(result)
        assert entries == []

    def test_export_json_empty_items_is_valid_json_array(self) -> None:
        """Test JSON export with no items is a parseable JSON array for all types."""
        for content_type in ContentType:
            result = export_items_json([], content_type)
            entries = json.loads(result)
            assert isinstance(entries, list)
            assert len(entries) == 0

    def test_export_json_multiple_items(self) -> None:
        """Test JSON export with multiple items preserves all entries."""
        items = [
            ContentItem(
                id="1",
                title="Dune",
                author="Frank Herbert",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.COMPLETED,
                rating=5,
                metadata={"genre": "Science Fiction"},
            ),
            ContentItem(
                id="2",
                title="Neuromancer",
                author="William Gibson",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.COMPLETED,
                rating=4,
                metadata={"genre": "Cyberpunk"},
            ),
            ContentItem(
                id="3",
                title="The Left Hand of Darkness",
                author="Ursula K. Le Guin",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.COMPLETED,
                rating=5,
                metadata={"isbn": "978-0441478125"},
            ),
        ]
        result = export_items_json(items, ContentType.BOOK)
        entries = json.loads(result)

        assert len(entries) == 3
        assert entries[0]["title"] == "Dune"
        assert entries[1]["title"] == "Neuromancer"
        assert entries[2]["title"] == "The Left Hand of Darkness"
        assert entries[0]["rating"] == 5
        assert entries[1]["rating"] == 4
        assert entries[2]["isbn"] == "978-0441478125"

    def test_export_json_item_with_no_rating(self) -> None:
        """Test JSON export handles None rating correctly."""
        items = [
            ContentItem(
                id="1",
                title="Unrated Book",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.CURRENTLY_CONSUMING,
                metadata={},
            ),
        ]
        result = export_items_json(items, ContentType.BOOK)
        entries = json.loads(result)

        assert len(entries) == 1
        assert entries[0]["title"] == "Unrated Book"
        assert entries[0]["rating"] is None

    def test_export_json_item_with_no_author(self) -> None:
        """Test JSON export handles None author as empty string."""
        items = [
            ContentItem(
                id="1",
                title="Anonymous Work",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.COMPLETED,
                metadata={},
            ),
        ]
        result = export_items_json(items, ContentType.BOOK)
        entries = json.loads(result)

        assert entries[0]["author"] == ""

    def test_export_json_video_game(self) -> None:
        """Test JSON export for video games uses developer field."""
        items = [
            ContentItem(
                id="1",
                title="Elden Ring",
                author="FromSoftware",
                content_type=ContentType.VIDEO_GAME,
                status=ConsumptionStatus.COMPLETED,
                rating=5,
                metadata={
                    "platform": "PC",
                    "genre": "Action RPG",
                    "hours_played": "120",
                },
            ),
        ]
        result = export_items_json(items, ContentType.VIDEO_GAME)
        entries = json.loads(result)

        assert len(entries) == 1
        assert entries[0]["developer"] == "FromSoftware"
        assert entries[0]["platform"] == "PC"
        assert entries[0]["hours_played"] == "120"
        assert "author" not in entries[0]

    def test_export_json_movie(self) -> None:
        """Test JSON export for movies uses director field."""
        items = [
            ContentItem(
                id="1",
                title="Blade Runner 2049",
                author="Denis Villeneuve",
                content_type=ContentType.MOVIE,
                status=ConsumptionStatus.COMPLETED,
                rating=5,
                metadata={"year": "2017", "runtime_minutes": "164", "genre": "Sci-Fi"},
            ),
        ]
        result = export_items_json(items, ContentType.MOVIE)
        entries = json.loads(result)

        assert len(entries) == 1
        assert entries[0]["director"] == "Denis Villeneuve"
        assert entries[0]["year"] == "2017"
        assert entries[0]["runtime_minutes"] == "164"
        assert "author" not in entries[0]

    def test_export_csv_item_with_no_rating(self) -> None:
        """Test CSV export handles None rating as empty string."""
        items = [
            ContentItem(
                id="1",
                title="Unrated Book",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.CURRENTLY_CONSUMING,
                metadata={},
            ),
        ]
        result = export_items_csv(items, ContentType.BOOK)
        reader = csv.DictReader(io.StringIO(result))
        rows = list(reader)

        assert len(rows) == 1
        assert rows[0]["title"] == "Unrated Book"
        assert rows[0]["rating"] == ""

    def test_export_csv_multiple_items(self) -> None:
        """Test CSV export with multiple items produces correct row count."""
        items = [
            ContentItem(
                id=str(index),
                title=f"Book {index}",
                author=f"Author {index}",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.COMPLETED,
                rating=index,
                metadata={},
            )
            for index in range(1, 4)
        ]
        result = export_items_csv(items, ContentType.BOOK)
        reader = csv.DictReader(io.StringIO(result))
        rows = list(reader)

        assert len(rows) == 3
        assert rows[0]["title"] == "Book 1"
        assert rows[1]["title"] == "Book 2"
        assert rows[2]["title"] == "Book 3"
        assert rows[0]["rating"] == "1"
        assert rows[2]["rating"] == "3"

    def test_export_csv_video_game(self) -> None:
        """Test CSV export for video games uses developer column."""
        items = [
            ContentItem(
                id="1",
                title="Hollow Knight",
                author="Team Cherry",
                content_type=ContentType.VIDEO_GAME,
                status=ConsumptionStatus.COMPLETED,
                rating=5,
                metadata={"platform": "PC", "hours_played": "45"},
            ),
        ]
        result = export_items_csv(items, ContentType.VIDEO_GAME)
        reader = csv.DictReader(io.StringIO(result))
        rows = list(reader)

        assert len(rows) == 1
        assert rows[0]["developer"] == "Team Cherry"
        assert rows[0]["platform"] == "PC"
        assert rows[0]["hours_played"] == "45"
        assert "author" not in reader.fieldnames  # type: ignore[operator]

    def test_export_csv_movie(self) -> None:
        """Test CSV export for movies uses director column."""
        items = [
            ContentItem(
                id="1",
                title="Arrival",
                author="Denis Villeneuve",
                content_type=ContentType.MOVIE,
                status=ConsumptionStatus.COMPLETED,
                rating=5,
                metadata={"year": "2016", "runtime_minutes": "116"},
            ),
        ]
        result = export_items_csv(items, ContentType.MOVIE)
        reader = csv.DictReader(io.StringIO(result))
        rows = list(reader)

        assert len(rows) == 1
        assert rows[0]["director"] == "Denis Villeneuve"
        assert rows[0]["year"] == "2016"
        assert "author" not in reader.fieldnames  # type: ignore[operator]


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
