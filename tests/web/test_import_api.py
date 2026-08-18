"""The upload door: POST /api/import and the picker's format list."""

from __future__ import annotations

import builtins
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from starlette.formparsers import MultiPartParser

from src.ingestion.import_templates import TEMPLATES_DIR
from src.storage.manager import StorageManager
from tests.factories import authenticated_client, booted_web_app

_BOOKS_CSV = b"title,author,status,rating\nDune,Frank Herbert,read,5\n,Nobody,read,3\n"


@pytest.fixture()
def storage(tmp_path: Path) -> StorageManager:
    return StorageManager(sqlite_path=tmp_path / "import.db")


@pytest.fixture()
def config() -> dict[str, Any]:
    return {"storage": {"database_path": "data/test.db"}}


@pytest.fixture()
def client(storage: StorageManager, config: dict[str, Any]) -> Iterator[TestClient]:
    with booted_web_app(storage, config) as app:
        yield authenticated_client(app)


def _upload(
    client: TestClient,
    body: bytes = _BOOKS_CSV,
    filename: str = "library.csv",
    **fields: str,
) -> Any:
    data = {"importer": "csv_import", "content_type": "book", **fields}
    return client.post(
        "/api/import",
        files={"file": (filename, body, "text/csv")},
        data={key: value for key, value in data.items() if value},
    )


def test_an_upload_imports_the_file_and_reports_the_line_it_skipped(
    client: TestClient, storage: StorageManager
) -> None:
    response = _upload(client)

    assert response.status_code == 200
    assert response.json() == {
        "importer": "csv_import",
        "content_type": "book",
        "filename": "library.csv",
        "added": 1,
        "updated": 0,
        "unchanged": 0,
        "skipped": 1,
        "failed": 0,
        "total_rows": 2,
        "errors": ["Skipped line 3: no title"],
    }
    assert [item.title for item in storage.get_content_items(user_id=1)] == ["Dune"]


def test_a_clean_file_still_carries_an_empty_errors_list(client: TestClient) -> None:
    """``errors`` is never absent: a client reading it may not check first."""
    body = _upload(client, b"title,author\nDune,Frank Herbert\n").json()

    assert (body["errors"], body["skipped"], body["failed"]) == ([], 0, 0)


class TestARefusedFile:
    """Every refusal names what to fix, and none of them is a 500."""

    def test_bytes_that_are_not_utf8_name_the_one_that_broke(
        self, client: TestClient
    ) -> None:
        response = _upload(client, "title\nCafé\n".encode("latin-1"))

        assert response.status_code == 400
        assert response.json()["detail"] == (
            "File is not UTF-8 text (byte 9 is not). "
            "Re-save the export as UTF-8 and upload it again."
        )

    def test_a_file_the_chosen_format_cannot_parse_says_what_refused_it(
        self, client: TestClient
    ) -> None:
        response = _upload(client, importer="json_import")

        assert response.status_code == 400
        assert response.json()["detail"].startswith("Failed to parse JSON:")

    def test_an_unknown_format_name_lists_the_ones_that_exist(
        self, client: TestClient
    ) -> None:
        response = _upload(client, importer="goodreads_rss")

        assert response.status_code == 400
        assert "goodreads_csv" in response.json()["detail"]

    def test_a_generic_format_with_no_content_type_says_it_needs_one(
        self, client: TestClient
    ) -> None:
        response = _upload(client, content_type="")

        assert response.status_code == 400
        assert response.json()["detail"] == (
            "CSV Import needs a content type. One of: book, movie, tv_show, video_game"
        )

    def test_a_content_type_that_is_not_one_of_ours_lists_them(
        self, client: TestClient
    ) -> None:
        response = _upload(client, content_type="graphic_novel")

        assert response.status_code == 400
        assert "book, movie, tv_show, video_game" in response.json()["detail"]


def test_an_upload_past_the_spool_threshold_writes_nothing_under_a_name(
    client: TestClient,
) -> None:
    """A library export runs past ``spool_max_size``, where Starlette rolls the
    part out of memory — the size the old 70-byte body never reached. The
    rollover is an unnamed descriptor, and the import opens no path of its own.
    """
    opened_to_write: list[Any] = []
    real_open = builtins.open

    def record(file: Any, mode: str = "r", *args: Any, **kwargs: Any) -> Any:
        # The rollover opens the temp directory through an opener that unlinks
        # the descriptor it returns. A save of ours would open a plain path.
        if set(mode) & set("wxa+") and "opener" not in kwargs:
            opened_to_write.append(file)
        return real_open(file, mode, *args, **kwargs)

    # A JSON string, not a CSV field: csv caps one field at 128KB.
    padding = b"H" * MultiPartParser.spool_max_size
    body = b'[{"title": "Dune", "author": "%s"}]' % padding

    # Both names: a bare ``open()`` resolves to the builtin, and ``Path.open``
    # — what a save would most likely be written as — calls ``io.open``.
    with patch("builtins.open", record), patch("io.open", record):
        response = _upload(client, body, importer="json_import")

    # Added, so the rolled-over part was read back whole.
    assert response.json()["added"] == 1
    assert opened_to_write == []


@pytest.mark.parametrize("auto_enrich", [True, False])
def test_the_config_gate_decides_whether_imported_items_are_queued(
    storage: StorageManager, auto_enrich: bool
) -> None:
    """``enrichment.auto_enrich_on_sync``, read as the sync path reads it: an
    upload ignoring it would leave every imported item unenriched.
    """
    config = {
        "storage": {"database_path": "data/test.db"},
        "enrichment": {"enabled": True, "auto_enrich_on_sync": auto_enrich},
    }

    with booted_web_app(storage, config) as app:
        assert _upload(authenticated_client(app)).status_code == 200

    item = storage.get_content_items(user_id=1)[0]
    assert item.db_id is not None
    assert (storage.enrichment.status(item.db_id) is not None) is auto_enrich


class TestTheTemplateAnOperatorFillsIn:
    """In Docker ``templates/`` is inside the image, with no shell to copy from."""

    def test_a_template_downloads_byte_for_byte_as_it_ships(
        self, client: TestClient
    ) -> None:
        response = client.get(
            "/api/import/templates/download",
            params={"importer": "csv_import", "content_type": "book"},
        )

        assert response.status_code == 200
        assert response.content == (TEMPLATES_DIR / "books.csv").read_bytes()
        assert (
            response.headers["content-disposition"]
            == 'attachment; filename="books.csv"'
        )
        assert response.headers["content-type"].startswith("text/csv")

    def test_the_listing_names_every_template_the_install_ships(
        self, client: TestClient
    ) -> None:
        """The picker renders from this, so a name hardcoded beside it is drift."""
        payload = client.get("/api/import/templates").json()

        assert {frozenset(entry) for entry in payload} == {
            frozenset({"importer", "content_type", "filename"})
        }
        assert len(payload) == 12
        assert {
            "importer": "markdown_import",
            "content_type": "tv_show",
            "filename": "tv_shows.md",
        } in payload

    @pytest.mark.parametrize(
        ("importer", "content_type"),
        [
            ("goodreads_csv", "book"),
            ("../../../etc/passwd", "book"),
            ("csv_import", "../../config/config.yaml"),
            ("csv_import", "../templates/book"),
            ("csv_import", "/etc/passwd"),
            ("csv_import", "book\x00"),
        ],
        ids=[
            "a format with no template",
            "traversal as the format",
            "as the type",
            "a traversal that would land back on a real template",
            "an absolute path as the type",
            "a null byte as the type",
        ],
    )
    def test_a_template_that_is_not_ours_is_refused_rather_than_resolved(
        self, client: TestClient, importer: str, content_type: str
    ) -> None:
        """Neither parameter is a path segment, so neither can name a file."""
        response = client.get(
            "/api/import/templates/download",
            params={"importer": importer, "content_type": content_type},
        )

        assert response.status_code == 404
        assert response.json()["detail"].startswith("No template for that import")

    @pytest.mark.parametrize(
        "url",
        [
            "/api/import/templates",
            "/api/import/templates/download?importer=csv_import&content_type=book",
        ],
        ids=["the listing", "the download"],
    )
    def test_a_missing_templates_directory_says_where_it_looked(
        self,
        client: TestClient,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        url: str,
    ) -> None:
        """An empty list, or a 404 on the download, reads as an install that
        ships no templates rather than a directory that is not there.
        """
        absent = tmp_path / "absent"
        monkeypatch.setattr("src.ingestion.import_templates.TEMPLATES_DIR", absent)

        response = client.get(url)

        assert response.status_code == 503
        assert str(absent) in response.json()["detail"]


def test_the_format_list_says_which_ones_need_a_content_type(
    client: TestClient,
) -> None:
    """Asking a Goodreads export for a content type asks a question the format
    already answers, and offering none for a generic CSV refuses the upload.
    """
    formats = client.get("/api/importers").json()

    assert {entry["name"]: entry["requires_content_type"] for entry in formats} == {
        "goodreads_csv": False,
        "storygraph_csv": False,
        "csv_import": True,
        "json_import": True,
        "markdown_import": True,
    }
    assert all(entry["display_name"] and entry["description"] for entry in formats)
