from __future__ import annotations

import json
import logging
from datetime import date, datetime
from pathlib import Path

import pytest

from src.ingestion.importers.base import Importer
from src.ingestion.importers.generic_csv.generic_csv import CsvImporter
from src.ingestion.importers.generic_json.generic_json import JsonImporter
from src.ingestion.importers.goodreads_csv.goodreads_csv import GoodreadsCsvImporter
from src.ingestion.importers.service import (
    MAX_REPORTED_ERRORS,
    ImportResult,
    import_file,
)
from src.models.content import ConsumptionStatus, ContentItem, ContentType
from src.storage.manager import StorageManager
from src.utils.series import latest_season_watched_date

SERVICE_LOGGER = "src.ingestion.importers.service"

_BOOKS_CSV = (
    "title,author,status,rating\n"
    "Dune,Frank Herbert,read,5\n"
    ",Nobody,read,3\n"
    "Neuromancer,William Gibson,unread,\n"
)


@pytest.fixture()
def storage(tmp_path: Path) -> StorageManager:
    return StorageManager(sqlite_path=tmp_path / "import.db")


def _import_books(storage: StorageManager, text: str = _BOOKS_CSV) -> ImportResult:
    return import_file(storage, 1, text, CsvImporter(), ContentType.BOOK)


def test_an_import_saves_the_rows_that_parsed_and_names_the_line_it_skipped(
    storage: StorageManager,
) -> None:
    result = _import_books(storage)

    assert (result.added, result.updated, result.unchanged) == (2, 0, 0)
    assert (result.skipped, result.failed, result.total_rows) == (1, 0, 3)
    assert result.errors == ["Skipped line 3: no title"]
    assert sorted(item.title for item in storage.get_content_items(user_id=1)) == [
        "Dune",
        "Neuromancer",
    ]


def test_both_kinds_of_malformed_line_are_named_and_the_file_still_imports(
    storage: StorageManager,
) -> None:
    columns = "title,author,isbn,pages,year_published,genre,rating,status,review"
    rows = [f"Book {n},Author {n},978{n},300,1990,sf,4,read,Fine" for n in range(1, 13)]
    rows[5] = "Short Book,Author S,9781"
    rows[10] = "Long, Book,Author L,9782,300,1990,sf,4,read,Ten fields"

    result = import_file(
        storage, 1, "\n".join([columns, *rows]) + "\n", CsvImporter(), ContentType.BOOK
    )

    assert (result.added, result.skipped, result.failed, result.total_rows) == (
        10,
        2,
        0,
        12,
    )
    assert result.errors == [
        "Skipped line 7: 6 fields short of the header",
        "Skipped line 12: 1 field more than the header",
    ]
    assert len(storage.get_content_items(user_id=1)) == 10


def test_a_file_that_refuses_every_row_caps_the_list_and_still_counts_them_all(
    storage: StorageManager,
) -> None:
    """A header missing a column skips every row, and the list crosses the wire."""
    refused = MAX_REPORTED_ERRORS + 5
    rows = [f"Book {n},Author {n}" for n in range(refused)]

    result = import_file(
        storage,
        1,
        "\n".join(["title,author,status,rating", *rows]) + "\n",
        CsvImporter(),
        ContentType.BOOK,
    )

    assert (result.added, result.updated, result.unchanged) == (0, 0, 0)
    assert (result.skipped, result.failed, result.total_rows) == (refused, 0, refused)
    assert len(result.errors) == MAX_REPORTED_ERRORS + 1
    assert result.errors[0] == "Skipped line 2: 2 fields short of the header"
    assert result.errors[-1] == "… and 5 more"


def test_a_skip_in_a_pretty_printed_json_array_names_an_entry_not_a_line(
    storage: StorageManager, caplog: pytest.LogCaptureFixture
) -> None:
    """Entry 2 sits nowhere near file line 2, and the total counts neither."""
    text = json.dumps([{"title": "Dune"}, {"title": ""}, {"title": "Ubik"}], indent=2)
    assert len(text.splitlines()) > 3, "the entries must not sit one per line"

    with caplog.at_level(logging.INFO, logger=SERVICE_LOGGER):
        result = import_file(storage, 1, text, JsonImporter(), ContentType.BOOK)

    assert result.errors == ["Skipped entry 2: no title"]
    assert "3 row(s) read" in caplog.text


def test_importing_the_same_file_twice_reports_unchanged_rather_than_added(
    storage: StorageManager,
) -> None:
    _import_books(storage)

    result = _import_books(storage)

    assert (result.added, result.updated, result.unchanged) == (0, 0, 2)
    assert result.total_rows == 3


def test_a_re_import_matches_on_the_id_in_the_file_rather_than_the_title(
    storage: StorageManager,
) -> None:
    header = "Title,Author,Book Id\n"
    import_file(
        storage, 1, header + "Dune,Frank Herbert,44767458\n", GoodreadsCsvImporter()
    )

    result = import_file(
        storage,
        1,
        header + "Dune Messiah,Frank Herbert,44767458\n",
        GoodreadsCsvImporter(),
    )

    assert (result.added, result.updated) == (0, 1)
    assert [item.title for item in storage.get_content_items(user_id=1)] == [
        "Dune Messiah"
    ]


def test_an_import_creates_no_source_and_records_no_sync_run(
    storage: StorageManager,
) -> None:
    """An upload is one shot: no cadence to own it, so no run to log."""
    storage.sources.upsert(1, "steam", "steam", {"vanity_url": "me"}, enabled=True)
    storage.sync_runs.record(
        1,
        "steam",
        started_at=datetime(2026, 1, 1),
        finished_at=datetime(2026, 1, 1),
        status="completed",
    )
    sources_before = storage.sources.list(1)
    runs_before = storage.sync_runs.latest_per_source(1)

    _import_books(storage)

    assert storage.sources.list(1) == sources_before
    assert storage.sync_runs.latest_per_source(1) == runs_before


def test_an_empty_file_reports_zero_rows_and_no_error(
    storage: StorageManager,
) -> None:
    result = import_file(storage, 1, "", CsvImporter(), ContentType.BOOK)

    assert (result.added, result.skipped, result.failed, result.total_rows) == (
        0,
        0,
        0,
        0,
    )
    assert result.errors == []


def test_a_format_that_owns_its_content_type_reports_it_unasked(
    storage: StorageManager,
) -> None:
    """The Goodreads picker offers no content type, so None would reach the UI."""
    result = import_file(
        storage, 1, "Title,Author\nDune,Frank Herbert\n", GoodreadsCsvImporter()
    )

    assert result.content_type is ContentType.BOOK
    assert result.added == 1


class TestARefusedValueCostsOneEntry:
    def test_the_rest_of_the_file_still_imports(
        self, storage: StorageManager, caplog: pytest.LogCaptureFixture
    ) -> None:
        text = json.dumps(
            [
                {"title": "Dune", "author": "Frank Herbert", "status": "read"},
                {"title": "Neuromancer", "isbn": {"value": "9780441569595"}},
                {"title": "Ubik", "author": "Philip K. Dick", "status": "read"},
            ]
        )

        with caplog.at_level(logging.WARNING, logger=SERVICE_LOGGER):
            result = import_file(storage, 1, text, JsonImporter(), ContentType.BOOK)

        assert (result.added, result.failed, result.total_rows) == (2, 1, 3)
        assert result.errors[0].startswith("Failed entry 2: ")
        assert result.errors[0].endswith("saving 'Neuromancer'")
        assert "json_import: entry 2 failed" in caplog.text
        assert sorted(item.title for item in storage.get_content_items(user_id=1)) == [
            "Dune",
            "Ubik",
        ]

    def test_two_isbns_in_one_field_are_flattened_rather_than_refused(
        self, storage: StorageManager
    ) -> None:
        text = json.dumps(
            [{"title": "Dune", "isbn": ["9780441013593", "9780441172719"]}]
        )

        result = import_file(storage, 1, text, JsonImporter(), ContentType.BOOK)

        assert result.errors == []
        items = storage.get_content_items(user_id=1)
        assert items[0].metadata["isbn"] == "9780441013593, 9780441172719"


class TestUserOwnedFieldsSurviveAReimport:
    """Regression: a re-import un-ignored the items the user had ignored."""

    def _only_item(self, storage: StorageManager) -> ContentItem:
        items = storage.get_content_items(user_id=1)
        assert len(items) == 1
        return items[0]

    def test_a_file_with_no_ignored_column_preserves_the_ignore(
        self, storage: StorageManager
    ) -> None:
        text = "title,author,rating,status\nDune,Frank Herbert,5,read\n"
        _import_books(storage, text)
        db_id = self._only_item(storage).db_id
        assert db_id is not None
        assert storage.set_item_ignored(db_id, True) is True

        _import_books(storage, text)

        stored = self._only_item(storage)
        assert (stored.db_id, stored.ignored) == (db_id, True)

    def test_a_file_that_says_ignored_false_still_clears_the_flag(
        self, storage: StorageManager
    ) -> None:
        text = "title,status,ignored\nDune,read,false\n"
        _import_books(storage, text)
        db_id = self._only_item(storage).db_id
        assert db_id is not None
        storage.set_item_ignored(db_id, True)

        _import_books(storage, text)

        stored = self._only_item(storage)
        assert (stored.db_id, stored.ignored) == (db_id, False)

    def test_the_rating_review_and_status_set_in_the_app_win(
        self, storage: StorageManager
    ) -> None:
        text = (
            "title,author,rating,status,review\n"
            "Dune,Frank Herbert,2,to-read,Imported note\n"
        )
        _import_books(storage, text)
        db_id = self._only_item(storage).db_id
        assert db_id is not None
        storage.update_item_from_ui(
            db_id=db_id, status="completed", rating=5, review="Loved it"
        )

        _import_books(storage, text)

        stored = self._only_item(storage)
        assert (stored.rating, stored.review) == (5, "Loved it")
        assert stored.status == ConsumptionStatus.COMPLETED


class TestARestoreLandsOnTheShowItNames:
    """Regression: a rebuilt detail table cascaded 866 watched seasons away, and
    this import path is the recovery route rather than a hand-edited row."""

    _RESTORE_CSV = (
        "title,seasons_watched,seasons_watched_dates\n"
        'Succession,"3,4","{""4"": ""2026-06-01T12:00:00+00:00""}"\n'
        'Severance,1,"{""1"": ""2026-02-14T09:30:00+00:00""}"\n'
    )
    _RESTORE_JSON = json.dumps(
        [
            {
                "title": "Succession",
                "seasons_watched": [3, 4],
                "seasons_watched_dates": {"4": "2026-06-01T12:00:00+00:00"},
            },
            {
                "title": "Severance",
                "seasons_watched": [1],
                "seasons_watched_dates": {"1": "2026-02-14T09:30:00+00:00"},
            },
        ]
    )

    @pytest.fixture()
    def succession_id(self, storage: StorageManager) -> int:
        return storage.save_content_item(
            ContentItem(
                title="Succession",
                content_type=ContentType.TV_SHOW,
                status=ConsumptionStatus.CURRENTLY_CONSUMING,
                metadata={"seasons_watched": [1, 2]},
            ),
            user_id=1,
        )

    @pytest.mark.parametrize(
        ("importer", "text"),
        [(CsvImporter(), _RESTORE_CSV), (JsonImporter(), _RESTORE_JSON)],
        ids=["csv", "json"],
    )
    def test_the_stored_show_gains_the_seasons_and_an_unknown_one_is_created(
        self,
        storage: StorageManager,
        succession_id: int,
        importer: Importer,
        text: str,
    ) -> None:
        result = import_file(storage, 1, text, importer, ContentType.TV_SHOW)

        stored = {item.title: item for item in storage.get_content_items(user_id=1)}
        assert (result.added, result.updated) == (1, 1)
        assert sorted(stored) == ["Severance", "Succession"]
        assert stored["Succession"].db_id == succession_id
        assert stored["Succession"].metadata["seasons_watched"] == [1, 2, 3, 4]
        assert latest_season_watched_date(stored["Succession"]) == date(2026, 6, 1)
        assert latest_season_watched_date(stored["Severance"]) == date(2026, 2, 14)


@pytest.mark.parametrize("mark_for_enrichment", [True, False])
def test_enrichment_is_queued_only_when_the_caller_opens_the_gate(
    storage: StorageManager, mark_for_enrichment: bool
) -> None:
    import_file(
        storage,
        1,
        "title\nDune\n",
        CsvImporter(),
        ContentType.BOOK,
        mark_for_enrichment=mark_for_enrichment,
    )

    db_id = storage.get_content_items(user_id=1)[0].db_id
    assert db_id is not None
    status = storage.enrichment.status(db_id)
    assert (status is not None and status["needs_enrichment"]) is mark_for_enrichment
