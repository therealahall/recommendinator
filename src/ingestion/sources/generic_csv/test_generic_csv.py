"""Tests for generic CSV import plugin."""

import logging
from collections.abc import Callable
from datetime import date
from pathlib import Path

import pytest

from src.ingestion.plugin_base import SourceError
from src.ingestion.sources.generic_csv.generic_csv import (
    CsvImportPlugin,
    parse_boolean_field,
    parse_seasons_watched,
)
from src.models.content import ConsumptionStatus, ContentType
from src.utils.series import MAX_SEASONS


@pytest.fixture()
def plugin() -> CsvImportPlugin:
    """Create a CsvImportPlugin instance."""
    return CsvImportPlugin()


class TestCsvImportPluginValidation:
    """Tests for CsvImportPlugin config validation."""

    def test_validate_missing_csv_path(self, plugin: CsvImportPlugin) -> None:
        errors = plugin.validate_config({"content_type": "book"})
        assert any("path" in error for error in errors)

    def test_validate_nonexistent_file(
        self, plugin: CsvImportPlugin, tmp_path: Path
    ) -> None:
        errors = plugin.validate_config(
            {"path": str(tmp_path / "missing.csv"), "content_type": "book"}
        )
        assert any("not found" in error for error in errors)

    def test_validate_invalid_content_type(
        self, plugin: CsvImportPlugin, tmp_path: Path
    ) -> None:
        csv_file = tmp_path / "books.csv"
        csv_file.write_text("title\n")
        errors = plugin.validate_config(
            {"path": str(csv_file), "content_type": "podcast"}
        )
        assert any("Invalid content_type" in error for error in errors)


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
        assert item.metadata["genres"] == ["Fantasy"]

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
        assert item.metadata["seasons"] == "5"
        assert item.metadata["release_year"] == "2008"


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
        assert item.metadata["platforms"] == ["PC"]
        assert item.metadata["genres"] == ["RPG"]
        assert item.metadata["playtime_hours"] == "120"


class TestCsvImportPluginStatusMapping:
    """Tests for status string mapping."""

    def test_status_unknown_defaults_to_unread(
        self, plugin: CsvImportPlugin, tmp_path: Path
    ) -> None:
        csv_file = tmp_path / "data.csv"
        csv_file.write_text("title,status\nTest,something_else\n")
        items = list(plugin.fetch({"path": str(csv_file), "content_type": "book"}))
        assert items[0].status == ConsumptionStatus.UNREAD.value

    def test_status_wishlist(self, plugin: CsvImportPlugin, tmp_path: Path) -> None:
        csv_file = tmp_path / "data.csv"
        csv_file.write_text("title,status\nTest,wishlist\n")
        items = list(plugin.fetch({"path": str(csv_file), "content_type": "book"}))
        assert items[0].status == ConsumptionStatus.UNREAD.value


class TestCsvImportPluginRating:
    """Tests for rating normalization."""

    def test_zero_rating_is_none(self, plugin: CsvImportPlugin, tmp_path: Path) -> None:
        csv_file = tmp_path / "data.csv"
        csv_file.write_text("title,rating\nTest,0\n")
        items = list(plugin.fetch({"path": str(csv_file), "content_type": "book"}))
        assert items[0].rating is None


class TestCsvImportPluginErrors:
    """Tests for error handling."""

    def test_file_not_found_raises_source_error(
        self, plugin: CsvImportPlugin, tmp_path: Path
    ) -> None:
        with pytest.raises(SourceError, match="CSV file not found"):
            list(
                plugin.fetch(
                    {"path": str(tmp_path / "missing.csv"), "content_type": "book"}
                )
            )

    def test_missing_title_column_raises_source_error(
        self, plugin: CsvImportPlugin, tmp_path: Path
    ) -> None:
        csv_file = tmp_path / "data.csv"
        csv_file.write_text("name,rating\nTest,5\n")
        with pytest.raises(SourceError, match="missing required column"):
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


class TestCsvTemplates:
    """Tests that template files are valid and can be parsed."""

    @pytest.fixture()
    def templates_dir(self, allowed_source_roots: Callable[[Path], None]) -> Path:
        """The repository templates, added to the file-import allowlist."""
        directory = Path("templates")
        allowed_source_roots(directory)
        return directory

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


class TestCsvImportIgnored:
    """Tests for ignored field parsing in CSV import."""

    def test_blank_ignored_cell_is_unspecified_regression(
        self, plugin: CsvImportPlugin, tmp_path: Path
    ) -> None:
        """A blank cell under an ignored header says nothing about the flag.

        Bug reported: the export-edit-re-import round trip the docs recommend
        cleared the ignore flag on every row the user had not touched. An
        export always writes the ``ignored`` column, and an editor that empties
        a cell leaves the header in place.
        Root cause: ``csv.DictReader`` puts every header key into every row, so
        a blank cell read as the string "" and parsed as False — a stated "not
        ignored" that storage duly wrote over the user's flag.
        Fix: only a real value counts. Blank means the file said nothing, which
        is ``ignored=None``, which storage preserves.
        """
        csv_file = tmp_path / "data.csv"
        csv_file.write_text("title,status,ignored\nTest,completed,\n")
        items = list(plugin.fetch({"path": str(csv_file), "content_type": "book"}))
        assert items[0].ignored is None

    def test_ignored_column_absent_is_unspecified(
        self, plugin: CsvImportPlugin, tmp_path: Path
    ) -> None:
        """A file with no ignored column says nothing about the flag.

        ``ignored=None`` is the "not specified by this source" contract on
        ContentItem, which storage honours by preserving the stored value.
        """
        csv_file = tmp_path / "data.csv"
        csv_file.write_text("title,status\nTest,completed\n")
        items = list(plugin.fetch({"path": str(csv_file), "content_type": "book"}))
        assert items[0].ignored is None


class TestCsvImportSeasonsWatched:
    """Tests for seasons_watched parsing in CSV import."""

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


class TestParseSeasonsWatched:
    """Tests for the parse_seasons_watched helper."""

    def test_comma_separated(self) -> None:
        assert parse_seasons_watched("1,2,5,6") == [1, 2, 5, 6]

    def test_single_integer(self) -> None:
        assert parse_seasons_watched(5) == [1, 2, 3, 4, 5]

    def test_unsorted_array_gets_sorted(self) -> None:
        assert parse_seasons_watched([6, 1, 5, 2]) == [1, 2, 5, 6]

    def test_huge_count_capped_at_max_seasons(self) -> None:
        """A malformed count must not expand into an unbounded list."""
        result = parse_seasons_watched(2_000_000_000)
        assert result == list(range(1, MAX_SEASONS + 1))
        assert len(result) == MAX_SEASONS

    def test_out_of_range_array_elements_dropped(self) -> None:
        """Above the cap (or below 1) is dropped; the cap itself is kept."""
        assert parse_seasons_watched(
            [1, 5, 0, -3, MAX_SEASONS, MAX_SEASONS + 1, 2_000_000]
        ) == [1, 5, MAX_SEASONS]


class TestCsvImportPathContainmentRegression:
    """Regression: source config as an arbitrary-file reader.

    Bug: ``path`` came straight from HTTP-writable source config, so any host
    file could be imported. Cause: no containment. Fix: validate and fetch
    resolve it against ``security.allowed_source_roots``.
    """

    def test_validate_refuses_a_path_outside_every_root(
        self, plugin: CsvImportPlugin
    ) -> None:
        errors = plugin.validate_config({"path": "/etc/passwd", "content_type": "book"})
        assert errors == [
            "Path is outside the allowed source roots: /etc/passwd. "
            "Add its directory to security.allowed_source_roots in config.yaml."
        ]

    def test_fetch_refuses_and_yields_nothing(
        self, plugin: CsvImportPlugin, tmp_path: Path
    ) -> None:
        outside = tmp_path.parent / f"{tmp_path.name}-outside"
        outside.mkdir()
        secret = outside / "secret.csv"
        secret.write_text("title\nLeaked\n")

        collected = []
        with pytest.raises(SourceError, match="outside the allowed source roots"):
            for item in plugin.fetch({"path": str(secret), "content_type": "book"}):
                collected.append(item)

        # list() would discard these, leaving the leak half of the name unproven.
        assert collected == []


CSV_LOGGER = "src.ingestion.sources.generic_csv.generic_csv"

FORGED_TITLE = "Dune\nImported 9999 items from CSV file"
ESCAPED_TITLE = "Dune\\nImported 9999 items from CSV file"


class TestCsvImportLogInjectionRegression:
    """Regression: an imported cell forged log entries.

    Bug: the title, the date and the unknown-column list were logged raw, and a
    CSV field carries any character. Cause: no sanitiser on this path. Fix:
    ``sanitize_for_log`` at every sink.
    """

    @staticmethod
    def _messages(caplog: pytest.LogCaptureFixture) -> list[str]:
        return [
            record.getMessage()
            for record in caplog.records
            if record.name == CSV_LOGGER
        ]

    def test_a_newline_in_a_title_cannot_forge_a_log_entry(
        self, plugin: CsvImportPlugin, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        csv_file = tmp_path / "books.csv"
        csv_file.write_text(f'title,date_completed\n"{FORGED_TITLE}",yesterday\n')

        with caplog.at_level(logging.WARNING, logger=CSV_LOGGER):
            items = list(plugin.fetch({"path": str(csv_file), "content_type": "book"}))

        # The item keeps the title it was given; only the log line is escaped.
        assert [item.title for item in items] == [FORGED_TITLE]
        assert self._messages(caplog) == [
            f"Invalid date format for '{ESCAPED_TITLE}': yesterday. "
            "Expected YYYY-MM-DD."
        ]

    def test_a_newline_in_a_header_cannot_forge_a_log_entry(
        self, plugin: CsvImportPlugin, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        csv_file = tmp_path / "books.csv"
        csv_file.write_text('title,"colour\nImported 9999 items"\nDune,blue\n')

        with caplog.at_level(logging.WARNING, logger=CSV_LOGGER):
            list(plugin.fetch({"path": str(csv_file), "content_type": "book"}))

        assert self._messages(caplog) == [
            "CSV contains unknown columns that will be ignored: "
            "colour\\nImported 9999 items"
        ]
