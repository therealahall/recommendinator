"""The blank files an operator fills in and uploads back.

Not under ``importers/``: every module there is proved to touch no filesystem,
and these are files on disk.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from src.models.content import ContentType

#: Beside ``src/`` in the checkout and in the image alike. Resolved from this
#: module rather than the working directory, which is the repository root for
#: neither the container nor the operator running the CLI.
TEMPLATES_DIR = Path(__file__).resolve().parents[2] / "templates"


class TemplatesUnavailable(Exception):
    """The templates that ship with the application are not where they ship to."""


@dataclass(frozen=True, slots=True)
class ImportTemplate:
    """One blank file: the format that parses it, and the content type it holds."""

    importer: str
    content_type: str
    filename: str
    media_type: str


#: The file each generic importer reads, by importer name. The two site exports
#: have no template — a Goodreads export is whatever Goodreads hands you.
_FORMATS: dict[str, tuple[str, str]] = {
    "csv_import": ("csv", "text/csv"),
    "json_import": ("json", "application/json"),
    "markdown_import": ("md", "text/markdown"),
}

#: Every template, keyed by importer and content type. A caller looks a key up
#: and gets a filename written here, so no request string is ever a path segment.
_TEMPLATES: dict[tuple[str, str], ImportTemplate] = {
    (importer, content_type.value): ImportTemplate(
        importer=importer,
        content_type=content_type.value,
        filename=f"{content_type.value}s.{suffix}",
        media_type=media_type,
    )
    for importer, (suffix, media_type) in _FORMATS.items()
    for content_type in ContentType
}

#: The formats a template exists for, offered as the picker and the CLI choice.
TEMPLATE_IMPORTERS: tuple[str, ...] = tuple(_FORMATS)


def _directory() -> Path:
    if not TEMPLATES_DIR.is_dir():
        raise TemplatesUnavailable(f"No import templates directory at {TEMPLATES_DIR}")
    return TEMPLATES_DIR


def available_templates() -> list[ImportTemplate]:
    """Every template this install actually ships, in picker order."""
    directory = _directory()
    return [
        template
        for template in _TEMPLATES.values()
        if (directory / template.filename).is_file()
    ]


def find_template(importer: str, content_type: str) -> ImportTemplate | None:
    """The template for that pair, or None where the install ships no such file."""
    directory = _directory()
    template = _TEMPLATES.get((importer, content_type))
    if template is None or not (directory / template.filename).is_file():
        return None
    return template


def read_template(template: ImportTemplate) -> bytes:
    return (_directory() / template.filename).read_bytes()
