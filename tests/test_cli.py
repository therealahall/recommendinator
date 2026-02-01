"""Tests for CLI commands."""

from unittest.mock import Mock, patch

import pytest
from click.testing import CliRunner

from src.cli.main import cli
from src.models.content import ConsumptionStatus, ContentItem, ContentType
from src.models.user_preferences import UserPreferenceConfig


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


def test_update_command_steam_success(mock_components):
    """Test update command with Steam source."""
    # Update mock config to include Steam
    mock_config = {
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
            "steam": {
                "api_key": "test_api_key",
                "steam_id": "76561198000000000",
                "enabled": True,
            }
        },
        "recommendations": {
            "min_rating_for_preference": 4,
        },
    }

    mock_steam_item = ContentItem(
        id="12345",
        title="Test Game",
        author=None,
        content_type=ContentType.VIDEO_GAME,
        status=ConsumptionStatus.COMPLETED,
        rating=4,
    )

    with (
        patch("src.cli.main.load_config", return_value=mock_config),
        patch("src.cli.commands.parse_steam_games", return_value=[mock_steam_item]),
    ):
        mock_components["embedding_gen"].generate_content_embedding.return_value = [
            0.1
        ] * 768
        mock_components["storage"].save_content_item.return_value = 1

        runner = CliRunner()
        result = runner.invoke(cli, ["update", "--source", "steam"])

        assert result.exit_code == 0
        assert "Updated" in result.output
        assert "Steam" in result.output


def test_update_command_steam_disabled(mock_components):
    """Test update command with disabled Steam source."""
    mock_config = {
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
            "steam": {
                "api_key": "test_api_key",
                "steam_id": "76561198000000000",
                "enabled": False,
            }
        },
        "recommendations": {
            "min_rating_for_preference": 4,
        },
    }

    with patch("src.cli.main.load_config", return_value=mock_config):
        runner = CliRunner()
        result = runner.invoke(cli, ["update", "--source", "steam"])

        assert result.exit_code == 0
        assert "disabled" in result.output.lower()


def test_update_command_steam_missing_api_key(mock_components):
    """Test update command with missing Steam API key."""
    mock_config = {
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
            "steam": {
                "api_key": "",
                "steam_id": "76561198000000000",
                "enabled": True,
            }
        },
        "recommendations": {
            "min_rating_for_preference": 4,
        },
    }

    with patch("src.cli.main.load_config", return_value=mock_config):
        runner = CliRunner()
        result = runner.invoke(cli, ["update", "--source", "steam"])

        assert result.exit_code == 0
        assert "API key" in result.output or "required" in result.output.lower()


def test_update_command_steam_api_error(mock_components):
    """Test update command with Steam API error."""
    from src.ingestion.sources.steam import SteamAPIError

    mock_config = {
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
            "steam": {
                "api_key": "test_api_key",
                "steam_id": "76561198000000000",
                "enabled": True,
            }
        },
        "recommendations": {
            "min_rating_for_preference": 4,
        },
    }

    with (
        patch("src.cli.main.load_config", return_value=mock_config),
        patch(
            "src.cli.commands.parse_steam_games",
            side_effect=SteamAPIError("API error"),
        ),
    ):
        runner = CliRunner()
        result = runner.invoke(cli, ["update", "--source", "steam"])

        assert result.exit_code == 0
        assert "Error" in result.output or "error" in result.output.lower()


# ---------------------------------------------------------------------------
# Preferences CLI tests (Phase 5)
# ---------------------------------------------------------------------------


def test_recommend_command_with_user(mock_components):
    """Test recommend command with --user option."""
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

    mock_components["engine"].generate_recommendations.return_value = (
        mock_recommendations
    )
    mock_components["storage"].get_user_preference_config = Mock(
        return_value=UserPreferenceConfig()
    )

    runner = CliRunner()
    result = runner.invoke(
        cli, ["recommend", "--type", "book", "--count", "1", "--user", "1"]
    )

    assert result.exit_code == 0
    assert "Test Book" in result.output
    mock_components["storage"].get_user_preference_config.assert_called_once_with(1)


def test_preferences_get(mock_components):
    """Test preferences get command."""
    mock_components["storage"].get_user_preference_config = Mock(
        return_value=UserPreferenceConfig(scorer_weights={"genre_match": 3.0})
    )

    runner = CliRunner()
    result = runner.invoke(cli, ["preferences", "get", "--format", "json"])

    assert result.exit_code == 0
    assert "genre_match" in result.output
    assert "3.0" in result.output


def test_preferences_set_weight(mock_components):
    """Test preferences set-weight command."""
    mock_components["storage"].get_user_preference_config = Mock(
        return_value=UserPreferenceConfig()
    )
    mock_components["storage"].save_user_preference_config = Mock()

    runner = CliRunner()
    result = runner.invoke(cli, ["preferences", "set-weight", "genre_match", "3.0"])

    assert result.exit_code == 0
    assert "Set genre_match weight to 3.0" in result.output
    mock_components["storage"].save_user_preference_config.assert_called_once()


def test_preferences_reset(mock_components):
    """Test preferences reset command."""
    mock_components["storage"].save_user_preference_config = Mock()

    runner = CliRunner()
    result = runner.invoke(cli, ["preferences", "reset"])

    assert result.exit_code == 0
    assert "Reset preferences to defaults" in result.output
    mock_components["storage"].save_user_preference_config.assert_called_once()


# ---------------------------------------------------------------------------
# Custom rules CLI tests (Phase 7)
# ---------------------------------------------------------------------------


def test_custom_rules_list_empty(mock_components):
    """Test listing custom rules when none exist."""
    mock_components["storage"].get_user_preference_config = Mock(
        return_value=UserPreferenceConfig()
    )

    runner = CliRunner()
    result = runner.invoke(cli, ["preferences", "custom-rules", "list"])

    assert result.exit_code == 0
    assert "No custom rules" in result.output


def test_custom_rules_add(mock_components):
    """Test adding a custom rule."""
    mock_config = UserPreferenceConfig()
    mock_components["storage"].get_user_preference_config = Mock(
        return_value=mock_config
    )
    mock_components["storage"].save_user_preference_config = Mock()

    runner = CliRunner()
    result = runner.invoke(cli, ["preferences", "custom-rules", "add", "avoid horror"])

    assert result.exit_code == 0
    assert "Added rule" in result.output
    assert "avoid horror" in result.output
    mock_components["storage"].save_user_preference_config.assert_called_once()


def test_custom_rules_list_with_rules(mock_components):
    """Test listing custom rules when some exist."""
    mock_config = UserPreferenceConfig(custom_rules=["avoid horror", "prefer sci-fi"])
    mock_components["storage"].get_user_preference_config = Mock(
        return_value=mock_config
    )

    runner = CliRunner()
    result = runner.invoke(cli, ["preferences", "custom-rules", "list"])

    assert result.exit_code == 0
    assert "0: avoid horror" in result.output
    assert "1: prefer sci-fi" in result.output


def test_custom_rules_remove(mock_components):
    """Test removing a custom rule."""
    mock_config = UserPreferenceConfig(custom_rules=["avoid horror"])
    mock_components["storage"].get_user_preference_config = Mock(
        return_value=mock_config
    )
    mock_components["storage"].save_user_preference_config = Mock()

    runner = CliRunner()
    result = runner.invoke(cli, ["preferences", "custom-rules", "remove", "0"])

    assert result.exit_code == 0
    assert "Removed rule" in result.output
    mock_components["storage"].save_user_preference_config.assert_called_once()


def test_custom_rules_remove_invalid_index(mock_components):
    """Test removing a rule with invalid index."""
    mock_config = UserPreferenceConfig()  # No rules
    mock_components["storage"].get_user_preference_config = Mock(
        return_value=mock_config
    )

    runner = CliRunner()
    result = runner.invoke(cli, ["preferences", "custom-rules", "remove", "99"])

    assert result.exit_code != 0
    assert "Invalid index" in result.output


def test_custom_rules_clear(mock_components):
    """Test clearing all custom rules."""
    mock_config = UserPreferenceConfig(custom_rules=["avoid horror", "prefer sci-fi"])
    mock_components["storage"].get_user_preference_config = Mock(
        return_value=mock_config
    )
    mock_components["storage"].save_user_preference_config = Mock()

    runner = CliRunner()
    result = runner.invoke(cli, ["preferences", "custom-rules", "clear", "--yes"])

    assert result.exit_code == 0
    assert "Cleared 2" in result.output
    mock_components["storage"].save_user_preference_config.assert_called_once()


def test_custom_rules_interpret_pattern(mock_components):
    """Test interpreting a rule using pattern matcher."""
    runner = CliRunner()
    result = runner.invoke(
        cli, ["preferences", "custom-rules", "interpret", "avoid horror"]
    )

    assert result.exit_code == 0
    assert "pattern-based" in result.output.lower()
    assert "horror" in result.output


def test_set_length_preference(mock_components):
    """Test setting a length preference."""
    mock_config = UserPreferenceConfig()
    mock_components["storage"].get_user_preference_config = Mock(
        return_value=mock_config
    )
    mock_components["storage"].save_user_preference_config = Mock()

    runner = CliRunner()
    result = runner.invoke(cli, ["preferences", "set-length", "book", "short"])

    assert result.exit_code == 0
    assert "book" in result.output
    assert "short" in result.output
    mock_components["storage"].save_user_preference_config.assert_called_once()


class TestConfigLoadingRegression:
    """Regression tests for configuration loading bugs."""

    def test_load_config_prefers_config_yaml_over_example_regression(self, tmp_path):
        """Regression test: load_config should prefer config.yaml over example.yaml.

        Bug reported: User had Steam enabled in config/config.yaml but web app
        was loading config/example.yaml (where Steam is disabled).

        Root cause: get_app() in web/app.py was explicitly defaulting to
        example.yaml instead of letting load_config() handle the default
        logic (which correctly tries config.yaml first).

        Fix: Removed the explicit example.yaml default from get_app().
        """

        from src.cli.config import load_config

        # Create a config directory with both files
        config_dir = tmp_path / "config"
        config_dir.mkdir()

        # config.yaml has steam enabled
        config_yaml = config_dir / "config.yaml"
        config_yaml.write_text(
            """
inputs:
  steam:
    enabled: true
    api_key: "test"
"""
        )

        # example.yaml has steam disabled
        example_yaml = config_dir / "example.yaml"
        example_yaml.write_text(
            """
inputs:
  steam:
    enabled: false
"""
        )

        # When load_config is called with None, it should use config.yaml
        # We need to temporarily change the working directory
        import os

        original_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            config = load_config(None)
            steam_enabled = config.get("inputs", {}).get("steam", {}).get("enabled")
            assert steam_enabled is True, (
                "load_config should prefer config.yaml (steam enabled) "
                "over example.yaml (steam disabled)"
            )
        finally:
            os.chdir(original_cwd)

    def test_load_config_falls_back_to_example_when_no_config(self, tmp_path):
        """Test that load_config falls back to example.yaml when config.yaml is missing."""

        from src.cli.config import load_config

        # Create a config directory with only example.yaml
        config_dir = tmp_path / "config"
        config_dir.mkdir()

        example_yaml = config_dir / "example.yaml"
        example_yaml.write_text(
            """
inputs:
  steam:
    enabled: false
"""
        )

        import os

        original_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            config = load_config(None)
            steam_enabled = config.get("inputs", {}).get("steam", {}).get("enabled")
            assert (
                steam_enabled is False
            ), "load_config should fall back to example.yaml when config.yaml missing"
        finally:
            os.chdir(original_cwd)
