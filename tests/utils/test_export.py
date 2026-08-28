import csv
import io
import json
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from src.ingestion.importers.base import ImportedRow, Importer
from src.ingestion.importers.generic_csv.generic_csv import CsvImporter
from src.ingestion.importers.generic_json.generic_json import JsonImporter
from src.ingestion.importers.goodreads_csv.goodreads_csv import GoodreadsCsvImporter
from src.ingestion.sources.arr_base import ArrPlugin
from src.ingestion.sources.gog.gog import GogPlugin
from src.ingestion.sources.radarr.radarr import RadarrPlugin
from src.ingestion.sources.sonarr.sonarr import SonarrPlugin
from src.models.content import ConsumptionStatus, ContentItem, ContentType
from src.models.templates import (
    COMMON_COLUMNS,
    CONTENT_TYPE_COLUMNS,
    CREATOR_COLUMNS,
    CREATOR_FIELD,
)
from src.storage.sqlite_db import SQLiteDB
from src.utils.export import export_items_csv, export_items_json
from src.utils.series import latest_season_watched_date


class TestExportSerialization:
    def test_export_csv_books(self) -> None:
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

    def test_export_json_books(self) -> None:
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

    def test_export_json_item_with_no_rating(self) -> None:
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


def _reimport(
    importer: Importer, text: str, content_type: ContentType
) -> list[ContentItem]:
    return [
        row.item
        for row in importer.parse(text, content_type)
        if isinstance(row, ImportedRow)
    ]


def _store_and_read_back(tmp_path: Path, item: ContentItem) -> ContentItem:
    database = SQLiteDB(tmp_path / "export.db")
    db_id = database.save_content_item(item)
    stored = database.get_content_item(db_id)
    assert stored is not None
    return stored


def _arr_item(plugin: ArrPlugin, payload: dict[str, Any]) -> ContentItem:
    return ContentItem(
        id=plugin.build_external_id(payload),
        title=payload["title"],
        content_type=plugin.arr_content_type,
        status=ConsumptionStatus.UNREAD,
        source=plugin.name,
        metadata=plugin.build_metadata(payload),
    )


def _gog_game(product: dict[str, Any]) -> ContentItem:
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


class TestExportOfStoredItems:
    def test_movie_export_includes_year_and_runtime_regression(
        self, tmp_path: Path
    ) -> None:
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

    def test_a_stored_object_platform_exports_its_name_regression(
        self, tmp_path: Path
    ) -> None:
        database = SQLiteDB(tmp_path / "object-platform.db")
        db_id = database.save_content_item(
            ContentItem(
                id="1207658924",
                title="The Witcher 3: Wild Hunt",
                content_type=ContentType.VIDEO_GAME,
                status=ConsumptionStatus.UNREAD,
            )
        )
        with database.connection() as conn:
            conn.execute(
                "UPDATE video_game_details SET platforms = ? WHERE content_item_id = ?",
                (json.dumps([{"name": "PC"}]), db_id),
            )
            conn.commit()
        stored = database.get_content_item(db_id)
        assert stored is not None

        rows = list(
            csv.DictReader(
                io.StringIO(export_items_csv([stored], ContentType.VIDEO_GAME))
            )
        )

        assert rows[0]["platform"] == "PC"


class TestCreatorSurvivesStorage:
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
        imported = _reimport(
            CsvImporter(),
            f"title,{creator_column},status\nRound Trip,{creator},unread\n",
            content_type,
        )[0]

        stored = _store_and_read_back(tmp_path, imported)

        assert stored.author == creator

        rows = list(
            csv.DictReader(io.StringIO(export_items_csv([stored], content_type)))
        )
        assert rows[0][creator_column] == creator

        entries = json.loads(export_items_json([stored], content_type))
        assert entries[0][creator_column] == creator

    def test_a_gog_wishlist_game_exports_the_company_names_it_states(
        self, tmp_path: Path
    ) -> None:
        stored = _store_and_read_back(
            tmp_path,
            _gog_wishlist_game(
                1207658924,
                {
                    "title": "Cyberpunk 2077",
                    "developers": [{"name": "CD Projekt Red"}],
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


class TestSeriesSurvivesAnExport:
    @pytest.mark.parametrize(
        ("importer", "export"),
        [(CsvImporter(), export_items_csv), (JsonImporter(), export_items_json)],
        ids=["csv", "json"],
    )
    def test_a_shelved_books_series_survives_an_export_and_its_reimport(
        self,
        tmp_path: Path,
        importer: Importer,
        export: Callable[[list[ContentItem], ContentType | None], str],
    ) -> None:
        shelved = _reimport(
            GoodreadsCsvImporter(),
            "Book Id,Title,Author,Exclusive Shelf\n"
            '1,"All Systems Red (The Murderbot Diaries, #1)",Martha Wells,read\n',
            ContentType.BOOK,
        )[0]
        stored = _store_and_read_back(tmp_path, shelved)

        restored = _reimport(
            importer, export([stored], ContentType.BOOK), ContentType.BOOK
        )[0]

        assert restored.title == "All Systems Red"
        assert restored.metadata.get("series") == "The Murderbot Diaries"
        assert float(restored.metadata["series_index"]) == 1.0


class TestCreatorExportEdges:
    @pytest.mark.parametrize(
        ("content_type", "creator_column"),
        [
            (ContentType.BOOK, "author"),
            (ContentType.VIDEO_GAME, "developer"),
        ],
        ids=["book", "video_game"],
    )
    def test_an_item_with_no_creator_exports_a_blank_cell(
        self, tmp_path: Path, content_type: ContentType, creator_column: str
    ) -> None:
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

        reimported = _reimport(
            CsvImporter(),
            export_items_csv([stored], ContentType.MOVIE),
            ContentType.MOVIE,
        )[0]

        assert reimported.author == "Joel Coen, Ethan Coen"

    def test_a_non_latin_creator_exports_and_re_imports_unchanged(
        self, tmp_path: Path
    ) -> None:
        stored = _store_and_read_back(
            tmp_path,
            ContentItem(
                id="unicode-movie",
                title="Untitled",
                content_type=ContentType.MOVIE,
                status=ConsumptionStatus.UNREAD,
                author="宮崎駿",
            ),
        )

        reimported = _reimport(
            CsvImporter(),
            export_items_csv([stored], ContentType.MOVIE),
            ContentType.MOVIE,
        )[0]

        entries = json.loads(export_items_json([stored], ContentType.MOVIE))

        assert entries[0]["director"] == "宮崎駿"
        assert reimported.author == "宮崎駿"

    def test_a_json_template_row_keeps_its_creator_through_storage(
        self, tmp_path: Path
    ) -> None:
        imported = _reimport(
            JsonImporter(),
            json.dumps(
                [
                    {
                        "title": "Round Trip",
                        "creator": "Vince Gilligan",
                        "status": "unread",
                    }
                ]
            ),
            ContentType.TV_SHOW,
        )[0]

        stored = _store_and_read_back(tmp_path, imported)

        entries = json.loads(export_items_json([stored], ContentType.TV_SHOW))
        assert stored.author == "Vince Gilligan"
        assert entries[0]["creator"] == "Vince Gilligan"


class TestWholeLibraryExport:
    @staticmethod
    def _library() -> list[ContentItem]:
        return [
            ContentItem(
                id="1",
                title="=1+1",
                author="Patrick Rothfuss",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.COMPLETED,
            ),
            ContentItem(
                id="2",
                title="Arrival",
                author="Denis Villeneuve",
                content_type=ContentType.MOVIE,
                status=ConsumptionStatus.UNREAD,
            ),
        ]

    def test_each_row_fills_its_own_type_columns_behind_the_formula_guard(
        self,
    ) -> None:
        rows = list(
            csv.DictReader(io.StringIO(export_items_csv(self._library(), None)))
        )

        assert rows[0]["title"] == "'=1+1"
        assert rows[0]["author"] == "Patrick Rothfuss"
        assert rows[0]["director"] == ""
        assert rows[1]["director"] == "Denis Villeneuve"
        assert rows[1]["author"] == ""

    def test_each_row_names_its_content_type(self) -> None:
        rows = list(
            csv.DictReader(io.StringIO(export_items_csv(self._library(), None)))
        )

        assert [row["content_type"] for row in rows] == ["book", "movie"]

    def test_the_header_carries_every_type_s_columns(self) -> None:
        header = set(
            csv.DictReader(io.StringIO(export_items_csv([], None))).fieldnames or []
        )

        for content_type in ContentType:
            assert set(_exported_header(content_type)) <= header

    def test_each_json_entry_uses_its_own_type_s_creator_field(self) -> None:
        entries = json.loads(export_items_json(self._library(), None))

        assert entries[0]["author"] == "Patrick Rothfuss"
        assert entries[1]["director"] == "Denis Villeneuve"
        assert "director" not in entries[0]


def _exported_header(content_type: ContentType) -> list[str]:
    reader = csv.DictReader(io.StringIO(export_items_csv([], content_type)))
    return list(reader.fieldnames or [])


class TestExportColumnConsistency:
    @pytest.mark.parametrize("content_type", list(ContentType))
    def test_the_export_writes_every_column_the_importer_accepts(
        self, content_type: ContentType
    ) -> None:
        columns = CONTENT_TYPE_COLUMNS[content_type.value]
        expected = (
            COMMON_COLUMNS
            | {CREATOR_FIELD[content_type.value]}
            | (set(columns) - CREATOR_COLUMNS)
        )

        assert sorted(_exported_header(content_type)) == sorted(expected)


class TestShippedTemplatesCarryTheExportColumns:
    @pytest.mark.parametrize(
        ("content_type", "template"),
        [
            (ContentType.BOOK, "books.csv"),
            (ContentType.VIDEO_GAME, "video_games.csv"),
        ],
        ids=["book", "video_game"],
    )
    def test_template_header_carries_the_export_columns(
        self, content_type: ContentType, template: str
    ) -> None:
        shipped = (Path("templates") / template).read_text(encoding="utf-8")

        header = list(csv.reader(io.StringIO(shipped)))[0]

        assert sorted(header) == sorted(_exported_header(content_type))


class TestCsvFormulaGuard:
    @pytest.mark.parametrize(
        "payload",
        ['=HYPERLINK("http://evil","x")', "+1+1", "\tA1"],
        ids=["equals", "plus", "tab"],
    )
    def test_a_formula_title_is_written_behind_an_apostrophe_regression(
        self, payload: str
    ) -> None:
        item = ContentItem(
            id="formula",
            title=payload,
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
        )

        rows = list(
            csv.DictReader(io.StringIO(export_items_csv([item], ContentType.BOOK)))
        )

        assert rows[0]["title"] == f"'{payload}"

    def test_every_text_column_is_guarded_not_just_the_title_regression(self) -> None:
        item = ContentItem(
            id="formula-columns",
            title="=1+1",
            author="+2+2",
            content_type=ContentType.VIDEO_GAME,
            status=ConsumptionStatus.UNREAD,
            review="@SUM(A1)",
            metadata={
                "notes": "-1+1",
                "genres": ['=WEBSERVICE("http://evil")'],
                "platforms": ["@PC"],
            },
        )

        rows = list(
            csv.DictReader(
                io.StringIO(export_items_csv([item], ContentType.VIDEO_GAME))
            )
        )

        assert rows[0]["title"] == "'=1+1"
        assert rows[0]["developer"] == "'+2+2"
        assert rows[0]["review"] == "'@SUM(A1)"
        assert rows[0]["notes"] == "'-1+1"
        assert rows[0]["genre"] == '\'=WEBSERVICE("http://evil")'
        assert rows[0]["platform"] == "'@PC"

    def test_a_hand_written_csv_keeps_its_leading_formula_character(self) -> None:
        reimported = _reimport(
            CsvImporter(), "title,status\n=1+1,unread\n", ContentType.BOOK
        )[0]

        assert reimported.title == "=1+1"

    @pytest.mark.parametrize(
        "content_type", [ContentType.BOOK, ContentType.TV_SHOW], ids=["book", "tv_show"]
    )
    def test_each_content_type_round_trips_a_formula_in_every_text_column_regression(
        self, content_type: ContentType
    ) -> None:
        creator_column = CREATOR_FIELD[content_type.value]
        item = ContentItem(
            id=f"formula-{content_type.value}",
            title="=1+1",
            author="+2+2",
            content_type=content_type,
            status=ConsumptionStatus.UNREAD,
            review="@SUM(A1)",
            metadata={"notes": "-1+1", "genres": ["=Action"]},
        )
        exported = export_items_csv([item], content_type)

        rows = list(csv.DictReader(io.StringIO(exported)))
        reimported = _reimport(CsvImporter(), exported, content_type)[0]

        assert rows[0]["title"] == "'=1+1"
        assert rows[0][creator_column] == "'+2+2"
        assert rows[0]["review"] == "'@SUM(A1)"
        assert rows[0]["notes"] == "'-1+1"
        assert rows[0]["genre"] == "'=Action"
        assert reimported.title == "=1+1"
        assert reimported.author == "+2+2"
        assert reimported.review == "@SUM(A1)"
        assert reimported.metadata["notes"] == "-1+1"
        assert reimported.metadata["genres"] == ["=Action"]

    def test_a_second_export_of_a_re_imported_row_is_byte_identical(self) -> None:
        item = ContentItem(
            id="formula-stable",
            title="=1+1",
            author="-2-2",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
        )
        first = export_items_csv([item], ContentType.BOOK)

        reimported = _reimport(CsvImporter(), first, ContentType.BOOK)[0]

        assert export_items_csv([reimported], ContentType.BOOK) == first

    @pytest.mark.parametrize(
        "title",
        ["'", "'Tis", "a=1+1"],
        ids=[
            "bare-quote",
            "quote-letter",
            "formula-mid-string",
        ],
    )
    def test_a_value_the_guard_does_not_own_is_written_and_read_verbatim(
        self, title: str
    ) -> None:
        item = ContentItem(
            id="not-a-guard",
            title=title,
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
        )
        exported = export_items_csv([item], ContentType.BOOK)

        rows = list(csv.DictReader(io.StringIO(exported)))
        reimported = _reimport(CsvImporter(), exported, ContentType.BOOK)[0]

        assert rows[0]["title"] == title
        assert reimported.title == title


class TestExportRoundtrip:
    def test_csv_roundtrip_book(self) -> None:
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

        reimported = _reimport(CsvImporter(), csv_content, ContentType.BOOK)

        assert len(reimported) == 1
        assert reimported[0].title == original.title
        assert reimported[0].author == original.author
        assert reimported[0].rating == original.rating
        assert reimported[0].ignored is True

    def test_json_roundtrip_tv_show_with_seasons(self) -> None:
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

        reimported = _reimport(JsonImporter(), json_content, ContentType.TV_SHOW)

        assert len(reimported) == 1
        assert reimported[0].title == original.title
        assert reimported[0].author == original.author
        assert reimported[0].metadata["seasons_watched"] == [1, 2, 5, 6]
        assert reimported[0].metadata["seasons"] == 8
        assert reimported[0].ignored is False

    def test_csv_roundtrip_tv_show_keeps_its_watch_dates_readable(self) -> None:
        original = ContentItem(
            id="rt3",
            title="Roundtrip Show",
            content_type=ContentType.TV_SHOW,
            status=ConsumptionStatus.CURRENTLY_CONSUMING,
            metadata={
                "seasons_watched": [1, 2],
                "seasons_watched_dates": {"2": "2026-03-09T22:30:00+00:00"},
            },
        )

        reimported = _reimport(
            CsvImporter(),
            export_items_csv([original], ContentType.TV_SHOW),
            ContentType.TV_SHOW,
        )

        assert reimported[0].metadata["seasons_watched"] == [1, 2]
        assert latest_season_watched_date(reimported[0]) == date(2026, 3, 9)

    def test_csv_roundtrip_movie_through_storage_regression(
        self, tmp_path: Path
    ) -> None:
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
        reimported = _reimport(
            CsvImporter(),
            export_items_csv([stored], ContentType.MOVIE),
            ContentType.MOVIE,
        )
        assert len(reimported) == 1

        target = SQLiteDB(tmp_path / "reimported.db")
        db_id = target.save_content_item(reimported[0])
        round_tripped = target.get_content_item(db_id)

        assert round_tripped is not None
        assert round_tripped.metadata["release_year"] == 2010
        assert round_tripped.metadata["runtime"] == 148
