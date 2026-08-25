"""What is installed, against what pyproject.toml declares.

Its own module so ``src/__init__.py`` stays standard-library only: the
container's HEALTHCHECK imports the package every 30 seconds and must not
pay for pydantic.
"""

from __future__ import annotations

import logging
from functools import cache
from importlib.metadata import PackageNotFoundError
from importlib.metadata import requires as _pkg_requires
from importlib.metadata import version as _pkg_version

from packaging.requirements import InvalidRequirement, Requirement
from pydantic import BaseModel, computed_field

from src import project_table

_logger = logging.getLogger(__name__)


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
    declared = project_table().get("dependencies")
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
