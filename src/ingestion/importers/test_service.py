"""Tests for the shared import service."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.ingestion.importers.generic_csv.generic_csv import CsvImporter
from src.ingestion.importers.generic_json.generic_json import JsonImporter
from src.ingestion.importers.goodreads_csv.goodreads_csv import GoodreadsCsvImporter
from src.ingestion.importers.service import ImportResult, import_file
from src.models.content import ConsumptionStatus, ContentItem, ContentType
from src.storage.enrichment_status import EnrichmentStore
from src.storage.manager import SavedItem, SaveOutcome, StorageManager

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
    """The short line used to raise on ``None.strip()`` and take the import
    down; the long one shifted every cell a column left and imported silently.
    """
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


def test_importing_the_same_file_twice_reports_unchanged_rather_than_added(
    storage: StorageManager,
) -> None:
    _import_books(storage)

    result = _import_books(storage)

    assert (result.added, result.updated, result.unchanged) == (0, 0, 2)
    assert result.total_rows == 3


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


class TestARefusedValueCostsOneLine:
    """A value no text column can hold fails its own line, not the file.

    The JSON importer forwards whatever the file gives for ``isbn``, so
    storage can be handed an object. ``to_text`` refuses it rather than
    storing a repr.
    """

    def test_the_rest_of_the_file_still_imports(self, storage: StorageManager) -> None:
        text = json.dumps(
            [
                {"title": "Dune", "author": "Frank Herbert", "status": "read"},
                {"title": "Neuromancer", "isbn": {"value": "9780441569595"}},
                {"title": "Ubik", "author": "Philip K. Dick", "status": "read"},
            ]
        )

        result = import_file(storage, 1, text, JsonImporter(), ContentType.BOOK)

        assert (result.added, result.failed, result.total_rows) == (2, 1, 3)
        assert result.errors[0].startswith("Failed line 2: ")
        assert result.errors[0].endswith("saving 'Neuromancer'")
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
    """Regression: a re-import un-ignored the items the user had ignored.

    Cause: the importers called ``parse_boolean_field`` on a missing key,
    which returns False, so every item carried an ``ignored=False`` storage
    wrote over the user's flag.
    """

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


@pytest.mark.parametrize("mark_for_enrichment", [True, False])
def test_enrichment_is_queued_only_when_the_caller_opens_the_gate(
    mark_for_enrichment: bool,
) -> None:
    """The gate is ``enrichment.auto_enrich_on_sync``, resolved by the caller."""
    storage = MagicMock(spec=StorageManager)
    storage.enrichment = MagicMock(spec=EnrichmentStore)
    storage.save_content_item_outcome.return_value = SavedItem(
        db_id=7, outcome=SaveOutcome.ADDED
    )

    import_file(
        storage,
        1,
        "title\nDune\n",
        CsvImporter(),
        ContentType.BOOK,
        mark_for_enrichment=mark_for_enrichment,
    )

    assert storage.enrichment.mark_needed.called is mark_for_enrichment
