import json
from collections.abc import Callable
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from src.enrichment.manager import (
    EnrichmentJobStatus,
    EnrichmentManager,
    EnrichmentStart,
    PinRefused,
)
from src.models.content import ConsumptionStatus, ContentItem, ContentType
from src.storage.manager import StorageManager
from src.utils.matching import Candidate
from tests.factories import make_storage_mock

from .conftest import _invoke_with_mocks


def _invoke_with_enrichment_manager(
    cli_runner: CliRunner,
    args: list[str],
    mock_storage: MagicMock,
    mock_manager: MagicMock,
    config: dict | None = None,
) -> object:
    with patch(
        "src.cli.commands._enrichment.EnrichmentManager", return_value=mock_manager
    ):
        return _invoke_with_mocks(cli_runner, args, mock_storage, config=config)


def _make_status(
    completed: bool = True,
    items_processed: int = 10,
    items_enriched: int = 8,
) -> MagicMock:
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


def _idle_manager() -> MagicMock:
    """A spec'd mock answers ``running`` with a truthy Mock, which spins the
    wait loop forever; the real status answers with a bool.
    """
    manager = MagicMock(spec=EnrichmentManager)
    manager.get_status.return_value = EnrichmentJobStatus()
    return manager


def _claims(storage: StorageManager) -> Callable[..., EnrichmentStart]:
    def start(**_: object) -> EnrichmentStart:
        storage.enrichment_jobs.claim(None)
        return EnrichmentStart.STARTED

    return start


class TestEnrichmentStart:
    def test_disabled_enrichment_names_the_surface_that_turns_it_on(
        self, cli_runner: CliRunner
    ) -> None:
        mock_storage = make_storage_mock()
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
        mock_storage = make_storage_mock()
        mock_manager = MagicMock(spec=EnrichmentManager)
        mock_manager.start_enrichment.return_value = EnrichmentStart.STARTED
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
        mock_storage = make_storage_mock()
        mock_manager = MagicMock(spec=EnrichmentManager)
        mock_manager.start_enrichment.return_value = EnrichmentStart.STARTED
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

    def test_start_does_not_call_a_run_that_gave_up_completed(
        self, cli_runner: CliRunner
    ) -> None:
        mock_storage = make_storage_mock()
        mock_manager = MagicMock(spec=EnrichmentManager)
        mock_manager.start_enrichment.return_value = EnrichmentStart.STARTED
        status = _make_status(completed=False, items_processed=5, items_enriched=0)
        status.errors = ["tmdb: abandoned for this run after 5 rejections (HTTP 401)"]
        mock_manager.get_status.return_value = status

        result = _invoke_with_enrichment_manager(
            cli_runner,
            ["enrichment", "start", "--type", "movie"],
            mock_storage,
            mock_manager,
            config={"enrichment": {"enabled": True, "batch_size": 50}},
        )

        assert result.exit_code == 0, result.output
        assert "completed" not in result.output.lower(), result.output
        assert "tmdb" in result.output

    def test_enrichment_already_running(self, cli_runner: CliRunner) -> None:
        mock_storage = make_storage_mock()
        mock_manager = MagicMock(spec=EnrichmentManager)
        mock_manager.start_enrichment.return_value = EnrichmentStart.ALREADY_RUNNING

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

    def test_job_prints_why_a_run_stopped_and_not_only_its_tallies(
        self, cli_runner: CliRunner, tmp_path: Path
    ) -> None:
        reason = "tmdb: abandoned for this run"
        storage = self._running(tmp_path)
        storage.enrichment_jobs.finish(completed=True, cancelled=False, errors=[reason])
        result = _invoke_with_mocks(cli_runner, ["enrichment", "job"], storage)
        assert reason in result.output

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
        storage = StorageManager(sqlite_path=tmp_path / "job.db")
        mock_manager = MagicMock(spec=EnrichmentManager)
        mock_manager.start_enrichment.side_effect = _claims(storage)
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

    def test_a_second_ctrl_c_during_the_wait_still_releases_the_claim(
        self, cli_runner: CliRunner, tmp_path: Path
    ) -> None:
        storage = StorageManager(sqlite_path=tmp_path / "job.db")
        mock_manager = MagicMock(spec=EnrichmentManager)
        mock_manager.start_enrichment.side_effect = _claims(storage)
        mock_manager.get_status.side_effect = KeyboardInterrupt
        mock_manager._wait_for_completion.side_effect = KeyboardInterrupt

        result = _invoke_with_enrichment_manager(
            cli_runner,
            ["enrichment", "start"],
            storage,
            mock_manager,
            config={"enrichment": {"enabled": True, "batch_size": 50}},
        )

        assert result.exit_code == 0, result.output
        assert storage.enrichment_jobs.claim(None) is True

    def test_the_interrupt_keeps_the_failures_the_run_had_already_published(
        self, cli_runner: CliRunner, tmp_path: Path
    ) -> None:
        storage = StorageManager(sqlite_path=tmp_path / "job.db")
        mock_manager = MagicMock(spec=EnrichmentManager)

        def claim_and_report(**_: object) -> EnrichmentStart:
            storage.enrichment_jobs.claim(None)
            storage.enrichment_jobs.heartbeat(
                items_processed=1,
                items_enriched=0,
                items_failed=1,
                items_not_found=0,
                total_items=99,
                current_item="Dune",
                errors=["tmdb: HTTP 401"],
            )
            return EnrichmentStart.STARTED

        mock_manager.start_enrichment.side_effect = claim_and_report
        mock_manager.get_status.side_effect = KeyboardInterrupt
        mock_manager._wait_for_completion.return_value = False

        _invoke_with_enrichment_manager(
            cli_runner,
            ["enrichment", "start"],
            storage,
            mock_manager,
            config={"enrichment": {"enabled": True, "batch_size": 50}},
        )

        assert storage.enrichment_jobs.read().errors == [
            "tmdb: HTTP 401",
            "Interrupted.",
        ]

    def test_a_run_that_released_itself_is_left_alone_by_the_interrupt(
        self, cli_runner: CliRunner, tmp_path: Path
    ) -> None:
        storage = StorageManager(sqlite_path=tmp_path / "job.db")
        mock_manager = MagicMock(spec=EnrichmentManager)

        def claim_then_finish(**_: object) -> EnrichmentStart:
            storage.enrichment_jobs.claim(None)
            storage.enrichment_jobs.finish(completed=True, cancelled=False, errors=[])
            return EnrichmentStart.STARTED

        mock_manager.start_enrichment.side_effect = claim_then_finish
        mock_manager.get_status.side_effect = KeyboardInterrupt
        mock_manager._wait_for_completion.return_value = True

        _invoke_with_enrichment_manager(
            cli_runner,
            ["enrichment", "start"],
            storage,
            mock_manager,
            config={"enrichment": {"enabled": True, "batch_size": 50}},
        )

        record = storage.enrichment_jobs.read()
        assert record.completed is True
        assert record.cancelled is False

    def test_stop_with_nothing_running_says_so_rather_than_claiming_success(
        self, cli_runner: CliRunner, tmp_path: Path
    ) -> None:
        storage = StorageManager(sqlite_path=tmp_path / "job.db")

        result = _invoke_with_mocks(cli_runner, ["enrichment", "stop"], storage)

        assert result.exit_code != 0
        assert "No enrichment job is running." in result.output


class TestEnrichmentStatus:
    def test_enrichment_status_json(self, cli_runner: CliRunner) -> None:
        mock_storage = make_storage_mock()
        stats = {
            "total": 100,
            "resettable": 88,
            "enriched": 80,
            "pending": 15,
            "not_found": 3,
            "failed": 2,
            "by_provider": {"tmdb": 50},
            "by_quality": {"high": 60},
        }
        mock_storage.enrichment.stats.return_value = stats

        result = _invoke_with_mocks(
            cli_runner, ["enrichment", "status", "--format", "json"], mock_storage
        )

        assert result.exit_code == 0
        assert json.loads(result.output) == {"enabled": False, **stats}


class TestEnrichmentPinning:
    @staticmethod
    def _storage() -> MagicMock:
        storage = make_storage_mock()
        storage.get_content_item.return_value = ContentItem(
            db_id=7,
            title="Prey",
            content_type=ContentType.VIDEO_GAME,
            status=ConsumptionStatus.UNREAD,
            metadata={"enrichment_ids": {"rawg": "3328"}},
        )
        return storage

    def test_candidates_emit_the_web_responses_key_set(
        self, cli_runner: CliRunner
    ) -> None:
        manager = MagicMock(spec=EnrichmentManager)
        manager.candidates.return_value = [
            ("rawg", Candidate(record_id="41494", title="Prey", year=2017))
        ]

        result = _invoke_with_enrichment_manager(
            cli_runner,
            [
                "enrichment",
                "candidates",
                "--id",
                "7",
                "--query",
                "Prey 2017",
                "--format",
                "json",
            ],
            self._storage(),
            manager,
        )

        assert result.exit_code == 0
        assert json.loads(result.output) == {
            "item_id": 7,
            "candidates": [
                {
                    "provider": "rawg",
                    "record_id": "41494",
                    "title": "Prey",
                    "year": 2017,
                    "creator": None,
                    "cover_url": None,
                }
            ],
            "pinned": {"rawg": "3328"},
        }
        assert manager.candidates.call_args.args[1] == "Prey 2017"

    @pytest.mark.parametrize(
        ("flags", "stored", "record"),
        [
            (["--record", "41494"], {"rawg": "41494"}, "41494"),
            (["--clear"], {}, None),
        ],
        ids=["binds", "clears"],
    )
    def test_pin_binds_the_item_to_the_record_named_or_to_none(
        self,
        cli_runner: CliRunner,
        flags: list[str],
        stored: dict[str, str],
        record: str | None,
    ) -> None:
        manager = _idle_manager()
        manager.pin.return_value = (
            stored,
            EnrichmentStart.STARTED if record else None,
        )

        result = _invoke_with_enrichment_manager(
            cli_runner,
            ["enrichment", "pin", "--id", "7", "--provider", "rawg", *flags]
            + ["--format", "json"],
            self._storage(),
            manager,
        )

        assert result.exit_code == 0
        assert json.loads(result.stdout)["pinned"] == stored
        assert manager.pin.call_args.args[0] == 7
        assert manager.pin.call_args.args[2:] == ("rawg", record)

    @pytest.mark.parametrize(
        ("started", "clause"),
        [
            (EnrichmentStart.STARTED, "Enriching it now."),
            (
                EnrichmentStart.ALREADY_RUNNING,
                "Queued for the next enrichment run.",
            ),
            (
                EnrichmentStart.UNAVAILABLE,
                "Queued: enrichment is off, or no enabled provider handles this type.",
            ),
        ],
        ids=["claimed", "claim-lost", "nobody-to-ask"],
    )
    def test_a_pin_says_whether_the_run_it_asked_for_started(
        self, cli_runner: CliRunner, started: EnrichmentStart, clause: str
    ) -> None:
        manager = _idle_manager()
        manager.pin.return_value = ({"rawg": "41494"}, started)

        result = _invoke_with_enrichment_manager(
            cli_runner,
            ["enrichment", "pin", "--id", "7", "--provider", "rawg"]
            + ["--record", "41494", "--format", "json"],
            self._storage(),
            manager,
        )

        assert result.exit_code == 0
        # Waiting out the run must leave stdout the JSON document alone.
        assert json.loads(result.stdout)["message"] == (
            f"Item 7 now enriches from rawg record 41494. {clause}"
        )

    def test_a_pin_naming_neither_a_record_nor_a_clear_is_refused(
        self, cli_runner: CliRunner
    ) -> None:
        manager = MagicMock(spec=EnrichmentManager)

        result = _invoke_with_enrichment_manager(
            cli_runner,
            ["enrichment", "pin", "--id", "7", "--provider", "rawg"],
            self._storage(),
            manager,
        )

        assert result.exit_code != 0
        assert "--record or --clear" in result.output
        manager.pin.assert_not_called()

    def test_a_pin_the_manager_refuses_reports_what_is_valid(
        self, cli_runner: CliRunner
    ) -> None:
        manager = MagicMock(spec=EnrichmentManager)
        manager.pin.side_effect = PinRefused("pin one of rawg, tmdb.")

        result = _invoke_with_enrichment_manager(
            cli_runner,
            ["enrichment", "pin", "--id", "7", "--provider", "rawgg", "--record", "1"],
            self._storage(),
            manager,
        )

        assert result.exit_code != 0
        assert "pin one of rawg, tmdb." in result.output


class TestEnrichmentReset:
    def test_reset_prompt_states_the_count_each_filter_leaves(
        self, cli_runner: CliRunner
    ) -> None:
        mock_storage = make_storage_mock()
        mock_storage.enrichment.stats.return_value = {
            "resettable": 1488,
            "by_provider": {"tmdb": 12},
        }

        def prompt(*args: str) -> str:
            result = _invoke_with_mocks(
                cli_runner,
                ["enrichment", "reset", *args],
                mock_storage,
                input_text="n\n",
            )
            assert result.exit_code == 0
            return result.output

        unfiltered = prompt()
        assert "1488 item(s)" in unfiltered
        assert "Aborted" in unfiltered
        assert "12 item(s)" in prompt("--provider", "tmdb")
        assert "1488" not in prompt("--type", "movie")
        mock_storage.enrichment.reset.assert_not_called()

    def test_enrichment_reset_all(self, cli_runner: CliRunner) -> None:
        mock_storage = make_storage_mock()
        mock_storage.enrichment.reset.return_value = 50

        result = _invoke_with_mocks(
            cli_runner, ["enrichment", "reset", "--yes"], mock_storage
        )

        assert result.exit_code == 0
        assert "Reset enrichment status for 50 item(s)" in result.output
        mock_storage.enrichment.reset.assert_called_once_with(
            provider=None, content_type=None, user_id=1, content_item_id=None
        )

    def test_enrichment_reset_re_queues_the_one_item_named_and_enriches_it(
        self, cli_runner: CliRunner
    ) -> None:
        mock_storage = make_storage_mock()
        mock_storage.enrichment.reset.return_value = 1
        manager = _idle_manager()
        manager.start_enrichment.return_value = EnrichmentStart.STARTED

        result = _invoke_with_enrichment_manager(
            cli_runner,
            ["enrichment", "reset", "--id", "42", "--yes"],
            mock_storage,
            manager,
        )

        assert result.exit_code == 0, result.output
        assert (
            "Reset enrichment status for 1 item(s). Enriching it now." in result.output
        )
        assert mock_storage.enrichment.reset.call_args.kwargs["content_item_id"] == 42
        assert manager.start_enrichment.call_args.kwargs["content_item_id"] == 42

    def test_enrichment_reset_refuses_an_id_beside_a_filter(
        self, cli_runner: CliRunner
    ) -> None:
        mock_storage = make_storage_mock()

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
        mock_storage = make_storage_mock()
        mock_storage.get_content_item.return_value = None

        result = _invoke_with_mocks(
            cli_runner, ["enrichment", "reset", "--id", "999", "--yes"], mock_storage
        )

        assert result.exit_code != 0
        assert "Item 999 not found" in result.output
        mock_storage.enrichment.reset.assert_not_called()
