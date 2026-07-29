"""Field-by-field CLI/web parity for the one-shot file import.

The two interfaces are meant to be mirrors: ``POST /api/import`` and
``import --source ... --format json`` must return the *same* JSON object for
the same file, and ``GET /api/import/sources`` must return the same listing as
``import --source list --format json``.

Every other import test asserts one side in isolation, so a key added to one
interface and forgotten on the other would pass both suites. These tests run
the identical input through both interfaces in the same test and compare the
parsed bodies directly, which is the only check that catches that drift.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import fields
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, Mock, patch

import pytest
from click.testing import CliRunner
from fastapi.testclient import TestClient

from src.ingestion.import_service import NO_ITEMS_WARNING
from src.llm.client import OllamaClient
from src.llm.embeddings import EmbeddingGenerator
from src.llm.recommendations import RecommendationGenerator
from src.models.content import ContentType
from src.recommendations.engine import RecommendationEngine
from src.storage.manager import StorageManager
from src.web.app import create_app
from src.web.state import AppState, app_state
from src.web.sync_manager import reset_sync_manager
from tests.cli.conftest import _invoke_with_mocks
from tests.factories import back_mock_settings_store
from tests.import_test_data import GOODREADS_CSV

# One sample per supported import format, keyed by plugin name. ``options`` is
# what BOTH interfaces send: multipart form fields on the web side, ``--option
# KEY=VALUE`` pairs on the CLI side.
_SAMPLES: dict[str, tuple[str, str, dict[str, str]]] = {
    "goodreads_csv": ("books.csv", GOODREADS_CSV, {}),
    "csv_import": (
        "books.csv",
        "title,author,status,rating\n"
        "Dune,Frank Herbert,read,5\n"
        "Neuromancer,William Gibson,read,4\n",
        {"content_type": "book"},
    ),
    "json_import": (
        "books.json",
        json.dumps(
            [
                {"title": "Dune", "author": "Frank Herbert", "status": "completed"},
                {"title": "Neuromancer", "author": "William Gibson", "status": "read"},
            ]
        ),
        {"content_type": "book"},
    ),
    "markdown_import": (
        "books.md",
        "## Completed\n"
        "- **Dune** by Frank Herbert | Rating: 5\n"
        "- **Neuromancer** by William Gibson | Rating: 4\n",
        {"content_type": "book"},
    ),
    "storygraph_csv": (
        "library.csv",
        "Title,Authors,Read Status,Star Rating\n"
        "Dune,Frank Herbert,read,5\n"
        "Neuromancer,William Gibson,read,4\n",
        {},
    ),
}


def _reset_app_state() -> None:
    fresh = AppState()
    for f in fields(fresh):
        setattr(app_state, f.name, getattr(fresh, f.name))


@pytest.fixture()
def cli_runner() -> CliRunner:
    """Local copy of the ``tests/cli`` runner fixture.

    This module sits at the ``tests/`` root (it spans both interfaces), so the
    ``tests/cli/conftest.py`` fixture is out of scope here.
    """
    return CliRunner()


@pytest.fixture()
def web_client() -> Iterator[TestClient]:
    """A TestClient over the real app with mocked storage/LLM boot deps."""
    reset_sync_manager()
    config: dict[str, Any] = {
        "ollama": {"base_url": "http://localhost:11434", "model": "x"},
        "storage": {"database_path": "data/test.db"},
        "inputs": {},
    }
    with (
        patch("src.web.app.load_config", return_value=config),
        patch("src.web.app.create_storage_manager") as mock_storage,
        patch("src.web.app.create_llm_components") as mock_llm,
        patch("src.web.app.create_recommendation_engine") as mock_engine,
        patch("src.web.app.migrate_config_credentials"),
        patch("src.web.app.migrate_source_labels"),
        patch("src.web.app.migrate_source_config_plugins"),
    ):
        storage = Mock(spec=StorageManager)
        storage.get_credentials_for_source.return_value = {}
        storage.list_source_configs.return_value = []
        storage.save_content_item.return_value = 1
        back_mock_settings_store(storage)
        mock_storage.return_value = storage
        mock_llm.return_value = (
            Mock(spec=OllamaClient),
            Mock(spec=EmbeddingGenerator),
            Mock(spec=RecommendationGenerator),
        )
        engine = Mock(spec=RecommendationEngine)
        engine.storage = storage
        mock_engine.return_value = engine

        _reset_app_state()
        app = create_app()
        app_state.storage = storage
        app_state.config = config
        yield TestClient(app)

    _reset_app_state()
    reset_sync_manager()


@pytest.fixture()
def web_storage(web_client: TestClient) -> Mock:
    """The storage mock the running app is wired to, for scripting saves."""
    storage = app_state.storage
    assert isinstance(storage, Mock)
    return storage


def _cli_import(
    cli_runner: CliRunner,
    source: str,
    file_path: Path,
    options: dict[str, str],
    storage: MagicMock | None = None,
) -> dict[str, Any]:
    """Run ``import --format json`` and return the parsed body.

    Pass *storage* to script the save calls (e.g. a failing row); the default
    is a mock that saves everything successfully.
    """
    if storage is None:
        storage = MagicMock(spec=StorageManager)
        storage.save_content_item.return_value = 1
    args = ["import", "--source", source, "--file", str(file_path)]
    for key, value in options.items():
        args += ["--option", f"{key}={value}"]
    args += ["--format", "json"]
    result = _invoke_with_mocks(cli_runner, args, storage)
    assert result.exit_code == 0, result.output
    parsed: dict[str, Any] = json.loads(result.output)
    return parsed


def _cli_import_argv(
    cli_runner: CliRunner,
    argv: list[str],
    file_path: Path,
    storage: MagicMock | None = None,
) -> dict[str, Any]:
    """Run ``import`` with caller-chosen arguments and return the parsed body.

    Unlike :func:`_cli_import`, which always spells options as ``--option
    KEY=VALUE``, this takes the argument list verbatim so a test can drive the
    dedicated flags — the paths ``--option`` bypasses.
    """
    if storage is None:
        storage = MagicMock(spec=StorageManager)
        storage.save_content_item.return_value = 1
    args = ["import", *argv, "--file", str(file_path), "--format", "json"]
    result = _invoke_with_mocks(cli_runner, args, storage)
    assert result.exit_code == 0, result.output
    parsed: dict[str, Any] = json.loads(result.output)
    return parsed


def _web_import(
    web_client: TestClient,
    source: str,
    filename: str,
    content: str,
    options: dict[str, str],
) -> dict[str, Any]:
    """POST the same file to ``/api/import`` and return the parsed body."""
    response = web_client.post(
        "/api/import",
        data={"source": source, **options},
        files={"file": (filename, content, "application/octet-stream")},
    )
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


class TestImportResultParity:
    """The import result body is identical from both interfaces."""

    @pytest.mark.parametrize("source", sorted(_SAMPLES))
    def test_same_file_yields_identical_json(
        self,
        cli_runner: CliRunner,
        web_client: TestClient,
        tmp_path: Path,
        source: str,
    ) -> None:
        """Every field of the import result matches across CLI and web.

        Compares the whole parsed object, so a key present on one side only,
        or a differing ``message``/``source``/count, fails here even though the
        per-interface suites would both still pass.
        """
        filename, content, options = _SAMPLES[source]
        data_file = tmp_path / filename
        data_file.write_text(content, encoding="utf-8")

        cli_body = _cli_import(cli_runner, source, data_file, options)
        web_body = _web_import(web_client, source, filename, content, options)

        assert cli_body == web_body
        # Pin the contract itself, not just that the two agree with each other.
        assert set(web_body) == {
            "message",
            "source",
            "items_synced",
            "total_items",
            "errors",
            "warning",
        }
        assert web_body["items_synced"] == 2
        assert web_body["total_items"] == 2
        assert web_body["errors"] == []
        assert web_body["warning"] is None

    def test_empty_file_warning_is_identical(
        self,
        cli_runner: CliRunner,
        web_client: TestClient,
        tmp_path: Path,
    ) -> None:
        """The zero-item warning body matches across interfaces, verbatim.

        Compared against the shared constant, not merely against each other: a
        reworded warning that both interfaces picked up would satisfy equality
        while silently changing what the user reads.
        """
        data_file = tmp_path / "empty.csv"
        data_file.write_text("", encoding="utf-8")

        cli_body = _cli_import(
            cli_runner, "csv_import", data_file, {"content_type": "book"}
        )
        web_body = _web_import(
            web_client, "csv_import", "empty.csv", "", {"content_type": "book"}
        )

        assert cli_body == web_body
        assert web_body["warning"] == NO_ITEMS_WARNING

    def test_unicode_titles_survive_both_interfaces(
        self,
        cli_runner: CliRunner,
        web_client: TestClient,
        tmp_path: Path,
    ) -> None:
        """Non-ASCII titles import identically over multipart and a local path.

        The web side round-trips the bytes through a multipart body and a temp
        file; the CLI reads the user's file directly. A mismatched encoding on
        either path would show up as a differing item count or a mojibake title.
        """
        content = (
            "title,author,status,rating\n"
            "Les Misérables,Victor Hugo,read,5\n"
            "こころ,夏目漱石,read,4\n"
            "Ficciones — relatos,Jorge Luis Borges,read,5\n"
        )
        data_file = tmp_path / "unicode.csv"
        data_file.write_text(content, encoding="utf-8")

        cli_body = _cli_import(
            cli_runner, "csv_import", data_file, {"content_type": "book"}
        )
        web_body = _web_import(
            web_client, "csv_import", "unicode.csv", content, {"content_type": "book"}
        )

        assert cli_body == web_body
        assert web_body["items_synced"] == 3
        assert web_body["errors"] == []

    def test_partially_failing_import_reports_the_same_body(
        self,
        cli_runner: CliRunner,
        web_client: TestClient,
        web_storage: Mock,
        tmp_path: Path,
    ) -> None:
        """One failing row out of two produces an identical body from both.

        ``errors[]`` is assembled by different code on each side — the web
        handler loops ``add_error`` and then returns ``result.errors``, the CLI
        reads ``result.errors`` directly — so drift in the per-item text, the
        ordering, or the surviving count only shows up when the same
        half-failing import runs through both interfaces.
        """
        data_file = tmp_path / "books.csv"
        data_file.write_text(GOODREADS_CSV, encoding="utf-8")

        cli_storage = MagicMock(spec=StorageManager)
        cli_storage.save_content_item.side_effect = [RuntimeError("db write failed"), 1]
        cli_body = _cli_import(cli_runner, "goodreads_csv", data_file, {}, cli_storage)

        web_storage.save_content_item.side_effect = [RuntimeError("db write failed"), 1]
        web_body = _web_import(
            web_client, "goodreads_csv", "books.csv", GOODREADS_CSV, {}
        )

        assert cli_body == web_body
        assert web_body["items_synced"] == 1
        assert web_body["total_items"] == 2
        assert web_body["errors"] == ["Failed to process 'Dune'"]
        assert web_body["warning"] is None
        # The raw exception text is never part of either body.
        assert "db write failed" not in json.dumps(web_body)

    def test_duplicate_rows_are_all_imported(
        self,
        cli_runner: CliRunner,
        web_client: TestClient,
        tmp_path: Path,
    ) -> None:
        """A file repeating the same row reports the raw row count on both sides.

        De-duplication is the storage layer's job (``save_content_item``
        upserts), so the import result must not quietly collapse the counts on
        one interface and not the other.
        """
        content = (
            "title,author,status,rating\n"
            "Dune,Frank Herbert,read,5\n"
            "Dune,Frank Herbert,read,5\n"
        )
        data_file = tmp_path / "dupes.csv"
        data_file.write_text(content, encoding="utf-8")

        cli_body = _cli_import(
            cli_runner, "csv_import", data_file, {"content_type": "book"}
        )
        web_body = _web_import(
            web_client, "csv_import", "dupes.csv", content, {"content_type": "book"}
        )

        assert cli_body == web_body
        assert web_body["total_items"] == 2
        assert web_body["items_synced"] == 2


class TestImportOptionHandlingParity:
    """The same option value produces the same outcome on both interfaces."""

    def test_uppercase_content_type_is_accepted_by_every_path(
        self,
        cli_runner: CliRunner,
        web_client: TestClient,
        web_storage: Mock,
        tmp_path: Path,
    ) -> None:
        """``BOOK`` imports identically via the flag, ``--option``, and the form.

        Regression: one logical parameter behaved three different ways, and the
        CLI disagreed with itself. ``--content-type BOOK`` is a
        ``click.Choice(case_sensitive=False)``, so Click lowercased it and the
        import succeeded; ``--option content_type=BOOK`` bypasses Click's
        conversion and hit the plugin's exact-case check; the web passed the
        multipart field through verbatim and hit the same check. The old parity
        test only drove ``--option``, so it recorded "both refuse it" and the
        flag's divergence stayed invisible. The plugins now resolve the option
        through the case-insensitive ``ContentType.from_string``, and this test
        drives the flag as well as the other two.

        The bodies carry no content type, so equality between them says nothing
        about what the value resolved *to* — a route that typed every row as a
        movie would return the identical body. The saved items are what pin it.
        """
        content = "title,author,status,rating\nDune,Frank Herbert,read,5\n"
        data_file = tmp_path / "books.csv"
        data_file.write_text(content, encoding="utf-8")

        flag_storage = MagicMock(spec=StorageManager)
        flag_storage.save_content_item.return_value = 1
        flag_body = _cli_import_argv(
            cli_runner,
            ["--source", "csv_import", "--content-type", "BOOK"],
            data_file,
            flag_storage,
        )
        option_body = _cli_import_argv(
            cli_runner,
            ["--source", "csv_import", "--option", "content_type=BOOK"],
            data_file,
        )
        web_body = _web_import(
            web_client, "csv_import", "books.csv", content, {"content_type": "BOOK"}
        )

        assert flag_body == option_body == web_body
        assert web_body["items_synced"] == 1
        for storage in (flag_storage, web_storage):
            saved = storage.save_content_item.call_args.args[0]
            assert saved.content_type == ContentType.BOOK

    def test_unrecognised_content_type_is_refused_by_both(
        self,
        cli_runner: CliRunner,
        web_client: TestClient,
        tmp_path: Path,
    ) -> None:
        """Case folding is not "anything goes" — a bad value still fails, alike.

        The control for the test above: making the comparison case-insensitive
        must not turn the option into a free-text field on either interface.
        """
        content = "title,author,status,rating\nDune,Frank Herbert,read,5\n"
        data_file = tmp_path / "books.csv"
        data_file.write_text(content, encoding="utf-8")

        storage = MagicMock(spec=StorageManager)
        cli_result = _invoke_with_mocks(
            cli_runner,
            [
                "import",
                "--source",
                "csv_import",
                "--file",
                str(data_file),
                "--option",
                "content_type=paperback",
            ],
            storage,
        )
        assert cli_result.exit_code != 0
        assert "Invalid content_type 'paperback'" in cli_result.output
        storage.save_content_item.assert_not_called()

        response = web_client.post(
            "/api/import",
            data={"source": "csv_import", "content_type": "paperback"},
            files={"file": ("books.csv", content, "text/csv")},
        )
        assert response.status_code == 400
        assert "Invalid content_type 'paperback'" in response.json()["detail"]

    def test_content_type_option_overrides_the_flag(
        self, cli_runner: CliRunner, tmp_path: Path
    ) -> None:
        """``--option content_type=`` wins over ``--content-type``, silently.

        Both write the same key, and the option loop runs after the flag, so
        the last write wins. Pinned rather than fixed: ``--option`` is the
        escape hatch for keys the CLI has no flag for, and last-write-wins is
        what a caller passing the same key twice gets everywhere else. What
        matters is that the precedence is a decision, not an accident.
        """
        content = "title,director,status,rating\nDune,Denis Villeneuve,watched,5\n"
        data_file = tmp_path / "films.csv"
        data_file.write_text(content, encoding="utf-8")
        storage = MagicMock(spec=StorageManager)
        storage.save_content_item.return_value = 1

        _cli_import_argv(
            cli_runner,
            [
                "--source",
                "csv_import",
                "--content-type",
                "book",
                "--option",
                "content_type=movie",
            ],
            data_file,
            storage,
        )

        saved = storage.save_content_item.call_args.args[0]
        assert saved.content_type == ContentType.MOVIE.value

    @pytest.mark.parametrize(
        ("key", "value"),
        [
            ("not_a_real_option", "x"),
            # ``_source_id`` is the key the gate exists for: ``execute_sync``
            # uses it as the source label for every imported item.
            ("_source_id", "steam"),
        ],
    )
    def test_undeclared_option_key_is_refused_by_both(
        self,
        cli_runner: CliRunner,
        web_client: TestClient,
        tmp_path: Path,
        key: str,
        value: str,
    ) -> None:
        """An option the plugin does not declare is refused, identically, by both.

        Regression: the schema gate was implemented once per interface and the
        two disagreed — the CLI aborted with nothing imported while the web
        returned ``items_synced: 1`` and dropped the key silently. "A browser
        only sends schema fields" described the bundled SPA, not the endpoint's
        contract. The gate now lives in ``import_file``, so one refusal serves
        both and a third caller cannot miss it.
        """
        content = "title,author,status,rating\nDune,Frank Herbert,read,5\n"
        data_file = tmp_path / "books.csv"
        data_file.write_text(content, encoding="utf-8")
        expected = (
            f"Unknown import option(s) for 'csv_import': {key}. "
            "This source accepts: content_type."
        )

        storage = MagicMock(spec=StorageManager)
        cli_result = _invoke_with_mocks(
            cli_runner,
            [
                "import",
                "--source",
                "csv_import",
                "--file",
                str(data_file),
                "--option",
                "content_type=book",
                "--option",
                f"{key}={value}",
            ],
            storage,
        )
        assert cli_result.exit_code != 0
        assert expected in cli_result.output
        assert "Traceback" not in cli_result.output
        storage.save_content_item.assert_not_called()

        response = web_client.post(
            "/api/import",
            data={"source": "csv_import", "content_type": "book", key: value},
            files={"file": ("books.csv", content, "text/csv")},
        )
        assert response.status_code == 400
        assert response.json()["detail"] == expected

    def test_path_option_cannot_redirect_the_import(
        self,
        cli_runner: CliRunner,
        web_client: TestClient,
        tmp_path: Path,
    ) -> None:
        """A caller-supplied ``path`` never overrides the file being imported.

        ``path`` is not in any file-import plugin's schema and the service
        injects it itself. If a caller could set it, an upload could be pointed
        at an arbitrary server-side file — so both interfaces refuse the run
        outright and neither reads the decoy.
        """
        content = "title,author,status,rating\nDune,Frank Herbert,read,5\n"
        data_file = tmp_path / "books.csv"
        data_file.write_text(content, encoding="utf-8")
        decoy = tmp_path / "decoy.csv"
        decoy.write_text(
            "title,author,status,rating\n"
            "Should Not Import,Nobody,read,1\n"
            "Also Not This,Nobody,read,1\n",
            encoding="utf-8",
        )

        storage = MagicMock(spec=StorageManager)
        cli_result = _invoke_with_mocks(
            cli_runner,
            [
                "import",
                "--source",
                "csv_import",
                "--file",
                str(data_file),
                "--option",
                "content_type=book",
                "--option",
                f"path={decoy}",
            ],
            storage,
        )
        assert cli_result.exit_code != 0
        assert "path" in cli_result.output
        storage.save_content_item.assert_not_called()

        response = web_client.post(
            "/api/import",
            data={"source": "csv_import", "content_type": "book", "path": str(decoy)},
            files={"file": ("books.csv", content, "text/csv")},
        )
        assert response.status_code == 400
        assert "path" in response.json()["detail"]

    def test_duplicate_option_field_takes_the_last_value(
        self, web_client: TestClient
    ) -> None:
        """A repeated multipart field resolves to its last value, not a list.

        A client can send ``content_type`` twice. Starlette's form lookup keeps
        the last occurrence, so the import must behave as if only that one was
        sent — never pass a list to the plugin, and never fail on the duplicate.
        """
        content = "title,author,status,rating\nDune,Frank Herbert,read,5\n"

        response = web_client.post(
            "/api/import",
            data={
                "source": "csv_import",
                "content_type": ["not_a_type", "book"],
            },
            files={"file": ("books.csv", content, "text/csv")},
        )

        assert response.status_code == 200, response.text
        assert response.json()["items_synced"] == 1


class TestImportSourceListingParity:
    """The importable-source listing is identical from both interfaces."""

    def test_listing_bodies_match_exactly(
        self, cli_runner: CliRunner, web_client: TestClient
    ) -> None:
        """``import --source list --format json`` == ``GET /api/import/sources``.

        Compares the full nested structure (including every field descriptor)
        so a serialiser change on one side — a dropped ``sensitive`` flag, a
        renamed ``field_type`` — cannot pass unnoticed.
        """
        response = web_client.get("/api/import/sources")
        assert response.status_code == 200
        web_body = response.json()

        result = _invoke_with_mocks(
            cli_runner,
            ["import", "--source", "list", "--format", "json"],
            MagicMock(spec=StorageManager),
        )
        assert result.exit_code == 0, result.output
        cli_body = json.loads(result.output)

        assert cli_body == web_body

    def test_listing_entries_carry_the_documented_keys(
        self, web_client: TestClient
    ) -> None:
        """Every entry mirrors ``ImportSourceResponse`` and ``SourceFieldSchema``."""
        body = web_client.get("/api/import/sources").json()
        # The exact set, matching ``test_cli_import.py``: a syncable plugin that
        # picked up ``is_file_import`` by accident would slip past a mere
        # non-empty check on the very listing this module exists to pin.
        assert {entry["name"] for entry in body} == {
            "csv_import",
            "goodreads_csv",
            "json_import",
            "markdown_import",
            "storygraph_csv",
        }
        for entry in body:
            assert set(entry) == {
                "name",
                "display_name",
                "description",
                "content_types",
                "accepted_extensions",
                "fields",
            }
            # The upload form's file picker is built from this, so an importer
            # that declared none would offer the user no filter at all.
            assert entry["accepted_extensions"]
            for field in entry["fields"]:
                assert set(field) == {
                    "name",
                    "field_type",
                    "required",
                    "default",
                    "description",
                    "sensitive",
                }
