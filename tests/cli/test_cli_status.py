"""Tests for CLI status command."""

import json
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from src.cli.main import cli
from src.llm.embeddings import EmbeddingGenerator
from src.llm.recommendations import RecommendationGenerator
from src.recommendations.engine import RecommendationEngine
from src.storage.manager import StorageManager

from .conftest import _cli_patches

_SENTINEL = object()


def _status_invoke(
    cli_runner: CliRunner,
    config: dict | None = None,
    llm_client: MagicMock | None = None,
    embedding_gen: MagicMock | None | object = _SENTINEL,
    rec_gen: MagicMock | None | object = _SENTINEL,
    args: list[str] | None = None,
    version: str = "0.6.0",
) -> object:
    """Invoke the status command with version patch and full LLM control."""
    p_config, p_storage, p_llm, p_engine = _cli_patches()
    mock_storage = MagicMock(spec=StorageManager)
    with (
        p_config as mock_load,
        p_storage as mock_storage_fn,
        p_llm as mock_llm,
        p_engine as mock_eng,
        patch(
            "src.cli.commands.importlib.metadata.version",
            return_value=version,
        ),
    ):
        mock_load.return_value = config or {}
        mock_storage_fn.return_value = mock_storage
        effective_embed = (
            MagicMock(spec=EmbeddingGenerator)
            if embedding_gen is _SENTINEL
            else embedding_gen
        )
        effective_rec = (
            MagicMock(spec=RecommendationGenerator) if rec_gen is _SENTINEL else rec_gen
        )
        mock_llm.return_value = (llm_client, effective_embed, effective_rec)
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

    def test_status_table_shows_components(self, cli_runner: CliRunner) -> None:
        """Test that status command displays component readiness.

        With AI disabled, embedding_generator is reported as ready
        (matches web API behavior — component is not required without AI).
        """
        result = _status_invoke(
            cli_runner,
            config={
                "features": {"ai_enabled": True},
                "recommendations": {"max_count": 20},
            },
            embedding_gen=None,
        )
        assert result.exit_code == 0
        assert "Components:" in result.output
        assert "engine: ready" in result.output
        assert "storage: ready" in result.output
        assert "embedding_generator: not available" in result.output

    def test_status_table_shows_features(self, cli_runner: CliRunner) -> None:
        """Test that status command displays feature flags."""
        result = _status_invoke(
            cli_runner,
            config={
                "features": {
                    "ai_enabled": True,
                    "embeddings_enabled": True,
                    "llm_reasoning_enabled": False,
                },
                "recommendations": {"max_count": 20},
            },
        )
        assert result.exit_code == 0
        assert "Features:" in result.output
        assert "ai_enabled: enabled" in result.output
        assert "embeddings_enabled: enabled" in result.output
        assert "llm_reasoning_enabled: disabled" in result.output

    def test_status_table_shows_max_recommendations(
        self, cli_runner: CliRunner
    ) -> None:
        """Test that status command displays max recommendation count."""
        result = _status_invoke(
            cli_runner,
            config={"recommendations": {"max_count": 15, "default_count": 3}},
        )
        assert result.exit_code == 0
        assert "max=15" in result.output
        assert "default=3" in result.output

    def test_status_table_default_max_count(self, cli_runner: CliRunner) -> None:
        """Test that status uses default max_count of 20 when not configured."""
        result = _status_invoke(cli_runner)
        assert result.exit_code == 0
        assert "max=20" in result.output
        assert "default=5" in result.output


class TestStatusJson:
    """Tests for status command with JSON output."""

    def test_status_json_output(self, cli_runner: CliRunner) -> None:
        """Test that status command JSON matches web API StatusResponse shape."""
        result = _status_invoke(
            cli_runner,
            config={
                "features": {
                    "ai_enabled": True,
                    "embeddings_enabled": False,
                    "llm_reasoning_enabled": False,
                },
                "recommendations": {"max_count": 10, "default_count": 3},
            },
            llm_client=MagicMock(),
            embedding_gen=None,
            args=["status", "--format", "json"],
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        # Top-level keys match StatusResponse
        assert set(data.keys()) == {
            "status",
            "version",
            "components",
            "features",
            "recommendations_config",
        }
        assert data["status"] == "initializing"
        assert data["version"] == "0.6.0"
        # Component keys match (no llm_client)
        assert set(data["components"].keys()) == {
            "engine",
            "storage",
            "embedding_generator",
        }
        assert data["components"]["engine"] is True
        assert data["components"]["storage"] is True
        assert data["components"]["embedding_generator"] is False
        # Feature keys match FeaturesStatus (no derived use_embeddings)
        assert set(data["features"].keys()) == {
            "ai_enabled",
            "embeddings_enabled",
            "llm_reasoning_enabled",
        }
        assert data["features"]["ai_enabled"] is True
        assert data["features"]["embeddings_enabled"] is False
        # Recommendations config includes both max_count and default_count
        assert data["recommendations_config"]["max_count"] == 10
        assert data["recommendations_config"]["default_count"] == 3

    def test_status_json_all_components_ready(self, cli_runner: CliRunner) -> None:
        """Test JSON output when all components are ready (AI disabled)."""
        result = _status_invoke(
            cli_runner,
            embedding_gen=None,
            rec_gen=None,
            args=["status", "--format", "json"],
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        # With ai_enabled=False, embedding_generator is reported as ready
        assert data["components"]["engine"] is True
        assert data["components"]["embedding_generator"] is True
        assert data["status"] == "ready"
        assert data["features"]["ai_enabled"] is False
