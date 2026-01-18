"""Tests for CLI commands."""

import pytest
from unittest.mock import Mock, patch, MagicMock
from pathlib import Path
from click.testing import CliRunner

from src.cli.main import cli
from src.models.content import ContentItem, ContentType, ConsumptionStatus


@pytest.fixture
def mock_config():
    """Create a mock configuration."""
    return {
        "ollama": {
            "base_url": "http://localhost:11434",
            "model": "mistral:7b",
            "embedding_model": "nomic-embed-text",
        },
        "storage": {
            "database_path": "data/test.db",
            "vector_db_path": "data/test_chroma",
        },
        "inputs": {
            "goodreads": {
                "path": "inputs/goodreads_library_export.csv",
                "enabled": True,
            }
        },
        "recommendations": {
            "min_rating_for_preference": 4,
        },
    }


@pytest.fixture
def mock_components(mock_config):
    """Create mock components."""
    with (
        patch("src.cli.main.load_config", return_value=mock_config),
        patch("src.cli.main.create_storage_manager") as mock_storage,
        patch("src.cli.main.create_llm_components") as mock_llm,
        patch("src.cli.main.create_recommendation_engine") as mock_engine,
    ):
        # Setup mocks
        mock_storage_manager = Mock()
        mock_storage.return_value = mock_storage_manager

        mock_client = Mock()
        mock_embedding_gen = Mock()
        mock_rec_gen = Mock()
        mock_llm.return_value = (mock_client, mock_embedding_gen, mock_rec_gen)

        mock_engine_instance = Mock()
        mock_engine.return_value = mock_engine_instance

        yield {
            "storage": mock_storage_manager,
            "client": mock_client,
            "embedding_gen": mock_embedding_gen,
            "rec_gen": mock_rec_gen,
            "engine": mock_engine_instance,
        }


def test_cli_help():
    """Test CLI help command."""
    runner = CliRunner()
    result = runner.invoke(cli, ["--help"])

    assert result.exit_code == 0
    assert "Personal Recommendations CLI" in result.output
    assert "recommend" in result.output
    assert "update" in result.output
    assert "complete" in result.output


def test_recommend_command_help(mock_components):
    """Test recommend command help."""
    runner = CliRunner()
    result = runner.invoke(cli, ["recommend", "--help"])

    assert result.exit_code == 0
    assert "Get personalized recommendations" in result.output
    assert "--type" in result.output
    assert "--count" in result.output


def test_recommend_command_basic(mock_components):
    """Test basic recommend command."""
    # Setup mock recommendations
    mock_item = ContentItem(
        id="1",
        title="Test Book",
        author="Test Author",
        content_type=ContentType.BOOK,
        status=ConsumptionStatus.UNREAD,
    )

    mock_recommendations = [
        {
            "item": mock_item,
            "score": 0.85,
            "similarity_score": 0.8,
            "preference_score": 0.7,
            "reasoning": "Recommended highly similar to items you've enjoyed",
        }
    ]

    # Mock storage to return consumed items
    mock_components["storage"].get_completed_items.return_value = [
        ContentItem(
            id="2",
            title="Read Book",
            author="Author",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            rating=5,
        )
    ]
    mock_components["storage"].get_unconsumed_items.return_value = [mock_item]
    mock_components["storage"].search_similar.return_value = [(mock_item, 0.8)]

    mock_components["engine"].generate_recommendations.return_value = (
        mock_recommendations
    )

    runner = CliRunner()
    result = runner.invoke(cli, ["recommend", "--type", "book", "--count", "1"])

    assert result.exit_code == 0
    assert "Test Book" in result.output
    assert "Test Author" in result.output


def test_recommend_command_json(mock_components):
    """Test recommend command with JSON output."""
    mock_item = ContentItem(
        id="1",
        title="Test Book",
        author="Test Author",
        content_type=ContentType.BOOK,
        status=ConsumptionStatus.UNREAD,
    )

    mock_recommendations = [
        {
            "item": mock_item,
            "score": 0.85,
            "similarity_score": 0.8,
            "preference_score": 0.7,
            "reasoning": "Recommended highly similar",
        }
    ]

    # Mock storage to return consumed items
    mock_components["storage"].get_completed_items.return_value = [
        ContentItem(
            id="2",
            title="Read Book",
            author="Author",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            rating=5,
        )
    ]
    mock_components["storage"].get_unconsumed_items.return_value = [mock_item]
    mock_components["storage"].search_similar.return_value = [(mock_item, 0.8)]

    mock_components["engine"].generate_recommendations.return_value = (
        mock_recommendations
    )

    runner = CliRunner()
    result = runner.invoke(
        cli, ["recommend", "--type", "book", "--count", "1", "--format", "json"]
    )

    assert result.exit_code == 0
    assert "Test Book" in result.output
    assert '"title"' in result.output
    assert '"score"' in result.output


def test_update_command_help(mock_components):
    """Test update command help."""
    runner = CliRunner()
    result = runner.invoke(cli, ["update", "--help"])

    assert result.exit_code == 0
    assert "Update data from input files" in result.output
    assert "--source" in result.output


def test_complete_command_help(mock_components):
    """Test complete command help."""
    runner = CliRunner()
    result = runner.invoke(cli, ["complete", "--help"])

    assert result.exit_code == 0
    assert "Mark content as completed" in result.output
    assert "--type" in result.output
    assert "--title" in result.output


def test_complete_command_basic(mock_components):
    """Test basic complete command."""
    mock_components["embedding_gen"].generate_content_embedding.return_value = [
        0.1
    ] * 768
    mock_components["storage"].save_content_item.return_value = 1

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "complete",
            "--type",
            "book",
            "--title",
            "Test Book",
            "--author",
            "Test Author",
            "--rating",
            "4",
        ],
    )

    assert result.exit_code == 0
    assert "Marked 'Test Book' as completed" in result.output


def test_complete_command_invalid_rating(mock_components):
    """Test complete command with invalid rating."""
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "complete",
            "--type",
            "book",
            "--title",
            "Test Book",
            "--rating",
            "6",
        ],
    )

    assert result.exit_code != 0
    assert "Rating must be between 1 and 5" in result.output
