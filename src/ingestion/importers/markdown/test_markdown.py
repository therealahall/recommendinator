"""Tests for the Markdown importer."""

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
from src.ingestion.importers.markdown.markdown import MarkdownImporter
from src.models.content import ConsumptionStatus, ContentItem, ContentType

IMPORTER = MarkdownImporter()

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


class TestListItems:
    def test_a_full_entry_reaches_every_field(self) -> None:
        parsed = items(
            "# My Books\n\n"
            "## Completed\n"
            "- **The Name of the Wind** by Patrick Rothfuss | Rating: 5 | Date: 2024-06-15\n"
        )

        assert len(parsed) == 1
        item = parsed[0]
        assert item.title == "The Name of the Wind"
        assert item.author == "Patrick Rothfuss"
        assert item.content_type == ContentType.BOOK.value
        assert item.rating == 5
        assert item.status == ConsumptionStatus.COMPLETED.value
        assert item.date_completed == date(2024, 6, 15)
        assert item.source == "markdown_import"

    def test_a_title_on_its_own_is_enough(self) -> None:
        parsed = items("## To Read\n- **Minimal Book**\n")

        assert parsed[0].title == "Minimal Book"
        assert parsed[0].author is None
        assert parsed[0].rating is None
        assert parsed[0].status == ConsumptionStatus.UNREAD.value

    def test_an_unrecognised_rating_or_date_keeps_the_entry(self) -> None:
        parsed = items(
            "## Completed\n"
            "- **Unrated** | Rating: 0\n"
            "- **Shouty** | Rating: 10\n"
            "- **Wordy** | Rating: abc\n"
            "- **Undated** | Date: not-a-date\n"
        )

        assert [item.rating for item in parsed] == [None, 5, None, None]
        assert parsed[3].date_completed is None


class TestSections:
    def test_each_section_sets_the_status_of_the_items_under_it(self) -> None:
        parsed = items(
            "## Completed\n"
            "- **Book A** by Author A | Rating: 5\n"
            "- **Book B** by Author B | Rating: 4\n\n"
            "## In Progress\n"
            "- **Book C** by Author C\n\n"
            "## To Read\n"
            "- **Book D** by Author D\n"
        )

        assert [item.status for item in parsed] == [
            ConsumptionStatus.COMPLETED.value,
            ConsumptionStatus.COMPLETED.value,
            ConsumptionStatus.CURRENTLY_CONSUMING.value,
            ConsumptionStatus.UNREAD.value,
        ]

    def test_a_heading_is_matched_by_keyword(self) -> None:
        parsed = items("## Currently Reading\n- **Test** by Author\n")

        assert parsed[0].status == ConsumptionStatus.CURRENTLY_CONSUMING.value

    def test_an_unrecognised_heading_leaves_the_status_alone(self) -> None:
        parsed = items(
            "## Completed\n"
            "- **First** by Author\n\n"
            "## Random Section\n"
            "- **Second** by Author\n"
        )

        assert parsed[1].status == ConsumptionStatus.COMPLETED.value


class TestSkippedRows:
    def test_a_list_item_that_parses_as_nothing_is_reported_by_line(self) -> None:
        """Prose is not a row, but a bullet the operator meant as one is.

        The line number is what lets them find the item that never imported.
        """
        text = (
            "# My Books\n\n"
            "Some descriptive paragraph.\n\n"
            "## Completed\n"
            "Random text here.\n"
            "- **Valid Book** by Author\n"
            "- Not a valid entry\n"
        )

        assert [item.title for item in items(text)] == ["Valid Book"]
        assert reported(text) == [(8, "list item has no **Title**")]

    def test_a_bolded_blank_title_is_skipped(self) -> None:
        text = "## Completed\n- ** ** by Author\n"

        assert items(text) == []
        assert reported(text) == [(2, "no title")]


class TestRefusals:
    def test_a_format_taking_any_type_refuses_to_guess_one(self) -> None:
        with pytest.raises(ImporterError, match="needs a content type"):
            list(IMPORTER.parse("- **Dune**\n"))


class TestTemplates:
    def test_the_shipped_books_template_still_parses(self) -> None:
        parsed = items(Path("templates/books.md").read_text(encoding="utf-8"))

        assert [item.title for item in parsed][0] == "The Name of the Wind"
        assert [item.status for item in parsed] == [
            ConsumptionStatus.COMPLETED.value,
            ConsumptionStatus.CURRENTLY_CONSUMING.value,
            ConsumptionStatus.UNREAD.value,
        ]


# Parsing is line-based, so a break never survives into a title here. The
# terminal control that erases the line an operator just read does.
FORGED_TITLE = "Dune\x1b[2KImported 9999 items"
ESCAPED_TITLE = "Dune\\u001b[2KImported 9999 items"


class TestLogInjectionRegression:
    """Regression: an imported list item rewrote log entries.

    Bug: the title and the date were logged raw, and neither is restricted to
    printable text. Fix: ``sanitize_for_log`` at every sink.
    """

    def test_a_control_character_in_a_title_cannot_rewrite_a_log_entry(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.WARNING, logger=ROW_LOGGER):
            parsed = items(f"- **{FORGED_TITLE}** | Date: yesterday\n")

        # The item keeps the title it was given; only the log line is escaped.
        assert [item.title for item in parsed] == [FORGED_TITLE]
        assert [
            record.getMessage()
            for record in caplog.records
            if record.name == ROW_LOGGER
        ] == [
            f"Invalid date format for '{ESCAPED_TITLE}': yesterday. "
            "Expected YYYY-MM-DD."
        ]
