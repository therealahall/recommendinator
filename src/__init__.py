"""Recommendinator.

A privacy-focused recommendation engine for books, movies, TV shows, and video games.
"""

from __future__ import annotations

import logging
import tomllib
from functools import cache
from importlib.metadata import PackageNotFoundError
from importlib.metadata import requires as _pkg_requires
from importlib.metadata import version as _pkg_version
from pathlib import Path
from typing import Any

from packaging.requirements import InvalidRequirement, Requirement
from pydantic import BaseModel, computed_field

_logger = logging.getLogger(__name__)


# importlib.metadata is written at install time: an editable install or a Docker
# dev container reports what it was built with, not what the source declares.
def _project_table(source_file: str | None = None) -> dict[str, Any]:
    base = Path(source_file if source_file is not None else __file__).resolve()
    try:
        with (base.parent.parent / "pyproject.toml").open("rb") as handle:
            data: dict[str, Any] = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError):
        return {}
    project = data.get("project")
    return project if isinstance(project, dict) else {}


def _read_source_version(source_file: str | None = None) -> str | None:
    version = _project_table(source_file).get("version")
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


class PackageDrift(BaseModel):
    package: str
    declared: str
    installed: str | None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def message(self) -> str:
        if self.installed is None:
            return f"{self.package} is not installed (needs {self.declared})"
        return f"{self.package} {self.installed} is installed (needs {self.declared})"


def _declared_requirements() -> list[str]:
    declared = _project_table().get("dependencies")
    if isinstance(declared, list):
        return [entry for entry in declared if isinstance(entry, str)]
    try:
        return list(_pkg_requires("recommendinator") or [])
    except PackageNotFoundError:
        return []


def _drift_of(entry: str) -> PackageDrift | None:
    try:
        requirement = Requirement(entry)
    except InvalidRequirement:
        return None
    marker = requirement.marker
    # Installed metadata carries the dev extra too, and none of it ships.
    if marker is not None and not marker.evaluate({"extra": ""}):
        return None
    declared = str(requirement.specifier)
    try:
        installed = _pkg_version(requirement.name)
    except PackageNotFoundError:
        return PackageDrift(package=requirement.name, declared=declared, installed=None)
    if requirement.specifier.contains(installed, prereleases=True):
        return None
    return PackageDrift(
        package=requirement.name, declared=declared, installed=installed
    )


@cache
def dependency_drift() -> tuple[PackageDrift, ...]:
    try:
        found = (_drift_of(entry) for entry in _declared_requirements())
        return tuple(drift for drift in found if drift is not None)
    except Exception as error:
        # A report on the environment may not be what stops the app serving.
        _logger.warning("Could not check installed dependencies: %r", error)
        return ()


def log_dependency_drift() -> None:
    for drift in dependency_drift():
        _logger.warning("Dependency drift: %s", drift.message)


__version__: str = _resolve_version()
