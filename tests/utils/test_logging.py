from __future__ import annotations

import codecs
import io
import logging
import socket
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any, TextIO

import pytest

from src.settings.metadata import default_of
from src.storage.manager import StorageManager
from src.storage.settings_migration import migrate_config_settings
from src.utils.logging import _LOG_BASE_DIR, _safe_log_path, configure_logging


def _configure(
    config: dict[str, Any],
    *,
    console: TextIO | None = None,
    console_tracebacks: bool = True,
) -> None:
    configure_logging(
        config,
        console_stream=sys.stdout if console is None else console,
        console_tracebacks=console_tracebacks,
        console_floor=logging.NOTSET,
    )


def _file_handler() -> logging.FileHandler:
    handlers = [
        h for h in logging.getLogger().handlers if isinstance(h, logging.FileHandler)
    ]
    assert len(handlers) == 1
    return handlers[0]


def _file_handler_path() -> Path:
    return Path(_file_handler().baseFilename)


def _written() -> str:
    return _file_handler_path().read_text(encoding="utf-8")


class TestTheLogFileTakesUtf8RatherThanTheLocaleRegression:
    def test_the_opened_file_took_utf8_rather_than_the_locales(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        restore_root_logging: None,
    ) -> None:
        monkeypatch.chdir(tmp_path)
        _configure({"logging": {"level": "INFO", "file": "data/logs/app.log"}})

        stream = _file_handler().stream

        assert codecs.lookup(stream.encoding).name == "utf-8"
        assert stream.errors == "backslashreplace"

    def test_an_unsanitized_surrogate_still_writes_its_entry(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        restore_root_logging: None,
    ) -> None:
        monkeypatch.chdir(tmp_path)
        _configure({"logging": {"level": "INFO", "file": "data/logs/app.log"}})

        logging.getLogger("tests.log_encoding").info("title=%s", "Dune\udcff")

        assert b"title=Dune\\udcff" in _file_handler_path().read_bytes()


class TestEveryFileEntryNamesTheHostThatWroteIt:
    def test_the_entry_carries_the_hostname(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        restore_root_logging: None,
    ) -> None:
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(socket, "gethostname", lambda: "in-a-container")
        _configure({"logging": {"level": "INFO", "file": "data/logs/app.log"}})

        logging.getLogger("tests.origin").info("Sync finished")

        assert "| in-a-container |" in _written()


class TestOneRecordIsOneLineInTheFile:
    def test_a_traceback_quoting_a_forged_entry_stays_on_one_line(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        restore_root_logging: None,
    ) -> None:
        monkeypatch.chdir(tmp_path)
        _configure({"logging": {"level": "INFO", "file": "data/logs/app.log"}})

        try:
            raise ValueError("boom\n2026-01-01 00:00:00 | host | ERROR | forged")
        except ValueError:
            logging.getLogger("tests.one_line").exception("Sync failed")

        written = _written()
        assert written.count("\n") == 1
        assert "Traceback (most recent call last)" in written


class TestConfigureLoggingContainment:
    @pytest.mark.parametrize(
        "section",
        [
            None,
            {"level": 3},
            "INFO",
        ],
    )
    def test_unusable_yaml_degrades_instead_of_aborting_boot(
        self,
        section: Any,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        restore_root_logging: None,
    ) -> None:
        monkeypatch.chdir(tmp_path)
        config: dict[str, Any] = {"logging": section}

        _configure(config)

        assert (
            _file_handler_path()
            == (tmp_path / "data" / "logs" / "recommendations.log").resolve()
        )
        assert logging.getLogger().level == logging.INFO

    @pytest.mark.parametrize(
        "level",
        ["verbose", "BASIC_FORMAT", "notset"],
    )
    def test_non_level_attribute_names_fall_back_and_warn(
        self,
        level: str,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        restore_root_logging: None,
    ) -> None:
        monkeypatch.chdir(tmp_path)

        _configure({"logging": {"level": level, "file": "data/logs/app.log"}})

        assert logging.getLogger().level == logging.INFO
        assert f"Ignoring unusable logging.level {level!r}" in _written()

    def test_stored_row_predating_the_pattern_is_contained_at_boot(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        restore_root_logging: None,
    ) -> None:
        monkeypatch.chdir(tmp_path)
        storage = StorageManager(sqlite_path=tmp_path / "test.db")
        storage.settings.set("logging.file", "logs/../../evil.log")

        config: dict[str, Any] = {"logging": {"file": "data/logs/app.log"}}
        migrate_config_settings(config, storage)

        assert config["logging"]["file"] == "data/logs/../../evil.log"

        _configure(config)

        assert (
            _file_handler_path()
            == (tmp_path / "data" / "logs" / "recommendations.log").resolve()
        )
        assert not (tmp_path / "evil.log").exists()
        assert "resolves outside the data/logs/ directory" in _written()


class TestSafeLogPath:
    def test_path_inside_the_base_is_returned_resolved(self) -> None:
        inside = _LOG_BASE_DIR / "app.log"
        assert _safe_log_path(str(inside))[0] == inside.resolve()

    @pytest.mark.parametrize(
        "escaping",
        [
            "data/logs/../../../tmp/pwned.log",
            "/etc/cron.d/evil.log",
        ],
    )
    def test_an_escaping_path_falls_back_to_the_default(self, escaping: str) -> None:
        assert _safe_log_path(escaping)[0] == Path(default_of("logging.file")).resolve()

    def test_the_fallback_survives_a_symlinked_default_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / _LOG_BASE_DIR).mkdir(parents=True)
        outside = tmp_path / "outside"
        outside.mkdir()
        default_name = Path(default_of("logging.file")).name
        (tmp_path / _LOG_BASE_DIR / default_name).symlink_to(outside / "pwned.log")

        fallback = _safe_log_path("data/logs/../../evil.log")[0]

        assert _LOG_BASE_DIR.resolve() in fallback.parents
        assert fallback != (outside / "pwned.log").resolve()

    def test_the_base_directory_itself_is_refused(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)

        fallback = _safe_log_path("data/logs")[0]

        assert fallback != _LOG_BASE_DIR.resolve()
        assert _LOG_BASE_DIR.resolve() in fallback.parents

    def test_symlink_out_of_the_base_is_refused(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / _LOG_BASE_DIR).mkdir(parents=True)
        outside = tmp_path / "outside"
        outside.mkdir()
        (tmp_path / _LOG_BASE_DIR / "app.log").symlink_to(outside / "pwned.log")

        resolved = _safe_log_path("data/logs/app.log")[0]

        assert resolved != (outside / "pwned.log").resolve()
        assert resolved == Path(default_of("logging.file")).resolve()


def _the_data_directory_is_a_file(tmp_path: Path) -> None:
    (tmp_path / "data").write_text("not a directory", encoding="utf-8")


def _the_log_file_is_a_directory(tmp_path: Path) -> None:
    (tmp_path / _LOG_BASE_DIR / "app.log").mkdir(parents=True)


_REFUSALS = {
    "data-is-a-file": _the_data_directory_is_a_file,
    "the-log-file-is-a-directory": _the_log_file_is_a_directory,
}


class TestAnUnopenableDestinationLeavesTheRootLoggerAloneRegression:
    @pytest.mark.parametrize("refuse", _REFUSALS.values(), ids=_REFUSALS)
    def test_the_handlers_that_were_there_are_still_there(
        self,
        refuse: Callable[[Path], None],
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        restore_root_logging: None,
    ) -> None:
        monkeypatch.chdir(tmp_path)
        refuse(tmp_path)
        sentinel = logging.NullHandler()
        root = logging.getLogger()
        root.addHandler(sentinel)
        handlers_before = list(root.handlers)
        level_before = root.level

        with pytest.raises(OSError):
            _configure({"logging": {"level": "DEBUG", "file": "data/logs/app.log"}})

        assert sentinel in handlers_before
        assert root.handlers == handlers_before
        assert root.level == level_before


class TestTheConsoleCanBeDeniedExceptionTextRegression:
    _FAULT = "the library is unreadable"

    _EMITTERS: dict[str, Callable[[logging.Logger], None]] = {
        "exc_info": lambda log: log.error("Recommendation failed", exc_info=True),
        "stack_info": lambda log: log.error("Recommendation failed", stack_info=True),
    }

    def _configure_and_log(
        self,
        *,
        console_tracebacks: bool,
        emit: Callable[[logging.Logger], None] | None = None,
    ) -> io.StringIO:
        console = io.StringIO()
        _configure(
            {"logging": {"level": "INFO", "file": "data/logs/app.log"}},
            console=console,
            console_tracebacks=console_tracebacks,
        )
        emitter = emit or self._EMITTERS["exc_info"]
        try:
            raise RuntimeError(self._FAULT)
        except RuntimeError:
            emitter(logging.getLogger("tests.console"))
        return console

    @pytest.mark.parametrize("emit", _EMITTERS.values(), ids=_EMITTERS)
    def test_no_way_of_attaching_detail_reaches_the_console(
        self,
        emit: Callable[[logging.Logger], None],
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        restore_root_logging: None,
    ) -> None:
        monkeypatch.chdir(tmp_path)

        console = self._configure_and_log(console_tracebacks=False, emit=emit)

        assert console.getvalue() == "ERROR | tests.console | Recommendation failed\n"

    def test_the_web_console_still_carries_it(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        restore_root_logging: None,
    ) -> None:
        monkeypatch.chdir(tmp_path)

        console = self._configure_and_log(console_tracebacks=True)

        assert f"RuntimeError: {self._FAULT}" in console.getvalue()


class TestTheConsoleKeepsARecordItsCodecCannotEncodeRegression:
    _SCANNED = "/roms/Pok\udce9mon.zip"

    def test_the_line_still_reaches_the_terminal(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        restore_root_logging: None,
    ) -> None:
        monkeypatch.chdir(tmp_path)
        raw = io.BytesIO()
        console = io.TextIOWrapper(raw, encoding="utf-8")
        _configure(
            {"logging": {"level": "INFO", "file": "data/logs/app.log"}}, console=console
        )

        logging.getLogger("tests.console_encoding").info("Scanning %s", self._SCANNED)
        console.flush()

        assert "Scanning /roms/Pok\\udce9mon.zip" in raw.getvalue().decode("utf-8")

    def test_an_accented_title_survives_a_console_that_cannot_spell_it(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        restore_root_logging: None,
    ) -> None:
        monkeypatch.chdir(tmp_path)
        raw = io.BytesIO()
        console = io.TextIOWrapper(raw, encoding="ascii")
        _configure(
            {"logging": {"level": "INFO", "file": "data/logs/app.log"}}, console=console
        )

        logging.getLogger("tests.console_encoding").info("Imported %s", "Pokémon Red")
        console.flush()

        assert "Imported Pok\\xe9mon Red" in raw.getvalue().decode("ascii")


_QUIETED_TRANSPORTS = {
    "httpx": "httpx",
    "httpcore": "httpcore.http11",
    "urllib3": "urllib3.connectionpool",
}

_REQUEST_TARGET = "/3/search/movie?api_key=s3cr3t"


class TestTheTransportsStayQuietWhenTheLevelIsDebugRegression:
    def _configure_at_debug(self, console: io.StringIO) -> None:
        for transport in _QUIETED_TRANSPORTS:
            logging.getLogger(transport).setLevel(logging.NOTSET)
        _configure(
            {"logging": {"level": "DEBUG", "file": "data/logs/app.log"}},
            console=console,
        )

    @pytest.mark.parametrize(
        "emitter", _QUIETED_TRANSPORTS.values(), ids=_QUIETED_TRANSPORTS
    )
    def test_a_transport_debug_record_reaches_neither_sink(
        self,
        emitter: str,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        restore_root_logging: None,
    ) -> None:
        monkeypatch.chdir(tmp_path)
        console = io.StringIO()
        self._configure_at_debug(console)

        logging.getLogger(emitter).debug("GET %s", _REQUEST_TARGET)

        assert _written() == ""
        assert console.getvalue() == ""

    def test_a_debug_record_from_anything_else_reaches_both(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        restore_root_logging: None,
    ) -> None:
        monkeypatch.chdir(tmp_path)
        console = io.StringIO()
        self._configure_at_debug(console)

        logging.getLogger("tests.transport").debug("GET %s", _REQUEST_TARGET)

        assert _REQUEST_TARGET in _written()
        assert _REQUEST_TARGET in console.getvalue()
