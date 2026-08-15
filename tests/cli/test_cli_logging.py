"""What the CLI's boot does to the root logger, and which stream it writes on."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from src.cli.commands._recommend import RECOMMEND_FAILED
from src.cli.main import cli
from src.settings.metadata import default_of
from src.storage.manager import StorageManager

# Bound at import, before the root conftest's per-test patch: this is the real
# callable, and handing it back to ``patch`` is how a test opts out of the
# blanket no-op that keeps every other test off the production log.
from src.utils.logging import configure_logging

#: The boot warning ``_config_that_warns_at_boot`` provokes, once handlers exist.
_BOOT_WARNING = "Ignoring unusable logging.level"


def _log_to(log_file: str) -> dict[str, Any]:
    """A config saying only where the log goes."""
    return {"logging": {"file": log_file}}


def _config_that_warns_at_boot(log_file: str) -> dict[str, Any]:
    """A config whose unusable ``level`` makes boot log one WARNING record.

    The tests below assert where a record lands, so each needs the boot to
    produce one at all.
    """
    return {"logging": {"file": log_file, "level": "verbose"}}


def _invoke_with_real_logging(
    runner: CliRunner,
    storage: StorageManager,
    config: dict[str, Any],
    args: list[str],
    input_text: str | None = None,
    engine: Any = None,
) -> Any:
    """Run *args* with the log wiring unstubbed."""
    with (
        patch("src.utils.logging.configure_logging", configure_logging),
        patch("src.cli.main.load_config", return_value=config),
        patch("src.cli.main.create_storage_manager", return_value=storage),
        patch(
            "src.cli.main.create_recommendation_engine",
            return_value=engine if engine is not None else MagicMock(),
        ),
    ):
        return runner.invoke(cli, args, input=input_text)


def _failing_engine(error: Exception) -> MagicMock:
    """An engine whose ``recommend`` call raises, to exercise the log funnel."""
    engine = MagicMock()
    engine.generate_recommendations.side_effect = error
    return engine


class TestTheConsoleNeverWritesToTheDataChannelRegression:
    """A record on stdout lands ahead of the JSON document or the CSV header row.

    Bug risk: the CLI's console handler is one argument from the data channel.
    Fix: the stream is required, and the CLI passes stderr.
    """

    def test_a_json_command_keeps_stdout_clean_while_the_boot_warns(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        restore_root_logging: None,
    ) -> None:
        """A boot warning would prefix a log line onto the JSON document."""
        storage = StorageManager(sqlite_path=tmp_path / "test.db")
        monkeypatch.chdir(tmp_path)

        result = _invoke_with_real_logging(
            CliRunner(mix_stderr=False),
            storage,
            _config_that_warns_at_boot("logs/cli.log"),
            ["status", "--format", "json"],
        )

        assert result.exit_code == 0, result.stderr
        assert json.loads(result.stdout)["status"] == "ready"
        # Anchors the assertion above: without a record on the wire it holds
        # over an invocation that logged nothing at all.
        assert _BOOT_WARNING in result.stderr
        assert _BOOT_WARNING in (tmp_path / "logs" / "cli.log").read_text(
            encoding="utf-8"
        )


def test_the_log_level_comes_from_the_settings_table(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, restore_root_logging: None
) -> None:
    """``logging.level`` is DB-backed, so the overlay has to run first."""
    storage = StorageManager(sqlite_path=tmp_path / "test.db")
    storage.set_setting("logging.level", "DEBUG")
    monkeypatch.chdir(tmp_path)

    result = _invoke_with_real_logging(
        CliRunner(), storage, _config_that_warns_at_boot("logs/cli.log"), ["status"]
    )

    assert result.exit_code == 0, result.output
    # Only the stored row says DEBUG: configuring before the overlay leaves the
    # root logger on the registry default.
    assert default_of("logging.level") == "INFO"
    assert logging.getLogger().level == logging.DEBUG


class TestCheckLogsForDetailsNamesAFileHoldingThemRegression:
    """Reported by QA: a failing command named a log nobody was writing.

    Bug: no CLI command configured logging, so every ``exc_info`` record fell
    to a root logger with no handler.
    Fix: the boot wires the shared file handler.
    """

    def test_the_traceback_the_command_points_at_is_on_disk(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        restore_root_logging: None,
    ) -> None:
        """Its handler logs the caught exception with ``exc_info`` and then
        prints "Check logs for details", so the traceback is what the file must
        hold."""
        storage = StorageManager(sqlite_path=tmp_path / "test.db")
        monkeypatch.chdir(tmp_path)

        result = _invoke_with_real_logging(
            CliRunner(mix_stderr=False),
            storage,
            _log_to("logs/cli.log"),
            ["recommend", "--type", "book"],
            engine=_failing_engine(RuntimeError("the library is unreadable")),
        )

        assert result.exit_code == 1
        assert "Check logs for details" in result.stderr
        # The data channel stays empty on the failure path too.
        assert result.stdout == ""
        written = (tmp_path / "logs" / "cli.log").read_text(encoding="utf-8")
        assert RECOMMEND_FAILED in written
        assert "RuntimeError: the library is unreadable" in written


class TestAnUnusableLogDestinationDegradesRatherThanAbortingRegression:
    """Reported by QA: an unwritable ``logs/`` killed every CLI command.

    Bug: the configure call sat inside the callback's ``except Exception``, so
    a refusal read as "Error initializing components".
    Fix: it degrades to the console, reporting once.
    """

    def test_the_degrade_is_reported_once_on_stderr(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        restore_root_logging: None,
    ) -> None:
        """Degrading in silence leaves an operator hunting for a log nobody writes.

        One line, on stderr, naming the destination — the data channel stays
        clean, which is what makes ``--format json`` survive the degraded run.
        """
        monkeypatch.chdir(tmp_path)
        (tmp_path / "logs").write_text("not a directory", encoding="utf-8")
        storage = StorageManager(sqlite_path=tmp_path / "test.db")

        result = _invoke_with_real_logging(
            CliRunner(mix_stderr=False),
            storage,
            _log_to("logs/cli.log"),
            ["status", "--format", "json"],
        )

        assert result.exit_code == 0, result.stderr
        assert json.loads(result.stdout)["status"] == "ready"
        reported = result.stderr.splitlines()
        assert len(reported) == 1
        assert reported[0].startswith("Warning: no log file for this run: ")
        assert str((tmp_path / "logs").resolve()) in reported[0]


class TestTheConsoleWithholdsTracebacksRegression:
    """Reported by QA: a console handler dumped a traceback above every "Check
    logs for details" line.

    Bug: ``Formatter.format`` appends it whatever the format string says.
    Fix: the CLI's console renders the message alone, on both paths.
    """

    def test_a_caught_fault_keeps_its_traceback_off_the_terminal(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        restore_root_logging: None,
    ) -> None:
        storage = StorageManager(sqlite_path=tmp_path / "test.db")
        monkeypatch.chdir(tmp_path)

        result = _invoke_with_real_logging(
            CliRunner(mix_stderr=False),
            storage,
            _log_to("logs/cli.log"),
            ["recommend", "--type", "book"],
            engine=_failing_engine(RuntimeError("the library is unreadable")),
        )

        assert "Check logs for details" in result.stderr
        assert "Traceback (most recent call last):" not in result.stderr
        assert "the library is unreadable" not in result.stderr
        # Anchors both: the record carried a traceback for the console to drop.
        assert "Traceback (most recent call last):" in (
            tmp_path / "logs" / "cli.log"
        ).read_text(encoding="utf-8")
