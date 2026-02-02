"""Goodreads CSV export plugin."""

import csv
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any

from src.ingestion.plugin_base import (
    ConfigField,
    ProgressCallback,
    SourceError,
    SourcePlugin,
)
from src.models.content import ConsumptionStatus, ContentItem, ContentType


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

    @classmethod
    def transform_config(cls, raw_config: dict[str, Any]) -> dict[str, Any]:
        """Map YAML ``path`` key to the ``csv_path`` key expected by fetch."""
        path = raw_config.get("path", "inputs/goodreads_library_export.csv")
        return {"csv_path": str(path)}

    def get_config_schema(self) -> list[ConfigField]:
        return [
            ConfigField(
                name="csv_path",
                field_type=str,
                required=True,
                description="Path to Goodreads CSV export file",
            ),
        ]

    def validate_config(self, config: dict[str, Any]) -> list[str]:
        errors = []
        csv_path = config.get("csv_path")
        if not csv_path:
            errors.append("'csv_path' is required")
        elif not Path(csv_path).exists():
            errors.append(f"CSV file not found: {csv_path}")
        return errors

    def fetch(
        self,
        config: dict[str, Any],
        progress_callback: ProgressCallback | None = None,
    ) -> Iterator[ContentItem]:
        """Fetch content items from a Goodreads CSV export.

        Args:
            config: Must contain 'csv_path' pointing to the CSV file
            progress_callback: Optional callback for progress updates

        Yields:
            ContentItem for each book in the export

        Raises:
            SourceError: If the file cannot be read or parsed
        """
        csv_path = config.get("csv_path", "")
        file_path = Path(csv_path)

        try:
            yield from self._parse_csv(file_path, progress_callback)
        except FileNotFoundError as error:
            raise SourceError(self.name, f"CSV file not found: {file_path}") from error
        except csv.Error as error:
            raise SourceError(self.name, f"Failed to parse CSV: {error}") from error

    def _parse_csv(
        self,
        file_path: Path,
        progress_callback: ProgressCallback | None = None,
    ) -> Iterator[ContentItem]:
        """Parse a Goodreads CSV export file.

        Args:
            file_path: Path to the Goodreads CSV export file
            progress_callback: Optional callback for progress updates

        Yields:
            ContentItem objects for each book in the export
        """
        source = self.get_source_identifier()

        with open(file_path, encoding="utf-8") as csv_file:
            reader = csv.DictReader(csv_file)
            rows = list(reader)

        total = len(rows)
        count = 0
        for row in rows:
            title = row.get("Title", "").strip()
            if not title:
                continue

            if progress_callback:
                progress_callback(count, total, title)

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
            count += 1
