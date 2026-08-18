"""Tests for the generic CSV importer."""

import logging
from datetime import date
from pathlib import Path

import pytest

from src.ingestion.importers.base import (
    ImportedRow,
    ImporterError,
    ParsedRow,
    SkippedRow,
)
from src.ingestion.importers.generic_csv.generic_csv import CsvImporter
from src.models.content import ConsumptionStatus, ContentItem, ContentType

IMPORTER = CsvImporter()

CSV_LOGGER = "src.ingestion.importers.generic_csv.generic_csv"
ROW_LOGGER = "src.ingestion.importers.rows"


def parse(text: str, content_type: ContentType = ContentType.BOOK) -> list[ParsedRow]:
    return list(IMPORTER.parse(text, content_type))


def items(text: str, content_type: ContentType = ContentType.BOOK) -> list[ContentItem]:
    return [
        row.item for row in parse(text, content_type) if isinstance(row, ImportedRow)
    ]


def reported(text: str, content_type: ContentType = ContentType.BOOK) -> list[tuple]:
    return [
        (row.number, row.reason)
        for row in parse(text, content_type)
        if isinstance(row, SkippedRow)
    ]


class TestBooks:
    def test_every_book_column_reaches_its_field(self) -> None:
        parsed = items(
            "title,author,rating,status,date_completed,review,notes,isbn,pages,year_published,genre\n"
            "The Name of the Wind,Patrick Rothfuss,5,completed,2024-06-15,Great book,,978-0756404741,662,2007,Fantasy\n"
        )

        assert len(parsed) == 1
        item = parsed[0]
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

    def test_each_row_gets_its_own_status_and_rating(self) -> None:
        parsed = items(
            "title,author,rating,status\n"
            "Book One,Author A,5,completed\n"
            "Book Two,Author B,3,in_progress\n"
            "Book Three,Author C,,unread\n"
        )

        assert [item.title for item in parsed] == ["Book One", "Book Two", "Book Three"]
        assert [item.rating for item in parsed] == [5, 3, None]
        assert [item.status for item in parsed] == [
            ConsumptionStatus.COMPLETED.value,
            ConsumptionStatus.CURRENTLY_CONSUMING.value,
            ConsumptionStatus.UNREAD.value,
        ]

    def test_an_unknown_status_falls_back_to_unread(self) -> None:
        parsed = items("title,status\nTest,something_else\n")

        assert parsed[0].status == ConsumptionStatus.UNREAD.value

    def test_a_zero_rating_is_unrated(self) -> None:
        assert items("title,rating\nTest,0\n")[0].rating is None


class TestOtherContentTypes:
    def test_a_tv_row_expands_its_watched_season_count(self) -> None:
        parsed = items(
            "title,creator,rating,status,seasons_watched,total_seasons,year,genre\n"
            "Breaking Bad,Vince Gilligan,5,completed,5,5,2008,Drama\n",
            ContentType.TV_SHOW,
        )

        assert parsed[0].author == "Vince Gilligan"
        assert parsed[0].content_type == ContentType.TV_SHOW.value
        assert parsed[0].metadata["seasons_watched"] == [1, 2, 3, 4, 5]
        assert parsed[0].metadata["seasons"] == "5"
        assert parsed[0].metadata["release_year"] == "2008"

    def test_a_blank_seasons_watched_cell_stores_nothing(self) -> None:
        parsed = items(
            "title,creator,status,seasons_watched,total_seasons\n"
            "Show,Creator,unread,,5\n",
            ContentType.TV_SHOW,
        )

        assert "seasons_watched" not in parsed[0].metadata

    def test_a_game_row_wraps_its_single_platform_and_genre(self) -> None:
        parsed = items(
            "title,developer,rating,status,platform,genre,hours_played\n"
            "The Witcher 3,CD Projekt Red,5,completed,PC,RPG,120\n",
            ContentType.VIDEO_GAME,
        )

        assert parsed[0].author == "CD Projekt Red"
        assert parsed[0].metadata["platforms"] == ["PC"]
        assert parsed[0].metadata["genres"] == ["RPG"]
        assert parsed[0].metadata["playtime_hours"] == "120"


class TestRefusals:
    def test_a_file_with_no_title_column_is_refused_whole(self) -> None:
        with pytest.raises(ImporterError, match="missing required column"):
            parse("name,rating\nTest,5\n")

    def test_a_format_taking_any_type_refuses_to_guess_one(self) -> None:
        with pytest.raises(ImporterError, match="needs a content type"):
            list(IMPORTER.parse("title\nDune\n"))

    def test_an_unparseable_date_keeps_the_row(self) -> None:
        parsed = items("title,date_completed\nTest,not-a-date\n")

        assert len(parsed) == 1
        assert parsed[0].date_completed is None


class TestSkippedRows:
    """Every dropped row is reported, because a silent one loses data.

    Bug: a blank title vanished, and a row shorter than its header crashed the
    import on ``.strip()`` of the ``None`` ``csv.DictReader`` leaves behind.
    """

    def test_a_row_shorter_than_its_header_is_skipped_not_a_crash(self) -> None:
        text = (
            "title,author,rating,status,review\n"
            "Dune,Frank Herbert,5,completed,Loved it\n"
            "Neuromancer,William Gibson\n"
            "Ubik,Philip K. Dick,4,completed,Odd\n"
        )

        assert [item.title for item in items(text)] == ["Dune", "Ubik"]
        assert reported(text) == [(3, "3 fields short of the header")]

    def test_a_row_longer_than_its_header_is_skipped_not_imported_mangled(self) -> None:
        """An unquoted comma in a title imported silently shifted.

        ``csv.DictReader`` read the author out of the title's tail and the
        rating out of the author, landing an unrated book nobody was told about.
        """
        text = (
            "title,author,rating\n"
            "Dune, Part Two,Frank Herbert,5\n"
            "Ubik,Philip K. Dick,4\n"
        )

        assert [item.title for item in items(text)] == ["Ubik"]
        assert reported(text) == [(2, "1 field more than the header")]

    def test_a_blank_title_is_skipped_with_its_line_number(self) -> None:
        text = (
            "title,author,rating,status\n"
            ",Author A,5,completed\n"
            "Valid Book,Author B,4,completed\n"
        )

        assert [item.title for item in items(text)] == ["Valid Book"]
        assert reported(text) == [(2, "no title")]

    def test_a_quoted_newline_does_not_shift_the_line_numbers(self) -> None:
        """The number is the file line, so a multi-line cell has to be counted.

        A review with a line break is ordinary, and numbering rows by position
        would report the wrong line for every row after one.
        """
        parsed = parse('title,review\nDune,"line one\nline two"\n,Orphaned\n')

        assert [row.number for row in parsed] == [3, 4]
        assert isinstance(parsed[1], SkippedRow)


class TestIgnoredColumn:
    """Regression: a re-import cleared the flag on every untouched row.

    Cause: ``csv.DictReader`` puts every header key into every row, so a blank
    cell read as a stated "not ignored". Fix: only a real value counts.
    """

    def test_a_blank_ignored_cell_states_nothing(self) -> None:
        assert items("title,status,ignored\nTest,completed,\n")[0].ignored is None

    def test_an_absent_ignored_column_states_nothing(self) -> None:
        assert items("title,status\nTest,completed\n")[0].ignored is None

    def test_a_stated_ignored_value_still_wins(self) -> None:
        assert items("title,status,ignored\nTest,completed,true\n")[0].ignored is True


class TestTemplates:
    def test_the_shipped_books_template_still_parses(self) -> None:
        parsed = items(Path("templates/books.csv").read_text(encoding="utf-8"))

        assert len(parsed) == 1
        assert parsed[0].title == "The Name of the Wind"


FORGED_TITLE = "Dune\nImported 9999 items from CSV file"
ESCAPED_TITLE = "Dune\\nImported 9999 items from CSV file"


class TestLogInjectionRegression:
    """Regression: an imported cell forged log entries.

    Bug: the title, the date and the unknown-column list were logged raw, and
    a CSV field carries any character. Fix: ``sanitize_for_log`` at every sink.
    """

    @staticmethod
    def _messages(caplog: pytest.LogCaptureFixture, logger: str) -> list[str]:
        return [
            record.getMessage() for record in caplog.records if record.name == logger
        ]

    def test_a_newline_in_a_title_cannot_forge_a_log_entry(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.WARNING, logger=ROW_LOGGER):
            parsed = items(f'title,date_completed\n"{FORGED_TITLE}",yesterday\n')

        # The item keeps the title it was given; only the log line is escaped.
        assert [item.title for item in parsed] == [FORGED_TITLE]
        assert self._messages(caplog, ROW_LOGGER) == [
            f"Invalid date format for '{ESCAPED_TITLE}': yesterday. "
            "Expected YYYY-MM-DD."
        ]

    def test_a_newline_in_a_header_cannot_forge_a_log_entry(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.WARNING, logger=CSV_LOGGER):
            items('title,"colour\nImported 9999 items"\nDune,blue\n')

        assert self._messages(caplog, CSV_LOGGER) == [
            "CSV contains unknown columns that will be ignored: "
            "colour\\nImported 9999 items"
        ]
