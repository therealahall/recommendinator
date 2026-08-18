"""Tests for The StoryGraph CSV export importer."""

from datetime import date

import pytest

from src.ingestion.importers.base import ImportedRow, ParsedRow, SkippedRow
from src.ingestion.importers.storygraph_csv.storygraph_csv import StorygraphCsvImporter
from src.models.content import ConsumptionStatus, ContentItem, ContentType

IMPORTER = StorygraphCsvImporter()

# A full StoryGraph export header row, matching the columns the site emits.
HEADER = (
    "Title,Authors,Contributors,ISBN/UID,Format,Read Status,Date Added,"
    "Last Date Read,Dates Read,Read Count,Moods,Pace,"
    "Character- or Plot-Driven?,Strong Character Development?,"
    "Loveable Characters?,Diverse Characters?,Flawed Characters?,"
    "Star Rating,Review,Content Warnings,Content Warning Description,Tags,Owned?"
)


def parse(rows: str) -> list[ParsedRow]:
    return list(IMPORTER.parse(f"{HEADER}\n{rows}"))


def items(rows: str) -> list[ContentItem]:
    return [row.item for row in parse(rows) if isinstance(row, ImportedRow)]


def reported(rows: str) -> list[tuple]:
    return [
        (row.number, row.reason) for row in parse(rows) if isinstance(row, SkippedRow)
    ]


class TestExport:
    def test_a_read_and_an_unread_book_keep_their_own_fields(self) -> None:
        parsed = items(
            "The Fifth Season,N. K. Jemisin,,9780316229296,Paperback,read,"
            "2024/01/01,2024/03/15,2024/01/01-2024/03/15,1,adventurous,medium,"
            "plot,Yes,Yes,Yes,Yes,4.5,A stunning read.,,,fantasy,Yes\n"
            "The Way of Kings,Brandon Sanderson,,,Hardcover,to-read,"
            "2024/02/01,,,0,,,,,,,,,,,,,No\n"
        )

        assert len(parsed) == 2
        first = parsed[0]
        assert first.title == "The Fifth Season"
        assert first.author == "N. K. Jemisin"
        assert first.content_type == ContentType.BOOK
        assert first.rating == 5
        assert first.status == ConsumptionStatus.COMPLETED
        assert first.date_completed == date(2024, 3, 15)
        assert first.review == "A stunning read."
        assert first.id == "9780316229296"
        assert first.source == "storygraph_csv"

        second = parsed[1]
        assert second.title == "The Way of Kings"
        assert second.author == "Brandon Sanderson"
        assert second.rating is None
        assert second.status == ConsumptionStatus.UNREAD
        assert second.date_completed is None
        assert second.review is None
        assert second.id is None

    def test_did_not_finish_counts_as_completed_and_keeps_the_raw_status(self) -> None:
        """A rated-then-abandoned book is a real preference signal.

        It scores as completed, and the true StoryGraph status stays in
        metadata so nothing is lost.
        """
        parsed = items("Gave Up,Some Author,,,,did-not-finish," + "," * 16 + "\n")

        assert parsed[0].status == ConsumptionStatus.COMPLETED
        assert parsed[0].metadata["read_status"] == "did-not-finish"

    def test_an_unknown_read_status_falls_back_to_unread(self) -> None:
        parsed = items("Mystery Status,Some Author,,,,wishlist," + "," * 16 + "\n")

        assert parsed[0].status == ConsumptionStatus.UNREAD

    def test_the_read_status_is_matched_case_insensitively(self) -> None:
        parsed = items(
            "Shouty Read,Author,,,,READ," + "," * 16 + "\n"
            "Mixed Case,Author,,,,Currently-Reading," + "," * 16 + "\n"
            "Title Case,Author,,,,To-Read," + "," * 16 + "\n"
        )

        assert [item.status for item in parsed] == [
            ConsumptionStatus.COMPLETED,
            ConsumptionStatus.CURRENTLY_CONSUMING,
            ConsumptionStatus.UNREAD,
        ]

    def test_an_unparseable_date_keeps_the_row(self) -> None:
        parsed = items(
            "Bad Date,Some Author,,,,read,2024/01/01,not-a-date," + "," * 14 + "\n"
        )

        assert parsed[0].date_completed is None

    def test_every_rich_signal_reaches_metadata(self) -> None:
        parsed = items(
            "Rich Signals,Author Name,Narrator Person,9781234567890,Audiobook,read,"
            "2024/01/01,2024/03/15,2024/01/01-2024/03/15,2,"
            '"adventurous, dark",fast,character,Yes,Yes,No,Yes,4,Loved it,'
            '"violence, grief",Some description,"fantasy, favorites",Yes\n'
        )

        meta = parsed[0].metadata
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

    def test_an_export_missing_optional_columns_still_parses(self) -> None:
        """StoryGraph tweaks its export shape, so a trimmed header is normal."""
        parsed = list(
            IMPORTER.parse(
                "Title,Authors,Read Status,Star Rating\nSlim Export,Terse Author,read,4\n"
            )
        )

        assert len(parsed) == 1
        assert isinstance(parsed[0], ImportedRow)
        assert parsed[0].item.title == "Slim Export"
        assert parsed[0].item.rating == 4
        assert parsed[0].item.status == ConsumptionStatus.COMPLETED
        assert parsed[0].item.id is None


class TestQuarterStarRatings:
    """StoryGraph rates in quarter stars; the library stores 1-5.

    Rounding half up rather than truncating is what keeps a 3.5 from reading as
    a 3, and a 0 is unrated rather than clamped up to 1.
    """

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [("3.5", 4), ("3.25", 3), ("0", None), ("9", 5), ("abc", None)],
    )
    def test_a_star_rating_lands_on_its_whole_star(
        self, raw: str, expected: int | None
    ) -> None:
        parsed = items("Rated,Some Author,,,,read," + "," * 11 + raw + "," * 5 + "\n")

        assert parsed[0].rating == expected


class TestSkippedRows:
    def test_a_row_longer_than_its_header_is_skipped_not_imported_mangled(self) -> None:
        """An unquoted comma in a title imported silently shifted.

        Every cell after it moved a column left, so the book landed with the
        wrong author and status, and nothing was reported.
        """
        rows = (
            "Dune, Part Two,Frank Herbert,,,,read," + "," * 16 + "\n"
            "Real Book,Real Author,,,,read," + "," * 16 + "\n"
        )

        assert [item.title for item in items(rows)] == ["Real Book"]
        assert reported(rows) == [(2, "1 field more than the header")]

    def test_a_row_with_no_title_is_skipped_with_its_line_number(self) -> None:
        rows = (
            ",Ghost Author,,,,read," + "," * 16 + "\n"
            "Real Book,Real Author,,,,read," + "," * 16 + "\n"
        )

        assert [item.title for item in items(rows)] == ["Real Book"]
        assert reported(rows) == [(2, "no title")]

    def test_a_row_shorter_than_its_header_is_skipped_not_a_crash(self) -> None:
        """A truncated row used to import as a book with every signal blank.

        Reporting it instead is what tells the operator their file is short,
        rather than leaving a half-empty book in the library.
        """
        rows = (
            "Short Row,Terse Author,,,,read\n"
            "Real Book,Real Author,,,,read," + "," * 16 + "\n"
        )

        assert [item.title for item in items(rows)] == ["Real Book"]
        assert reported(rows) == [(2, "17 fields short of the header")]
