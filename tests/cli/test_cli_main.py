"""Tests for CLI __main__ entry point."""

import logging
import sqlite3
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import requests
from click.testing import CliRunner

from src.cli.commands import _update
from src.cli.main import cli
from src.ingestion.sync import SyncResult
from src.recommendations.engine import RecommendationEngine
from src.storage.global_secrets import GLOBAL_SECRET_USER_ID
from src.storage.manager import StorageManager
from tests.cli.conftest import _invoke_with_mocks


def test_cli_main_module_exposes_cli_entry_point() -> None:
    """src.cli.__main__ exposes the cli Click group."""
    import src.cli.__main__ as main_module

    assert main_module.cli is cli


def test_cli_help_via_runner() -> None:
    """CLI --help exits cleanly via CliRunner."""
    runner = CliRunner()
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "Recommendinator CLI" in result.output


def test_non_update_command_runs_every_source_migration() -> None:
    """All three source migrations run on a non-update command (``status``).

    They live on the top-level ``cli`` callback, so a CLI-only user is migrated
    even without ``update``. Attribution's completion record hides a deleted
    call from any later side effect.
    """
    mock_storage = MagicMock(spec=StorageManager)
    config: dict[str, Any] = {}
    with (
        patch("src.cli.main.load_config", return_value=config),
        patch("src.cli.main.create_storage_manager", return_value=mock_storage),
        patch(
            "src.cli.main.create_recommendation_engine",
            return_value=MagicMock(spec=RecommendationEngine),
        ),
        patch("src.cli.main.migrate_source_labels") as spy_labels,
        patch("src.cli.main.migrate_source_config_plugins") as spy_plugins,
        patch("src.cli.main.migrate_source_attribution") as spy_attribution,
        patch(
            "src.cli.commands._status.importlib.metadata.version",
            return_value="0.6.0",
        ),
    ):
        result = CliRunner().invoke(cli, ["status"])

    assert result.exit_code == 0, result.output
    spy_labels.assert_called_once_with(mock_storage)
    spy_plugins.assert_called_once_with(mock_storage)
    spy_attribution.assert_called_once_with(config, mock_storage)


def test_update_does_not_double_invoke_source_migrations(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """``update`` runs each source migration exactly once, not twice.

    The migrations were hoisted to the ``cli`` callback; ``update`` must no
    longer invoke them itself. Rather than patching the ``src.cli.main``
    bindings (which a reintroduced call through ``update``'s OWN import would
    bypass entirely), this drives the REAL migration against a REAL on-disk
    ``StorageManager``:

    * The ``cli`` callback runs the unpatched, real migration on the seeded
      ``goodreads`` row, so its side effect (the relabel + its single
      "Relabeled" log line) is observable proof the callback migration fired.
    * A ``create=True`` spy on ``src.cli.commands._update.migrate_source_labels``
      / ``migrate_source_config_plugins`` — the exact binding a reintroduced
      ``from src.storage.source_migration import ...`` in ``_update.py`` plus a
      call inside ``update()`` would resolve through — is asserted NEVER called.
      Because the real migration is idempotent (a second run on already-migrated
      data is a silent no-op), a re-invocation cannot be caught by its own side
      effect; the command-binding guard is what fails if the bug returns.

    The sync layer and plugin validation are mocked so the command body actually
    executes (rather than exiting early on ``--help``).
    """
    config = {
        "inputs": {
            "goodreads_csv": {
                "plugin": "goodreads_csv",
                "path": "inputs/goodreads_library_export.csv",
                "enabled": True,
            }
        },
        "recommendations": {"min_rating_for_preference": 4},
    }
    storage = StorageManager(sqlite_path=tmp_path / "test.db")
    with storage.connection() as conn:
        conn.execute(
            "INSERT INTO content_items (user_id, title, content_type, status, source) "
            "VALUES (1, 'Some Title', 'book', 'completed', 'goodreads')"
        )
        conn.commit()

    def _fake_sync(**kwargs: Any) -> list[SyncResult]:
        sources = kwargs.get("sources") or []
        return [
            SyncResult(source_name=plugin.display_name) for plugin, _config in sources
        ]

    with (
        patch("src.cli.main.load_config", return_value=config),
        patch("src.cli.main.create_storage_manager", return_value=storage),
        patch(
            "src.cli.main.create_recommendation_engine",
            return_value=MagicMock(spec=RecommendationEngine),
        ),
        patch(
            "src.cli.commands._update.execute_multi_source_sync", side_effect=_fake_sync
        ) as spy_sync,
        patch(
            "src.ingestion.sources.goodreads_csv.GoodreadsCsvPlugin.validate_config",
            return_value=[],
        ),
        patch(
            "src.cli.commands._update.migrate_source_labels", create=True
        ) as guard_labels,
        patch(
            "src.cli.commands._update.migrate_source_config_plugins", create=True
        ) as guard_plugins,
    ):
        with caplog.at_level(logging.INFO):
            result = CliRunner().invoke(cli, ["update"])

    assert result.exit_code == 0, result.output
    # Proves the update body actually executed (it reached the sync call)
    # rather than exiting early as the old ``--help`` invocation did.
    spy_sync.assert_called_once()

    # The real callback migration ran exactly once against real storage: the
    # seeded 'goodreads' row is relabeled and exactly one "Relabeled" line logs.
    with storage.connection() as conn:
        rows = conn.execute("SELECT source FROM content_items").fetchall()
    assert [row[0] for row in rows] == ["goodreads_csv"]
    relabel_logs = [
        record for record in caplog.records if "Relabeled" in record.getMessage()
    ]
    assert len(relabel_logs) == 1
    assert "content item(s)" in relabel_logs[0].getMessage()

    # ``update`` must NOT re-invoke either migration through its own import.
    # ``create=True`` invents the attribute, so a spy aimed at a module the
    # command has moved out of watches nothing and passes regardless.
    assert cli.commands["update"] is _update.update
    guard_labels.assert_not_called()
    guard_plugins.assert_not_called()


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
        storage.save_credential(1, "calibre-web", "api_key", "top-secret")

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
            patch("src.cli.main.migrate_source_labels") as spy_labels,
            patch("src.cli.main.migrate_source_config_plugins") as spy_plugins,
            patch("src.cli.main.migrate_source_attribution"),
            patch(f"src.cli.main.{patched}", side_effect=error),
        ):
            result = CliRunner(mix_stderr=False).invoke(cli, ["status"])
        return result, spy_labels, spy_plugins

    def test_a_missing_config_file_exits_one_naming_the_fault(self) -> None:
        result, _, _ = self._boot_failing_at(
            "load_config", FileNotFoundError("config.yaml not found")
        )

        assert result.exit_code == 1
        assert result.stderr.startswith(
            f"{_CONFIG_EXIT}FileNotFoundError: config.yaml not found"
        )
        assert result.stdout == ""

    @pytest.mark.parametrize(
        ("patched", "error", "rendered"),
        [
            (
                "create_storage_manager",
                sqlite3.OperationalError("unable to open database file"),
                "OperationalError: unable to open database file",
            ),
            (
                "migrate_config_settings",
                sqlite3.OperationalError("no such table: settings"),
                "OperationalError: no such table: settings",
            ),
            (
                "create_recommendation_engine",
                ValueError("bad scorer weight"),
                "ValueError: bad scorer weight",
            ),
        ],
        ids=["storage", "settings-migration", "engine"],
    )
    def test_a_component_failure_exits_one_naming_the_fault(
        self, patched: str, error: Exception, rendered: str
    ) -> None:
        """The migration hooks are inside the guard, not beside it."""
        result, _, _ = self._boot_failing_at(patched, error)

        assert result.exit_code == 1
        assert result.stderr.startswith(f"{_COMPONENT_EXIT}{rendered}")
        assert result.stdout == ""

    def test_a_url_borne_token_does_not_reach_the_terminal(self) -> None:
        """A ``requests`` fault quotes the URL it failed on, query string too.

        ``exception_for_log`` routes one through ``scrub_request_error``, which
        keeps the class name and drops the URL.
        """
        token = "sk-live-9f3c2a"

        result, _, _ = self._boot_failing_at(
            "create_recommendation_engine",
            requests.ConnectionError(
                f"HTTPConnectionPool: /api/tags?api_key={token} refused"
            ),
        )

        assert result.exit_code == 1
        assert result.stderr == f"{_COMPONENT_EXIT}ConnectionError\n"
        assert token not in result.stderr

    def test_a_failed_boot_runs_no_source_migration(self) -> None:
        """They sit after the guard, so a half-built storage never reaches them."""
        result, spy_labels, spy_plugins = self._boot_failing_at(
            "create_storage_manager", sqlite3.OperationalError("disk I/O error")
        )

        assert result.exit_code == 1
        spy_labels.assert_not_called()
        spy_plugins.assert_not_called()


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


class TestMockedBootSecretSweepRegression:
    """The mocked CLI storage must look like an empty credentials table.

    Bug: ``back_mock_settings_store`` — the shared backing behind every mocked
    CLI storage — stubbed only the settings methods. Every other method on a
    ``Mock(spec=StorageManager)`` returns a truthy ``Mock``, so the
    ``migrate_config_secrets`` hook that ``src.cli.main`` runs on every boot read
    ``get_credential`` as "an encrypted secret already exists and takes
    precedence" and discarded the config value instead of storing it.

    Fix: the helper now reports an empty credentials table, so a mocked boot
    takes the same branch a fresh install does.
    """

    def test_boot_sweeps_a_yaml_api_key_into_encrypted_storage(
        self, cli_runner: CliRunner
    ) -> None:
        """The secret is saved under the ``settings:`` namespace and stripped."""
        storage = MagicMock(spec=StorageManager)
        config: dict[str, Any] = {
            "enrichment": {"providers": {"tmdb": {"api_key": "yaml-secret"}}}
        }

        result = _invoke_with_mocks(cli_runner, ["status"], storage, config=config)

        assert result.exit_code == 0, result.output
        storage.save_credential.assert_called_once_with(
            GLOBAL_SECRET_USER_ID,
            "settings:enrichment.providers.tmdb",
            "api_key",
            "yaml-secret",
        )
        # And no plaintext copy lingers in the running config.
        assert "api_key" not in config["enrichment"]["providers"]["tmdb"]


class TestASourceRowStubSurvivesTheBootRegression:
    """Nothing depended on it yet, which is what made it a trap.

    The shared backing assigned ``get_source_config`` after the test set it, so
    a row-backed source read as fresh. Fix: it fills only what the caller left
    unset.
    """

    def test_a_pre_set_source_row_still_shadows_the_file_secret(
        self, cli_runner: CliRunner
    ) -> None:
        storage = MagicMock(spec=StorageManager)
        storage.get_source_config.return_value = {
            "source_id": "gog_work",
            "plugin": "gog",
            "config": {},
            "enabled": True,
        }
        config: dict[str, Any] = {
            "inputs": {
                "gog_work": {
                    "plugin": "gog",
                    "enabled": True,
                    "refresh_token": "from-yaml",
                }
            }
        }

        result = _invoke_with_mocks(cli_runner, ["status"], storage, config=config)

        assert result.exit_code == 0, result.output
        # Both branches drop the plaintext copy, so this only says the sweep
        # ran; storing nothing is what says it saw the row.
        assert "refresh_token" not in config["inputs"]["gog_work"]
        storage.save_credential.assert_not_called()
