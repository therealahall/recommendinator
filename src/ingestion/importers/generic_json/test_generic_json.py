import json
import logging
from datetime import date

import pytest

from src.ingestion.importers.base import (
    ImportedRow,
    ImporterError,
    ParsedRow,
    SkippedRow,
)
from src.ingestion.importers.generic_json.generic_json import JsonImporter
from src.models.content import ConsumptionStatus, ContentItem, ContentType

IMPORTER = JsonImporter()

ROW_LOGGER = "src.ingestion.importers.rows"


def parse(text: str, content_type: ContentType = ContentType.BOOK) -> list[ParsedRow]:
    return list(IMPORTER.parse(text, content_type))


def items(text: str, content_type: ContentType = ContentType.BOOK) -> list[ContentItem]:
    return [
        row.item for row in parse(text, content_type) if isinstance(row, ImportedRow)
    ]


def reported(text: str, content_type: ContentType = ContentType.BOOK) -> list[tuple]:
    return [
        (row.unit, row.number, row.reason)
        for row in parse(text, content_type)
        if isinstance(row, SkippedRow)
    ]


class TestEntries:
    def test_every_book_field_reaches_its_place(self) -> None:
        parsed = items(
            json.dumps(
                [
                    {
                        "title": "The Name of the Wind",
                        "author": "Patrick Rothfuss",
                        "rating": 5,
                        "status": "completed",
                        "date_completed": "2024-06-15",
                        "review": "Great book",
                        "isbn": "978-0756404741",
                        "pages": 662,
                        "year_published": 2007,
                        "genre": "Fantasy",
                    }
                ]
            )
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
        assert item.source == "json_import"
        assert item.metadata["isbn"] == "978-0756404741"
        assert item.metadata["pages"] == 662
        assert item.metadata["genres"] == ["Fantasy"]

    def test_a_series_crammed_into_the_title_splits_out_unless_stated(self) -> None:
        parsed = items(
            json.dumps(
                [
                    {"title": "All Systems Red (The Murderbot Diaries, #1)"},
                    {
                        "title": "Leviathan Wakes (The Expanse, #1)",
                        "series": "Expanse Novels",
                        "series_index": 4,
                    },
                ]
            )
        )

        assert [item.title for item in parsed] == ["All Systems Red", "Leviathan Wakes"]
        assert parsed[0].metadata["series"] == "The Murderbot Diaries"
        assert parsed[0].metadata["series_index"] == 1.0
        assert parsed[1].metadata["series"] == "Expanse Novels"
        assert parsed[1].metadata["series_index"] == 4

    def test_a_tv_entry_expands_its_watched_season_count(self) -> None:
        parsed = items(
            json.dumps(
                [
                    {
                        "title": "Breaking Bad",
                        "creator": "Vince Gilligan",
                        "status": "completed",
                        "seasons_watched": 5,
                        "total_seasons": 5,
                    }
                ]
            ),
            ContentType.TV_SHOW,
        )

        assert parsed[0].author == "Vince Gilligan"
        assert parsed[0].metadata["seasons_watched"] == [1, 2, 3, 4, 5]
        assert parsed[0].metadata["seasons"] == 5

    def test_an_array_of_seasons_passes_through_whole(self) -> None:
        parsed = items(
            json.dumps([{"title": "Show", "seasons_watched": [1, 2, 5, 6]}]),
            ContentType.TV_SHOW,
        )

        assert parsed[0].metadata["seasons_watched"] == [1, 2, 5, 6]

    def test_a_list_valued_field_is_not_wrapped_again(self) -> None:
        parsed = items(
            json.dumps(
                [
                    {
                        "title": "Hades",
                        "developer": "Supergiant Games",
                        "platform": ["PC", "Switch"],
                        "genre": ["Roguelike", "Action"],
                    }
                ]
            ),
            ContentType.VIDEO_GAME,
        )

        assert parsed[0].metadata["platforms"] == ["PC", "Switch"]
        assert parsed[0].metadata["genres"] == ["Roguelike", "Action"]

    def test_one_object_per_line_parses_as_jsonl(self) -> None:
        parsed = items(
            json.dumps({"title": "Book One", "rating": 5, "status": "completed"})
            + "\n"
            + json.dumps({"title": "Book Two", "rating": 4, "status": "unread"})
        )

        assert [item.title for item in parsed] == ["Book One", "Book Two"]

    def test_a_zero_rating_is_unrated_and_a_high_one_clamps(self) -> None:
        parsed = items(
            json.dumps(
                [{"title": "Unrated", "rating": 0}, {"title": "Loud", "rating": 10}]
            )
        )

        assert [item.rating for item in parsed] == [None, 5]


class TestRefusals:
    def test_text_that_is_not_json_is_refused_whole(self) -> None:
        with pytest.raises(ImporterError, match="Failed to parse JSON"):
            parse("{not valid json")

    def test_a_format_taking_any_type_refuses_to_guess_one(self) -> None:
        with pytest.raises(ImporterError, match="needs a content type"):
            list(IMPORTER.parse(json.dumps([{"title": "Dune"}])))

    def test_an_unparseable_date_keeps_the_row(self) -> None:
        parsed = items(json.dumps([{"title": "Test", "date_completed": "not-a-date"}]))

        assert len(parsed) == 1
        assert parsed[0].date_completed is None


class TestSkippedRows:
    """A dropped entry is reported rather than vanishing from the count."""

    def test_a_blank_title_is_skipped_by_its_position_in_the_array(self) -> None:
        text = json.dumps([{"title": "First"}, {"title": ""}, {"title": "Third"}])

        assert [item.title for item in items(text)] == ["First", "Third"]
        assert reported(text) == [("entry", 2, "no title")]

    def test_an_array_element_that_is_not_an_object_is_skipped_not_a_crash(
        self,
    ) -> None:
        """A bare value used to reach ``entry.get`` and take the import down."""
        text = json.dumps([{"title": "First"}, "just a string", {"title": "Third"}])

        assert [item.title for item in items(text)] == ["First", "Third"]
        assert reported(text) == [("entry", 2, "not a JSON object")]

    def test_a_jsonl_skip_names_the_line_it_was_on(self) -> None:
        text = '{"title": "First"}\n\n{"title": ""}\n{"title": "Fourth"}\n'

        assert [item.title for item in items(text)] == ["First", "Fourth"]
        assert reported(text) == [("line", 3, "no title")]


class TestIgnoredField:
    """Regression: a re-import cleared the flag on every untouched entry."""

    def test_an_absent_ignored_field_states_nothing(self) -> None:
        parsed = items(json.dumps([{"title": "Test", "status": "completed"}]))

        assert parsed[0].ignored is None

    def test_a_null_ignored_field_states_nothing(self) -> None:
        parsed = items(json.dumps([{"title": "Test", "ignored": None}]))

        assert parsed[0].ignored is None


FORGED_TITLE = "Dune\nImported 9999 items from JSON file"
ESCAPED_TITLE = "Dune\\nImported 9999 items from JSON file"


class TestLogInjectionRegression:
    """Regression: an imported field forged log entries."""

    def test_a_newline_in_a_title_cannot_forge_a_log_entry(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.WARNING, logger=ROW_LOGGER):
            parsed = items(
                json.dumps([{"title": FORGED_TITLE, "date_completed": "yesterday"}])
            )

        assert [item.title for item in parsed] == [FORGED_TITLE]
        assert [
            record.getMessage()
            for record in caplog.records
            if record.name == ROW_LOGGER
        ] == [
            f"Invalid date format for '{ESCAPED_TITLE}': yesterday. "
            "Expected YYYY-MM-DD."
        ]
