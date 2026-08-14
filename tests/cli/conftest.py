"""Shared fixtures and helpers for CLI tests."""

from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from src.cli.main import cli
from src.storage.manager import StorageManager
from tests.factories import back_mock_settings_store


@pytest.fixture
def cli_runner() -> CliRunner:
    """Create CLI test runner."""
    return CliRunner()


def _cli_patches():
    """Context manager stack for CLI patches.

    The top-level ``cli`` callback runs the source migrations on every command,
    so they are patched here to keep them off the MagicMock storage in tests.

    The ``migrate_config_settings`` and ``migrate_config_credentials`` boot
    hooks are NOT stubbed — both run against the mocked StorageManager, which
    ``back_mock_settings_store`` makes behave like an empty database. Without
    that the settings overlay would leak state across tests, and the credential
    sweep would read a source row that is not there and discard every sensitive
    field the test's config declares.
    """
    return (
        patch("src.cli.main.load_config"),
        patch("src.cli.main.create_storage_manager"),
        patch("src.cli.main.create_recommendation_engine"),
        patch("src.cli.main.migrate_source_labels"),
        patch("src.cli.main.migrate_source_config_plugins"),
        patch("src.cli.main.migrate_source_attribution"),
    )


def _invoke_with_mocks(
    cli_runner: CliRunner,
    args: list[str],
    mock_storage: MagicMock | StorageManager,
    config: dict | None = None,
    input_text: str | None = None,
    engine: MagicMock | None = None,
) -> object:
    """Invoke CLI with standard mock setup.

    Args:
        cli_runner: Click test runner
        args: CLI arguments
        mock_storage: Pre-configured storage mock, or a real temp-DB
            StorageManager when a test needs the command to hit real storage
        config: Config dict (default: empty)
        input_text: Simulated stdin input
        engine: Pre-configured engine mock, for a command whose output is
            built from what the engine returns (default: a bare mock)
    """
    (
        p_config,
        p_storage,
        p_engine,
        p_labels,
        p_plugins,
        p_attribution,
    ) = _cli_patches()
    with (
        p_config as mock_load,
        p_storage as mock_storage_fn,
        p_engine as mock_engine_fn,
        p_labels,
        p_plugins,
        p_attribution,
    ):
        mock_load.return_value = config or {}
        back_mock_settings_store(mock_storage)
        mock_storage_fn.return_value = mock_storage
        if engine is not None:
            mock_engine_fn.return_value = engine
        return cli_runner.invoke(cli, args, input=input_text)
