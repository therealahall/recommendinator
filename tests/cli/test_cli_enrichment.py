"""Tests for CLI enrichment commands."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from src.enrichment.manager import EnrichmentJobStatus, EnrichmentManager
from src.models.content import ConsumptionStatus, ContentItem, ContentType
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
    with patch(
        "src.cli.commands._enrichment.EnrichmentManager", return_value=mock_manager
    ):
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

    def test_disabled_enrichment_names_the_surface_that_turns_it_on(
        self, cli_runner: CliRunner
    ) -> None:
        """It used to send the user to a config.yaml key the app no longer reads."""
        mock_storage = MagicMock(spec=StorageManager)
        result = _invoke_with_mocks(
            cli_runner,
            ["enrichment", "start"],
            mock_storage,
            config={"enrichment": {"enabled": False}},
        )

        assert result.exit_code != 0
        assert "config.yaml" not in result.output
        assert "Data tab" in result.output
        assert "settings set enrichment.enabled true" in result.output

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


class TestEnrichmentJobControl:
    """The CLI could not see or stop a job the web UI started."""

    @staticmethod
    def _running(tmp_path: Path) -> StorageManager:
        storage = StorageManager(sqlite_path=tmp_path / "job.db")
        storage.enrichment_jobs.claim("movie")
        storage.enrichment_jobs.heartbeat(
            items_processed=4,
            items_enriched=3,
            items_failed=0,
            items_not_found=1,
            total_items=8,
            current_item="Arrival",
            errors=[],
        )
        return storage

    def test_job_reports_a_run_this_invocation_did_not_start(
        self, cli_runner: CliRunner, tmp_path: Path
    ) -> None:
        result = _invoke_with_mocks(
            cli_runner, ["enrichment", "job"], self._running(tmp_path)
        )

        assert result.exit_code == 0, result.output
        assert "running" in result.output
        assert "Arrival" in result.output
        assert "4/8" in result.output

    def test_job_json_carries_the_web_response_field_set(
        self, cli_runner: CliRunner, tmp_path: Path
    ) -> None:
        """Parity: the key set must match EnrichmentJobStatusResponse exactly."""
        result = _invoke_with_mocks(
            cli_runner,
            ["enrichment", "job", "--format", "json"],
            self._running(tmp_path),
        )

        assert result.exit_code == 0, result.output
        assert set(json.loads(result.output)) == {
            "running",
            "completed",
            "cancelled",
            "items_processed",
            "items_enriched",
            "items_failed",
            "items_not_found",
            "total_items",
            "current_item",
            "content_type",
            "errors",
            "elapsed_seconds",
            "progress_percent",
        }

    def test_job_says_so_when_nothing_has_ever_run(
        self, cli_runner: CliRunner, tmp_path: Path
    ) -> None:
        storage = StorageManager(sqlite_path=tmp_path / "job.db")

        result = _invoke_with_mocks(cli_runner, ["enrichment", "job"], storage)

        assert result.exit_code == 0
        assert "No enrichment job has run." in result.output

    def test_stop_asks_the_running_job_to_end(
        self, cli_runner: CliRunner, tmp_path: Path
    ) -> None:
        storage = self._running(tmp_path)

        result = _invoke_with_mocks(cli_runner, ["enrichment", "stop"], storage)

        assert result.exit_code == 0, result.output
        assert storage.enrichment_jobs.stop_requested() is True

    def test_ctrl_c_releases_the_claim_rather_than_stranding_it(
        self, cli_runner: CliRunner, tmp_path: Path
    ) -> None:
        """The worker is a daemon thread, so the process can exit before it sees
        the stop; the claim would then block both Start doors until it staled."""
        storage = StorageManager(sqlite_path=tmp_path / "job.db")
        mock_manager = MagicMock(spec=EnrichmentManager)
        mock_manager.start_enrichment.side_effect = (
            lambda **_: storage.enrichment_jobs.claim(None)
        )
        mock_manager.get_status.side_effect = KeyboardInterrupt
        mock_manager._wait_for_completion.return_value = False

        result = _invoke_with_enrichment_manager(
            cli_runner,
            ["enrichment", "start"],
            storage,
            mock_manager,
            config={"enrichment": {"enabled": True, "batch_size": 50}},
        )

        assert result.exit_code == 0, result.output
        assert storage.enrichment_jobs.read().running is False
        assert storage.enrichment_jobs.claim(None) is True

    def test_stop_with_nothing_running_says_so_rather_than_claiming_success(
        self, cli_runner: CliRunner, tmp_path: Path
    ) -> None:
        storage = StorageManager(sqlite_path=tmp_path / "job.db")

        result = _invoke_with_mocks(cli_runner, ["enrichment", "stop"], storage)

        assert result.exit_code != 0
        assert "No enrichment job is running." in result.output


class TestEnrichmentStatus:
    """Tests for enrichment status command."""

    def test_enrichment_status_json(self, cli_runner: CliRunner) -> None:
        """Test status JSON output matches web API EnrichmentStatsResponse shape."""
        mock_storage = MagicMock(spec=StorageManager)
        mock_storage.enrichment.stats.return_value = {
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
        mock_storage.enrichment.reset.return_value = 50

        result = _invoke_with_mocks(
            cli_runner,
            ["enrichment", "reset", "--yes"],
            mock_storage,
        )

        assert result.exit_code == 0
        assert "Reset enrichment status for 50 item(s)" in result.output
        mock_storage.enrichment.reset.assert_called_once_with(
            provider=None, content_type=None, user_id=1, content_item_id=None
        )

    def test_enrichment_reset_hands_one_item_back_to_automatic_enrichment(
        self, cli_runner: CliRunner, tmp_path: Path
    ) -> None:
        """A dialog edit stamps the item manual, and nothing undid that."""
        storage = StorageManager(sqlite_path=tmp_path / "reset.db")
        db_id = storage.save_content_item(
            ContentItem(
                id="movie-1",
                title="Arrival",
                content_type=ContentType.MOVIE,
                status=ConsumptionStatus.UNREAD,
            ),
            user_id=1,
        )
        storage.update_item_from_ui(db_id=db_id, genres=["Sci-Fi"], user_id=1)
        edited = storage.get_content_item(db_id, user_id=1)
        assert edited is not None and edited.manually_enriched is True

        result = _invoke_with_mocks(
            cli_runner,
            ["enrichment", "reset", "--id", str(db_id), "--yes"],
            storage,
        )

        assert result.exit_code == 0, result.output
        assert "Reset enrichment status for 1 item(s)" in result.output
        restored = storage.get_content_item(db_id, user_id=1)
        assert restored is not None
        assert restored.enriched is False
        assert restored.manually_enriched is False
        assert restored.metadata.get("genres") == ["Sci-Fi"]
        assert [
            item.db_id
            for item in storage.get_content_items(user_id=1, enrichment="not_enriched")
        ] == [db_id]

    def test_enrichment_reset_refuses_an_id_beside_a_filter(
        self, cli_runner: CliRunner
    ) -> None:
        mock_storage = MagicMock(spec=StorageManager)

        result = _invoke_with_mocks(
            cli_runner,
            ["enrichment", "reset", "--id", "7", "--provider", "tmdb", "--yes"],
            mock_storage,
        )

        assert result.exit_code != 0
        assert "--id cannot be combined with --provider or --type." in result.output
        mock_storage.enrichment.reset.assert_not_called()

    def test_enrichment_reset_names_an_id_that_is_not_there(
        self, cli_runner: CliRunner
    ) -> None:
        mock_storage = MagicMock(spec=StorageManager)
        mock_storage.get_content_item.return_value = None

        result = _invoke_with_mocks(
            cli_runner, ["enrichment", "reset", "--id", "999", "--yes"], mock_storage
        )

        assert result.exit_code != 0
        assert "Item 999 not found." in result.output
        mock_storage.enrichment.reset.assert_not_called()

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
        mock_storage.enrichment.reset.assert_not_called()
