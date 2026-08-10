"""What ``src.web.app`` writes to the log file, and where.

``logging.file`` is network-settable, so a path escaping ``logs/`` falls back
to the registry default. The encoding must not be the locale's, and a boot
failure must not forge an entry.
"""

from __future__ import annotations

import codecs
import logging
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from src.storage.manager import StorageManager
from src.storage.settings_migration import migrate_config_settings
from src.web.app import configure_logging, create_app
from tests.factories import API_TOKEN


@pytest.fixture()
def restore_root_logging() -> Iterator[None]:
    """Snapshot and restore the root logger so tests don't leak handlers."""
    root = logging.getLogger()
    saved_handlers = root.handlers[:]
    saved_level = root.level
    try:
        yield
    finally:
        for handler in root.handlers[:]:
            if handler not in saved_handlers:
                handler.close()
                root.removeHandler(handler)
        for handler in saved_handlers:
            if handler not in root.handlers:
                root.addHandler(handler)
        root.setLevel(saved_level)


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


class TestTheLogFileTakesUtf8RatherThanTheLocaleRegression:
    """Reported by review: the log file inherited the process locale.

    Bug: the handler named no encoding, so under a non-UTF-8 locale an
    accented title deleted its own entry.
    Fix: open it UTF-8, and backslash-escape what UTF-8 cannot carry.

    The encoding half is pinned on the handler rather than by logging an
    accented title: under this suite's UTF-8 locale the unfixed handler wrote
    that title correctly too, so such a test passes against the bug.
    """

    def test_the_handler_names_utf8_rather_than_taking_the_locale(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        restore_root_logging: None,
    ) -> None:
        monkeypatch.chdir(tmp_path)
        configure_logging({"logging": {"level": "INFO", "file": "logs/app.log"}})

        handler = _file_handler()

        assert (handler.encoding, handler.errors) == ("utf-8", "backslashreplace")

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
        configure_logging({"logging": {"level": "INFO", "file": "logs/app.log"}})

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
        configure_logging({"logging": {"level": "INFO", "file": "logs/app.log"}})

        logging.getLogger("tests.log_encoding").info("title=%s", "Dune\udcff")

        assert b"title=Dune\\udcff" in _file_handler_path().read_bytes()


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

        configure_logging(config)

        assert _file_handler_path() == (tmp_path / "logs" / "app.log").resolve()

    def test_traversal_path_falls_back_to_default(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        restore_root_logging: None,
    ) -> None:
        """A path escaping logs/ is refused; the default under logs/ is used.

        ``logs/../../evil.log`` is rejected by the registry pattern at the
        Settings API, but config.yaml is unvalidated and a row persisted before
        that pattern gained its ``..`` lookahead still overlays at boot — so the
        containment backstop must reject it and never create the escape target.
        """
        monkeypatch.chdir(tmp_path)
        config: dict[str, Any] = {
            "logging": {"level": "INFO", "file": "logs/../../evil.log"}
        }

        configure_logging(config)

        assert (
            _file_handler_path()
            == (tmp_path / "logs" / "recommendations.log").resolve()
        )
        # The escape target was never created. `logs/../../evil.log` resolved
        # against tmp_path is tmp_path.parent/evil.log — the previous assertion
        # checked one level higher, a path nothing would ever have created, so
        # it could not fail.
        assert not (tmp_path.parent / "evil.log").exists()

    @pytest.mark.parametrize(
        "section",
        [
            None,  # a bare `logging:` header parses to None, not {}
            {"level": 3},  # .upper() on an int raises AttributeError
            {"file": 3},  # Path(3) raises TypeError
            {"level": ["INFO"], "file": {"path": "x"}},
            "INFO",  # the whole section mistyped as a scalar
        ],
    )
    def test_unusable_yaml_degrades_instead_of_aborting_boot(
        self,
        section: Any,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        restore_root_logging: None,
        caplog: pytest.LogCaptureFixture,
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

        with caplog.at_level(logging.WARNING, logger="src.web.app"):
            configure_logging(config)

        assert (
            _file_handler_path()
            == (tmp_path / "logs" / "recommendations.log").resolve()
        )
        assert logging.getLogger().level == logging.INFO

    @pytest.mark.parametrize(
        "level",
        ["verbose", "TRACE", "BASIC_FORMAT", "root", "raiseExceptions", "notset"],
    )
    def test_non_level_attribute_names_fall_back_and_warn(
        self,
        level: str,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        restore_root_logging: None,
        caplog: pytest.LogCaptureFixture,
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

        with caplog.at_level(logging.WARNING, logger="src.web.app"):
            configure_logging({"logging": {"level": level, "file": "logs/app.log"}})

        assert logging.getLogger().level == logging.INFO
        assert any("logging.level" in m and level in m for m in caplog.messages)

    def test_unusable_level_and_file_are_reported(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        restore_root_logging: None,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Falling back silently leaves the operator with logs they never chose."""
        monkeypatch.chdir(tmp_path)

        with caplog.at_level(logging.WARNING, logger="src.web.app"):
            configure_logging({"logging": {"level": 3, "file": ["x"]}})

        assert any("logging.level" in m and "3" in m for m in caplog.messages)
        assert any("logging.file" in m for m in caplog.messages)

    def test_valid_section_logs_nothing(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        restore_root_logging: None,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """The common case must stay quiet, or the warning trains itself away."""
        monkeypatch.chdir(tmp_path)

        with caplog.at_level(logging.WARNING, logger="src.web.app"):
            configure_logging({"logging": {"level": "DEBUG", "file": "logs/app.log"}})

        assert caplog.messages == []
        assert logging.getLogger().level == logging.DEBUG

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
        storage.set_setting("logging.file", "logs/../../evil.log")

        config: dict[str, Any] = {"logging": {"level": "INFO", "file": "logs/app.log"}}
        migrate_config_settings(config, storage)

        # The overlay applied the stored value verbatim — no re-validation.
        assert config["logging"]["file"] == "logs/../../evil.log"

        configure_logging(config)

        assert (
            _file_handler_path()
            == (tmp_path / "logs" / "recommendations.log").resolve()
        )
        assert not (tmp_path.parent / "evil.log").exists()


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
