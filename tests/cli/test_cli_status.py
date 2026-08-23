"""Tests for CLI status command."""

import json
from importlib.metadata import PackageNotFoundError
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from src import __version__ as APP_VERSION
from src.cli.main import cli
from src.recommendations.engine import RecommendationEngine
from src.storage.manager import StorageManager
from tests.factories import (
    authenticated_client,
    back_mock_settings_store,
    booted_web_app,
)

from .conftest import _cli_patches


def _status_invoke(
    cli_runner: CliRunner,
    config: dict | None = None,
    args: list[str] | None = None,
) -> object:
    """Invoke the status command against mocked components."""
    p_config, p_storage, p_engine = _cli_patches()
    mock_storage = MagicMock(spec=StorageManager)
    back_mock_settings_store(mock_storage)
    with (
        p_config as mock_load,
        p_storage as mock_storage_fn,
        p_engine as mock_eng,
    ):
        mock_load.return_value = config or {}
        mock_storage_fn.return_value = mock_storage
        mock_eng.return_value = MagicMock(spec=RecommendationEngine)
        return cli_runner.invoke(cli, args or ["status"])


def _web_status_version() -> str:
    """Return the version GET /api/status serves for this tree."""
    storage = MagicMock(spec=StorageManager)
    with booted_web_app(
        storage, {}, engine=MagicMock(spec=RecommendationEngine)
    ) as app:
        return str(authenticated_client(app).get("/api/status").json()["version"])


class TestStatusTable:
    """Tests for status command with table output."""

    def test_status_table_shows_version(self, cli_runner: CliRunner) -> None:
        """Test that status command displays version."""
        result = _status_invoke(
            cli_runner,
            config={"recommendations": {"max_count": 20}},
        )
        assert result.exit_code == 0
        assert APP_VERSION in result.output


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
        assert set(data["components"].keys()) == {"engine", "storage"}
        assert data["components"]["engine"] is True
        assert data["components"]["storage"] is True
        # Recommendations config includes both max_count and default_count
        assert data["recommendations_config"]["max_count"] == 10
        assert data["recommendations_config"]["default_count"] == 3


class TestStatusVersion:
    """Both interfaces serve the version src/__init__.py resolves."""

    def test_status_json_version_matches_web_status_endpoint(
        self, cli_runner: CliRunner
    ) -> None:
        result = _status_invoke(cli_runner, args=["status", "--format", "json"])
        assert result.exit_code == 0
        assert json.loads(result.output)["version"] == _web_status_version()

    def test_status_succeeds_when_package_is_not_installed(
        self, cli_runner: CliRunner
    ) -> None:
        with patch(
            "importlib.metadata.version",
            side_effect=PackageNotFoundError("recommendinator"),
        ):
            result = _status_invoke(cli_runner, args=["status", "--format", "json"])
        assert result.exit_code == 0
        assert json.loads(result.output)["version"] == APP_VERSION
