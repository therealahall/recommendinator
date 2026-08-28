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
from src.utils.logging import configure_logging

_BOOT_WARNING = "Ignoring unusable logging.level"


def _log_to(log_file: str) -> dict[str, Any]:
    return {"logging": {"file": log_file}}


def _config_that_warns_at_boot(log_file: str) -> dict[str, Any]:
    return {"logging": {"file": log_file, "level": "verbose"}}


def _invoke_with_real_logging(
    runner: CliRunner,
    storage: StorageManager,
    config: dict[str, Any],
    args: list[str],
    input_text: str | None = None,
    engine: Any = None,
) -> Any:
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
    engine = MagicMock()
    engine.generate_recommendations.side_effect = error
    return engine


class TestTheConsoleNeverWritesToTheDataChannelRegression:
    def test_a_json_command_keeps_stdout_clean_while_the_boot_warns(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        restore_root_logging: None,
    ) -> None:
        storage = StorageManager(sqlite_path=tmp_path / "test.db")
        monkeypatch.chdir(tmp_path)

        result = _invoke_with_real_logging(
            CliRunner(),
            storage,
            _config_that_warns_at_boot("data/logs/cli.log"),
            ["status", "--format", "json"],
        )

        assert result.exit_code == 0, result.stderr
        assert json.loads(result.stdout)["status"] == "ready"
        assert _BOOT_WARNING in result.stderr
        assert _BOOT_WARNING in (tmp_path / "data" / "logs" / "cli.log").read_text(
            encoding="utf-8"
        )


def test_the_log_level_comes_from_the_settings_table(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, restore_root_logging: None
) -> None:
    storage = StorageManager(sqlite_path=tmp_path / "test.db")
    storage.settings.set("logging.level", "DEBUG")
    monkeypatch.chdir(tmp_path)

    result = _invoke_with_real_logging(
        CliRunner(),
        storage,
        _config_that_warns_at_boot("data/logs/cli.log"),
        ["status"],
    )

    assert result.exit_code == 0, result.output
    assert default_of("logging.level") == "INFO"
    assert logging.getLogger().level == logging.DEBUG


class TestCheckLogsForDetailsNamesAFileHoldingThemRegression:
    def test_the_traceback_the_command_points_at_is_on_disk(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        restore_root_logging: None,
    ) -> None:
        storage = StorageManager(sqlite_path=tmp_path / "test.db")
        monkeypatch.chdir(tmp_path)

        result = _invoke_with_real_logging(
            CliRunner(),
            storage,
            _log_to("data/logs/cli.log"),
            ["recommend", "--type", "book"],
            engine=_failing_engine(RuntimeError("the library is unreadable")),
        )

        assert result.exit_code == 1
        assert "Check logs for details" in result.stderr
        assert result.stdout == ""
        written = (tmp_path / "data" / "logs" / "cli.log").read_text(encoding="utf-8")
        assert RECOMMEND_FAILED in written
        assert "RuntimeError: the library is unreadable" in written


class TestAnUnusableLogDestinationDegradesRatherThanAbortingRegression:
    def test_the_degrade_is_reported_once_on_stderr(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        restore_root_logging: None,
    ) -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / "data").write_text("not a directory", encoding="utf-8")
        storage = StorageManager(sqlite_path=tmp_path / "test.db")

        result = _invoke_with_real_logging(
            CliRunner(),
            storage,
            _log_to("data/logs/cli.log"),
            ["status", "--format", "json"],
        )

        assert result.exit_code == 0, result.stderr
        assert json.loads(result.stdout)["status"] == "ready"
        reported = result.stderr.splitlines()
        assert len(reported) == 1
        assert reported[0].startswith("Warning: no log file for this run: ")
        assert str((tmp_path / "data" / "logs").resolve()) in reported[0]


class TestTheConsoleWithholdsTracebacksRegression:
    def test_a_caught_fault_keeps_its_traceback_off_the_terminal(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        restore_root_logging: None,
    ) -> None:
        storage = StorageManager(sqlite_path=tmp_path / "test.db")
        monkeypatch.chdir(tmp_path)

        result = _invoke_with_real_logging(
            CliRunner(),
            storage,
            _log_to("data/logs/cli.log"),
            ["recommend", "--type", "book"],
            engine=_failing_engine(RuntimeError("the library is unreadable")),
        )

        assert "Check logs for details" in result.stderr
        assert "Traceback (most recent call last):" not in result.stderr
        assert "the library is unreadable" not in result.stderr
        assert "Traceback (most recent call last):" in (
            tmp_path / "data" / "logs" / "cli.log"
        ).read_text(encoding="utf-8")
