"""Markdown import plugin using a prescriptive list format per content type."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from typing import TYPE_CHECKING, Any

from src.ingestion.importers.base import ImporterError, SkippedRow
from src.ingestion.importers.markdown.markdown import MarkdownImporter
from src.ingestion.paths import PathNotAllowed, resolve_source_path
from src.ingestion.plugin_base import (
    ConfigField,
    ProgressCallback,
    SourceError,
    SourcePlugin,
)
from src.models.content import ContentItem, ContentType
from src.utils.text import sanitize_for_log

if TYPE_CHECKING:
    from src.storage.manager import StorageManager

logger = logging.getLogger(__name__)

_IMPORTER = MarkdownImporter()


class MarkdownImportPlugin(SourcePlugin):
    """Plugin for importing content from Markdown files.

    Uses a prescriptive format with ## headings for status sections
    and list items for entries. Template files show the expected format
    in the templates/ directory.

    Format:
        ## Completed
        - **Title** by Creator | Rating: 5 | Date: 2024-06-15

        ## In Progress
        - **Title** by Creator

        ## To Read
        - **Title** by Creator
    """

    @property
    def name(self) -> str:
        return "markdown_import"

    @property
    def display_name(self) -> str:
        return "Markdown Import"

    @property
    def description(self) -> str:
        return "Import from Markdown file"

    @property
    def content_types(self) -> list[ContentType]:
        return [
            ContentType.BOOK,
            ContentType.MOVIE,
            ContentType.TV_SHOW,
            ContentType.VIDEO_GAME,
        ]

    @property
    def requires_api_key(self) -> bool:
        return False

    @property
    def requires_network(self) -> bool:
        return False

    def get_config_schema(self) -> list[ConfigField]:
        return [
            ConfigField(
                name="path",
                field_type=str,
                required=True,
                reads_path=True,
                description="Path to Markdown file in the prescribed format",
            ),
            ConfigField(
                name="content_type",
                field_type=str,
                required=True,
                description="Content type: book, movie, tv_show, or video_game",
            ),
        ]

    def validate_config(
        self,
        config: dict[str, Any],
        storage: StorageManager | None = None,
        user_id: int = 1,
    ) -> list[str]:
        errors = []

        path = config.get("path")
        if not path:
            errors.append("'path' is required")
        else:
            # Containment before existence: the "not found" message is an
            # oracle for any path the caller cares to probe.
            try:
                resolved = resolve_source_path(str(path))
            except PathNotAllowed as error:
                errors.append(str(error))
            else:
                if not resolved.exists():
                    errors.append(f"Markdown file not found: {path}")

        content_type = config.get("content_type", "")
        valid_types = [content_type_enum.value for content_type_enum in ContentType]
        if not content_type:
            errors.append("'content_type' is required")
        elif content_type not in valid_types:
            errors.append(
                f"Invalid content_type '{content_type}'. "
                f"Must be one of: {', '.join(valid_types)}"
            )

        return errors

    def fetch(
        self,
        config: dict[str, Any],
        progress_callback: ProgressCallback | None = None,
    ) -> Iterator[ContentItem]:
        """Read the configured file and hand its text to the importer.

        Raises:
            SourceError: If the file cannot be read or parsed
        """
        path = config.get("path", "")
        content_type_str = config.get("content_type", "")

        try:
            content_type = ContentType(content_type_str)
        except ValueError as error:
            raise SourceError(
                self.name, f"Invalid content type: {content_type_str}"
            ) from error

        try:
            file_path = resolve_source_path(str(path))
        except PathNotAllowed as error:
            raise SourceError(self.name, str(error)) from error

        logger.info("Parsing Markdown file: %s", sanitize_for_log(str(file_path)))
        try:
            text = file_path.read_text(encoding="utf-8")
        except FileNotFoundError as error:
            raise SourceError(
                self.name, f"Markdown file not found: {file_path}"
            ) from error

        source = self.get_source_identifier(config)
        try:
            for row in _IMPORTER.parse(text, content_type):
                if isinstance(row, SkippedRow):
                    logger.warning(
                        "Skipped line %d: %s", row.number, sanitize_for_log(row.reason)
                    )
                    continue
                row.item.source = source
                yield row.item
        except ImporterError as error:
            raise SourceError(self.name, str(error)) from error
