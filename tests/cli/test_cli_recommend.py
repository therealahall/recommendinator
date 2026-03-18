"""Tests for CLI recommend command."""

from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from src.cli.main import cli
from src.llm.embeddings import EmbeddingGenerator
from src.llm.recommendations import RecommendationGenerator
from src.recommendations.engine import RecommendationEngine
from src.storage.manager import StorageManager


class TestRecommendEmptyResultsRegression:
    """Regression tests for the recommend command empty-results messaging."""

    def test_empty_recommendations_shows_unconsumed_guidance_regression(self) -> None:
        """CLI explains that recommendations come from unconsumed items.

        Bug: the old message said 'add more consumed content' which was
        misleading — recommendations are based on items the user has NOT
        consumed yet. If all items are completed, there is nothing to
        recommend. The message now explains this.
        """
        runner = CliRunner()
        mock_engine = MagicMock(spec=RecommendationEngine)
        mock_engine.generate_recommendations.return_value = []

        with patch("src.cli.main.load_config") as mock_load:
            mock_load.return_value = {}
            with patch("src.cli.main.create_storage_manager") as mock_storage:
                mock_storage.return_value = MagicMock(spec=StorageManager)
                with patch("src.cli.main.create_llm_components") as mock_llm:
                    mock_llm.return_value = (
                        None,
                        MagicMock(spec=EmbeddingGenerator),
                        MagicMock(spec=RecommendationGenerator),
                    )
                    with patch(
                        "src.cli.main.create_recommendation_engine"
                    ) as mock_create_engine:
                        mock_create_engine.return_value = mock_engine
                        result = runner.invoke(
                            cli, ["recommend", "--type", "video_game"]
                        )

        assert result.exit_code == 0
        assert "haven't consumed yet" in result.output
        assert "add more consumed content" not in result.output
