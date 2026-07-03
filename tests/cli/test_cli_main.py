"""Tests for CLI __main__ entry point."""

from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner

from src.cli.main import cli
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


def test_cli_boot_overlays_db_settings_without_seeding(tmp_path: Path) -> None:
    """CLI boot assembles the effective config against an isolated DB.

    Drives the *real* ``migrate_config_settings`` hook with a real temp-DB
    StorageManager (no stub): a stored DB leaf must win over the YAML value,
    and boot must not write anything else to the settings table.
    """
    runner = CliRunner()
    config = {"web": {"port": 18473}}
    storage = StorageManager(sqlite_path=tmp_path / "test.db")
    # A DB leaf the operator set must win over the YAML value on boot.
    storage.set_setting("web.port", 9999)

    with (
        patch("src.cli.main.load_config", return_value=config),
        patch("src.cli.main.create_storage_manager", return_value=storage),
        patch("src.cli.main.create_llm_components", return_value=(None, None, None)),
        patch("src.cli.main.create_recommendation_engine"),
    ):
        result = runner.invoke(cli, ["status"])

    assert result.exit_code == 0
    # Real hook overlaid the DB leaf onto the in-memory config (DB wins).
    assert config["web"]["port"] == 9999
    # Boot seeded nothing: only the pre-existing leaf remains in the DB.
    assert storage.list_settings() == {"web.port": 9999}
