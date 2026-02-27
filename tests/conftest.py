"""Root test configuration — prevents tests from polluting production logs."""

import logging
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
    ``logs/recommendations.log`` to the root logger.  This happens both:

    1. At **import time** — ``app = get_app()`` at module level.
    2. Inside tests that call ``create_app()`` explicitly.

    Patching ``configure_logging`` blocks (2) but cannot block (1) because
    importing the module to set up the patch triggers the module-level code
    first.  So we also strip handlers in **setup** (before the test) to
    clean up any that leaked from imports, and again in **teardown** to
    catch any added during the test.
    """
    _remove_production_log_handlers()
    with patch("src.web.app.configure_logging"):
        yield  # type: ignore[misc]
    _remove_production_log_handlers()
