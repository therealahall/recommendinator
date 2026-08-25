"""What ``src.utils.logging`` writes to the log file, and where.

``logging.file`` is network-settable, so a path escaping ``logs/`` falls back
to the registry default, and the encoding must not be the locale's.
"""

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
    """Configure logging, defaulting to a console this suite need not assert on.

    ``console_floor`` is the web's, so a level this suite sets is the level
    the console takes.
    """
    configure_logging(
        config,
        console_stream=sys.stdout if console is None else console,
        console_tracebacks=console_tracebacks,
        console_floor=logging.NOTSET,
    )


def _file_handler() -> logging.FileHandler:
    """Return the root logger's single FileHandler."""
    handlers = [
        h for h in logging.getLogger().handlers if isinstance(h, logging.FileHandler)
    ]
    assert len(handlers) == 1
    return handlers[0]


def _file_handler_path() -> Path:
    """Return the absolute path of the root logger's single FileHandler."""
    return Path(_file_handler().baseFilename)


def _written() -> str:
    """Return what the configured log file holds."""
    return _file_handler_path().read_text(encoding="utf-8")


class TestTheLogFileTakesUtf8RatherThanTheLocaleRegression:
    """Reported by review: the log file inherited the process locale.

    Bug: the handler named no encoding, so under a non-UTF-8 locale an
    accented title deleted its own entry.
    Fix: open it UTF-8, and backslash-escape what UTF-8 cannot carry.

    The encoding half is pinned on the handler rather than by logging an
    accented title: under this suite's UTF-8 locale the unfixed handler wrote
    that title correctly too, so such a test passes against the bug.
    """

    def test_the_opened_file_took_utf8_rather_than_the_locales(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        restore_root_logging: None,
    ) -> None:
        """What the constructor was handed is not what the file was opened as.

        Normalised through ``codecs.lookup`` so this turns on the codec rather
        than on whichever alias spelling the stream echoes back.
        """
        monkeypatch.chdir(tmp_path)
        _configure({"logging": {"level": "INFO", "file": "logs/app.log"}})

        stream = _file_handler().stream

        assert codecs.lookup(stream.encoding).name == "utf-8"
        assert stream.errors == "backslashreplace"

    def test_an_unsanitized_surrogate_still_writes_its_entry(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        restore_root_logging: None,
    ) -> None:
        """``sanitize_for_log`` protects the sinks that remember to call it.

        This is the one that forgot: UTF-8 cannot encode a lone surrogate
        either, and strict errors make the encoder delete the whole entry.
        """
        monkeypatch.chdir(tmp_path)
        _configure({"logging": {"level": "INFO", "file": "logs/app.log"}})

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
        _configure({"logging": {"level": "INFO", "file": "logs/app.log"}})

        logging.getLogger("tests.origin").info("Sync finished")

        assert "| in-a-container |" in _written()


class TestConfigureLoggingContainment:
    """The FileHandler path is confined to the logs/ directory."""

    def test_normal_log_path_is_used(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        restore_root_logging: None,
    ) -> None:
        """A plain ``logs/x.log`` value opens a handler under logs/."""
        monkeypatch.chdir(tmp_path)
        config: dict[str, Any] = {"logging": {"level": "INFO", "file": "logs/app.log"}}

        _configure(config)

        assert _file_handler_path() == (tmp_path / "logs" / "app.log").resolve()

    @pytest.mark.parametrize(
        "section",
        [
            None,  # a bare `logging:` header parses to None, not {}
            {"level": 3},  # .upper() on an int raises AttributeError
            "INFO",  # the whole section mistyped as a scalar
        ],
    )
    def test_unusable_yaml_degrades_instead_of_aborting_boot(
        self,
        section: Any,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        restore_root_logging: None,
    ) -> None:
        """Every other YAML leaf type-guards and falls back; these two did not.

        Regression: ``config.get("logging", {}).get("level", ...).upper()`` and
        ``Path(log_file)`` took the file's word for it. Both run inside
        ``create_app``'s try, so a one-character typo in config.yaml surfaced as
        "Failed to initialize components" with the real cause swallowed —
        instead of degrading to the default the way ``web.host``/``web.port``
        and ``web.allowed_origins`` already do.
        """
        monkeypatch.chdir(tmp_path)
        config: dict[str, Any] = {"logging": section}

        _configure(config)

        assert (
            _file_handler_path()
            == (tmp_path / "logs" / "recommendations.log").resolve()
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
        """A string that is not a level name must degrade, not be looked up blind.

        Regression: the level was resolved with ``getattr(logging, name,
        logging.INFO)``, which searches the whole ``logging`` module namespace
        rather than its level names. ``verbose`` missed and fell back silently,
        with none of the warning every other malformed leaf here emits;
        ``notset`` resolved to 0, putting the root logger at "log everything";
        ``raiseExceptions`` resolved to True, which is an int and so ran the
        root logger at level 1; and ``BASIC_FORMAT``/``root`` resolved to a str
        and a RootLogger, making ``setLevel`` raise inside ``create_app``'s try
        — surfacing as "Failed to initialize components", exactly the failure
        the surrounding guards exist to prevent.
        """
        monkeypatch.chdir(tmp_path)

        _configure({"logging": {"level": level, "file": "logs/app.log"}})

        assert logging.getLogger().level == logging.INFO
        # One phrase, not two substrings: separately they pass on a file
        # holding the level warning and the name in unrelated entries.
        assert f"Ignoring unusable logging.level {level!r}" in _written()

    def test_stored_row_predating_the_pattern_is_contained_at_boot(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        restore_root_logging: None,
    ) -> None:
        """The real reason the containment backstop is not dead code.

        The other tests hand ``configure_logging`` a hostile string directly,
        which only proves the function guards its own argument. This drives the
        actual boot path: a row written to the settings table BEFORE the registry
        pattern gained its ``..`` lookahead — exactly what an upgrade leaves
        behind — overlaid by ``migrate_config_settings``, which applies stored
        rows without re-validating them.

        ``set_setting`` is deliberate here, not a shortcut around the API: it is
        how such a row got there under the old pattern, and it is the only way to
        reproduce a value the current API would reject.
        """
        monkeypatch.chdir(tmp_path)
        storage = StorageManager(sqlite_path=tmp_path / "test.db")
        storage.settings.set("logging.file", "logs/../../evil.log")

        config: dict[str, Any] = {"logging": {"level": "INFO", "file": "logs/app.log"}}
        migrate_config_settings(config, storage)

        # The overlay applied the stored value verbatim — no re-validation.
        assert config["logging"]["file"] == "logs/../../evil.log"

        _configure(config)

        assert (
            _file_handler_path()
            == (tmp_path / "logs" / "recommendations.log").resolve()
        )
        assert not (tmp_path.parent / "evil.log").exists()


class TestSafeLogPath:
    """``logging.file`` containment — the control the registry pattern relies on.

    Load-bearing for the inputs that pattern never sees: an unvalidated
    ``config.yaml``, a row persisted before the ``..`` lookahead, and a symlink
    under ``logs/``.
    """

    def test_path_inside_logs_is_returned_resolved(self) -> None:
        assert _safe_log_path("logs/app.log")[0] == (Path("logs") / "app.log").resolve()

    @pytest.mark.parametrize(
        "escaping",
        [
            "logs/../../../tmp/pwned.log",
            "/etc/cron.d/evil.log",
        ],
    )
    def test_path_escaping_logs_falls_back_to_the_default(self, escaping: str) -> None:
        """Anything resolving outside logs/ falls back — fail safe, never write.

        Unreachable from the Settings API now, but a hand-edited
        ``config.yaml`` is unvalidated and a row predating the ``..`` lookahead
        still overlays.
        """
        assert _safe_log_path(escaping)[0] == Path(default_of("logging.file")).resolve()

    def test_the_fallback_survives_a_symlinked_default_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The fail-safe branch must not become the escape it refuses.

        Regression: it returned ``Path(default_of(...)).resolve()``, and
        ``resolve`` follows symlinks — so planting the default log file as a
        link out of ``logs/`` redirected every refused path. Now built from the
        base.
        """
        monkeypatch.chdir(tmp_path)
        (tmp_path / "logs").mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()
        default_name = Path(default_of("logging.file")).name
        (tmp_path / "logs" / default_name).symlink_to(outside / "pwned.log")

        fallback = _safe_log_path("logs/../../evil.log")[0]

        assert _LOG_BASE_DIR.resolve() in fallback.parents
        assert fallback != (outside / "pwned.log").resolve()

    def test_the_logs_directory_itself_is_refused(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``file: logs`` names the directory, which is never a valid log file.

        Regression: containment accepted ``resolved == base``, so the
        FileHandler opened on a directory and raised inside ``create_app``'s
        try, surfacing as "Failed to initialize components".
        """
        monkeypatch.chdir(tmp_path)

        fallback = _safe_log_path("logs")[0]

        assert fallback != _LOG_BASE_DIR.resolve()
        assert _LOG_BASE_DIR.resolve() in fallback.parents

    def test_symlink_out_of_logs_is_refused(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The third input the pattern cannot see.

        ``logs/app.log`` satisfies the pattern completely. If it is a symlink,
        containment is the only thing between a network-set value and an
        arbitrary file opened for append.
        """
        monkeypatch.chdir(tmp_path)
        (tmp_path / "logs").mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()
        (tmp_path / "logs" / "app.log").symlink_to(outside / "pwned.log")

        # The RESOLVED path is what is compared: a check written against the
        # unresolved string would pass this.
        resolved = _safe_log_path("logs/app.log")[0]

        assert resolved != (outside / "pwned.log").resolve()
        assert resolved == Path(default_of("logging.file")).resolve()


def _logs_is_a_file(tmp_path: Path) -> None:
    """``mkdir`` is what refuses: nothing has been built yet."""
    (tmp_path / "logs").write_text("not a directory", encoding="utf-8")


def _the_log_file_is_a_directory(tmp_path: Path) -> None:
    """Contained by ``_safe_log_path`` and still unopenable: ``IsADirectoryError``."""
    (tmp_path / "logs" / "app.log").mkdir(parents=True)


#: Every shape a ``logs/app.log`` this process cannot open comes in.
_REFUSALS = {
    "logs-is-a-file": _logs_is_a_file,
    "the-log-file-is-a-directory": _the_log_file_is_a_directory,
}


class TestAnUnopenableDestinationLeavesTheRootLoggerAloneRegression:
    """Reported by QA: an unwritable ``logs/`` killed every CLI command.

    Bug: handlers were attached as built, so a refused open left the root
    logger stripped.
    Fix: build both handlers first, raising before any is swapped.
    """

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
            _configure({"logging": {"level": "DEBUG", "file": "logs/app.log"}})

        # Anchors the comparison: an empty list would match a stripped logger.
        assert sentinel in handlers_before
        assert root.handlers == handlers_before
        assert root.level == level_before


class TestTheConsoleCanBeDeniedExceptionTextRegression:
    """Reported by QA: wiring a console handler put tracebacks on the terminal.

    Bug: ``Formatter.format`` appends ``exc_text`` whatever the format string
    says, so every ``exc_info=True`` call printed one beside the friendly line.
    Fix: ``console_tracebacks=False`` renders the message alone.
    """

    _FAULT = "the library is unreadable"

    #: Every call that puts exception or stack text on a record. ``exc_info``
    #: is what the CLI's own handlers use; the other two are what the next one
    #: might, and each reaches the console by a different attribute.
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
            {"logging": {"level": "INFO", "file": "logs/app.log"}},
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
        """``exc_info=True`` is not the only door.

        ``logger.exception`` sets it implicitly, ``stack_info`` fills a second
        attribute ``Formatter.format`` appends from, and both are cached on the
        shared record by the file handler before the console handler sees it.
        """
        monkeypatch.chdir(tmp_path)

        console = self._configure_and_log(console_tracebacks=False, emit=emit)

        assert console.getvalue() == "ERROR | tests.console | Recommendation failed\n"

    def test_the_web_console_still_carries_it(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        restore_root_logging: None,
    ) -> None:
        """The argument is what suppresses it, not the format string."""
        monkeypatch.chdir(tmp_path)

        console = self._configure_and_log(console_tracebacks=True)

        assert f"RuntimeError: {self._FAULT}" in console.getvalue()


class TestTheConsoleKeepsARecordItsCodecCannotEncodeRegression:
    """Reported: a ROM scan lost the line naming a Latin-1 filename.

    Bug: the console inherited ``strict`` from ``sys.stdout``, so a lone
    surrogate raised inside ``emit`` and ``handleError`` swallowed the record.
    The log file, opened ``backslashreplace``, kept it.
    """

    #: What ``os.scandir`` hands the ROMs plugin for a name that is not UTF-8.
    _SCANNED = "/roms/Pok\udce9mon.zip"

    def test_the_line_still_reaches_the_terminal(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        restore_root_logging: None,
    ) -> None:
        monkeypatch.chdir(tmp_path)
        raw = io.BytesIO()
        # Encoding exactly as an unredirected ``sys.stdout`` does, strict
        # errors included: a StringIO takes a surrogate without complaint and
        # so cannot see this bug.
        console = io.TextIOWrapper(raw, encoding="utf-8")
        _configure(
            {"logging": {"level": "INFO", "file": "logs/app.log"}}, console=console
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
        """The same bug where stdout is ASCII rather than UTF-8.

        Escaping the surrogate range alone leaves this record dropped and the
        case above green, so the escape has to follow the stream's own codec.
        """
        monkeypatch.chdir(tmp_path)
        raw = io.BytesIO()
        console = io.TextIOWrapper(raw, encoding="ascii")
        _configure(
            {"logging": {"level": "INFO", "file": "logs/app.log"}}, console=console
        )

        logging.getLogger("tests.console_encoding").info("Imported %s", "Pokémon Red")
        console.flush()

        assert "Imported Pok\\xe9mon Red" in raw.getvalue().decode("ascii")


#: Every transport the wiring holds at WARNING, named by the child that emits:
#: ``urllib3.connectionpool`` is what writes the request target, and the level
#: on its parent is what silences it.
_QUIETED_TRANSPORTS = {
    "httpx": "httpx",
    "httpcore": "httpcore.http11",
    "urllib3": "urllib3.connectionpool",
}

#: A request target of the shape urllib3 logs, carrying the secret TMDB, RAWG,
#: Steam and GOG each put in the query string.
_REQUEST_TARGET = "/3/search/movie?api_key=s3cr3t"


class TestTheTransportsStayQuietWhenTheLevelIsDebugRegression:
    """Reported by review: ``logging.level: DEBUG`` wrote API keys to the log.

    Bug: ``urllib3`` was not quieted, and it carries the TMDB, RAWG, Steam and
    GOG calls, logging each request target with its query string.
    """

    def _configure_at_debug(self, console: io.StringIO) -> None:
        """The levels ``_install`` sets outlive the test that provoked them, so
        clearing them first is what makes this turn on the wiring rather than on
        collection order."""
        for transport in _QUIETED_TRANSPORTS:
            logging.getLogger(transport).setLevel(logging.NOTSET)
        _configure(
            {"logging": {"level": "DEBUG", "file": "logs/app.log"}}, console=console
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
        """Anchors the sweep above, which a run emitting no DEBUG record at all
        would satisfy."""
        monkeypatch.chdir(tmp_path)
        console = io.StringIO()
        self._configure_at_debug(console)

        logging.getLogger("tests.transport").debug("GET %s", _REQUEST_TARGET)

        assert _REQUEST_TARGET in _written()
        assert _REQUEST_TARGET in console.getvalue()
