"""Recommendinator.

A privacy-focused recommendation engine for books, movies, TV shows, and video games.
"""

from __future__ import annotations

import tomllib
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version
from pathlib import Path


def _read_source_version(source_file: str | None = None) -> str | None:
    """Return the version from a pyproject.toml adjacent to this source tree.

    Why: importlib.metadata caches the version at install time, so editable
    installs and Docker dev containers report a stale version after
    semantic-release bumps pyproject.toml. Reading the file directly when
    it sits next to src/ keeps the dev/Docker version in sync with the
    source of truth without requiring a reinstall.
    """
    base = Path(source_file if source_file is not None else __file__).resolve()
    pyproject = base.parent.parent / "pyproject.toml"
    try:
        with pyproject.open("rb") as handle:
            data = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError):
        return None
    version = data.get("project", {}).get("version")
    # Treat empty strings and non-string values as "not present" so the
    # caller falls back to importlib.metadata instead of returning bogus data.
    return version if isinstance(version, str) and version else None


def _resolve_version() -> str:
    """Return the package version, preferring adjacent pyproject.toml.

    Precedence: pyproject.toml (dev/Docker source layout) -> importlib.metadata
    (installed wheel) -> "0.0.0" sentinel (uninstalled clone).
    """
    source_version = _read_source_version()
    if source_version is not None:
        return source_version
    try:
        return _pkg_version("recommendinator")
    except PackageNotFoundError:
        return "0.0.0"


__version__: str = _resolve_version()
