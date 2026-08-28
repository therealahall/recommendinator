from __future__ import annotations

import tomllib
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version
from pathlib import Path
from typing import Any


# importlib.metadata is written at install time: an editable install or a Docker
# dev container reports what it was built with, not what the source declares.
def project_table(source_file: str | None = None) -> dict[str, Any]:
    base = Path(source_file if source_file is not None else __file__).resolve()
    try:
        with (base.parent.parent / "pyproject.toml").open("rb") as handle:
            data: dict[str, Any] = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError):
        return {}
    project = data.get("project")
    return project if isinstance(project, dict) else {}


def _read_source_version(source_file: str | None = None) -> str | None:
    version = project_table(source_file).get("version")
    # An empty or non-string value is "not present", so the caller falls back
    # to importlib.metadata rather than serving bogus data.
    return version if isinstance(version, str) and version else None


def _resolve_version() -> str:
    source_version = _read_source_version()
    if source_version is not None:
        return source_version
    try:
        return _pkg_version("recommendinator")
    except PackageNotFoundError:
        return "0.0.0"


__version__: str = _resolve_version()
