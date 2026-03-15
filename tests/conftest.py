"""Root test configuration — prevents tests from polluting production logs."""

import logging
from pathlib import Path
from unittest.mock import patch

import pytest


def _remove_production_log_handlers() -> None:
    """Remove FileHandlers targeting ``recommendations.log`` from the root logger."""
    root = logging.getLogger()
    for handler in root.handlers[:]:
        if isinstance(handler, logging.FileHandler) and handler.baseFilename.endswith(
            "recommendations.log"
        ):
            handler.close()
            root.removeHandler(handler)


@pytest.fixture(autouse=True)
def _isolate_production_log_handlers() -> None:  # type: ignore[misc]
    """Prevent tests from writing to the production log file.

    ``src.web.app.configure_logging`` attaches a ``FileHandler`` for
    ``logs/recommendations.log`` to the root logger whenever ``create_app``
    is called.  Patching it as a no-op prevents new handlers from being
    created.  The handler-stripping in setup and teardown is a safety net
    in case any code path bypasses the patch (e.g. a direct import that
    triggers module-level initialisation).
    """
    _remove_production_log_handlers()
    with patch("src.web.app.configure_logging"):
        yield
    _remove_production_log_handlers()


@pytest.fixture(autouse=True)
def _isolate_credential_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolate credential encryption key to a temp dir for each test.

    Overrides RECOMMENDINATOR_KEY_PATH so no test reads from or writes to
    the real key file alongside the database (default: ``data/.credential_key``).
    """
    monkeypatch.setenv(
        "RECOMMENDINATOR_KEY_PATH",
        str(tmp_path / ".credential_key"),
    )
