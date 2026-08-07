"""Tests for CLI recommend command."""

import json
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from src.cli.main import cli
from src.llm.embeddings import EmbeddingGenerator
from src.llm.recommendations import RecommendationGenerator
from src.models.content import ConsumptionStatus, ContentItem, ContentType
from src.recommendations.engine import RecommendationEngine
from src.storage.manager import StorageManager
from tests.factories import back_mock_settings_store

from .conftest import _invoke_with_mocks


class TestRecommendEmptyResultsRegression:
    """Regression tests for the recommend command empty-results messaging."""

    def test_empty_recommendations_shows_unconsumed_guidance_regression(
        self, cli_runner: CliRunner
    ) -> None:
        """CLI explains that recommendations come from unconsumed items.

        Bug: the old message said 'add more consumed content' which was
        misleading — recommendations are based on items the user has NOT
        consumed yet. If all items are completed, there is nothing to
        recommend. The message now explains this.
        """
        mock_engine = MagicMock(spec=RecommendationEngine)
        mock_engine.generate_recommendations.return_value = []

        result = _invoke_recommend_with_engine(
            cli_runner,
            ["recommend", "--type", "video_game"],
            mock_engine,
        )

        assert result.exit_code == 0
        assert "haven't consumed yet" in result.output
        assert "add more consumed content" not in result.output


class TestRecommendCountMaxEnforcement:
    """Tests for config-driven --count max enforcement (matches web API)."""

    def test_count_exceeds_max_count_aborts(self, cli_runner: CliRunner) -> None:
        """--count greater than configured max_count aborts."""
        mock_storage = MagicMock(spec=StorageManager)
        config = {"recommendations": {"max_count": 5}}
        result = _invoke_with_mocks(
            cli_runner,
            ["recommend", "--type", "video_game", "--count", "10"],
            mock_storage,
            config=config,
        )

        assert result.exit_code != 0
        assert "exceeds configured max_count=5" in result.output

    def test_count_equal_to_max_count_is_accepted(self, cli_runner: CliRunner) -> None:
        """--count exactly equal to max_count is the boundary and is accepted."""
        mock_engine = MagicMock(spec=RecommendationEngine)
        mock_engine.generate_recommendations.return_value = []

        result = _invoke_recommend_with_engine(
            cli_runner,
            ["recommend", "--type", "video_game", "--count", "5"],
            mock_engine,
            config={"recommendations": {"max_count": 5}},
        )

        assert result.exit_code == 0
        call_kwargs = mock_engine.generate_recommendations.call_args[1]
        assert call_kwargs["count"] == 5

    def test_default_max_count_of_20_applies_when_no_config(
        self, cli_runner: CliRunner
    ) -> None:
        """With no recommendations section in config, default max is 20."""
        mock_storage = MagicMock(spec=StorageManager)
        result = _invoke_with_mocks(
            cli_runner,
            ["recommend", "--type", "video_game", "--count", "21"],
            mock_storage,
            config={},
        )

        assert result.exit_code != 0
        assert "exceeds configured max_count=20" in result.output


def _invoke_recommend_with_engine(
    cli_runner: CliRunner,
    args: list[str],
    mock_engine: MagicMock,
    config: dict | None = None,
) -> object:
    """Invoke the recommend CLI with a pre-configured engine mock.

    Can't use _invoke_with_mocks because we need to control the engine's
    return value, not just its existence.
    """
    with (
        patch("src.cli.main.load_config") as mock_load,
        patch("src.cli.main.create_storage_manager") as mock_storage_fn,
        patch("src.cli.main.create_llm_components") as mock_llm,
        patch("src.cli.main.create_recommendation_engine", return_value=mock_engine),
        patch("src.cli.main.migrate_source_labels"),
        patch("src.cli.main.migrate_source_config_plugins"),
    ):
        mock_load.return_value = config or {}
        mock_storage = MagicMock(spec=StorageManager)
        mock_storage.get_user_preference_config.return_value = MagicMock()
        # Real migrate_config_settings boot hook runs against an empty settings
        # store (no stub) — DB overlay is a no-op, no cross-test leak.
        back_mock_settings_store(mock_storage)
        mock_storage_fn.return_value = mock_storage
        mock_llm.return_value = (
            None,
            MagicMock(spec=EmbeddingGenerator),
            MagicMock(spec=RecommendationGenerator),
        )
        return cli_runner.invoke(cli, args)


class TestRecommendJsonOutput:
    """Tests for recommend --format json matching web RecommendationResponse."""

    def test_json_output_matches_web_shape(self, cli_runner: CliRunner) -> None:
        """Test JSON output includes all RecommendationResponse fields."""
        item = ContentItem(
            id="ext-1",
            title="Book One",
            author="Author A",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
        )
        item.db_id = 42
        mock_engine = MagicMock(spec=RecommendationEngine)
        mock_engine.generate_recommendations.return_value = [
            {
                "item": item,
                "score": 0.9,
                "reasoning": "Great match",
                "llm_reasoning": "LLM says so",
                "score_breakdown": {"genre": 0.5, "theme": 0.4},
            }
        ]

        result = _invoke_recommend_with_engine(
            cli_runner,
            ["recommend", "--type", "book", "--format", "json"],
            mock_engine,
        )

        assert result.exit_code == 0
        # Strip the "Generating..." preamble to parse JSON
        json_start = result.output.find("[")
        parsed = json.loads(result.output[json_start:])
        rec = parsed[0]
        # Field set matches web RecommendationResponse
        assert set(rec.keys()) == {
            "db_id",
            "title",
            "author",
            "score",
            "reasoning",
            "llm_reasoning",
            "score_breakdown",
            "variety_penalty",
        }
        assert rec["db_id"] == 42
        assert rec["llm_reasoning"] == "LLM says so"
        assert rec["score_breakdown"] == {"genre": 0.5, "theme": 0.4}


class TestRecommendCreatorColumnRegression:
    """`recommend` headed the creator column "Author" for every type.

    Bug reported: `recommend --type movie` printed directors under a column
    headed "Author". Root cause: the header was hardcoded, which only looked
    right while non-book items read back with no author at all. Fix: every
    row is the one requested type, so the header is that type's own creator
    column.
    """

    def test_a_movie_run_heads_its_creator_column_director_regression(
        self, cli_runner: CliRunner
    ) -> None:
        """The column naming a director says so."""
        item = ContentItem(
            id="ext-1",
            title="Arrival",
            author="Villeneuve",
            content_type=ContentType.MOVIE,
            status=ConsumptionStatus.UNREAD,
        )
        item.db_id = 7
        mock_engine = MagicMock(spec=RecommendationEngine)
        mock_engine.generate_recommendations.return_value = [
            {
                "item": item,
                "score": 0.9,
                "reasoning": "Great match",
            }
        ]

        result = _invoke_recommend_with_engine(
            cli_runner, ["recommend", "--type", "movie"], mock_engine
        )

        assert result.exit_code == 0
        assert "Director" in result.output
        assert "Author" not in result.output
        assert "Villeneuve" in result.output


class TestRecommendLlmFlag:
    """Tests for --use-llm/--no-use-llm flag behavior."""

    def test_no_use_llm_disables_llm(self, cli_runner: CliRunner) -> None:
        """--no-use-llm forwards use_llm=False to the engine."""
        mock_engine = MagicMock(spec=RecommendationEngine)
        mock_engine.generate_recommendations.return_value = []

        result = _invoke_recommend_with_engine(
            cli_runner,
            ["recommend", "--type", "video_game", "--no-use-llm"],
            mock_engine,
        )

        assert result.exit_code == 0
        call_kwargs = mock_engine.generate_recommendations.call_args[1]
        assert call_kwargs["use_llm"] is False

    def test_default_uses_llm(self, cli_runner: CliRunner) -> None:
        """With no flag specified, use_llm defaults to True (matches web API)."""
        mock_engine = MagicMock(spec=RecommendationEngine)
        mock_engine.generate_recommendations.return_value = []

        result = _invoke_recommend_with_engine(
            cli_runner,
            ["recommend", "--type", "video_game"],
            mock_engine,
        )

        assert result.exit_code == 0
        call_kwargs = mock_engine.generate_recommendations.call_args[1]
        assert call_kwargs["use_llm"] is True
