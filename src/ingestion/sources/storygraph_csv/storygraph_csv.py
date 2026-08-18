"""The StoryGraph CSV export plugin."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from typing import TYPE_CHECKING, Any

from src.ingestion.importers.base import ImporterError, SkippedRow
from src.ingestion.importers.storygraph_csv.storygraph_csv import StorygraphCsvImporter
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

_IMPORTER = StorygraphCsvImporter()


class StorygraphCsvPlugin(SourcePlugin):
    """Plugin for importing books from The StoryGraph CSV exports.

    The StoryGraph has no public API, so users export their library as a CSV
    from Manage Account -> Manage Your Data -> Export StoryGraph Library and
    point this plugin at the downloaded file.
    """

    default_sync_interval = "weekly"

    @property
    def name(self) -> str:
        return "storygraph_csv"

    @property
    def display_name(self) -> str:
        return "The StoryGraph (CSV Export)"

    @property
    def description(self) -> str:
        return "Import books from a The StoryGraph library CSV export"

    @property
    def content_types(self) -> list[ContentType]:
        return [ContentType.BOOK]

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
                description="Path to The StoryGraph library CSV export file",
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
        return errors

    def fetch(
        self,
        config: dict[str, Any],
        progress_callback: ProgressCallback | None = None,
    ) -> Iterator[ContentItem]:
        """Read the configured export and hand its text to the importer.

        Raises:
            SourceError: If the file cannot be read or parsed
        """
        path = config.get("path", "")

        try:
            file_path = resolve_source_path(str(path))
        except PathNotAllowed as error:
            raise SourceError(self.name, str(error)) from error

        logger.info("Parsing StoryGraph CSV file: %s", sanitize_for_log(str(file_path)))
        try:
            text = file_path.read_text(encoding="utf-8")
        except FileNotFoundError as error:
            raise SourceError(self.name, f"CSV file not found: {file_path}") from error

        source = self.get_source_identifier(config)
        try:
            for row in _IMPORTER.parse(text):
                if isinstance(row, SkippedRow):
                    logger.warning(
                        "Skipped line %d: %s", row.number, sanitize_for_log(row.reason)
                    )
                    continue
                row.item.source = source
                yield row.item
        except ImporterError as error:
            raise SourceError(self.name, str(error)) from error
