"""Tests for CLI enrichment commands."""

from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from src.cli.main import cli


@pytest.fixture
def cli_runner() -> CliRunner:
    """Create CLI test runner."""
    return CliRunner()


@pytest.fixture
def mock_context() -> dict:
    """Create mock CLI context objects."""
    mock_storage = MagicMock()
    mock_config = {
        "enrichment": {
            "enabled": True,
            "batch_size": 50,
            "providers": {
                "tmdb": {"enabled": True, "api_key": "test-key"},
            },
        },
    }
    return {
        "storage": mock_storage,
        "config": mock_config,
    }


class TestEnrichmentStart:
    """Tests for enrichment start command."""

    def test_enrichment_disabled_error(self, cli_runner: CliRunner) -> None:
        """Test error when enrichment is disabled."""
        with patch("src.cli.main.load_config") as mock_load:
            mock_load.return_value = {"enrichment": {"enabled": False}}
            with patch("src.cli.main.create_storage_manager"):
                with patch("src.cli.main.create_llm_components") as mock_llm:
                    mock_llm.return_value = (None, MagicMock(), MagicMock())
                    with patch("src.cli.main.create_recommendation_engine"):
                        result = cli_runner.invoke(cli, ["enrichment", "start"])

        assert result.exit_code != 0
        assert "disabled" in result.output.lower()

    def test_enrichment_start_success(self, cli_runner: CliRunner) -> None:
        """Test successful enrichment start."""
        mock_status = MagicMock()
        mock_status.running = False
        mock_status.completed = True
        mock_status.cancelled = False
        mock_status.items_processed = 10
        mock_status.items_enriched = 8
        mock_status.items_not_found = 2
        mock_status.items_failed = 0
        mock_status.elapsed_seconds = 5.0
        mock_status.progress_percent = 100.0
        mock_status.errors = []

        with patch("src.cli.main.load_config") as mock_load:
            mock_load.return_value = {
                "enrichment": {"enabled": True, "batch_size": 50},
            }
            with patch("src.cli.main.create_storage_manager"):
                with patch("src.cli.main.create_llm_components") as mock_llm:
                    mock_llm.return_value = (None, MagicMock(), MagicMock())
                    with patch("src.cli.main.create_recommendation_engine"):
                        with patch(
                            "src.enrichment.manager.EnrichmentManager"
                        ) as mock_manager_cls:
                            mock_manager = MagicMock()
                            mock_manager.start_enrichment.return_value = True
                            mock_manager.get_status.return_value = mock_status
                            mock_manager_cls.return_value = mock_manager

                            result = cli_runner.invoke(cli, ["enrichment", "start"])

        assert result.exit_code == 0
        assert "completed" in result.output.lower()
        assert "Items processed: 10" in result.output

    def test_enrichment_start_with_type(self, cli_runner: CliRunner) -> None:
        """Test enrichment start with content type filter."""
        mock_status = MagicMock()
        mock_status.running = False
        mock_status.completed = True
        mock_status.cancelled = False
        mock_status.items_processed = 5
        mock_status.items_enriched = 5
        mock_status.items_not_found = 0
        mock_status.items_failed = 0
        mock_status.elapsed_seconds = 2.0
        mock_status.progress_percent = 100.0
        mock_status.errors = []

        with patch("src.cli.main.load_config") as mock_load:
            mock_load.return_value = {
                "enrichment": {"enabled": True, "batch_size": 50},
            }
            with patch("src.cli.main.create_storage_manager"):
                with patch("src.cli.main.create_llm_components") as mock_llm:
                    mock_llm.return_value = (None, MagicMock(), MagicMock())
                    with patch("src.cli.main.create_recommendation_engine"):
                        with patch(
                            "src.enrichment.manager.EnrichmentManager"
                        ) as mock_manager_cls:
                            mock_manager = MagicMock()
                            mock_manager.start_enrichment.return_value = True
                            mock_manager.get_status.return_value = mock_status
                            mock_manager_cls.return_value = mock_manager

                            result = cli_runner.invoke(
                                cli, ["enrichment", "start", "--type", "movie"]
                            )

        assert result.exit_code == 0
        assert "movie" in result.output.lower()

    def test_enrichment_already_running(self, cli_runner: CliRunner) -> None:
        """Test error when enrichment is already running."""
        with patch("src.cli.main.load_config") as mock_load:
            mock_load.return_value = {
                "enrichment": {"enabled": True, "batch_size": 50},
            }
            with patch("src.cli.main.create_storage_manager"):
                with patch("src.cli.main.create_llm_components") as mock_llm:
                    mock_llm.return_value = (None, MagicMock(), MagicMock())
                    with patch("src.cli.main.create_recommendation_engine"):
                        with patch(
                            "src.enrichment.manager.EnrichmentManager"
                        ) as mock_manager_cls:
                            mock_manager = MagicMock()
                            mock_manager.start_enrichment.return_value = False
                            mock_manager_cls.return_value = mock_manager

                            result = cli_runner.invoke(cli, ["enrichment", "start"])

        assert result.exit_code != 0
        assert "already running" in result.output.lower()


class TestEnrichmentStatus:
    """Tests for enrichment status command."""

    def test_enrichment_status_table(self, cli_runner: CliRunner) -> None:
        """Test status command with table output."""
        mock_stats = {
            "total": 100,
            "enriched": 80,
            "pending": 15,
            "not_found": 3,
            "failed": 2,
            "by_provider": {"tmdb": 50, "openlibrary": 30},
            "by_quality": {"high": 60, "medium": 20},
        }

        with patch("src.cli.main.load_config") as mock_load:
            mock_load.return_value = {}
            with patch("src.cli.main.create_storage_manager") as mock_storage_fn:
                mock_storage = MagicMock()
                mock_storage.get_enrichment_stats.return_value = mock_stats
                mock_storage_fn.return_value = mock_storage
                with patch("src.cli.main.create_llm_components") as mock_llm:
                    mock_llm.return_value = (None, MagicMock(), MagicMock())
                    with patch("src.cli.main.create_recommendation_engine"):
                        result = cli_runner.invoke(cli, ["enrichment", "status"])

        assert result.exit_code == 0
        assert "Total items: 100" in result.output
        assert "Enriched: 80" in result.output
        assert "Pending: 15" in result.output
        assert "tmdb: 50" in result.output

    def test_enrichment_status_json(self, cli_runner: CliRunner) -> None:
        """Test status command with JSON output."""
        mock_stats = {
            "total": 100,
            "enriched": 80,
            "pending": 15,
            "not_found": 3,
            "failed": 2,
            "by_provider": {"tmdb": 50},
            "by_quality": {"high": 60},
        }

        with patch("src.cli.main.load_config") as mock_load:
            mock_load.return_value = {}
            with patch("src.cli.main.create_storage_manager") as mock_storage_fn:
                mock_storage = MagicMock()
                mock_storage.get_enrichment_stats.return_value = mock_stats
                mock_storage_fn.return_value = mock_storage
                with patch("src.cli.main.create_llm_components") as mock_llm:
                    mock_llm.return_value = (None, MagicMock(), MagicMock())
                    with patch("src.cli.main.create_recommendation_engine"):
                        result = cli_runner.invoke(
                            cli, ["enrichment", "status", "--format", "json"]
                        )

        assert result.exit_code == 0
        assert '"total": 100' in result.output
        assert '"enriched": 80' in result.output


class TestEnrichmentReset:
    """Tests for enrichment reset command."""

    def test_enrichment_reset_all(self, cli_runner: CliRunner) -> None:
        """Test reset command for all items."""
        with patch("src.cli.main.load_config") as mock_load:
            mock_load.return_value = {}
            with patch("src.cli.main.create_storage_manager") as mock_storage_fn:
                mock_storage = MagicMock()
                mock_storage.reset_enrichment_status.return_value = 50
                mock_storage_fn.return_value = mock_storage
                with patch("src.cli.main.create_llm_components") as mock_llm:
                    mock_llm.return_value = (None, MagicMock(), MagicMock())
                    with patch("src.cli.main.create_recommendation_engine"):
                        result = cli_runner.invoke(
                            cli, ["enrichment", "reset", "--yes"]
                        )

        assert result.exit_code == 0
        assert "Reset enrichment status for 50 item(s)" in result.output
        mock_storage.reset_enrichment_status.assert_called_once_with(
            provider=None, content_type=None, user_id=1
        )

    def test_enrichment_reset_by_provider(self, cli_runner: CliRunner) -> None:
        """Test reset command filtered by provider."""
        with patch("src.cli.main.load_config") as mock_load:
            mock_load.return_value = {}
            with patch("src.cli.main.create_storage_manager") as mock_storage_fn:
                mock_storage = MagicMock()
                mock_storage.reset_enrichment_status.return_value = 20
                mock_storage_fn.return_value = mock_storage
                with patch("src.cli.main.create_llm_components") as mock_llm:
                    mock_llm.return_value = (None, MagicMock(), MagicMock())
                    with patch("src.cli.main.create_recommendation_engine"):
                        result = cli_runner.invoke(
                            cli,
                            ["enrichment", "reset", "--provider", "tmdb", "--yes"],
                        )

        assert result.exit_code == 0
        assert "Reset enrichment status for 20 item(s)" in result.output

    def test_enrichment_reset_by_type(self, cli_runner: CliRunner) -> None:
        """Test reset command filtered by content type."""
        with patch("src.cli.main.load_config") as mock_load:
            mock_load.return_value = {}
            with patch("src.cli.main.create_storage_manager") as mock_storage_fn:
                mock_storage = MagicMock()
                mock_storage.reset_enrichment_status.return_value = 15
                mock_storage_fn.return_value = mock_storage
                with patch("src.cli.main.create_llm_components") as mock_llm:
                    mock_llm.return_value = (None, MagicMock(), MagicMock())
                    with patch("src.cli.main.create_recommendation_engine"):
                        result = cli_runner.invoke(
                            cli,
                            ["enrichment", "reset", "--type", "book", "--yes"],
                        )

        assert result.exit_code == 0
        assert "Reset enrichment status for 15 item(s)" in result.output

    def test_enrichment_reset_requires_confirmation(
        self, cli_runner: CliRunner
    ) -> None:
        """Test that reset requires confirmation without --yes."""
        with patch("src.cli.main.load_config") as mock_load:
            mock_load.return_value = {}
            with patch("src.cli.main.create_storage_manager") as mock_storage_fn:
                mock_storage = MagicMock()
                mock_storage_fn.return_value = mock_storage
                with patch("src.cli.main.create_llm_components") as mock_llm:
                    mock_llm.return_value = (None, MagicMock(), MagicMock())
                    with patch("src.cli.main.create_recommendation_engine"):
                        # Input 'n' to decline
                        result = cli_runner.invoke(
                            cli, ["enrichment", "reset"], input="n\n"
                        )

        assert result.exit_code == 0
        assert "Aborted" in result.output
        mock_storage.reset_enrichment_status.assert_not_called()
