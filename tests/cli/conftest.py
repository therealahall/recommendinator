"""Shared fixtures and helpers for CLI tests."""

from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from src.cli.main import cli
from src.llm.embeddings import EmbeddingGenerator
from src.llm.recommendations import RecommendationGenerator


@pytest.fixture
def cli_runner() -> CliRunner:
    """Create CLI test runner."""
    return CliRunner()


def _cli_patches():
    """Context manager stack for CLI patches."""
    return (
        patch("src.cli.main.load_config"),
        patch("src.cli.main.create_storage_manager"),
        patch("src.cli.main.create_llm_components"),
        patch("src.cli.main.create_recommendation_engine"),
    )


def _invoke_with_mocks(
    cli_runner: CliRunner,
    args: list[str],
    mock_storage: MagicMock,
    config: dict | None = None,
    input_text: str | None = None,
    llm_client: MagicMock | None = None,
) -> object:
    """Invoke CLI with standard mock setup.

    Args:
        cli_runner: Click test runner
        args: CLI arguments
        mock_storage: Pre-configured storage mock
        config: Config dict (default: empty)
        input_text: Simulated stdin input
        llm_client: Optional LLM client mock (default: None/AI disabled)
    """
    p_config, p_storage, p_llm, p_engine = _cli_patches()
    with (
        p_config as mock_load,
        p_storage as mock_storage_fn,
        p_llm as mock_llm,
        p_engine,
    ):
        mock_load.return_value = config or {}
        mock_storage_fn.return_value = mock_storage
        mock_llm.return_value = (
            llm_client,
            MagicMock(spec=EmbeddingGenerator),
            MagicMock(spec=RecommendationGenerator),
        )
        return cli_runner.invoke(cli, args, input=input_text)
