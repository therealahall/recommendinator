"""Generic CSV import plugin with prescriptive templates per content type."""

import csv
import logging
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any

from src.ingestion.plugin_base import ConfigField, SourceError, SourcePlugin
from src.models.content import ConsumptionStatus, ContentItem, ContentType

logger = logging.getLogger(__name__)

# Required columns shared by all content types
COMMON_COLUMNS = {"title", "rating", "status", "date_completed", "review", "notes"}

# Additional columns per content type
CONTENT_TYPE_COLUMNS: dict[str, set[str]] = {
    "book": {"author", "isbn", "pages", "year_published", "genre"},
    "movie": {"director", "year", "runtime_minutes", "genre"},
    "tv_show": {"creator", "seasons_watched", "total_seasons", "year", "genre"},
    "video_game": {"developer", "platform", "genre", "hours_played"},
}

# Status string mapping
STATUS_MAP: dict[str, ConsumptionStatus] = {
    "completed": ConsumptionStatus.COMPLETED,
    "in_progress": ConsumptionStatus.CURRENTLY_CONSUMING,
    "currently_consuming": ConsumptionStatus.CURRENTLY_CONSUMING,
    "unread": ConsumptionStatus.UNREAD,
    "to_read": ConsumptionStatus.UNREAD,
    "to_watch": ConsumptionStatus.UNREAD,
    "to_play": ConsumptionStatus.UNREAD,
    "wishlist": ConsumptionStatus.UNREAD,
}

# Map content type string to creator field name
CREATOR_FIELD: dict[str, str] = {
    "book": "author",
    "movie": "director",
    "tv_show": "creator",
    "video_game": "developer",
}


class CsvImportPlugin(SourcePlugin):
    """Plugin for importing content from CSV files using prescriptive templates.

    Each content type has a fixed column template. Users adapt their data
    to match the template. Template files are available in the templates/ directory.
    """

    @property
    def name(self) -> str:
        return "csv_import"

    @property
    def display_name(self) -> str:
        return "CSV Import"

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
                name="csv_path",
                field_type=str,
                required=True,
                description="Path to CSV file matching the template for the content type",
            ),
            ConfigField(
                name="content_type",
                field_type=str,
                required=True,
                description="Content type: book, movie, tv_show, or video_game",
            ),
        ]

    def validate_config(self, config: dict[str, Any]) -> list[str]:
        errors = []

        csv_path = config.get("csv_path")
        if not csv_path:
            errors.append("'csv_path' is required")
        elif not Path(csv_path).exists():
            errors.append(f"CSV file not found: {csv_path}")

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

    def fetch(self, config: dict[str, Any]) -> Iterator[ContentItem]:
        """Fetch content items from a CSV file.

        Args:
            config: Must contain 'csv_path' and 'content_type'

        Yields:
            ContentItem for each row in the CSV

        Raises:
            SourceError: If the file cannot be read or parsed
        """
        csv_path = config.get("csv_path", "")
        content_type_str = config.get("content_type", "")
        file_path = Path(csv_path)

        try:
            content_type = ContentType(content_type_str)
        except ValueError as error:
            raise SourceError(
                self.name, f"Invalid content type: {content_type_str}"
            ) from error

        try:
            yield from self._parse_csv(file_path, content_type)
        except FileNotFoundError as error:
            raise SourceError(
                self.name, f"CSV file not found: {file_path}"
            ) from error
        except csv.Error as error:
            raise SourceError(self.name, f"Failed to parse CSV: {error}") from error

    def _parse_csv(
        self, file_path: Path, content_type: ContentType
    ) -> Iterator[ContentItem]:
        """Parse a CSV file using the template for the given content type.

        Args:
            file_path: Path to the CSV file
            content_type: The content type to parse as

        Yields:
            ContentItem objects for each row
        """
        source = self.get_source_identifier()
        expected_columns = COMMON_COLUMNS | CONTENT_TYPE_COLUMNS.get(
            content_type.value, set()
        )
        creator_field = CREATOR_FIELD.get(content_type.value)

        with open(file_path, encoding="utf-8") as csv_file:
            reader = csv.DictReader(csv_file)

            if reader.fieldnames:
                actual_columns = set(reader.fieldnames)
                missing = {"title"} - actual_columns
                if missing:
                    raise SourceError(
                        self.name,
                        f"CSV missing required column: {', '.join(sorted(missing))}",
                    )

                unknown = actual_columns - expected_columns
                if unknown:
                    logger.warning(
                        f"CSV contains unknown columns that will be ignored: "
                        f"{', '.join(sorted(unknown))}"
                    )

            for row in reader:
                title = row.get("title", "").strip()
                if not title:
                    continue

                # Parse rating (1-5 integer, empty = None)
                rating = self.normalize_rating(row.get("rating", "").strip() or None)

                # Parse status
                status_str = row.get("status", "").strip().lower()
                status = STATUS_MAP.get(status_str, ConsumptionStatus.UNREAD)

                # Parse date completed
                date_completed = None
                date_str = row.get("date_completed", "").strip()
                if date_str:
                    try:
                        date_completed = datetime.strptime(date_str, "%Y-%m-%d").date()
                    except ValueError:
                        logger.warning(
                            f"Invalid date format for '{title}': {date_str}. "
                            "Expected YYYY-MM-DD."
                        )

                # Parse review and notes
                review = row.get("review", "").strip() or None
                notes = row.get("notes", "").strip() or None

                # Get creator (author/director/creator/developer)
                author = None
                if creator_field:
                    author = row.get(creator_field, "").strip() or None

                # Build metadata from type-specific columns
                metadata = _build_metadata(row, content_type)
                if notes:
                    metadata["notes"] = notes

                yield ContentItem(
                    title=title,
                    author=author,
                    content_type=content_type,
                    rating=rating,
                    review=review,
                    status=status,
                    date_completed=date_completed,
                    metadata=metadata,
                    source=source,
                )


def _build_metadata(row: dict[str, str], content_type: ContentType) -> dict[str, Any]:
    """Build metadata dict from type-specific CSV columns.

    Args:
        row: CSV row as dict
        content_type: Content type for determining which columns to extract

    Returns:
        Metadata dictionary with non-empty values
    """
    metadata: dict[str, Any] = {}
    type_columns = CONTENT_TYPE_COLUMNS.get(content_type.value, set())

    # Common metadata fields to skip (already in ContentItem fields)
    skip_fields = {"author", "director", "creator", "developer"}

    for column in type_columns:
        if column in skip_fields:
            continue
        value = row.get(column, "").strip()
        if value:
            metadata[column] = value

    return metadata
