"""Tests for CLI __main__ entry point."""

import os
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import requests
from click.testing import CliRunner

from src.cli.main import cli
from src.ingestion.sync import SyncResult
from src.recommendations.engine import RecommendationEngine
from src.storage.manager import StorageManager
from tests.cli.conftest import _invoke_with_mocks


def test_a_source_named_goodreads_keeps_its_items_across_boots(
    tmp_path: Path,
) -> None:
    """Regression: a source named ``goodreads`` lost its label on every boot.

    A startup pass rewrote ``content_items.source`` to ``goodreads_csv``, and
    nothing reserves that id, so the library landed under a source that does
    not exist.
    """
    storage = StorageManager(sqlite_path=tmp_path / "test.db")
    storage.upsert_source_config(
        1, "goodreads", "goodreads_csv", {"path": "inputs/library.csv"}, enabled=True
    )
    with storage.connection() as conn:
        conn.execute(
            "INSERT INTO content_items (user_id, title, content_type, status, source) "
            "VALUES (1, 'Some Title', 'book', 'completed', 'goodreads')"
        )
        conn.commit()

    config: dict[str, Any] = {"inputs": {}}
    with (
        patch("src.cli.main.load_config", return_value=config),
        patch("src.cli.main.create_storage_manager", return_value=storage),
        patch(
            "src.cli.main.create_recommendation_engine",
            return_value=MagicMock(spec=RecommendationEngine),
        ),
    ):
        for _ in range(2):
            result = CliRunner().invoke(cli, ["status"])
            assert result.exit_code == 0, result.output

    with storage.connection() as conn:
        rows = conn.execute("SELECT source FROM content_items").fetchall()
    assert [row[0] for row in rows] == ["goodreads"]


@pytest.mark.usefixtures("registry_with_source_fakes")
class TestUpdateDbOnlySourceRegression:
    """``update`` must sync sources that live only in the database.

    Bug: the CLI ``update`` single-source branch gated on the YAML
    ``inputs`` map (``config.get("inputs", {}).get(source)``) and aborted
    "Unknown source" before reaching ``resolve_inputs``. A source created via
    ``source create`` or the web Add-source modal lives only in the
    ``source_configs`` table (with its secret in ``credentials``) and has no
    YAML entry, so it could not be synced from the CLI even though the web
    ``/update`` endpoint had just been fixed to sync it — a CLI/web parity gap.
    ``update --source list`` had the same YAML-only blind spot, so the id was
    not even discoverable.

    Root cause: the single-source branch (and ``--source list``) read the YAML
    ``inputs`` map directly instead of the DB-aware ``resolve_inputs`` /
    ``get_available_sync_sources`` helpers.

    Fix: resolve the single source through ``resolve_inputs(config,
    storage=storage)`` filtered by ``source_id`` (mirroring the web branch and
    the ``--source all`` path), and list via ``get_available_sync_sources``.
    """

    def _db_only_config(self) -> dict[str, Any]:
        """Config with an empty ``inputs`` map — the source is DB-only."""
        return {"inputs": {}, "recommendations": {"min_rating_for_preference": 4}}

    def _seed_db_source(self, storage: StorageManager, enabled: bool = True) -> None:
        """Create a DB-only ``calibre-web`` source with its secret.

        Uses the ``fake_api`` fake plugin (sensitive ``api_key``) so the
        resolved config carries the injected ``_source_id`` — mirroring the web
        regression test. It has no config.yaml entry: the row and its secret
        live only in the database.
        """
        storage.upsert_source_config(
            1,
            "calibre-web",
            "fake_api",
            {"user_id": "reader"},
            enabled=enabled,
        )
        storage.credentials.save(1, "calibre-web", "api_key", "top-secret")

    def _run_update(
        self,
        storage: StorageManager,
        config: dict[str, Any],
        args: list[str],
        sync_side_effect: Any,
    ) -> object:
        with (
            patch("src.cli.main.load_config", return_value=config),
            patch("src.cli.main.create_storage_manager", return_value=storage),
            patch(
                "src.cli.main.create_recommendation_engine",
                return_value=MagicMock(spec=RecommendationEngine),
            ),
            patch(
                "src.cli.commands._update.execute_multi_source_sync",
                side_effect=sync_side_effect,
            ),
        ):
            return CliRunner().invoke(cli, args)

    def test_enabled_db_only_source_syncs_end_to_end_regression(
        self, tmp_path: Path
    ) -> None:
        """An enabled DB-only source syncs, resolving config + secret + id.

        Asserts end to end (not just that the gate passes): the plugin the sync
        boundary receives carries the injected ``_source_id`` and the decrypted
        ``password`` credential plus the DB config, proving the single-source
        path actually resolves and runs the DB source.
        """
        storage = StorageManager(sqlite_path=tmp_path / "test.db")
        self._seed_db_source(storage, enabled=True)

        captured: dict[str, Any] = {}

        def _fake_sync(**kwargs: Any) -> list[SyncResult]:
            captured["sources"] = kwargs.get("sources") or []
            return [
                SyncResult(source_name=plugin.display_name)
                for plugin, _config in captured["sources"]
            ]

        result = self._run_update(
            storage,
            self._db_only_config(),
            ["update", "--source", "calibre-web"],
            _fake_sync,
        )

        assert result.exit_code == 0, result.output
        assert "Unknown" not in result.output
        sources = captured["sources"]
        assert len(sources) == 1
        _plugin, resolved_config = sources[0]
        assert resolved_config["_source_id"] == "calibre-web"
        assert resolved_config["api_key"] == "top-secret"
        assert resolved_config["user_id"] == "reader"

    def test_disabled_db_only_source_aborts_regression(self, tmp_path: Path) -> None:
        """A disabled DB-only source aborts with a nonzero exit."""
        storage = StorageManager(sqlite_path=tmp_path / "test.db")
        self._seed_db_source(storage, enabled=False)

        def _never(**_: Any) -> list[SyncResult]:
            raise AssertionError("sync must not run for a disabled source")

        result = self._run_update(
            storage,
            self._db_only_config(),
            ["update", "--source", "calibre-web"],
            _never,
        )

        assert result.exit_code != 0
        assert "Unknown or disabled source 'calibre-web'" in result.output

    def test_unknown_source_aborts_regression(self, tmp_path: Path) -> None:
        """A source id that matches nothing aborts with a nonzero exit."""
        storage = StorageManager(sqlite_path=tmp_path / "test.db")

        def _never(**_: Any) -> list[SyncResult]:
            raise AssertionError("sync must not run for an unknown source")

        result = self._run_update(
            storage,
            self._db_only_config(),
            ["update", "--source", "no_such_source"],
            _never,
        )

        assert result.exit_code != 0
        assert "Unknown or disabled source 'no_such_source'" in result.output

    def test_source_list_surfaces_db_only_source_regression(
        self, tmp_path: Path
    ) -> None:
        """``update --source list`` shows a DB-only source id.

        Without the DB-aware ``get_available_sync_sources`` the id never
        appears, so the user cannot discover the value to pass to ``--source``.
        """
        storage = StorageManager(sqlite_path=tmp_path / "test.db")
        self._seed_db_source(storage, enabled=True)

        def _never(**_: Any) -> list[SyncResult]:
            raise AssertionError("list must not run a sync")

        result = self._run_update(
            storage,
            self._db_only_config(),
            ["update", "--source", "list"],
            _never,
        )

        assert result.exit_code == 0, result.output
        assert "calibre-web" in result.output
        assert "enabled" in result.output


class TestUndecodableRomNameDoesNotAbortUpdateRegression:
    """Reported: one ROM named in invalid UTF-8 lost the whole sync.

    Symptom: UnicodeEncodeError, nothing stored. Cause: a strict encode of the
    lone surrogate ``iterdir`` returns. Fix: ``backslashreplace`` in the id
    hash, one escape at the storage door.
    """

    @staticmethod
    def _run(storage: StorageManager, root: Path) -> Any:
        """``update --source roms`` over *root*, against real storage."""
        config: dict[str, Any] = {
            "inputs": {
                "roms": {"plugin": "roms", "enabled": True, "paths": [str(root)]}
            },
        }
        with (
            patch("src.cli.main.load_config", return_value=config),
            patch("src.cli.main.create_storage_manager", return_value=storage),
            patch(
                "src.cli.main.create_recommendation_engine",
                return_value=MagicMock(spec=RecommendationEngine),
            ),
        ):
            return CliRunner().invoke(cli, ["update", "--source", "roms"])

    def test_the_odd_rom_and_its_neighbours_all_land(self, tmp_path: Path) -> None:
        """Catches a revert of either half.

        A strict encode in ``_entry_id`` kills the scan; a missing escape at
        the door kills the save.
        """
        storage = StorageManager(sqlite_path=tmp_path / "test.db")
        root = tmp_path / "roms"
        root.mkdir()
        (root / os.fsdecode(b"Metr\xffoid (USA).zip")).write_bytes(b"rom")
        (root / "Chrono Trigger.zip").write_bytes(b"rom")
        (root / "Super Mario World.zip").write_bytes(b"rom")
        assert b"Metr\xffoid (USA).zip" in os.listdir(os.fsencode(root))

        result = self._run(storage, root)

        assert result.exit_code == 0, result.output
        assert "Total: 3 of 3 items saved (3 added, 0 updated, 0 unchanged)." in (
            result.output
        )
        with storage.connection() as conn:
            rows = conn.execute(
                "SELECT title, external_id FROM content_items"
            ).fetchall()
        assert {row["title"] for row in rows} == {
            "Metr\\udcffoid",
            "Chrono Trigger",
            "Super Mario World",
        }
        assert len({row["external_id"] for row in rows}) == 3


def test_cli_boot_overlays_db_settings_without_seeding(tmp_path: Path) -> None:
    """CLI boot assembles the effective config against an isolated DB.

    Drives the *real* ``migrate_config_settings`` hook with a real temp-DB
    StorageManager (no stub): a stored DB leaf must win over the YAML value,
    and boot must not write anything else to the settings table.
    """
    runner = CliRunner()
    config: dict[str, Any] = {"recommendations": {"default_count": 11}}
    storage = StorageManager(sqlite_path=tmp_path / "test.db")
    # A DB leaf the operator set must win over the YAML value on boot. All three
    # layers differ (const 5 < YAML 11 < DB 9), so 9 can only come from the DB.
    storage.set_setting("recommendations.default_count", 9)

    with (
        patch("src.cli.main.load_config", return_value=config),
        patch("src.cli.main.create_storage_manager", return_value=storage),
        patch("src.cli.main.create_recommendation_engine"),
    ):
        result = runner.invoke(cli, ["status"])

    assert result.exit_code == 0
    # Real hook overlaid the DB leaf onto the in-memory config (DB wins).
    assert config["recommendations"]["default_count"] == 9
    # Boot seeded nothing: only the pre-existing leaf remains in the DB.
    assert storage.list_settings() == {"recommendations.default_count": 9}


#: Both boot exits, and the prefix each one prints.
_CONFIG_EXIT = "Error: "
_COMPONENT_EXIT = "Error initializing components: "


class TestCliBootstrapFailures:
    """The first-run experience: no log exists to point the operator at.

    ``configure_logging`` runs after the storage these guards build, so both
    name the fault — sanitized, since one can quote a URL holding a token.
    """

    @staticmethod
    def _boot_failing_at(patched: str, error: Exception) -> Any:
        """Boot ``status`` with one component raising, everything else healthy."""
        with (
            patch("src.cli.main.load_config", return_value={}),
            patch("src.cli.main.create_storage_manager", return_value=MagicMock()),
            patch("src.cli.main.migrate_config_settings"),
            patch("src.cli.main.migrate_config_credentials"),
            patch("src.cli.main.migrate_config_secrets"),
            patch("src.cli.main.create_recommendation_engine"),
            patch(f"src.cli.main.{patched}", side_effect=error),
        ):
            return CliRunner(mix_stderr=False).invoke(cli, ["status"])

    def test_a_missing_config_file_exits_one_naming_the_fault(self) -> None:
        result = self._boot_failing_at(
            "load_config", FileNotFoundError("config.yaml not found")
        )

        assert result.exit_code == 1
        assert result.stderr.startswith(
            f"{_CONFIG_EXIT}FileNotFoundError: config.yaml not found"
        )
        assert result.stdout == ""

    def test_a_url_borne_token_does_not_reach_the_terminal(self) -> None:
        """A ``requests`` fault quotes the URL it failed on, query string too.

        ``exception_for_log`` routes one through ``scrub_request_error``, which
        keeps the class name and drops the URL.
        """
        token = "sk-live-9f3c2a"

        result = self._boot_failing_at(
            "create_recommendation_engine",
            requests.ConnectionError(
                f"HTTPConnectionPool: /api/tags?api_key={token} refused"
            ),
        )

        assert result.exit_code == 1
        assert result.stderr == f"{_COMPONENT_EXIT}ConnectionError\n"
        assert token not in result.stderr


class TestAMalformedInputsBlockDoesNotAbortTheBoot:
    """A list-shaped ``inputs:`` is truthy and has no ``.items()``.

    The credential sweep runs on the callback, so letting it raise takes down
    every verb. ``source list`` reads ``inputs`` itself and still faults; this
    covers the verbs that do not.
    """

    def test_a_read_only_command_still_runs(self, cli_runner: CliRunner) -> None:
        storage = MagicMock(spec=StorageManager)
        config: dict[str, Any] = {"inputs": [{"plugin": "gog", "enabled": True}]}

        result = _invoke_with_mocks(cli_runner, ["status"], storage, config=config)

        assert result.exit_code == 0, result.output
        # Anchored: the command reached its own body, rather than a quiet exit.
        assert "Components:" in result.output
