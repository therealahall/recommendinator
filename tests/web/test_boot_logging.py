from __future__ import annotations

import logging
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from src.utils.logging import configure_logging
from src.web.app import create_app


class TestAnUnopenableLogAbortsTheBoot:
    def test_a_log_the_server_cannot_open_stops_create_app(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        restore_root_logging: None,
    ) -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / "data").write_text("not a directory", encoding="utf-8")
        config: dict[str, Any] = {"logging": {"file": "data/logs/app.log"}}

        with (
            patch("src.utils.logging.configure_logging", configure_logging),
            patch("src.web.app.load_config", return_value=config),
            patch("src.web.app.create_storage_manager", return_value=MagicMock()),
            patch("src.web.app.migrate_config_settings"),
        ):
            with pytest.raises(OSError) as raised:
                create_app()

        assert Path(str(raised.value.filename)).name == "logs"


class TestBootFailureLoggingRegression:
    _FORGED = "config/nope.yaml\nERROR    | forged | line"
    _RENDERED = "FileNotFoundError: config/nope.yaml\\nERROR    | forged | line"

    def test_a_missing_config_file_cannot_forge_a_second_entry(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
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
        config: dict[str, Any] = {"web": {}}
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
