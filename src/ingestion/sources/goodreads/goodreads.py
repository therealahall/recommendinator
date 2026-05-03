"""Goodreads CSV export plugin."""

from __future__ import annotations

import csv
import logging
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from src.ingestion.plugin_base import (
    ConfigField,
    ProgressCallback,
    SourceError,
    SourcePlugin,
)
from src.models.content import ConsumptionStatus, ContentItem, ContentType

if TYPE_CHECKING:
    from src.storage.manager import StorageManager

logger = logging.getLogger(__name__)


class GoodreadsPlugin(SourcePlugin):
    """Plugin for importing books from Goodreads CSV exports.

    Goodreads allows exporting your library as a CSV file from:
    https://www.goodreads.com/review/import

    The export includes title, author, rating, shelves, dates, and more.
    """

    @property
    def name(self) -> str:
        return "goodreads"

    @property
    def display_name(self) -> str:
        return "Goodreads"

    @property
    def description(self) -> str:
        return "Import books from Goodreads export"

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
                description="Path to Goodreads CSV export file",
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
        elif not Path(path).exists():
            errors.append(f"CSV file not found: {path}")
        return errors

    def fetch(
        self,
        config: dict[str, Any],
        progress_callback: ProgressCallback | None = None,
    ) -> Iterator[ContentItem]:
        """Fetch content items from a Goodreads CSV export.

        Args:
            config: Must contain 'path' pointing to the CSV file
            progress_callback: Optional callback for progress updates

        Yields:
            ContentItem for each book in the export

        Raises:
            SourceError: If the file cannot be read or parsed
        """
        path = config.get("path", "")
        file_path = Path(path)

        try:
            yield from self._parse_csv(file_path, config, progress_callback)
        except FileNotFoundError as error:
            raise SourceError(self.name, f"CSV file not found: {file_path}") from error
        except csv.Error as error:
            raise SourceError(self.name, f"Failed to parse CSV: {error}") from error

    def _parse_csv(
        self,
        file_path: Path,
        config: dict[str, Any],
        progress_callback: ProgressCallback | None = None,
    ) -> Iterator[ContentItem]:
        """Parse a Goodreads CSV export file.

        Args:
            file_path: Path to the Goodreads CSV export file
            config: Plugin config dict (used for source identifier resolution)
            progress_callback: Optional callback for progress updates

        Yields:
            ContentItem objects for each book in the export
        """
        source = self.get_source_identifier(config)
        logger.info("Parsing Goodreads CSV file: %s", file_path)

        with open(file_path, encoding="utf-8") as csv_file:
            reader = csv.DictReader(csv_file)
            rows = list(reader)

        total = len(rows)
        logger.info("Found %d entries in Goodreads CSV file", total)
        processed_count = 0
        for row in rows:
            title = row.get("Title", "").strip()
            if not title:
                continue

            if progress_callback:
                progress_callback(processed_count, total, title)

            author = row.get("Author", "").strip() or None

            # Parse rating (0 means unrated/unread)
            rating_str = row.get("My Rating", "0").strip()
            try:
                rating = int(rating_str) if rating_str and rating_str != "0" else None
            except ValueError:
                rating = None

            # Parse status from Exclusive Shelf
            shelf = row.get("Exclusive Shelf", "").strip().lower()
            if shelf == "read":
                status = ConsumptionStatus.COMPLETED
            elif shelf == "currently-reading":
                status = ConsumptionStatus.CURRENTLY_CONSUMING
            else:  # to-read or empty
                status = ConsumptionStatus.UNREAD

            # Parse date read
            date_read_str = row.get("Date Read", "").strip()
            date_completed = None
            if date_read_str:
                try:
                    date_completed = datetime.strptime(date_read_str, "%Y/%m/%d").date()
                except ValueError:
                    pass

            # Extract review
            review = row.get("My Review", "").strip() or None

            # Extract additional metadata
            metadata = {
                "book_id": row.get("Book Id", "").strip(),
                "isbn": row.get("ISBN", "").strip() or None,
                "isbn13": row.get("ISBN13", "").strip() or None,
                "pages": row.get("Number of Pages", "").strip() or None,
                "year_published": row.get("Year Published", "").strip() or None,
                "publisher": row.get("Publisher", "").strip() or None,
            }

            yield ContentItem(
                id=metadata.get("book_id"),
                title=title,
                author=author,
                content_type=ContentType.BOOK,
                rating=rating,
                review=review,
                status=status,
                date_completed=date_completed,
                metadata=metadata,
                source=source,
            )
            processed_count += 1

        logger.info("Imported %d items from Goodreads CSV file", processed_count)
