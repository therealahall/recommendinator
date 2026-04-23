"""Tests for CLI enrichment commands."""

import json
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from src.enrichment.manager import EnrichmentJobStatus, EnrichmentManager
from src.models.content import ContentType
from src.storage.manager import StorageManager

from .conftest import _invoke_with_mocks


def _invoke_with_enrichment_manager(
    cli_runner: CliRunner,
    args: list[str],
    mock_storage: MagicMock,
    mock_manager: MagicMock,
    config: dict | None = None,
) -> object:
    """Invoke CLI with the standard mocks plus a mocked EnrichmentManager."""
    with patch("src.cli.commands.EnrichmentManager", return_value=mock_manager):
        return _invoke_with_mocks(cli_runner, args, mock_storage, config=config)


def _make_status(
    completed: bool = True,
    items_processed: int = 10,
    items_enriched: int = 8,
) -> MagicMock:
    """Build an EnrichmentJobStatus mock with sensible defaults."""
    mock_status = MagicMock(spec=EnrichmentJobStatus)
    mock_status.running = False
    mock_status.completed = completed
    mock_status.cancelled = False
    mock_status.items_processed = items_processed
    mock_status.items_enriched = items_enriched
    mock_status.items_not_found = max(0, items_processed - items_enriched)
    mock_status.items_failed = 0
    mock_status.elapsed_seconds = 5.0
    mock_status.progress_percent = 100.0
    mock_status.errors = []
    return mock_status


class TestEnrichmentStart:
    """Tests for enrichment start command."""

    def test_enrichment_disabled_error(self, cli_runner: CliRunner) -> None:
        """Test error when enrichment is disabled."""
        mock_storage = MagicMock(spec=StorageManager)
        result = _invoke_with_mocks(
            cli_runner,
            ["enrichment", "start"],
            mock_storage,
            config={"enrichment": {"enabled": False}},
        )

        assert result.exit_code != 0
        assert "disabled" in result.output.lower()

    def test_enrichment_start_success(self, cli_runner: CliRunner) -> None:
        """Test successful enrichment start forwards correct args to the manager."""
        mock_storage = MagicMock(spec=StorageManager)
        mock_manager = MagicMock(spec=EnrichmentManager)
        mock_manager.start_enrichment.return_value = True
        mock_manager.get_status.return_value = _make_status()

        result = _invoke_with_enrichment_manager(
            cli_runner,
            ["enrichment", "start"],
            mock_storage,
            mock_manager,
            config={"enrichment": {"enabled": True, "batch_size": 50}},
        )

        assert result.exit_code == 0
        assert "completed" in result.output.lower()
        assert "Items processed: 10" in result.output
        mock_manager.start_enrichment.assert_called_once_with(
            content_type=None, user_id=1, include_not_found=False
        )

    def test_enrichment_start_with_type(self, cli_runner: CliRunner) -> None:
        """Test enrichment start with content type filter forwards the enum."""
        mock_storage = MagicMock(spec=StorageManager)
        mock_manager = MagicMock(spec=EnrichmentManager)
        mock_manager.start_enrichment.return_value = True
        mock_manager.get_status.return_value = _make_status(
            items_processed=5, items_enriched=5
        )

        result = _invoke_with_enrichment_manager(
            cli_runner,
            ["enrichment", "start", "--type", "movie"],
            mock_storage,
            mock_manager,
            config={"enrichment": {"enabled": True, "batch_size": 50}},
        )

        assert result.exit_code == 0
        assert "movie" in result.output.lower()
        mock_manager.start_enrichment.assert_called_once_with(
            content_type=ContentType.MOVIE, user_id=1, include_not_found=False
        )

    def test_enrichment_start_retry_not_found(self, cli_runner: CliRunner) -> None:
        """--retry-not-found forwards include_not_found=True to the manager.

        Bug: earlier revisions silently dropped the flag because the CLI did
        not forward it through to EnrichmentManager.start_enrichment. The web
        API's /api/enrichment/start accepts retry_not_found, so parity
        requires the CLI to do the same.
        """
        mock_storage = MagicMock(spec=StorageManager)
        mock_manager = MagicMock(spec=EnrichmentManager)
        mock_manager.start_enrichment.return_value = True
        mock_manager.get_status.return_value = _make_status()

        result = _invoke_with_enrichment_manager(
            cli_runner,
            ["enrichment", "start", "--retry-not-found"],
            mock_storage,
            mock_manager,
            config={"enrichment": {"enabled": True, "batch_size": 50}},
        )

        assert result.exit_code == 0
        mock_manager.start_enrichment.assert_called_once_with(
            content_type=None, user_id=1, include_not_found=True
        )

    def test_enrichment_already_running(self, cli_runner: CliRunner) -> None:
        """Test error when enrichment is already running."""
        mock_storage = MagicMock(spec=StorageManager)
        mock_manager = MagicMock(spec=EnrichmentManager)
        mock_manager.start_enrichment.return_value = False

        result = _invoke_with_enrichment_manager(
            cli_runner,
            ["enrichment", "start"],
            mock_storage,
            mock_manager,
            config={"enrichment": {"enabled": True, "batch_size": 50}},
        )

        assert result.exit_code != 0
        assert "already running" in result.output.lower()


class TestEnrichmentStatus:
    """Tests for enrichment status command."""

    def test_enrichment_status_table(self, cli_runner: CliRunner) -> None:
        """Test status command with table output."""
        mock_storage = MagicMock(spec=StorageManager)
        mock_storage.get_enrichment_stats.return_value = {
            "total": 100,
            "enriched": 80,
            "pending": 15,
            "not_found": 3,
            "failed": 2,
            "by_provider": {"tmdb": 50, "openlibrary": 30},
            "by_quality": {"high": 60, "medium": 20},
        }

        result = _invoke_with_mocks(
            cli_runner,
            ["enrichment", "status"],
            mock_storage,
        )

        assert result.exit_code == 0
        assert "Total items: 100" in result.output
        assert "Enriched: 80" in result.output
        assert "Pending: 15" in result.output
        assert "tmdb: 50" in result.output
        mock_storage.get_enrichment_stats.assert_called_once_with(user_id=1)

    def test_enrichment_status_json(self, cli_runner: CliRunner) -> None:
        """Test status JSON output matches web API EnrichmentStatsResponse shape."""
        mock_storage = MagicMock(spec=StorageManager)
        mock_storage.get_enrichment_stats.return_value = {
            "total": 100,
            "enriched": 80,
            "pending": 15,
            "not_found": 3,
            "failed": 2,
            "by_provider": {"tmdb": 50},
            "by_quality": {"high": 60},
        }

        result = _invoke_with_mocks(
            cli_runner,
            ["enrichment", "status", "--format", "json"],
            mock_storage,
        )

        assert result.exit_code == 0
        parsed = json.loads(result.output)
        # Field set matches web API EnrichmentStatsResponse (includes `enabled`)
        assert set(parsed.keys()) >= {
            "enabled",
            "total",
            "enriched",
            "pending",
            "not_found",
            "failed",
            "by_provider",
            "by_quality",
        }
        assert parsed["total"] == 100
        assert parsed["enriched"] == 80
        assert parsed["enabled"] is False


class TestEnrichmentReset:
    """Tests for enrichment reset command."""

    def test_enrichment_reset_all(self, cli_runner: CliRunner) -> None:
        """Test reset command for all items forwards correct filters."""
        mock_storage = MagicMock(spec=StorageManager)
        mock_storage.reset_enrichment_status.return_value = 50

        result = _invoke_with_mocks(
            cli_runner,
            ["enrichment", "reset", "--yes"],
            mock_storage,
        )

        assert result.exit_code == 0
        assert "Reset enrichment status for 50 item(s)" in result.output
        mock_storage.reset_enrichment_status.assert_called_once_with(
            provider=None, content_type=None, user_id=1
        )

    def test_enrichment_reset_by_provider(self, cli_runner: CliRunner) -> None:
        """Test reset filtered by provider forwards provider=tmdb to storage."""
        mock_storage = MagicMock(spec=StorageManager)
        mock_storage.reset_enrichment_status.return_value = 20

        result = _invoke_with_mocks(
            cli_runner,
            ["enrichment", "reset", "--provider", "tmdb", "--yes"],
            mock_storage,
        )

        assert result.exit_code == 0
        assert "Reset enrichment status for 20 item(s)" in result.output
        mock_storage.reset_enrichment_status.assert_called_once_with(
            provider="tmdb", content_type=None, user_id=1
        )

    def test_enrichment_reset_by_type(self, cli_runner: CliRunner) -> None:
        """Test reset filtered by content type forwards content_type=book."""
        mock_storage = MagicMock(spec=StorageManager)
        mock_storage.reset_enrichment_status.return_value = 15

        result = _invoke_with_mocks(
            cli_runner,
            ["enrichment", "reset", "--type", "book", "--yes"],
            mock_storage,
        )

        assert result.exit_code == 0
        assert "Reset enrichment status for 15 item(s)" in result.output
        mock_storage.reset_enrichment_status.assert_called_once_with(
            provider=None, content_type=ContentType.BOOK, user_id=1
        )

    def test_enrichment_reset_requires_confirmation(
        self, cli_runner: CliRunner
    ) -> None:
        """Test that reset requires confirmation without --yes."""
        mock_storage = MagicMock(spec=StorageManager)

        result = _invoke_with_mocks(
            cli_runner,
            ["enrichment", "reset"],
            mock_storage,
            input_text="n\n",
        )

        assert result.exit_code == 0
        assert "Aborted" in result.output
        mock_storage.reset_enrichment_status.assert_not_called()
