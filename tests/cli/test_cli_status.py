"""Tests for CLI status command."""

import json
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from src.cli.main import cli
from src.recommendations.engine import RecommendationEngine
from src.storage.manager import StorageManager
from tests.factories import back_mock_settings_store

from .conftest import _cli_patches


def _status_invoke(
    cli_runner: CliRunner,
    config: dict | None = None,
    args: list[str] | None = None,
    version: str = "0.6.0",
) -> object:
    """Invoke the status command against mocked components and a fixed version."""
    p_config, p_storage, p_engine = _cli_patches()
    mock_storage = MagicMock(spec=StorageManager)
    back_mock_settings_store(mock_storage)
    with (
        p_config as mock_load,
        p_storage as mock_storage_fn,
        p_engine as mock_eng,
        patch(
            "src.cli.commands._status.importlib.metadata.version",
            return_value=version,
        ),
    ):
        mock_load.return_value = config or {}
        mock_storage_fn.return_value = mock_storage
        mock_eng.return_value = MagicMock(spec=RecommendationEngine)
        return cli_runner.invoke(cli, args or ["status"])


class TestStatusTable:
    """Tests for status command with table output."""

    def test_status_table_shows_version(self, cli_runner: CliRunner) -> None:
        """Test that status command displays version."""
        result = _status_invoke(
            cli_runner,
            config={"recommendations": {"max_count": 20}},
        )
        assert result.exit_code == 0
        assert "Recommendinator v0.6.0" in result.output


class TestStatusJson:
    """Tests for status command with JSON output."""

    def test_status_json_output(self, cli_runner: CliRunner) -> None:
        """Test that status command JSON matches web API StatusResponse shape."""
        result = _status_invoke(
            cli_runner,
            config={"recommendations": {"max_count": 10, "default_count": 3}},
            args=["status", "--format", "json"],
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        # Top-level keys match StatusResponse
        assert set(data.keys()) == {
            "status",
            "version",
            "components",
            "recommendations_config",
        }
        assert data["status"] == "ready"
        assert data["version"] == "0.6.0"
        assert set(data["components"].keys()) == {"engine", "storage"}
        assert data["components"]["engine"] is True
        assert data["components"]["storage"] is True
        # Recommendations config includes both max_count and default_count
        assert data["recommendations_config"]["max_count"] == 10
        assert data["recommendations_config"]["default_count"] == 3
