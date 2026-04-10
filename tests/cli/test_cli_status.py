"""Tests for CLI status command."""

import json
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from src.cli.main import cli
from src.llm.embeddings import EmbeddingGenerator
from src.llm.recommendations import RecommendationGenerator
from src.recommendations.engine import RecommendationEngine
from src.storage.manager import StorageManager


@pytest.fixture
def cli_runner() -> CliRunner:
    """Create CLI test runner."""
    return CliRunner()


class TestStatusTable:
    """Tests for status command with table output."""

    def test_status_table_shows_version(self, cli_runner: CliRunner) -> None:
        """Test that status command displays version."""
        with patch("src.cli.main.load_config") as mock_load:
            mock_load.return_value = {
                "recommendations": {"max_count": 20},
            }
            with patch("src.cli.main.create_storage_manager") as mock_storage_fn:
                mock_storage = MagicMock(spec=StorageManager)
                mock_storage_fn.return_value = mock_storage
                with patch("src.cli.main.create_llm_components") as mock_llm:
                    mock_llm.return_value = (
                        None,
                        MagicMock(spec=EmbeddingGenerator),
                        MagicMock(spec=RecommendationGenerator),
                    )
                    with patch("src.cli.main.create_recommendation_engine") as mock_eng:
                        mock_eng.return_value = MagicMock(spec=RecommendationEngine)
                        with patch(
                            "src.cli.commands.importlib.metadata.version",
                            return_value="0.6.0",
                        ):
                            result = cli_runner.invoke(cli, ["status"])

        assert result.exit_code == 0
        assert "Recommendinator v0.6.0" in result.output

    def test_status_table_shows_components(self, cli_runner: CliRunner) -> None:
        """Test that status command displays component readiness."""
        with patch("src.cli.main.load_config") as mock_load:
            mock_load.return_value = {
                "recommendations": {"max_count": 20},
            }
            with patch("src.cli.main.create_storage_manager") as mock_storage_fn:
                mock_storage = MagicMock(spec=StorageManager)
                mock_storage_fn.return_value = mock_storage
                with patch("src.cli.main.create_llm_components") as mock_llm:
                    mock_llm.return_value = (
                        None,
                        None,
                        MagicMock(spec=RecommendationGenerator),
                    )
                    with patch("src.cli.main.create_recommendation_engine") as mock_eng:
                        mock_eng.return_value = MagicMock(spec=RecommendationEngine)
                        with patch(
                            "src.cli.commands.importlib.metadata.version",
                            return_value="0.6.0",
                        ):
                            result = cli_runner.invoke(cli, ["status"])

        assert result.exit_code == 0
        assert "Components:" in result.output
        assert "engine: ready" in result.output
        assert "storage: ready" in result.output
        assert "embedding_gen: not available" in result.output
        assert "llm_client: not available" in result.output

    def test_status_table_shows_features(self, cli_runner: CliRunner) -> None:
        """Test that status command displays feature flags."""
        with patch("src.cli.main.load_config") as mock_load:
            mock_load.return_value = {
                "features": {
                    "ai_enabled": True,
                    "embeddings_enabled": True,
                    "llm_reasoning_enabled": False,
                },
                "recommendations": {"max_count": 20},
            }
            with patch("src.cli.main.create_storage_manager") as mock_storage_fn:
                mock_storage = MagicMock(spec=StorageManager)
                mock_storage_fn.return_value = mock_storage
                with patch("src.cli.main.create_llm_components") as mock_llm:
                    mock_llm.return_value = (
                        None,
                        MagicMock(spec=EmbeddingGenerator),
                        MagicMock(spec=RecommendationGenerator),
                    )
                    with patch("src.cli.main.create_recommendation_engine") as mock_eng:
                        mock_eng.return_value = MagicMock(spec=RecommendationEngine)
                        with patch(
                            "src.cli.commands.importlib.metadata.version",
                            return_value="0.6.0",
                        ):
                            result = cli_runner.invoke(cli, ["status"])

        assert result.exit_code == 0
        assert "Features:" in result.output
        assert "ai_enabled: enabled" in result.output
        assert "embeddings_enabled: enabled" in result.output
        assert "llm_reasoning_enabled: disabled" in result.output
        assert "use_embeddings: enabled" in result.output

    def test_status_table_shows_max_recommendations(
        self, cli_runner: CliRunner
    ) -> None:
        """Test that status command displays max recommendation count."""
        with patch("src.cli.main.load_config") as mock_load:
            mock_load.return_value = {
                "recommendations": {"max_count": 15},
            }
            with patch("src.cli.main.create_storage_manager") as mock_storage_fn:
                mock_storage = MagicMock(spec=StorageManager)
                mock_storage_fn.return_value = mock_storage
                with patch("src.cli.main.create_llm_components") as mock_llm:
                    mock_llm.return_value = (
                        None,
                        MagicMock(spec=EmbeddingGenerator),
                        MagicMock(spec=RecommendationGenerator),
                    )
                    with patch("src.cli.main.create_recommendation_engine") as mock_eng:
                        mock_eng.return_value = MagicMock(spec=RecommendationEngine)
                        with patch(
                            "src.cli.commands.importlib.metadata.version",
                            return_value="0.6.0",
                        ):
                            result = cli_runner.invoke(cli, ["status"])

        assert result.exit_code == 0
        assert "Max recommendations: 15" in result.output

    def test_status_table_default_max_count(self, cli_runner: CliRunner) -> None:
        """Test that status uses default max_count of 20 when not configured."""
        with patch("src.cli.main.load_config") as mock_load:
            mock_load.return_value = {}
            with patch("src.cli.main.create_storage_manager") as mock_storage_fn:
                mock_storage = MagicMock(spec=StorageManager)
                mock_storage_fn.return_value = mock_storage
                with patch("src.cli.main.create_llm_components") as mock_llm:
                    mock_llm.return_value = (
                        None,
                        MagicMock(spec=EmbeddingGenerator),
                        MagicMock(spec=RecommendationGenerator),
                    )
                    with patch("src.cli.main.create_recommendation_engine") as mock_eng:
                        mock_eng.return_value = MagicMock(spec=RecommendationEngine)
                        with patch(
                            "src.cli.commands.importlib.metadata.version",
                            return_value="0.6.0",
                        ):
                            result = cli_runner.invoke(cli, ["status"])

        assert result.exit_code == 0
        assert "Max recommendations: 20" in result.output


class TestStatusJson:
    """Tests for status command with JSON output."""

    def test_status_json_output(self, cli_runner: CliRunner) -> None:
        """Test that status command produces valid JSON output."""
        with patch("src.cli.main.load_config") as mock_load:
            mock_load.return_value = {
                "features": {
                    "ai_enabled": True,
                    "embeddings_enabled": False,
                    "llm_reasoning_enabled": False,
                },
                "recommendations": {"max_count": 10},
            }
            with patch("src.cli.main.create_storage_manager") as mock_storage_fn:
                mock_storage = MagicMock(spec=StorageManager)
                mock_storage_fn.return_value = mock_storage
                with patch("src.cli.main.create_llm_components") as mock_llm:
                    mock_llm.return_value = (
                        MagicMock(),
                        None,
                        MagicMock(spec=RecommendationGenerator),
                    )
                    with patch("src.cli.main.create_recommendation_engine") as mock_eng:
                        mock_eng.return_value = MagicMock(spec=RecommendationEngine)
                        with patch(
                            "src.cli.commands.importlib.metadata.version",
                            return_value="0.6.0",
                        ):
                            result = cli_runner.invoke(
                                cli, ["status", "--format", "json"]
                            )

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["version"] == "0.6.0"
        assert data["components"]["engine"] is True
        assert data["components"]["storage"] is True
        assert data["components"]["embedding_gen"] is False
        assert data["components"]["llm_client"] is True
        assert data["features"]["ai_enabled"] is True
        assert data["features"]["embeddings_enabled"] is False
        assert data["features"]["use_embeddings"] is False
        assert data["recommendations"]["max_count"] == 10

    def test_status_json_all_components_unavailable(
        self, cli_runner: CliRunner
    ) -> None:
        """Test JSON output when no optional components are available."""
        with patch("src.cli.main.load_config") as mock_load:
            mock_load.return_value = {}
            with patch("src.cli.main.create_storage_manager") as mock_storage_fn:
                mock_storage = MagicMock(spec=StorageManager)
                mock_storage_fn.return_value = mock_storage
                with patch("src.cli.main.create_llm_components") as mock_llm:
                    mock_llm.return_value = (
                        None,
                        None,
                        None,
                    )
                    with patch("src.cli.main.create_recommendation_engine") as mock_eng:
                        mock_eng.return_value = MagicMock(spec=RecommendationEngine)
                        with patch(
                            "src.cli.commands.importlib.metadata.version",
                            return_value="0.6.0",
                        ):
                            result = cli_runner.invoke(
                                cli, ["status", "--format", "json"]
                            )

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["components"]["llm_client"] is False
        assert data["components"]["embedding_gen"] is False
        assert data["features"]["ai_enabled"] is False
        assert data["features"]["use_embeddings"] is False
