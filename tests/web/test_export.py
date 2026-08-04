"""Tests for export functionality."""

import csv
import io
import json
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from src.ingestion.sources.arr_base import ArrPlugin
from src.ingestion.sources.generic_csv import (
    COMMON_COLUMNS,
    CONTENT_TYPE_COLUMNS,
    CsvImportPlugin,
)
from src.ingestion.sources.generic_json import JsonImportPlugin
from src.ingestion.sources.gog.gog import GogPlugin
from src.ingestion.sources.radarr.radarr import RadarrPlugin
from src.ingestion.sources.sonarr.sonarr import SonarrPlugin
from src.ingestion.sources.steam.steam import SteamPlugin
from src.models.content import ConsumptionStatus, ContentItem, ContentType
from src.storage.sqlite_db import SQLiteDB
from src.web.export import _CSV_COLUMN_ORDER, export_items_csv, export_items_json


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
                metadata={"isbn": "978-0756404741", "genres": ["Fantasy"]},
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
                    "seasons": "6",
                    "genres": ["Drama"],
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
                metadata={"isbn": "978-0756404741", "genres": ["Fantasy"]},
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
                    "seasons": "6",
                    "genres": ["Drama"],
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
                metadata={"genres": ["Science Fiction"]},
            ),
            ContentItem(
                id="2",
                title="Neuromancer",
                author="William Gibson",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.COMPLETED,
                rating=4,
                metadata={"genres": ["Cyberpunk"]},
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
                    "platforms": ["PC"],
                    "genres": ["Action RPG"],
                    "playtime_hours": "120",
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
                metadata={
                    "release_year": "2017",
                    "runtime": "164",
                    "genres": ["Sci-Fi"],
                },
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
                metadata={"platforms": ["PC"], "playtime_hours": "45"},
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
                metadata={"release_year": "2016", "runtime": "116"},
            ),
        ]
        result = export_items_csv(items, ContentType.MOVIE)
        reader = csv.DictReader(io.StringIO(result))
        rows = list(reader)

        assert len(rows) == 1
        assert rows[0]["director"] == "Denis Villeneuve"
        assert rows[0]["year"] == "2016"
        assert "author" not in reader.fieldnames  # type: ignore[operator]


def _store_and_read_back(tmp_path: Path, item: ContentItem) -> ContentItem:
    """Save an item through a real database and read it back.

    The returned item carries the metadata keys storage exposes, which is
    what the exporter sees in production and what a hand-built metadata dict
    in a test does not.
    """
    database = SQLiteDB(tmp_path / "export.db")
    db_id = database.save_content_item(item)
    stored = database.get_content_item(db_id)
    assert stored is not None
    return stored


def _arr_item(plugin: ArrPlugin, payload: dict[str, Any]) -> ContentItem:
    """Build the item an *arr plugin yields for *payload*, minus the HTTP call.

    The metadata comes from the plugin's own extractor, so this fixture
    cannot claim a key Radarr or Sonarr does not write.
    """
    return ContentItem(
        id=plugin.build_external_id(payload),
        title=payload["title"],
        content_type=plugin.arr_content_type,
        status=ConsumptionStatus.UNREAD,
        source=plugin.name,
        metadata=plugin.build_metadata(payload),
    )


def _gog_game(product: dict[str, Any]) -> ContentItem:
    """Fetch the item the GOG plugin yields for *product*, minus the network."""
    with (
        patch(
            "src.ingestion.sources.gog.gog.refresh_access_token",
            return_value={"access_token": "access", "refresh_token": "refresh"},
        ),
        patch("src.ingestion.sources.gog.gog.get_owned_games", return_value=[product]),
    ):
        return next(
            GogPlugin().fetch({"refresh_token": "token", "include_wishlist": False})
        )


def _gog_wishlist_game(product_id: int, details: dict[str, Any]) -> ContentItem:
    """Fetch the item GOG's wishlist path yields, minus the network."""
    with (
        patch(
            "src.ingestion.sources.gog.gog.refresh_access_token",
            return_value={"access_token": "access", "refresh_token": "refresh"},
        ),
        patch("src.ingestion.sources.gog.gog.get_owned_games", return_value=[]),
        patch(
            "src.ingestion.sources.gog.gog.get_wishlist_product_ids",
            return_value=[product_id],
        ),
        patch(
            "src.ingestion.sources.gog.gog.get_multiple_product_details",
            return_value={product_id: details},
        ),
    ):
        return next(
            GogPlugin().fetch({"refresh_token": "token", "include_wishlist": True})
        )


def _steam_game(game: dict[str, Any]) -> ContentItem:
    """Fetch the item the Steam plugin yields for *game*, minus the network."""
    with patch(
        "src.ingestion.sources.steam.steam.get_owned_games", return_value=[game]
    ):
        return next(
            SteamPlugin().fetch({"api_key": "key", "steam_id": "76561197960287930"})
        )


def _csv_book(tmp_path: Path, csv_content: str) -> ContentItem:
    """Fetch the item the CSV importer yields for a one-row book file."""
    csv_path = tmp_path / "books.csv"
    csv_path.write_text(csv_content, encoding="utf-8")
    return next(
        CsvImportPlugin().fetch({"path": str(csv_path), "content_type": "book"})
    )


class TestExportOfStoredItems:
    """Export of items the shipped sources actually produce.

    Bug reported: the year, runtime, total_seasons and platform columns came
    out blank for every library item that did not originate from a generic
    CSV/JSON import — a Radarr movie, a Sonarr show, a GOG game — so the
    documented export/edit/re-import round trip silently dropped them.

    Root cause: two mismatched vocabularies. The exporter looked each
    type-specific value up in ``item.metadata`` under the *template* column
    name (``year``, ``runtime_minutes``, ``total_seasons``, ``platform``),
    while an item read back from storage carries the detail-table keys
    (``release_year``, ``runtime``, ``seasons``, ``platforms``) — and the
    detail tables in turn only recognised the canonical key, so the values
    the *arr and Trakt plugins write (``year``, ``runtime_minutes``) never
    reached their columns, which stayed NULL.

    Fix: ``CONTENT_TYPE_COLUMNS`` names the metadata key each template column
    carries and the exporter reads that key, and the detail-table config
    accepts each plugin spelling as an alias of its column.

    Every fixture below is produced by the named plugin rather than written
    by hand, because a hand-written dict is what let both halves of this bug
    pass their tests: they asserted keys the sources they were labelled with
    never emit.
    """

    def test_movie_export_includes_year_and_runtime_regression(
        self, tmp_path: Path
    ) -> None:
        """A stored Radarr movie exports its release year and runtime."""
        stored = _store_and_read_back(
            tmp_path,
            _arr_item(
                RadarrPlugin(),
                {
                    "title": "Inception",
                    "tmdbId": 27205,
                    "imdbId": "tt1375666",
                    "year": 2010,
                    "runtime": 148,
                    "studio": "Warner Bros. Pictures",
                    "genres": ["Action", "Sci-Fi"],
                },
            ),
        )

        rows = list(
            csv.DictReader(io.StringIO(export_items_csv([stored], ContentType.MOVIE)))
        )

        assert rows[0]["year"] == "2010"
        assert rows[0]["runtime_minutes"] == "148"
        assert rows[0]["genre"] == "Action"

    def test_tv_export_includes_total_seasons_regression(self, tmp_path: Path) -> None:
        """A stored Sonarr show exports its season count and year."""
        stored = _store_and_read_back(
            tmp_path,
            _arr_item(
                SonarrPlugin(),
                {
                    "title": "Breaking Bad",
                    "tvdbId": 81189,
                    "imdbId": "tt0903747",
                    "year": 2008,
                    "network": "AMC",
                    "genres": ["Drama", "Crime"],
                    "statistics": {"seasonCount": 5, "episodeCount": 62},
                },
            ),
        )

        rows = list(
            csv.DictReader(io.StringIO(export_items_csv([stored], ContentType.TV_SHOW)))
        )

        assert rows[0]["total_seasons"] == "5"
        assert rows[0]["year"] == "2008"

    def test_game_export_includes_platform_regression(self, tmp_path: Path) -> None:
        """A stored GOG game exports a platform name, not a flag dict."""
        stored = _store_and_read_back(
            tmp_path,
            _gog_game(
                {
                    "id": 1207658924,
                    "title": "The Witcher 3: Wild Hunt",
                    "slug": "the_witcher_3_wild_hunt",
                    "genres": ["Role-playing"],
                    "worksOn": {"Windows": True, "Mac": False, "Linux": True},
                }
            ),
        )

        rows = list(
            csv.DictReader(
                io.StringIO(export_items_csv([stored], ContentType.VIDEO_GAME))
            )
        )

        assert rows[0]["platform"] == "Windows"
        assert rows[0]["genre"] == "Role-playing"

    def test_game_export_includes_playtime(self, tmp_path: Path) -> None:
        """A stored Steam game exports the hours it recorded."""
        stored = _store_and_read_back(
            tmp_path,
            _steam_game(
                {
                    "appid": 292030,
                    "name": "The Witcher 3: Wild Hunt",
                    "playtime_forever": 7200,
                }
            ),
        )

        rows = list(
            csv.DictReader(
                io.StringIO(export_items_csv([stored], ContentType.VIDEO_GAME))
            )
        )

        assert rows[0]["hours_played"] == "120.0"

    def test_book_export_includes_isbn_pages_and_year(self, tmp_path: Path) -> None:
        """The book columns whose names already matched keep working."""
        stored = _store_and_read_back(
            tmp_path,
            _csv_book(
                tmp_path,
                "title,author,rating,status,isbn,pages,year_published,genre\n"
                "The Name of the Wind,Patrick Rothfuss,5,read,"
                "978-0756404741,662,2007,Fantasy\n",
            ),
        )

        rows = list(
            csv.DictReader(io.StringIO(export_items_csv([stored], ContentType.BOOK)))
        )

        assert rows[0]["isbn"] == "978-0756404741"
        assert rows[0]["pages"] == "662"
        assert rows[0]["year_published"] == "2007"
        assert rows[0]["genre"] == "Fantasy"


class TestCreatorSurvivesStorage:
    """The creator column round-trips for every content type, not just books.

    Bug reported: a movie, TV show or video game exported a blank
    director/creator/developer cell, and a row imported from the documented
    template lost the value outright. Books were unaffected.

    Root cause: the creator was a special case rather than an ordinary
    column. The write path only took ``item.author`` for a book, so an
    imported director never reached ``movie_details.director``; the read path
    only produced ``ContentItem.author`` for a book, so the exporter — which
    writes the creator cell from ``item.author`` — had nothing to write even
    for the TMDB-enriched items whose column was filled. On top of that the
    tv_show template column ``creator`` was declared against a metadata key
    of the same name, while storage stores ``creators``.

    Fix: every content type declares one ``FieldKind.CREATOR`` column, and
    the tv_show template column maps to the stored ``creators`` key.

    Every fixture below goes through storage, because an item built by hand
    with ``author`` already set is what let this bug pass a green suite.
    """

    @pytest.mark.parametrize(
        ("content_type", "creator_column", "creator"),
        [
            (ContentType.BOOK, "author", "Patrick Rothfuss"),
            (ContentType.MOVIE, "director", "Denis Villeneuve"),
            (ContentType.TV_SHOW, "creator", "Vince Gilligan"),
            (ContentType.VIDEO_GAME, "developer", "Team Cherry"),
        ],
        ids=["book", "movie", "tv_show", "video_game"],
    )
    def test_imported_creator_exports_after_storage_regression(
        self,
        tmp_path: Path,
        content_type: ContentType,
        creator_column: str,
        creator: str,
    ) -> None:
        """A template row's creator survives import, storage and export."""
        csv_path = tmp_path / f"{content_type.value}.csv"
        csv_path.write_text(
            f"title,{creator_column},status\nRound Trip,{creator},unread\n",
            encoding="utf-8",
        )
        imported = next(
            CsvImportPlugin().fetch(
                {"path": str(csv_path), "content_type": content_type.value}
            )
        )

        stored = _store_and_read_back(tmp_path, imported)

        assert stored.author == creator

        rows = list(
            csv.DictReader(io.StringIO(export_items_csv([stored], content_type)))
        )
        assert rows[0][creator_column] == creator

        entries = json.loads(export_items_json([stored], content_type))
        assert entries[0][creator_column] == creator

    def test_gog_wishlist_developers_export_as_the_developer_regression(
        self, tmp_path: Path
    ) -> None:
        """GOG's plural ``developers`` reaches the singular column."""
        stored = _store_and_read_back(
            tmp_path,
            _gog_wishlist_game(
                1207658924,
                {
                    "title": "Cyberpunk 2077",
                    "developers": ["CD Projekt Red"],
                    "publishers": ["CD Projekt"],
                },
            ),
        )

        rows = list(
            csv.DictReader(
                io.StringIO(export_items_csv([stored], ContentType.VIDEO_GAME))
            )
        )

        assert stored.author == "CD Projekt Red"
        assert stored.metadata["publisher"] == "CD Projekt"
        assert rows[0]["developer"] == "CD Projekt Red"


class TestCreatorExportEdges:
    """The creator cell at its boundaries, for every content type.

    Each case stores the item first, because the exporter reads the creator
    off ``ContentItem.author`` and only storage puts it there.
    """

    @pytest.mark.parametrize(
        ("content_type", "creator_column"),
        [
            (ContentType.BOOK, "author"),
            (ContentType.MOVIE, "director"),
            (ContentType.TV_SHOW, "creator"),
            (ContentType.VIDEO_GAME, "developer"),
        ],
        ids=["book", "movie", "tv_show", "video_game"],
    )
    def test_an_item_with_no_creator_exports_a_blank_cell(
        self, tmp_path: Path, content_type: ContentType, creator_column: str
    ) -> None:
        """A missing creator is an empty cell, never the string "None"."""
        stored = _store_and_read_back(
            tmp_path,
            ContentItem(
                id=f"creatorless-{content_type.value}",
                title="Anonymous",
                content_type=content_type,
                status=ConsumptionStatus.UNREAD,
            ),
        )

        rows = list(
            csv.DictReader(io.StringIO(export_items_csv([stored], content_type)))
        )
        entries = json.loads(export_items_json([stored], content_type))

        assert stored.author is None
        assert rows[0][creator_column] == ""
        assert entries[0][creator_column] == ""

    def test_a_creator_holding_a_comma_survives_the_csv_round_trip(
        self, tmp_path: Path
    ) -> None:
        """A joined multi-director name re-imports as one name, not two columns.

        ``to_text`` joins several directors with a comma, which is also the
        CSV delimiter, so the export/edit/re-import loop is the case that
        would shear the value in half if the writer stopped quoting it.
        """
        stored = _store_and_read_back(
            tmp_path,
            ContentItem(
                id="movie-comma",
                title="Fargo",
                content_type=ContentType.MOVIE,
                status=ConsumptionStatus.UNREAD,
                metadata={"director": ["Joel Coen", "Ethan Coen"]},
            ),
        )
        assert stored.author == "Joel Coen, Ethan Coen"

        exported = tmp_path / "movies.csv"
        exported.write_text(
            export_items_csv([stored], ContentType.MOVIE), encoding="utf-8"
        )
        reimported = next(
            CsvImportPlugin().fetch({"path": str(exported), "content_type": "movie"})
        )

        assert reimported.author == "Joel Coen, Ethan Coen"

    @pytest.mark.parametrize(
        ("content_type", "creator_column", "creator"),
        [
            (ContentType.BOOK, "author", "Åsa Larsson"),
            (ContentType.MOVIE, "director", "宮崎駿"),
            (ContentType.TV_SHOW, "creator", "Ólafur Ólafsson"),
            (ContentType.VIDEO_GAME, "developer", "ゲームフリーク"),
        ],
        ids=["book", "movie", "tv_show", "video_game"],
    )
    def test_a_non_latin_creator_exports_and_re_imports_unchanged(
        self,
        tmp_path: Path,
        content_type: ContentType,
        creator_column: str,
        creator: str,
    ) -> None:
        """A creator outside ASCII survives storage, export and re-import."""
        stored = _store_and_read_back(
            tmp_path,
            ContentItem(
                id=f"unicode-{content_type.value}",
                title="Untitled",
                content_type=content_type,
                status=ConsumptionStatus.UNREAD,
                author=creator,
            ),
        )

        exported = tmp_path / f"{content_type.value}.csv"
        exported.write_text(export_items_csv([stored], content_type), encoding="utf-8")
        reimported = next(
            CsvImportPlugin().fetch(
                {"path": str(exported), "content_type": content_type.value}
            )
        )

        entries = json.loads(export_items_json([stored], content_type))

        assert entries[0][creator_column] == creator
        assert reimported.author == creator

    @pytest.mark.parametrize(
        ("content_type", "creator_column", "creator"),
        [
            (ContentType.BOOK, "author", "Patrick Rothfuss"),
            (ContentType.MOVIE, "director", "Denis Villeneuve"),
            (ContentType.TV_SHOW, "creator", "Vince Gilligan"),
            (ContentType.VIDEO_GAME, "developer", "Team Cherry"),
        ],
        ids=["book", "movie", "tv_show", "video_game"],
    )
    def test_a_json_template_row_keeps_its_creator_through_storage(
        self,
        tmp_path: Path,
        content_type: ContentType,
        creator_column: str,
        creator: str,
    ) -> None:
        """The JSON importer's creator field reaches the column too.

        The JSON door reads the same declaration as the CSV one, so a type
        whose creator only worked on one of them would be half-fixed.
        """
        json_path = tmp_path / f"{content_type.value}.json"
        json_path.write_text(
            json.dumps(
                [{"title": "Round Trip", creator_column: creator, "status": "unread"}]
            ),
            encoding="utf-8",
        )
        imported = next(
            JsonImportPlugin().fetch(
                {"path": str(json_path), "content_type": content_type.value}
            )
        )

        stored = _store_and_read_back(tmp_path, imported)

        entries = json.loads(export_items_json([stored], content_type))
        assert stored.author == creator
        assert entries[0][creator_column] == creator


class TestExportColumnConsistency:
    """Ensures the export column order stays in sync with the templates."""

    def test_csv_column_order_matches_template_columns(self) -> None:
        """Every template column is exported, and nothing else is.

        ``_CSV_COLUMN_ORDER`` fixes the export layout while
        ``CONTENT_TYPE_COLUMNS`` declares what the templates hold. A column
        added to one and not the other either exports blank forever or never
        exports at all.
        """
        for content_type, columns in CONTENT_TYPE_COLUMNS.items():
            assert set(_CSV_COLUMN_ORDER[content_type]) == COMMON_COLUMNS | set(columns)


# The exact header every export writes, which users' saved spreadsheets and
# scripts read positionally. Written out per type rather than derived, so a
# reordering of the field declaration these are now built from shows up here
# as a diff instead of agreeing with itself.
_EXPECTED_CSV_HEADERS: dict[ContentType, list[str]] = {
    ContentType.BOOK: [
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
    ],
    ContentType.MOVIE: [
        "title",
        "director",
        "rating",
        "status",
        "date_completed",
        "review",
        "notes",
        "year",
        "runtime_minutes",
        "genre",
        "ignored",
    ],
    ContentType.TV_SHOW: [
        "title",
        "creator",
        "rating",
        "status",
        "date_completed",
        "review",
        "notes",
        "seasons_watched",
        "total_seasons",
        "year",
        "genre",
        "ignored",
    ],
    ContentType.VIDEO_GAME: [
        "title",
        "developer",
        "rating",
        "status",
        "date_completed",
        "review",
        "notes",
        "platform",
        "genre",
        "hours_played",
        "ignored",
    ],
}

# JSON puts ``ignored`` with the other common fields rather than last, so the
# two orders are not the same list.
_EXPECTED_JSON_KEYS: dict[ContentType, list[str]] = {
    content_type: [
        *[column for column in columns if column != "ignored"][:7],
        "ignored",
        *[column for column in columns if column != "ignored"][7:],
    ]
    for content_type, columns in _EXPECTED_CSV_HEADERS.items()
}


class TestExportLayoutIsStable:
    """The exported layout is the one users already have files in.

    Both orders are derived — the CSV header from ``CONTENT_TYPE_COLUMNS``
    and the JSON keys from the same mapping's iteration order — so nothing
    but these expectations stops a reordering of the underlying field
    declaration from silently reshuffling every exported file.
    """

    @pytest.mark.parametrize("content_type", list(ContentType))
    def test_csv_header_order(self, content_type: ContentType) -> None:
        """Each type's CSV header is exactly the documented column order."""
        result = export_items_csv([], content_type)

        reader = csv.DictReader(io.StringIO(result))
        assert list(reader.fieldnames or []) == _EXPECTED_CSV_HEADERS[content_type]

    @pytest.mark.parametrize("content_type", list(ContentType))
    def test_json_key_order(self, content_type: ContentType) -> None:
        """Each type's JSON entry carries exactly those keys, in order."""
        item = ContentItem(
            id="layout",
            title="Layout Fixture",
            author="Someone",
            content_type=content_type,
            status=ConsumptionStatus.COMPLETED,
            rating=3,
            metadata={},
        )

        entries = json.loads(export_items_json([item], content_type))

        assert list(entries[0]) == _EXPECTED_JSON_KEYS[content_type]


class TestExportRoundtrip:
    """Tests that exported data can be re-imported identically."""

    def test_csv_roundtrip_book(self) -> None:
        """Export a book to CSV, re-import it, verify fields match."""
        original = ContentItem(
            id="rt1",
            title="Roundtrip Book",
            author="Test Author",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            rating=4,
            ignored=True,
            metadata={"genres": ["Fantasy"]},
        )

        csv_content = export_items_csv([original], ContentType.BOOK)

        # Write to temp file and re-import
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
                "seasons": 8,
                "genres": ["Drama"],
            },
        )

        json_content = export_items_json([original], ContentType.TV_SHOW)

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
        assert reimported[0].metadata["seasons"] == 8
        assert reimported[0].ignored is False

    def test_csv_roundtrip_movie_through_storage_regression(
        self, tmp_path: Path
    ) -> None:
        """A stored movie survives export, re-import and a second save.

        This is the harm the blank export columns caused: the exported file
        carried no year or runtime, so re-importing it dropped both and
        content-length scoring fell back to its no-metadata default.
        """
        stored = _store_and_read_back(
            tmp_path,
            _arr_item(
                RadarrPlugin(),
                {
                    "title": "Inception",
                    "tmdbId": 27205,
                    "imdbId": "tt1375666",
                    "year": 2010,
                    "runtime": 148,
                },
            ),
        )
        csv_path = tmp_path / "movies.csv"
        csv_path.write_text(
            export_items_csv([stored], ContentType.MOVIE), encoding="utf-8"
        )

        reimported = list(
            CsvImportPlugin().fetch({"path": str(csv_path), "content_type": "movie"})
        )
        assert len(reimported) == 1

        target = SQLiteDB(tmp_path / "reimported.db")
        db_id = target.save_content_item(reimported[0])
        round_tripped = target.get_content_item(db_id)

        assert round_tripped is not None
        assert round_tripped.metadata["release_year"] == 2010
        assert round_tripped.metadata["runtime"] == 148
