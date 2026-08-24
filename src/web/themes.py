"""The installed UI themes, read by the web API and by the ``theme`` CLI group."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

from src.utils.text import sanitize_for_log

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent / "static"
THEMES_DIR = STATIC_DIR / "themes"
THEMES_URL = "/static/themes"

PRIVATE_THEMES_DIR = Path(__file__).resolve().parents[2] / "private" / "themes"
PRIVATE_THEMES_URL = "/static/private-themes"

#: What the app paints for a user who has picked nothing.
DEFAULT_THEME_ID = "nord"

#: Longest theme id a door accepts, so a refusal comes before the disk scan.
MAX_THEME_ID_LENGTH = 64


class ThemeResponse(BaseModel):
    """One installed theme; the id pattern keeps a folder name safe in an href."""

    id: str = Field(pattern=r"^[A-Za-z0-9_-]+$", max_length=MAX_THEME_ID_LENGTH)
    name: str
    description: str
    author: str
    version: str
    theme_type: Literal["dark", "light"]
    css_url: str


def discover_themes(themes_dir: Path, url_prefix: str) -> list[ThemeResponse]:
    """Every subdirectory of *themes_dir* holding a theme.json this app can paint."""
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
                    css_url=f"{url_prefix}/{entry.name}/colors.css",
                )
            )
        except (json.JSONDecodeError, KeyError, OSError, ValidationError):
            # A directory name may hold anything but "/" and NUL, and this one
            # arrived with whatever theme the operator unpacked.
            logger.warning(
                "Skipping invalid theme directory: %s", sanitize_for_log(entry.name)
            )
            continue

    return themes


def installed_theme_ids() -> list[str]:
    """The ids both doors accept, so neither stores one nothing can paint."""
    return [theme.id for theme in installed_themes()]


def installed_themes() -> list[ThemeResponse]:
    themes = discover_themes(THEMES_DIR, THEMES_URL)
    shipped = {theme.id for theme in themes}

    for private in discover_themes(PRIVATE_THEMES_DIR, PRIVATE_THEMES_URL):
        if private.id in shipped:
            logger.warning(
                "Private theme %s carries the id of a shipped one, so it was "
                "skipped: the shipped theme keeps the id.",
                sanitize_for_log(private.id),
            )
            continue
        themes.append(private)

    return sorted(themes, key=lambda theme: theme.id)


def themed_shell(document: str, stored_theme_id: str) -> str:
    by_id = {theme.id: theme for theme in installed_themes()}
    theme = by_id.get(stored_theme_id) or by_id.get(DEFAULT_THEME_ID)
    if theme is None:
        return document

    attributes = f' data-theme="{theme.id}" data-theme-type="{theme.theme_type}"'
    link = f'<link id="theme-stylesheet" rel="stylesheet" href="{theme.css_url}">'
    return document.replace("<html", f"<html{attributes}", 1).replace(
        "</head>", f"{link}</head>", 1
    )
