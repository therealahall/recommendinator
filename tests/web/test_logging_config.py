"""Tests that configure_logging contains the log file under the logs/ directory.

``logging.file`` is settable over the network Settings API and is opened as a
``FileHandler`` (arbitrary file create/append). ``configure_logging`` must keep
the resolved path inside ``logs/`` and fall back to the registry default for any
path that escapes it, so a hostile value can never write outside ``logs/``.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from src.web.app import configure_logging


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


def _file_handler_path() -> Path:
    """Return the absolute path of the root logger's single FileHandler."""
    handlers = [
        h for h in logging.getLogger().handlers if isinstance(h, logging.FileHandler)
    ]
    assert len(handlers) == 1
    return Path(handlers[0].baseFilename)


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

        ``logs/../../evil.log`` matches the registry pattern (the char class
        admits ``..``) but resolves outside logs/, so the containment backstop
        must reject it and never create the escape target.
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
        # The escape target was never created.
        assert not (tmp_path.parent.parent / "evil.log").exists()
