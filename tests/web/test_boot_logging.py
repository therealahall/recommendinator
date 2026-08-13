"""What ``create_app`` writes to the log when the boot itself fails."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from src.utils.logging import configure_logging
from src.web.app import create_app
from tests.factories import API_TOKEN


class TestAnUnopenableLogAbortsTheBoot:
    """The web takes the opposite branch from the CLI, on purpose.

    A server has no console to degrade onto, so it fails loudly rather than
    serve unlogged for weeks.
    """

    def test_a_log_the_server_cannot_open_stops_create_app(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        restore_root_logging: None,
    ) -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / "logs").write_text("not a directory", encoding="utf-8")
        config: dict[str, Any] = {
            "web": {"api_token": API_TOKEN},
            "logging": {"file": "logs/app.log"},
        }

        with (
            patch("src.utils.logging.configure_logging", configure_logging),
            patch("src.web.app.load_config", return_value=config),
            patch("src.web.app.create_storage_manager", return_value=MagicMock()),
            patch("src.web.app.migrate_config_settings"),
        ):
            with pytest.raises(OSError) as raised:
                create_app()

        # Tied to the log destination: a bare OSError is satisfied by any
        # unmocked component inside this boot failing for its own reason.
        assert Path(str(raised.value.filename)).name == "logs"


class TestBootFailureLoggingRegression:
    """Regression: both boot handlers interpolated the caught exception raw.

    Bug: ``%s`` on it let a line break in the message forge an entry, and a
    message-less exception logged a bare trailing colon.
    Fix: both render it through ``exception_for_log``.
    """

    _FORGED = "config/nope.yaml\nERROR    | forged | line"
    _RENDERED = "FileNotFoundError: config/nope.yaml\\nERROR    | forged | line"

    def test_a_missing_config_file_cannot_forge_a_second_entry(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The loader's message is the path it was handed."""
        fault = FileNotFoundError(self._FORGED)
        with patch("src.web.app.load_config", side_effect=fault):
            with caplog.at_level(logging.ERROR, logger="src.web.app"):
                with pytest.raises(FileNotFoundError):
                    create_app()

        assert [record.getMessage() for record in caplog.records] == [
            f"Config file not found: {self._RENDERED}"
        ]
        assert not any(record.exc_info for record in caplog.records)

    def test_a_message_less_boot_fault_still_names_its_class(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """``%s`` on a bare ``RuntimeError()`` logged the colon and nothing else."""
        config: dict[str, Any] = {"web": {"api_token": API_TOKEN}}
        with (
            patch("src.web.app.load_config", return_value=config),
            patch("src.web.app.create_storage_manager", side_effect=RuntimeError()),
        ):
            with caplog.at_level(logging.ERROR, logger="src.web.app"):
                with pytest.raises(RuntimeError):
                    create_app()

        assert [record.getMessage() for record in caplog.records] == [
            "Failed to initialize components: RuntimeError: "
        ]
        assert not any(record.exc_info for record in caplog.records)
