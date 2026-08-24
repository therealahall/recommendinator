"""The installed UI themes, read by the web API and by the ``theme`` CLI group."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from pydantic import BaseModel

from src.utils.text import sanitize_for_log

logger = logging.getLogger(__name__)

THEMES_DIR = Path(__file__).resolve().parent / "static" / "themes"

#: What the app paints for a user who has picked nothing.
DEFAULT_THEME_ID = "nord"

#: Longest theme id a door accepts, so a refusal comes before the disk scan.
MAX_THEME_ID_LENGTH = 64


class ThemeResponse(BaseModel):
    """Response model for a theme."""

    id: str
    name: str
    description: str
    author: str
    version: str
    theme_type: str


def discover_themes(themes_dir: Path) -> list[ThemeResponse]:
    """Every subdirectory of *themes_dir* holding a readable ``theme.json``,
    sorted by directory name."""
    themes: list[ThemeResponse] = []

    if not themes_dir.is_dir():
        return themes

    for entry in sorted(themes_dir.iterdir()):
        if not entry.is_dir():
            continue

        theme_file = entry / "theme.json"
        if not theme_file.is_file():
            continue

        try:
            raw = json.loads(theme_file.read_text(encoding="utf-8"))
            themes.append(
                ThemeResponse(
                    id=entry.name,
                    name=raw["name"],
                    description=raw["description"],
                    author=raw["author"],
                    version=raw["version"],
                    theme_type=raw["type"],
                )
            )
        except (json.JSONDecodeError, KeyError, OSError):
            # A directory name may hold anything but "/" and NUL, and this one
            # arrived with whatever theme the operator unpacked.
            logger.warning(
                "Skipping invalid theme directory: %s", sanitize_for_log(entry.name)
            )
            continue

    return themes


def installed_theme_ids(themes_dir: Path) -> list[str]:
    """The ids both doors accept, so neither stores one nothing can paint."""
    return [theme.id for theme in discover_themes(themes_dir)]
