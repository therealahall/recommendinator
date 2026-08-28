from __future__ import annotations

import logging
import pkgutil
import sys
from pathlib import Path

from src.utils.text import sanitize_for_log

logger = logging.getLogger(__name__)


def private_plugin_module_names(project_root: Path, scanning_for: str) -> list[str]:
    """Both registries import all of them, so ``scanning_for`` names the caller."""
    private_path = project_root / "private" / "plugins"

    if not private_path.is_dir():
        logger.debug(
            "No private plugins directory at %s, skipping the scan for %s",
            private_path,
            scanning_for,
        )
        return []

    for package_init in (
        private_path.parent / "__init__.py",
        private_path / "__init__.py",
    ):
        if not package_init.exists():
            logger.debug(
                "%s not found, skipping the scan for %s", package_init, scanning_for
            )
            return []

    project_root_str = str(project_root.absolute())
    if project_root_str not in sys.path:
        sys.path.insert(0, project_root_str)

    names = [
        module.name
        for module in pkgutil.iter_modules([str(private_path)])
        if not module.name.startswith("_")
    ]
    _warn_about_directories_that_are_not_packages(private_path, names, scanning_for)
    return names


def _warn_about_directories_that_are_not_packages(
    private_path: Path, found: list[str], scanning_for: str
) -> None:
    """A folder missing its ``__init__.py`` is invisible to ``pkgutil``."""
    for entry in sorted(private_path.iterdir()):
        if (
            entry.is_dir()
            and not entry.name.startswith("_")
            and entry.name not in found
            and any(entry.glob("*.py"))
        ):
            logger.warning(
                "Private plugin directory %s has no __init__.py, so it was "
                "skipped while scanning for %s",
                sanitize_for_log(entry.name),
                scanning_for,
            )
