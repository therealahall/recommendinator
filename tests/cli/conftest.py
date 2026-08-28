from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from src.cli.main import cli
from src.storage.manager import StorageManager
from tests.factories import back_mock_settings_store


@pytest.fixture
def cli_runner() -> CliRunner:
    return CliRunner()


def _cli_patches():
    return (
        patch("src.cli.main.load_config"),
        patch("src.cli.main.create_storage_manager"),
        patch("src.cli.main.create_recommendation_engine"),
    )


def _invoke_with_mocks(
    cli_runner: CliRunner,
    args: list[str],
    mock_storage: MagicMock | StorageManager,
    config: dict | None = None,
    input_text: str | None = None,
    engine: MagicMock | None = None,
) -> object:
    p_config, p_storage, p_engine = _cli_patches()
    with (
        p_config as mock_load,
        p_storage as mock_storage_fn,
        p_engine as mock_engine_fn,
    ):
        mock_load.return_value = config or {}
        back_mock_settings_store(mock_storage)
        mock_storage_fn.return_value = mock_storage
        if engine is not None:
            mock_engine_fn.return_value = engine
        return cli_runner.invoke(cli, args, input=input_text)
