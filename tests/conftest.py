"""Configuration for the cross-cutting tests under ``tests/``.

Session-wide isolation fixtures live in the repository-root ``conftest.py`` so
that the plugin-local tests under ``src/`` get them too.
"""

import logging
from collections.abc import Iterator

import pytest

# Make shared fakes / fixtures available to every test file without an import
# (which otherwise collides with pytest's fixture-name-as-parameter idiom).
pytest_plugins = ["tests.fakes.source_plugins"]


@pytest.fixture()
def restore_root_logging() -> Iterator[None]:
    """Snapshot and restore the root logger so tests don't leak handlers.

    Shared, because ``configure_logging`` detaches every root handler —
    pytest's own included — and two trees now call it.
    """
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
