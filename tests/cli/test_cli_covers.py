import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner

from src.covers.fetch import CoverUnavailable
from src.storage.cover_jobs import CoverBackfillRecord
from tests.factories import make_item, make_storage_mock

from .conftest import _invoke_with_mocks


def _storage_reading(record: CoverBackfillRecord) -> object:
    storage = make_storage_mock()
    storage.cover_jobs.read.return_value = record
    return storage


def test_backfill_reports_the_run_with_the_web_actions_keys(
    cli_runner: CliRunner,
) -> None:
    finished = CoverBackfillRecord(
        completed=True, total=4, processed=4, cached=3, cleared=1
    )

    with patch(
        "src.cli.commands._covers.start_backfill", return_value=finished
    ) as mock_start:
        result = _invoke_with_mocks(
            cli_runner,
            ["covers", "backfill", "--format", "json"],
            _storage_reading(finished),
        )

    assert result.exit_code == 0
    assert json.loads(result.output) == finished.payload()
    assert mock_start.call_args.kwargs["user_id"] == 1


def test_backfill_names_the_remedy_for_the_items_it_cannot_fetch(
    cli_runner: CliRunner,
) -> None:
    finished = CoverBackfillRecord(completed=True, without_cover=7)

    with patch("src.cli.commands._covers.start_backfill", return_value=finished):
        result = _invoke_with_mocks(
            cli_runner, ["covers", "backfill"], _storage_reading(finished)
        )

    assert result.exit_code == 0
    assert "Items with no cover art to fetch: 7" in result.output
    assert "'enrichment reset' then 'enrichment start'" in result.output


def test_backfill_refuses_to_start_beside_the_one_the_web_started(
    cli_runner: CliRunner,
) -> None:
    with patch("src.cli.commands._covers.start_backfill", return_value=None):
        result = _invoke_with_mocks(
            cli_runner, ["covers", "backfill"], make_storage_mock()
        )

    assert result.exit_code != 0
    assert "already running" in result.output


def test_backfill_exits_non_zero_when_the_walk_did_not_complete(
    cli_runner: CliRunner,
) -> None:
    crashed = CoverBackfillRecord(errors=["the backfill stopped on an error"])

    with patch("src.cli.commands._covers.start_backfill", return_value=crashed):
        result = _invoke_with_mocks(
            cli_runner, ["covers", "backfill"], _storage_reading(crashed)
        )

    assert result.exit_code == 1
    assert "Cover backfill: stopped on an error" in result.output
    assert "the backfill stopped on an error" in result.output


def test_a_backfill_the_user_stopped_is_not_reported_as_a_failure(
    cli_runner: CliRunner,
) -> None:
    stopped = CoverBackfillRecord(cancelled=True, total=4, processed=2, cached=2)

    with patch("src.cli.commands._covers.start_backfill", return_value=stopped):
        result = _invoke_with_mocks(
            cli_runner, ["covers", "backfill"], _storage_reading(stopped)
        )

    assert result.exit_code == 0
    assert "Cover backfill: cancelled" in result.output


def test_a_ctrl_c_releases_the_claim_rather_than_blocking_the_next_start(
    cli_runner: CliRunner,
) -> None:
    running = CoverBackfillRecord(running=True, total=9, processed=2)
    storage = _storage_reading(running)

    with (
        patch("src.cli.commands._covers.start_backfill", return_value=running),
        patch("src.cli.commands._covers.time.sleep", side_effect=KeyboardInterrupt),
    ):
        result = _invoke_with_mocks(cli_runner, ["covers", "backfill"], storage)

    assert result.exit_code == 0
    assert "Cover backfill stopped." in result.output
    finished = storage.cover_jobs.finish.call_args.args[0]
    assert finished.cancelled
    assert finished.errors == []


def test_stop_ends_a_backfill_this_process_did_not_start(
    cli_runner: CliRunner,
) -> None:
    storage = make_storage_mock()
    storage.cover_jobs.request_stop.return_value = True

    result = _invoke_with_mocks(cli_runner, ["covers", "stop"], storage)

    assert result.exit_code == 0
    assert "Stop requested. The walk ends after the item it is on." in result.output


def test_stop_reports_an_idle_walk_the_way_the_web_action_does(
    cli_runner: CliRunner,
) -> None:
    storage = make_storage_mock()
    storage.cover_jobs.request_stop.return_value = False

    result = _invoke_with_mocks(cli_runner, ["covers", "stop"], storage)

    assert result.exit_code != 0
    assert "No cover backfill is running." in result.output


def test_status_reads_a_backfill_this_process_did_not_start(
    cli_runner: CliRunner,
) -> None:
    running = CoverBackfillRecord(
        running=True, total=9, processed=2, started_at=datetime.now(UTC)
    )

    result = _invoke_with_mocks(
        cli_runner, ["covers", "status"], _storage_reading(running)
    )

    assert result.exit_code == 0
    assert "Cover backfill: running" in result.output
    assert "Progress: 2/9" in result.output


def test_status_says_so_when_no_backfill_has_ever_run(cli_runner: CliRunner) -> None:
    result = _invoke_with_mocks(
        cli_runner, ["covers", "status"], _storage_reading(CoverBackfillRecord())
    )

    assert result.exit_code == 0
    assert result.output.strip() == "No cover backfill has run."


def test_show_prints_where_the_cover_is_cached(cli_runner: CliRunner) -> None:
    storage = make_storage_mock()
    storage.get_content_item.return_value = make_item(
        db_id=3, cover_url="https://1.2.3.4/c.jpg"
    )

    with patch(
        "src.cli.commands._covers.fill_cover", return_value=Path("/data/covers/3-abc")
    ) as mock_fill:
        result = _invoke_with_mocks(
            cli_runner, ["covers", "show", "3", "--user", "2"], storage
        )

    assert result.exit_code == 0
    assert result.output.strip() == "/data/covers/3-abc"
    assert mock_fill.call_args.kwargs["user_id"] == 2


def test_show_says_an_item_has_no_cover_rather_than_naming_a_url(
    cli_runner: CliRunner,
) -> None:
    storage = make_storage_mock()
    storage.get_content_item.return_value = make_item(title="Unadorned", db_id=3)
    unavailable = CoverUnavailable("this item has no cover art", permanent=False)

    with patch("src.cli.commands._covers.fill_cover", return_value=unavailable):
        result = _invoke_with_mocks(
            cli_runner, ["covers", "show", "3", "--format", "json"], storage
        )

    assert result.exit_code == 0
    assert json.loads(result.output) == {
        "db_id": 3,
        "title": "Unadorned",
        "path": None,
        "reason": "this item has no cover art",
    }
