"""Generic CSV import plugin with prescriptive templates per content type."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from typing import TYPE_CHECKING, Any

from src.ingestion.importers.base import ImporterError, SkippedRow
from src.ingestion.importers.generic_csv.generic_csv import CsvImporter
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

# Without this, the package's star re-export would republish every import above.
__all__ = ["CsvImportPlugin"]

logger = logging.getLogger(__name__)

_IMPORTER = CsvImporter()


class CsvImportPlugin(SourcePlugin):
    """Plugin for importing content from CSV files using prescriptive templates.

    Each content type has a fixed column template. Users adapt their data
    to match the template. Template files are available in the templates/ directory.
    """

    default_sync_interval = "weekly"

    @property
    def name(self) -> str:
        return "csv_import"

    @property
    def display_name(self) -> str:
        return "CSV Import"

    @property
    def description(self) -> str:
        return "Import from CSV file"

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
                description="Path to CSV file matching the template for the content type",
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
                    errors.append(f"CSV file not found: {path}")

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
        """Read the configured CSV and hand its text to the importer.

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

        logger.info("Parsing CSV file: %s", sanitize_for_log(str(file_path)))
        try:
            text = file_path.read_text(encoding="utf-8")
        except FileNotFoundError as error:
            raise SourceError(self.name, f"CSV file not found: {file_path}") from error

        source = self.get_source_identifier(config)
        try:
            for row in _IMPORTER.parse(text, content_type):
                if isinstance(row, SkippedRow):
                    logger.warning(
                        "Skipped row %d: %s", row.number, sanitize_for_log(row.reason)
                    )
                    continue
                row.item.source = source
                yield row.item
        except ImporterError as error:
            raise SourceError(self.name, str(error)) from error
