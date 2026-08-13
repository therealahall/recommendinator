"""Configuration for the cross-cutting tests under ``tests/``.

Session-wide isolation fixtures live in the repository-root ``conftest.py`` so
that the plugin-local tests under ``src/`` get them too.
"""

import logging
from collections.abc import Iterator
from pathlib import Path

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


@pytest.fixture()
def restore_directory_modes(tmp_path: Path) -> Iterator[None]:
    """Make every directory under ``tmp_path`` writable again at teardown.

    A test that drops write permission otherwise leaves a tree pytest's own
    ``tmp_path`` reaper cannot delete, days later and in another file.
    """
    yield
    for path in tmp_path.rglob("*"):
        if path.is_dir():
            path.chmod(0o700)
