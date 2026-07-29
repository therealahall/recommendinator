"""Tests for generic CSV import plugin."""

import logging
from datetime import date
from pathlib import Path

import pytest

from src.ingestion.plugin_base import SourceError, SourcePlugin
from src.ingestion.sources.generic_csv.generic_csv import (
    CsvImportPlugin,
    parse_boolean_field,
    parse_seasons_watched,
)
from src.models.content import ConsumptionStatus, ContentType
from src.utils.series import MAX_SEASONS

_CSV_LOGGER = "src.ingestion.sources.generic_csv.generic_csv"


def _invalid_date_message(caplog: pytest.LogCaptureFixture) -> str:
    """Return the one invalid-date warning the parse emitted."""
    messages = [
        record.getMessage()
        for record in caplog.records
        if "Invalid date format" in record.getMessage()
    ]
    assert len(messages) == 1
    return messages[0]


@pytest.fixture()
def plugin() -> CsvImportPlugin:
    """Create a CsvImportPlugin instance."""
    return CsvImportPlugin()


class TestCsvImportPluginProperties:
    """Tests for CsvImportPlugin metadata properties."""

    def test_is_source_plugin(self, plugin: CsvImportPlugin) -> None:
        assert isinstance(plugin, SourcePlugin)

    def test_name(self, plugin: CsvImportPlugin) -> None:
        assert plugin.name == "csv_import"

    def test_display_name(self, plugin: CsvImportPlugin) -> None:
        assert plugin.display_name == "CSV Import"

    def test_content_types(self, plugin: CsvImportPlugin) -> None:
        assert ContentType.BOOK in plugin.content_types
        assert ContentType.MOVIE in plugin.content_types
        assert ContentType.TV_SHOW in plugin.content_types
        assert ContentType.VIDEO_GAME in plugin.content_types

    def test_requires_api_key(self, plugin: CsvImportPlugin) -> None:
        assert plugin.requires_api_key is False

    def test_requires_network(self, plugin: CsvImportPlugin) -> None:
        assert plugin.requires_network is False

    def test_is_file_import(self, plugin: CsvImportPlugin) -> None:
        assert plugin.is_file_import is True

    def test_config_schema_has_no_path(self, plugin: CsvImportPlugin) -> None:
        """Path is injected by the import service; only content_type remains."""
        names = [field.name for field in plugin.get_config_schema()]
        assert names == ["content_type"]

    def test_get_source_identifier(self, plugin: CsvImportPlugin) -> None:
        assert plugin.get_source_identifier() == "csv_import"

    def test_get_info(self, plugin: CsvImportPlugin) -> None:
        info = plugin.get_info()
        assert info.name == "csv_import"
        assert info.display_name == "CSV Import"
        assert info.requires_api_key is False
        assert info.requires_network is False
        assert info.is_file_import is True


class TestCsvImportPluginValidation:
    """Tests for CsvImportPlugin config validation."""

    def test_validate_valid_config(self, plugin: CsvImportPlugin) -> None:
        assert plugin.validate_config({"content_type": "book"}) == []

    def test_validate_does_not_require_path(self, plugin: CsvImportPlugin) -> None:
        """validate_config no longer requires a path — the service injects it."""
        assert (
            plugin.validate_config(
                {"path": "/nonexistent/path.csv", "content_type": "book"}
            )
            == []
        )

    def test_validate_missing_content_type(self, plugin: CsvImportPlugin) -> None:
        errors = plugin.validate_config({})
        assert any("content_type" in error for error in errors)

    def test_validate_invalid_content_type(self, plugin: CsvImportPlugin) -> None:
        errors = plugin.validate_config({"content_type": "podcast"})
        assert any("Invalid content_type" in error for error in errors)

    def test_validate_all_content_types(self, plugin: CsvImportPlugin) -> None:
        for content_type in ContentType:
            errors = plugin.validate_config({"content_type": content_type.value})
            assert errors == [], f"Failed for content_type={content_type.value}"

    def test_validate_accepts_any_case(self, plugin: CsvImportPlugin) -> None:
        """Every content type validates however it is cased.

        ``ContentType.value`` is always lowercase, so the loop above only ever
        exercised one spelling. The value arrives raw from ``--option
        content_type=BOOK`` and from the multipart field on POST /api/import,
        and only the ``--content-type`` flag lowercases it first.
        """
        for content_type in ContentType:
            spelled = content_type.value.upper()
            assert plugin.validate_config({"content_type": spelled}) == [], spelled


class TestCsvImportPluginFetchBooks:
    """Tests for CSV import of books."""

    def test_fetch_basic_book(self, plugin: CsvImportPlugin, tmp_path: Path) -> None:
        csv_file = tmp_path / "books.csv"
        csv_file.write_text(
            "title,author,rating,status,date_completed,review,notes,isbn,pages,year_published,genre\n"
            "The Name of the Wind,Patrick Rothfuss,5,completed,2024-06-15,Great book,,978-0756404741,662,2007,Fantasy\n"
        )

        items = list(plugin.fetch({"path": str(csv_file), "content_type": "book"}))

        assert len(items) == 1
        item = items[0]
        assert item.title == "The Name of the Wind"
        assert item.author == "Patrick Rothfuss"
        assert item.content_type == ContentType.BOOK.value
        assert item.rating == 5
        assert item.status == ConsumptionStatus.COMPLETED.value
        assert item.date_completed == date(2024, 6, 15)
        assert item.review == "Great book"
        assert item.source == "csv_import"
        assert item.metadata["isbn"] == "978-0756404741"
        assert item.metadata["pages"] == "662"
        assert item.metadata["year_published"] == "2007"
        assert item.metadata["genre"] == "Fantasy"

    def test_fetch_multiple_books(
        self, plugin: CsvImportPlugin, tmp_path: Path
    ) -> None:
        csv_file = tmp_path / "books.csv"
        csv_file.write_text(
            "title,author,rating,status\n"
            "Book One,Author A,5,completed\n"
            "Book Two,Author B,3,in_progress\n"
            "Book Three,Author C,,unread\n"
        )

        items = list(plugin.fetch({"path": str(csv_file), "content_type": "book"}))

        assert len(items) == 3
        assert items[0].title == "Book One"
        assert items[0].rating == 5
        assert items[0].status == ConsumptionStatus.COMPLETED.value
        assert items[1].title == "Book Two"
        assert items[1].rating == 3
        assert items[1].status == ConsumptionStatus.CURRENTLY_CONSUMING.value
        assert items[2].title == "Book Three"
        assert items[2].rating is None
        assert items[2].status == ConsumptionStatus.UNREAD.value

    def test_fetch_empty_title_skipped(
        self, plugin: CsvImportPlugin, tmp_path: Path
    ) -> None:
        csv_file = tmp_path / "books.csv"
        csv_file.write_text(
            "title,author,rating,status\n"
            ",Author A,5,completed\n"
            "Valid Book,Author B,4,completed\n"
        )

        items = list(plugin.fetch({"path": str(csv_file), "content_type": "book"}))

        assert len(items) == 1
        assert items[0].title == "Valid Book"

    def test_fetch_notes_in_metadata(
        self, plugin: CsvImportPlugin, tmp_path: Path
    ) -> None:
        csv_file = tmp_path / "books.csv"
        csv_file.write_text(
            "title,author,rating,status,notes\n"
            "Test Book,Author,5,completed,Recommended by friend\n"
        )

        items = list(plugin.fetch({"path": str(csv_file), "content_type": "book"}))

        assert items[0].metadata["notes"] == "Recommended by friend"

    def test_fetch_resolves_a_mixed_case_content_type(
        self, plugin: CsvImportPlugin, tmp_path: Path
    ) -> None:
        """``BOOK`` types the rows exactly as ``book`` does.

        Validation is only half the route: ``fetch`` resolves the option again
        on its own, so a case-sensitive lookup there would accept the config and
        then refuse the file it had just approved.
        """
        csv_file = tmp_path / "books.csv"
        csv_file.write_text(
            "title,author,rating,status\nDune,Frank Herbert,5,completed\n"
        )

        items = list(plugin.fetch({"path": str(csv_file), "content_type": "BOOK"}))

        assert [item.content_type for item in items] == [ContentType.BOOK.value]


class TestCsvImportPluginFetchMovies:
    """Tests for CSV import of movies."""

    def test_fetch_movie(self, plugin: CsvImportPlugin, tmp_path: Path) -> None:
        csv_file = tmp_path / "movies.csv"
        csv_file.write_text(
            "title,director,rating,status,year,runtime_minutes,genre\n"
            "Inception,Christopher Nolan,5,completed,2010,148,Sci-Fi\n"
        )

        items = list(plugin.fetch({"path": str(csv_file), "content_type": "movie"}))

        assert len(items) == 1
        item = items[0]
        assert item.title == "Inception"
        assert item.author == "Christopher Nolan"
        assert item.content_type == ContentType.MOVIE.value
        assert item.rating == 5
        assert item.metadata["year"] == "2010"
        assert item.metadata["runtime_minutes"] == "148"
        assert item.metadata["genre"] == "Sci-Fi"


class TestCsvImportPluginFetchTvShows:
    """Tests for CSV import of TV shows."""

    def test_fetch_tv_show(self, plugin: CsvImportPlugin, tmp_path: Path) -> None:
        csv_file = tmp_path / "tv_shows.csv"
        csv_file.write_text(
            "title,creator,rating,status,seasons_watched,total_seasons,year,genre\n"
            "Breaking Bad,Vince Gilligan,5,completed,5,5,2008,Drama\n"
        )

        items = list(plugin.fetch({"path": str(csv_file), "content_type": "tv_show"}))

        assert len(items) == 1
        item = items[0]
        assert item.title == "Breaking Bad"
        assert item.author == "Vince Gilligan"
        assert item.content_type == ContentType.TV_SHOW.value
        assert item.metadata["seasons_watched"] == [1, 2, 3, 4, 5]
        assert item.metadata["total_seasons"] == "5"


class TestCsvImportPluginFetchVideoGames:
    """Tests for CSV import of video games."""

    def test_fetch_video_game(self, plugin: CsvImportPlugin, tmp_path: Path) -> None:
        csv_file = tmp_path / "games.csv"
        csv_file.write_text(
            "title,developer,rating,status,platform,genre,hours_played\n"
            "The Witcher 3,CD Projekt Red,5,completed,PC,RPG,120\n"
        )

        items = list(
            plugin.fetch({"path": str(csv_file), "content_type": "video_game"})
        )

        assert len(items) == 1
        item = items[0]
        assert item.title == "The Witcher 3"
        assert item.author == "CD Projekt Red"
        assert item.content_type == ContentType.VIDEO_GAME.value
        assert item.metadata["platform"] == "PC"
        assert item.metadata["genre"] == "RPG"
        assert item.metadata["hours_played"] == "120"


class TestCsvImportPluginStatusMapping:
    """Tests for status string mapping."""

    def test_status_completed(self, plugin: CsvImportPlugin, tmp_path: Path) -> None:
        csv_file = tmp_path / "data.csv"
        csv_file.write_text("title,status\nTest,completed\n")
        items = list(plugin.fetch({"path": str(csv_file), "content_type": "book"}))
        assert items[0].status == ConsumptionStatus.COMPLETED.value

    def test_status_in_progress(self, plugin: CsvImportPlugin, tmp_path: Path) -> None:
        csv_file = tmp_path / "data.csv"
        csv_file.write_text("title,status\nTest,in_progress\n")
        items = list(plugin.fetch({"path": str(csv_file), "content_type": "book"}))
        assert items[0].status == ConsumptionStatus.CURRENTLY_CONSUMING.value

    def test_status_unread(self, plugin: CsvImportPlugin, tmp_path: Path) -> None:
        csv_file = tmp_path / "data.csv"
        csv_file.write_text("title,status\nTest,unread\n")
        items = list(plugin.fetch({"path": str(csv_file), "content_type": "book"}))
        assert items[0].status == ConsumptionStatus.UNREAD.value

    def test_status_unknown_defaults_to_unread(
        self, plugin: CsvImportPlugin, tmp_path: Path
    ) -> None:
        csv_file = tmp_path / "data.csv"
        csv_file.write_text("title,status\nTest,something_else\n")
        items = list(plugin.fetch({"path": str(csv_file), "content_type": "book"}))
        assert items[0].status == ConsumptionStatus.UNREAD.value

    def test_status_empty_defaults_to_unread(
        self, plugin: CsvImportPlugin, tmp_path: Path
    ) -> None:
        csv_file = tmp_path / "data.csv"
        csv_file.write_text("title,status\nTest,\n")
        items = list(plugin.fetch({"path": str(csv_file), "content_type": "book"}))
        assert items[0].status == ConsumptionStatus.UNREAD.value

    def test_status_wishlist(self, plugin: CsvImportPlugin, tmp_path: Path) -> None:
        csv_file = tmp_path / "data.csv"
        csv_file.write_text("title,status\nTest,wishlist\n")
        items = list(plugin.fetch({"path": str(csv_file), "content_type": "book"}))
        assert items[0].status == ConsumptionStatus.UNREAD.value


class TestCsvImportPluginRating:
    """Tests for rating normalization."""

    def test_valid_ratings(self, plugin: CsvImportPlugin, tmp_path: Path) -> None:
        csv_file = tmp_path / "data.csv"
        csv_file.write_text("title,rating\n" "A,1\n" "B,2\n" "C,3\n" "D,4\n" "E,5\n")

        items = list(plugin.fetch({"path": str(csv_file), "content_type": "book"}))

        assert [item.rating for item in items] == [1, 2, 3, 4, 5]

    def test_empty_rating_is_none(
        self, plugin: CsvImportPlugin, tmp_path: Path
    ) -> None:
        csv_file = tmp_path / "data.csv"
        csv_file.write_text("title,rating\nTest,\n")
        items = list(plugin.fetch({"path": str(csv_file), "content_type": "book"}))
        assert items[0].rating is None

    def test_zero_rating_is_none(self, plugin: CsvImportPlugin, tmp_path: Path) -> None:
        csv_file = tmp_path / "data.csv"
        csv_file.write_text("title,rating\nTest,0\n")
        items = list(plugin.fetch({"path": str(csv_file), "content_type": "book"}))
        assert items[0].rating is None


class TestCsvImportPluginErrors:
    """Tests for error handling."""

    def test_file_not_found_raises_source_error(self, plugin: CsvImportPlugin) -> None:
        with pytest.raises(SourceError, match="CSV file not found"):
            list(
                plugin.fetch({"path": "/nonexistent/file.csv", "content_type": "book"})
            )

    def test_invalid_content_type_raises_source_error(
        self, plugin: CsvImportPlugin, tmp_path: Path
    ) -> None:
        csv_file = tmp_path / "data.csv"
        csv_file.write_text("title\nTest\n")
        with pytest.raises(SourceError, match="Invalid content type"):
            list(plugin.fetch({"path": str(csv_file), "content_type": "podcast"}))

    def test_missing_title_column_raises_source_error(
        self, plugin: CsvImportPlugin, tmp_path: Path
    ) -> None:
        csv_file = tmp_path / "data.csv"
        csv_file.write_text("name,rating\nTest,5\n")
        with pytest.raises(SourceError, match="missing required column"):
            list(plugin.fetch({"path": str(csv_file), "content_type": "book"}))

    def test_non_utf8_file_raises_source_error(
        self, plugin: CsvImportPlugin, tmp_path: Path
    ) -> None:
        """A Latin-1 export is refused as a SourceError, not a UnicodeDecodeError.

        Proves the plugin reads through the shared reader: the decode failure
        used to escape ``fetch`` and surface as an unhandled 500.
        """
        csv_file = tmp_path / "data.csv"
        csv_file.write_text("title\nCafé\n", encoding="latin-1")

        with pytest.raises(SourceError, match="not UTF-8 text"):
            list(plugin.fetch({"path": str(csv_file), "content_type": "book"}))

    def test_invalid_date_does_not_crash(
        self, plugin: CsvImportPlugin, tmp_path: Path
    ) -> None:
        """Invalid dates should warn but not crash."""
        csv_file = tmp_path / "data.csv"
        csv_file.write_text("title,date_completed\nTest,not-a-date\n")
        items = list(plugin.fetch({"path": str(csv_file), "content_type": "book"}))
        assert len(items) == 1
        assert items[0].date_completed is None


class TestCsvImportBomRegression:
    """Regression: an Excel-saved CSV imported instead of failing.

    Reported: a CSV saved by Excel was rejected with "CSV missing required
    column: title" even though the column was there. Root cause: Excel writes
    a UTF-8 BOM and the reader opened the file as plain ``utf-8``, so the BOM
    became part of the first column name. Fix: open with ``utf-8-sig``, which
    strips the BOM when present and decodes a BOM-less file identically.
    """

    def test_fetch_bom_prefixed_csv(
        self, plugin: CsvImportPlugin, tmp_path: Path
    ) -> None:
        csv_file = tmp_path / "books.csv"
        # Writing as utf-8-sig emits the real byte-order mark Excel prepends.
        csv_file.write_text(
            "title,author,rating,status\nDune,Frank Herbert,5,completed\n",
            encoding="utf-8-sig",
        )

        items = list(plugin.fetch({"path": str(csv_file), "content_type": "book"}))

        assert len(items) == 1
        assert items[0].title == "Dune"
        assert items[0].author == "Frank Herbert"


class TestCsvTemplates:
    """Tests that template files are valid and can be parsed."""

    @pytest.fixture()
    def templates_dir(self) -> Path:
        return Path("templates")

    def test_books_template_exists(self, templates_dir: Path) -> None:
        assert (templates_dir / "books.csv").exists()

    def test_movies_template_exists(self, templates_dir: Path) -> None:
        assert (templates_dir / "movies.csv").exists()

    def test_tv_shows_template_exists(self, templates_dir: Path) -> None:
        assert (templates_dir / "tv_shows.csv").exists()

    def test_video_games_template_exists(self, templates_dir: Path) -> None:
        assert (templates_dir / "video_games.csv").exists()

    def test_books_template_parseable(
        self, plugin: CsvImportPlugin, templates_dir: Path
    ) -> None:
        items = list(
            plugin.fetch(
                {"path": str(templates_dir / "books.csv"), "content_type": "book"}
            )
        )
        assert len(items) == 1
        assert items[0].title == "The Name of the Wind"

    def test_movies_template_parseable(
        self, plugin: CsvImportPlugin, templates_dir: Path
    ) -> None:
        items = list(
            plugin.fetch(
                {
                    "path": str(templates_dir / "movies.csv"),
                    "content_type": "movie",
                }
            )
        )
        assert len(items) == 1
        assert items[0].title == "Inception"

    def test_tv_shows_template_parseable(
        self, plugin: CsvImportPlugin, templates_dir: Path
    ) -> None:
        items = list(
            plugin.fetch(
                {
                    "path": str(templates_dir / "tv_shows.csv"),
                    "content_type": "tv_show",
                }
            )
        )
        assert len(items) == 1
        assert items[0].title == "Breaking Bad"

    def test_video_games_template_parseable(
        self, plugin: CsvImportPlugin, templates_dir: Path
    ) -> None:
        items = list(
            plugin.fetch(
                {
                    "path": str(templates_dir / "video_games.csv"),
                    "content_type": "video_game",
                }
            )
        )
        assert len(items) == 1
        assert items[0].title == "The Witcher 3"


class TestCsvImportIgnored:
    """Tests for ignored field parsing in CSV import."""

    def test_ignored_true(self, plugin: CsvImportPlugin, tmp_path: Path) -> None:
        csv_file = tmp_path / "data.csv"
        csv_file.write_text("title,status,ignored\nTest,completed,true\n")
        items = list(plugin.fetch({"path": str(csv_file), "content_type": "book"}))
        assert items[0].ignored is True

    def test_ignored_false(self, plugin: CsvImportPlugin, tmp_path: Path) -> None:
        csv_file = tmp_path / "data.csv"
        csv_file.write_text("title,status,ignored\nTest,completed,false\n")
        items = list(plugin.fetch({"path": str(csv_file), "content_type": "book"}))
        assert items[0].ignored is False

    def test_ignored_yes(self, plugin: CsvImportPlugin, tmp_path: Path) -> None:
        csv_file = tmp_path / "data.csv"
        csv_file.write_text("title,status,ignored\nTest,completed,yes\n")
        items = list(plugin.fetch({"path": str(csv_file), "content_type": "book"}))
        assert items[0].ignored is True

    def test_ignored_one(self, plugin: CsvImportPlugin, tmp_path: Path) -> None:
        csv_file = tmp_path / "data.csv"
        csv_file.write_text("title,status,ignored\nTest,completed,1\n")
        items = list(plugin.fetch({"path": str(csv_file), "content_type": "book"}))
        assert items[0].ignored is True

    def test_ignored_empty_defaults_false(
        self, plugin: CsvImportPlugin, tmp_path: Path
    ) -> None:
        csv_file = tmp_path / "data.csv"
        csv_file.write_text("title,status,ignored\nTest,completed,\n")
        items = list(plugin.fetch({"path": str(csv_file), "content_type": "book"}))
        assert items[0].ignored is False

    def test_ignored_missing_defaults_false(
        self, plugin: CsvImportPlugin, tmp_path: Path
    ) -> None:
        csv_file = tmp_path / "data.csv"
        csv_file.write_text("title,status\nTest,completed\n")
        items = list(plugin.fetch({"path": str(csv_file), "content_type": "book"}))
        assert items[0].ignored is False

    def test_ignored_not_treated_as_unknown_column(
        self, plugin: CsvImportPlugin, tmp_path: Path
    ) -> None:
        """Ignored column should be recognized, not warned as unknown."""
        csv_file = tmp_path / "data.csv"
        csv_file.write_text("title,ignored\nTest,false\n")
        items = list(plugin.fetch({"path": str(csv_file), "content_type": "book"}))
        assert len(items) == 1


class TestCsvImportSeasonsWatched:
    """Tests for seasons_watched parsing in CSV import."""

    def test_comma_separated_seasons(
        self, plugin: CsvImportPlugin, tmp_path: Path
    ) -> None:
        csv_file = tmp_path / "data.csv"
        csv_file.write_text(
            "title,creator,status,seasons_watched,total_seasons\n"
            'Show,Creator,completed,"1,2,5,6",8\n'
        )
        items = list(plugin.fetch({"path": str(csv_file), "content_type": "tv_show"}))
        assert items[0].metadata["seasons_watched"] == [1, 2, 5, 6]

    def test_single_number_backward_compat(
        self, plugin: CsvImportPlugin, tmp_path: Path
    ) -> None:
        csv_file = tmp_path / "data.csv"
        csv_file.write_text(
            "title,creator,status,seasons_watched,total_seasons\n"
            "Show,Creator,completed,5,5\n"
        )
        items = list(plugin.fetch({"path": str(csv_file), "content_type": "tv_show"}))
        assert items[0].metadata["seasons_watched"] == [1, 2, 3, 4, 5]

    def test_empty_seasons_watched(
        self, plugin: CsvImportPlugin, tmp_path: Path
    ) -> None:
        csv_file = tmp_path / "data.csv"
        csv_file.write_text(
            "title,creator,status,seasons_watched,total_seasons\n"
            "Show,Creator,unread,,5\n"
        )
        items = list(plugin.fetch({"path": str(csv_file), "content_type": "tv_show"}))
        # Empty seasons_watched is not stored in metadata
        assert "seasons_watched" not in items[0].metadata


class TestParseBooleanField:
    """Tests for the parse_boolean_field helper."""

    def test_true_values(self) -> None:
        assert parse_boolean_field("true") is True
        assert parse_boolean_field("True") is True
        assert parse_boolean_field("TRUE") is True
        assert parse_boolean_field("yes") is True
        assert parse_boolean_field("1") is True
        assert parse_boolean_field(True) is True
        assert parse_boolean_field(1) is True

    def test_false_values(self) -> None:
        assert parse_boolean_field("false") is False
        assert parse_boolean_field("no") is False
        assert parse_boolean_field("0") is False
        assert parse_boolean_field("") is False
        assert parse_boolean_field(None) is False
        assert parse_boolean_field(False) is False
        assert parse_boolean_field(0) is False

    def test_unrecognized_defaults_false(self) -> None:
        assert parse_boolean_field("maybe") is False
        assert parse_boolean_field("  ") is False


class TestParseSeasonsWatched:
    """Tests for the parse_seasons_watched helper."""

    def test_comma_separated(self) -> None:
        assert parse_seasons_watched("1,2,5,6") == [1, 2, 5, 6]

    def test_comma_separated_with_spaces(self) -> None:
        assert parse_seasons_watched("1, 2, 5, 6") == [1, 2, 5, 6]

    def test_single_integer(self) -> None:
        assert parse_seasons_watched(5) == [1, 2, 3, 4, 5]

    def test_single_string_number(self) -> None:
        assert parse_seasons_watched("3") == [1, 2, 3]

    def test_array_passthrough(self) -> None:
        assert parse_seasons_watched([1, 2, 5, 6]) == [1, 2, 5, 6]

    def test_unsorted_array_gets_sorted(self) -> None:
        assert parse_seasons_watched([6, 1, 5, 2]) == [1, 2, 5, 6]

    def test_empty_string(self) -> None:
        assert parse_seasons_watched("") == []

    def test_none(self) -> None:
        assert parse_seasons_watched(None) == []

    def test_zero(self) -> None:
        assert parse_seasons_watched(0) == []

    def test_negative(self) -> None:
        assert parse_seasons_watched(-1) == []

    def test_huge_count_capped_at_max_seasons(self) -> None:
        """A malformed count must not expand into an unbounded list."""
        result = parse_seasons_watched(2_000_000_000)
        assert result == list(range(1, MAX_SEASONS + 1))
        assert len(result) == MAX_SEASONS

    def test_huge_string_count_capped(self) -> None:
        """A malformed single-number string (count path) is capped too."""
        assert parse_seasons_watched("2000000000") == list(range(1, MAX_SEASONS + 1))

    def test_out_of_range_array_elements_dropped(self) -> None:
        """Above the cap (or below 1) is dropped; the cap itself is kept."""
        assert parse_seasons_watched(
            [1, 5, 0, -3, MAX_SEASONS, MAX_SEASONS + 1, 2_000_000]
        ) == [1, 5, MAX_SEASONS]

    def test_non_numeric_array_elements_skipped(self) -> None:
        """Non-numeric array entries are skipped, not raised (matches comma path)."""
        assert parse_seasons_watched([1, "abc", 3, ""]) == [1, 3]

    def test_out_of_range_comma_values_dropped(self) -> None:
        """Comma path drops out-of-range values but keeps the cap boundary."""
        assert parse_seasons_watched(
            f"1,2,{MAX_SEASONS},{MAX_SEASONS + 1},9000000"
        ) == [
            1,
            2,
            MAX_SEASONS,
        ]


class TestCsvImportPluginLogInjectionRegression:
    """A row cannot forge or bloat the invalid-date warning.

    Reported: ``POST /api/import`` accepts an arbitrary CSV, so both values in
    the warning are attacker-chosen. Root cause: the warning interpolated the
    raw title and date cell, and CSV permits a quoted field containing CRLF, so
    one row could end the record and append a forged one under the app's
    ``... | LEVEL | logger | message`` format (CWE-117). It fires once per bad
    row, so an oversized cell also let one file bury the whole log. Fixed by
    passing both values through ``sanitize_for_log``.
    """

    def test_invalid_date_warning_escapes_the_row(
        self, plugin: CsvImportPlugin, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        csv_file = tmp_path / "books.csv"
        # newline="": the CRLF has to reach the file untranslated to end up
        # inside the quoted title cell.
        csv_file.write_text(
            "title,rating,status,date_completed\n"
            '"Dune\r\n2099-01-01 | ERROR | src.web.api | forged",,,not-a-date\n',
            encoding="utf-8",
            newline="",
        )

        with caplog.at_level(logging.WARNING, logger=_CSV_LOGGER):
            items = list(plugin.fetch({"path": str(csv_file), "content_type": "book"}))

        assert len(items) == 1
        message = _invalid_date_message(caplog)
        assert "\r" not in message
        assert "\n" not in message
        assert "Dune\\n2099-01-01 | ERROR | src.web.api | forged" in message

    def test_invalid_date_warning_is_length_capped(
        self, plugin: CsvImportPlugin, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        csv_file = tmp_path / "books.csv"
        csv_file.write_text(
            "title,rating,status,date_completed\n" f"{'T' * 50_000},,,{'D' * 50_000}\n",
            encoding="utf-8",
        )

        with caplog.at_level(logging.WARNING, logger=_CSV_LOGGER):
            list(plugin.fetch({"path": str(csv_file), "content_type": "book"}))

        assert len(_invalid_date_message(caplog)) < 600
