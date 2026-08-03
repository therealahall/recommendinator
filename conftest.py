"""Autouse isolation for every test in every tree — off the developer's real data.

This file is at the repository root rather than in ``tests/`` because a conftest
only applies to its own subtree, and tests are collected from three trees:
``tests/``, the plugin-local ``test_<plugin>.py`` files under ``src/`` (both are
in ``testpaths``), and the private plugins under ``private/`` when they are run
explicitly. Fixtures defined here are the only ones all three get.
"""

import logging
import os
import time
from collections.abc import Callable, Iterator
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
def _isolate_production_log_handlers() -> Iterator[None]:
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
def host_timezone() -> Iterator[Callable[[str], None]]:
    """Pin the process timezone to UTC and let a test choose another zone.

    ``src.utils.dates.local_date_from_iso_timestamp`` narrows a UTC instant to
    the calendar day of the *host's* zone, so any assertion on a narrowed date
    would otherwise depend on where the suite runs. Every test gets UTC; a test
    exercising the conversion requests this fixture and calls it with the zone
    it wants, which is restored afterwards either way.

    This lives at the repository root rather than in ``tests/`` for the same
    reason as the fixtures around it, and for one of its own: the Trakt plugin's
    tests are plugin-local under ``src/``, and they are the tests that most need
    the zone pinned.
    """
    previous = os.environ.get("TZ")

    def use(zone: str) -> None:
        os.environ["TZ"] = zone
        time.tzset()

    use("UTC")
    yield use
    if previous is None:
        os.environ.pop("TZ", None)
    else:
        os.environ["TZ"] = previous
    time.tzset()


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
