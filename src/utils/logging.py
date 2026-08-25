"""Root-logger wiring, shared by both interfaces.

The module name shadows the standard library's inside ``src.utils`` alone —
under absolute imports the ``import logging`` below is still the stdlib one.
"""

from __future__ import annotations

import logging
import socket
from pathlib import Path
from typing import Any, TextIO

from src.settings.metadata import default_of
from src.utils.text import sanitize_for_log

logger = logging.getLogger(__name__)

# Every log file lives here, inside ``data/`` — whose bind mount already carries
# it to the host. ``logging.file`` is network-settable, so a path escaping this
# base is refused before a FileHandler opens it (see ``_safe_log_path``).
_LOG_BASE_DIR = Path("data/logs")

# The authoritative name -> level map, minus NOTSET. ``logging.NOTSET`` is a
# real name in that mapping but not a usable threshold: the root logger has no
# parent to inherit from, so level 0 enables every record — a DEBUG firehose
# written to disk from a value that reads like "off".
_LOG_LEVELS = {
    name: level
    for name, level in logging.getLevelNamesMapping().items()
    if level != logging.NOTSET
}


class _MessageOnlyFormatter(logging.Formatter):
    """Render the message alone, without traceback or stack text.

    ``Formatter.format`` appends ``exc_text`` whatever the format string says,
    and the file handler formats first and caches it on the shared record — so
    dropping it takes an override.
    """

    def format(self, record: logging.LogRecord) -> str:
        record.message = record.getMessage()
        if self.usesTime():
            record.asctime = self.formatTime(record, self.datefmt)
        return self.formatMessage(record)


class _OneLineFormatter(logging.Formatter):
    """Escape the traceback the base class appends verbatim: one record is one
    line here, and a frame quoting a source's response forges an entry."""

    def format(self, record: logging.LogRecord) -> str:
        return sanitize_for_log(super().format(record))


def _safe_log_path(log_file: str) -> tuple[Path, str | None]:
    """Resolve *log_file*, refusing any path escaping ``data/logs/``.

    The backstop behind the registry pattern, for what it cannot see: an
    unvalidated ``config.yaml``, a row predating the ``..`` lookahead, a
    symlink. An escaper takes the default's name inside the base.
    """
    base = _LOG_BASE_DIR.resolve()
    resolved = Path(log_file).resolve()
    # ``base`` itself is excluded deliberately: it names the directory, which
    # FileHandler cannot open (IsADirectoryError), not a log file.
    if base in resolved.parents:
        return resolved, None
    # Built from ``base`` rather than resolving the default, so the fail-safe
    # branch cannot itself escape — via an absolute registry default or a
    # symlinked default file.
    return base / Path(default_of("logging.file")).name, (
        f"Log file {log_file!r} resolves outside the data/logs/ directory; "
        "using the default."
    )


def _logging_section(config: dict[str, Any]) -> dict[str, Any]:
    """Return the ``logging`` section, type-guarded like every other YAML leaf.

    An unguarded `logging: 3` aborts boot inside create_app's try, and a bare
    `logging:` header parses to None rather than {}.
    """
    raw_section = config.get("logging")
    return raw_section if isinstance(raw_section, dict) else {}


def _resolve_level(section: dict[str, Any]) -> tuple[int, list[str]]:
    """Resolve ``logging.level``, with the fallbacks its caller reports."""
    raw_level = section.get("level", default_of("logging.level"))
    if isinstance(raw_level, str) and raw_level.upper() in _LOG_LEVELS:
        return _LOG_LEVELS[raw_level.upper()], []

    log_level_str = default_of("logging.level")
    return _LOG_LEVELS[log_level_str], [
        f"Ignoring unusable logging.level {raw_level!r} in config.yaml; using "
        f"{log_level_str} instead. It must be one of: "
        f"{', '.join(sorted(_LOG_LEVELS))}."
    ]


def _resolve_path(section: dict[str, Any]) -> tuple[Path, list[str]]:
    """Resolve ``logging.file`` and contain it under the base, with its fallbacks."""
    fallbacks: list[str] = []

    raw_file = section.get("file", default_of("logging.file"))
    if isinstance(raw_file, str):
        log_file = raw_file
    else:
        log_file = default_of("logging.file")
        fallbacks.append(
            f"Ignoring unusable logging.file {raw_file!r} in config.yaml; using "
            f"{log_file} instead. It must be a string."
        )

    log_path, containment_fallback = _safe_log_path(log_file)
    if containment_fallback is not None:
        fallbacks.append(containment_fallback)
    return log_path, fallbacks


class _EscapingStreamHandler(logging.StreamHandler[TextIO]):
    """Escape what the stream's codec cannot encode.

    ``StreamHandler`` takes no ``errors=``, so a lone surrogate (``os.scandir``
    yields them for non-UTF-8 filenames) raised against strict ``sys.stdout``
    inside ``emit``, and ``handleError`` swallowed the record with it.
    """

    def format(self, record: logging.LogRecord) -> str:
        encoding = getattr(self.stream, "encoding", None) or "utf-8"
        rendered = super().format(record).encode(encoding, "backslashreplace")
        return rendered.decode(encoding, "backslashreplace")


def _console_handler(
    stream: TextIO, level: int, tracebacks: bool
) -> logging.StreamHandler[TextIO]:
    # The stream keeps its own encoding: PYTHONUTF8 is the operator's lever for
    # the console, and rewrapping it here would close the real stream when the
    # wrapper is collected.
    handler = _EscapingStreamHandler(stream)
    handler.setLevel(level)
    console_format = "%(levelname)s | %(name)s | %(message)s"
    handler.setFormatter(
        logging.Formatter(console_format)
        if tracebacks
        else _MessageOnlyFormatter(console_format)
    )
    return handler


def _install(handlers: list[logging.Handler], level: int, fallbacks: list[str]) -> None:
    """Swap *handlers* onto the root logger, then report the held-back fallbacks.

    Called once every handler is built: a destination that refuses to open must
    leave the root logger as it was, not stripped of what it had.
    """
    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    # Remove existing handlers to avoid duplicates on reload
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)
    for handler in handlers:
        root_logger.addHandler(handler)

    # Both interfaces reach httpx — the enrichment providers use it from the
    # CLI too. Server-loop noise is the web app's.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    # urllib3 logs the request target — `?api_key=` for TMDB, RAWG, Steam,
    # GOG — at DEBUG and, on retry, at WARNING. This closes the DEBUG half;
    # requests' default Retry(0) closes the other, so a retrying adapter
    # would leak keys.
    logging.getLogger("urllib3").setLevel(logging.WARNING)

    # Held until now: a warning from before the swap reaches only
    # ``logging.lastResort``, unformatted and never the log file.
    for fallback in fallbacks:
        logger.warning(fallback)


def configure_logging(
    config: dict[str, Any],
    *,
    console_stream: TextIO,
    console_tracebacks: bool,
    console_floor: int,
) -> None:
    """Configure logging from application config.

    ``console_stream`` is required because the wrong answer is silent: the
    CLI's stdout is its data channel. An unopenable log file raises OSError
    with the root logger untouched.
    """
    section = _logging_section(config)
    log_level, level_fallbacks = _resolve_level(section)
    log_path, path_fallbacks = _resolve_path(section)

    log_path.parent.mkdir(parents=True, exist_ok=True)

    # UTF-8 by name, not by locale: a non-UTF-8 one silently drops every
    # accented title. ``backslashreplace`` for the reason
    # ``_EscapingStreamHandler`` has it: strict loses the whole record to a
    # single unencodable character.
    file_handler = logging.FileHandler(
        log_path, encoding="utf-8", errors="backslashreplace"
    )
    file_handler.setLevel(log_level)
    file_handler.setFormatter(
        _OneLineFormatter(
            "%(asctime)s | %(origin)s | %(levelname)-8s | %(name)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
            # Container and host-side CLI runs share one bind-mounted file, and
            # the hostname is what tells their records apart.
            defaults={"origin": socket.gethostname()},
        )
    )

    # The console floors where the file does not: a command printing every
    # INFO record beside its own output buries it, and the file is what
    # "check the logs" points at.
    _install(
        [
            file_handler,
            _console_handler(
                console_stream, max(log_level, console_floor), console_tracebacks
            ),
        ],
        log_level,
        level_fallbacks + path_fallbacks,
    )


def configure_console_only(
    config: dict[str, Any],
    *,
    console_stream: TextIO,
    console_tracebacks: bool,
    console_floor: int,
) -> None:
    """Wire the console alone, for a caller whose log file could not be opened.

    With no handler at all, records fall to ``logging.lastResort``, whose
    default formatter appends the tracebacks the CLI withholds.
    """
    log_level, fallbacks = _resolve_level(_logging_section(config))
    _install(
        [
            _console_handler(
                console_stream, max(log_level, console_floor), console_tracebacks
            )
        ],
        log_level,
        fallbacks,
    )
