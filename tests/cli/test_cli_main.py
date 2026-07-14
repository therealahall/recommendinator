"""Tests for CLI __main__ entry point."""

import logging
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from src.cli.main import cli
from src.ingestion.sync import SyncResult
from src.llm.embeddings import EmbeddingGenerator
from src.llm.recommendations import RecommendationGenerator
from src.recommendations.engine import RecommendationEngine
from src.storage.manager import StorageManager


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


def test_non_update_command_runs_both_source_migrations() -> None:
    """Both source migrations run on a non-update command (``status``).

    The rename migrations live on the top-level ``cli`` callback, so a CLI-only
    user is migrated even when they never run ``update``. Each migration must
    fire exactly once with the real storage instance.
    """
    mock_storage = MagicMock(spec=StorageManager)
    with (
        patch("src.cli.main.load_config", return_value={}),
        patch("src.cli.main.create_storage_manager", return_value=mock_storage),
        patch(
            "src.cli.main.create_llm_components",
            return_value=(
                None,
                MagicMock(spec=EmbeddingGenerator),
                MagicMock(spec=RecommendationGenerator),
            ),
        ),
        patch(
            "src.cli.main.create_recommendation_engine",
            return_value=MagicMock(spec=RecommendationEngine),
        ),
        patch("src.cli.main.migrate_source_labels") as spy_labels,
        patch("src.cli.main.migrate_source_config_plugins") as spy_plugins,
        patch(
            "src.cli.commands.importlib.metadata.version",
            return_value="0.6.0",
        ),
    ):
        result = CliRunner().invoke(cli, ["status"])

    assert result.exit_code == 0, result.output
    spy_labels.assert_called_once_with(mock_storage)
    spy_plugins.assert_called_once_with(mock_storage)


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
    * A ``create=True`` spy on ``src.cli.commands.migrate_source_labels`` /
      ``migrate_source_config_plugins`` — the exact binding a reintroduced
      ``from src.storage.source_migration import ...`` in ``commands.py`` plus a
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
            "src.cli.main.create_llm_components",
            return_value=(
                None,
                MagicMock(spec=EmbeddingGenerator),
                MagicMock(spec=RecommendationGenerator),
            ),
        ),
        patch(
            "src.cli.main.create_recommendation_engine",
            return_value=MagicMock(spec=RecommendationEngine),
        ),
        patch(
            "src.cli.commands.execute_multi_source_sync", side_effect=_fake_sync
        ) as spy_sync,
        patch(
            "src.ingestion.sources.goodreads_csv.GoodreadsCsvPlugin.validate_config",
            return_value=[],
        ),
        patch("src.cli.commands.migrate_source_labels", create=True) as guard_labels,
        patch(
            "src.cli.commands.migrate_source_config_plugins", create=True
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
    guard_labels.assert_not_called()
    guard_plugins.assert_not_called()
