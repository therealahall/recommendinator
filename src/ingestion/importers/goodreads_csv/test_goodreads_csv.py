"""Tests for the Goodreads CSV export importer."""

from datetime import date

from src.ingestion.importers.base import ImportedRow, ParsedRow, SkippedRow
from src.ingestion.importers.goodreads_csv.goodreads_csv import GoodreadsCsvImporter
from src.models.content import ConsumptionStatus, ContentItem, ContentType

IMPORTER = GoodreadsCsvImporter()


def parse(text: str) -> list[ParsedRow]:
    return list(IMPORTER.parse(text))


def items(text: str) -> list[ContentItem]:
    return [row.item for row in parse(text) if isinstance(row, ImportedRow)]


def reported(text: str) -> list[tuple]:
    return [
        (row.number, row.reason) for row in parse(text) if isinstance(row, SkippedRow)
    ]


class TestExport:
    def test_a_read_and_an_unread_book_keep_their_own_fields(self) -> None:
        parsed = items(
            "Book Id,Title,Author,My Rating,Exclusive Shelf,Date Read,My Review\n"
            "123,Test Book,Test Author,4,read,2025/01/15,Great book!\n"
            "456,Another Book,Another Author,0,to-read,,\n"
        )

        assert len(parsed) == 2
        assert parsed[0].title == "Test Book"
        assert parsed[0].author == "Test Author"
        assert parsed[0].rating == 4
        assert parsed[0].status == ConsumptionStatus.COMPLETED
        assert parsed[0].date_completed == date(2025, 1, 15)
        assert parsed[0].review == "Great book!"
        assert parsed[0].content_type == ContentType.BOOK
        assert parsed[0].source == "goodreads_csv"
        assert parsed[1].title == "Another Book"
        assert parsed[1].rating is None
        assert parsed[1].status == ConsumptionStatus.UNREAD
        assert parsed[1].date_completed is None
        assert parsed[1].review is None

    def test_the_currently_reading_shelf_maps_to_in_progress(self) -> None:
        parsed = items(
            "Book Id,Title,Author,My Rating,Exclusive Shelf,Date Read\n"
            "789,Reading Now,Author Name,0,currently-reading,\n"
        )

        assert parsed[0].status == ConsumptionStatus.CURRENTLY_CONSUMING

    def test_the_book_id_becomes_the_external_id(self) -> None:
        parsed = items(
            "Book Id,Title,Author,My Rating,Exclusive Shelf,"
            "ISBN,ISBN13,Number of Pages,Year Published,Publisher\n"
            '123,Test Book,Test Author,4,read,"=""1234567890=""","=""9781234567890=""",'
            "350,2020,Test Publisher\n"
        )

        assert parsed[0].id == "123"
        assert parsed[0].metadata["book_id"] == "123"
        assert parsed[0].metadata["pages"] == "350"
        assert parsed[0].metadata["year_published"] == "2020"
        assert parsed[0].metadata["publisher"] == "Test Publisher"

    def test_an_unparseable_date_keeps_the_row(self) -> None:
        parsed = items(
            "Book Id,Title,Author,My Rating,Exclusive Shelf,Date Read\n"
            "123,Test Book,Test Author,4,read,not-a-date\n"
        )

        assert parsed[0].date_completed is None


class TestSkippedRows:
    def test_a_row_longer_than_its_header_is_skipped_not_imported_mangled(self) -> None:
        """An unquoted comma in a title imported silently shifted.

        Every cell after it moved a column left, so the book landed with
        another book's author and no rating, and nothing was reported.
        """
        text = (
            "Book Id,Title,Author,My Rating,Exclusive Shelf\n"
            "123,Dune, Part Two,Frank Herbert,5,read\n"
            "456,Valid Book,Author,4,read\n"
        )

        assert [item.title for item in items(text)] == ["Valid Book"]
        assert reported(text) == [(2, "1 field more than the header")]

    def test_a_row_with_no_title_is_skipped_with_its_line_number(self) -> None:
        text = (
            "Book Id,Title,Author,My Rating,Exclusive Shelf\n"
            "123,,Test Author,4,read\n"
            "456,Valid Book,Author,4,read\n"
        )

        assert [item.title for item in items(text)] == ["Valid Book"]
        assert reported(text) == [(2, "no title")]

    def test_a_row_shorter_than_its_header_is_skipped_not_a_crash(self) -> None:
        """A hand-edited export used to take the whole import down.

        ``csv.DictReader`` fills the missing trailing fields with ``None``, and
        ``.strip()`` on one raised straight out of the parse.
        """
        text = (
            "Book Id,Title,Author,My Rating,Exclusive Shelf\n"
            "123,Truncated,Test Author\n"
            "456,Valid Book,Author,4,read\n"
        )

        assert [item.title for item in items(text)] == ["Valid Book"]
        assert reported(text) == [(2, "2 fields short of the header")]
