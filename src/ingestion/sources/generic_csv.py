"""Generic CSV import plugin with prescriptive templates per content type."""

import csv
import logging
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

logger = logging.getLogger(__name__)

# Required columns shared by all content types
COMMON_COLUMNS = {
    "title",
    "rating",
    "status",
    "date_completed",
    "review",
    "notes",
    "ignored",
}

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
    "read": ConsumptionStatus.COMPLETED,
    "watched": ConsumptionStatus.COMPLETED,
    "played": ConsumptionStatus.COMPLETED,
    "finished": ConsumptionStatus.COMPLETED,
    "in_progress": ConsumptionStatus.CURRENTLY_CONSUMING,
    "currently_consuming": ConsumptionStatus.CURRENTLY_CONSUMING,
    "reading": ConsumptionStatus.CURRENTLY_CONSUMING,
    "watching": ConsumptionStatus.CURRENTLY_CONSUMING,
    "playing": ConsumptionStatus.CURRENTLY_CONSUMING,
    "unread": ConsumptionStatus.UNREAD,
    "unwatched": ConsumptionStatus.UNREAD,
    "unplayed": ConsumptionStatus.UNREAD,
    "to_read": ConsumptionStatus.UNREAD,
    "to_watch": ConsumptionStatus.UNREAD,
    "to_play": ConsumptionStatus.UNREAD,
    "wishlist": ConsumptionStatus.UNREAD,
}

# Content-type-specific status labels for templates and exports.
# Maps (content_type, ConsumptionStatus) → display string.
STATUS_DISPLAY: dict[str, dict[str, str]] = {
    "book": {
        "completed": "read",
        "currently_consuming": "reading",
        "unread": "unread",
    },
    "movie": {
        "completed": "watched",
        "currently_consuming": "watching",
        "unread": "unwatched",
    },
    "tv_show": {
        "completed": "watched",
        "currently_consuming": "watching",
        "unread": "unwatched",
    },
    "video_game": {
        "completed": "played",
        "currently_consuming": "playing",
        "unread": "unplayed",
    },
}

# Map content type string to creator field name
CREATOR_FIELD: dict[str, str] = {
    "book": "author",
    "movie": "director",
    "tv_show": "creator",
    "video_game": "developer",
}


def parse_boolean_field(value: str | bool | int | None) -> bool:
    """Parse a boolean value from CSV or JSON input.

    Handles true/false, yes/no, 1/0, bool, int. Defaults to False.

    Args:
        value: Raw value to parse

    Returns:
        Boolean result
    """
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value != 0
    normalized = str(value).strip().lower()
    return normalized in {"true", "yes", "1"}


def parse_seasons_watched(value: str | int | list[int] | None) -> list[int]:
    """Parse a seasons_watched value into a list of season numbers.

    Handles multiple formats for backward compatibility:
    - Comma-separated string "1,2,5,6" -> [1, 2, 5, 6]
    - Single integer 5 -> [1, 2, 3, 4, 5] (legacy: treated as count)
    - Array [1, 2, 5, 6] -> pass through
    - Empty/None -> []

    Args:
        value: Raw seasons_watched value

    Returns:
        Sorted list of season numbers
    """
    if value is None:
        return []

    if isinstance(value, list):
        return sorted(int(season) for season in value if str(season).strip())

    if isinstance(value, int):
        if value <= 0:
            return []
        return list(range(1, value + 1))

    text = str(value).strip()
    if not text:
        return []

    # Check if comma-separated
    if "," in text:
        seasons = []
        for part in text.split(","):
            part = part.strip()
            if part:
                try:
                    seasons.append(int(part))
                except ValueError:
                    continue
        return sorted(seasons)

    # Single number — treat as count for backward compatibility
    try:
        count = int(text)
        if count <= 0:
            return []
        return list(range(1, count + 1))
    except ValueError:
        return []


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

        path = config.get("path")
        if not path:
            errors.append("'path' is required")
        elif not Path(path).exists():
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
        """Fetch content items from a CSV file.

        Args:
            config: Must contain 'csv_path' and 'content_type'
            progress_callback: Optional callback for progress updates

        Yields:
            ContentItem for each row in the CSV

        Raises:
            SourceError: If the file cannot be read or parsed
        """
        path = config.get("path", "")
        content_type_str = config.get("content_type", "")
        file_path = Path(path)

        try:
            content_type = ContentType(content_type_str)
        except ValueError as error:
            raise SourceError(
                self.name, f"Invalid content type: {content_type_str}"
            ) from error

        try:
            yield from self._parse_csv(
                file_path, content_type, config, progress_callback
            )
        except FileNotFoundError as error:
            raise SourceError(self.name, f"CSV file not found: {file_path}") from error
        except csv.Error as error:
            raise SourceError(self.name, f"Failed to parse CSV: {error}") from error

    def _parse_csv(
        self,
        file_path: Path,
        content_type: ContentType,
        config: dict[str, Any],
        progress_callback: ProgressCallback | None = None,
    ) -> Iterator[ContentItem]:
        """Parse a CSV file using the template for the given content type.

        Args:
            file_path: Path to the CSV file
            content_type: The content type to parse as
            config: Plugin config dict (used for source identifier resolution)
            progress_callback: Optional callback for progress updates

        Yields:
            ContentItem objects for each row
        """
        source = self.get_source_identifier(config)
        expected_columns = COMMON_COLUMNS | CONTENT_TYPE_COLUMNS.get(
            content_type.value, set()
        )
        creator_field = CREATOR_FIELD.get(content_type.value)

        with open(file_path, encoding="utf-8") as csv_file:
            reader = csv.DictReader(csv_file)
            rows = list(reader)

        if rows and reader.fieldnames:
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

        total = len(rows)
        count = 0
        for row in rows:
            title = row.get("title", "").strip()
            if not title:
                continue

            if progress_callback:
                progress_callback(count, total, title)

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

            # Parse ignored flag
            ignored = parse_boolean_field(row.get("ignored", ""))

            # Build metadata from type-specific columns
            metadata = _build_metadata(row, content_type)
            if notes:
                metadata["notes"] = notes

            # Post-process seasons_watched for TV shows
            if content_type == ContentType.TV_SHOW and "seasons_watched" in metadata:
                metadata["seasons_watched"] = parse_seasons_watched(
                    metadata["seasons_watched"]
                )

            yield ContentItem(
                title=title,
                author=author,
                content_type=content_type,
                rating=rating,
                review=review,
                status=status,
                date_completed=date_completed,
                ignored=ignored,
                metadata=metadata,
                source=source,
            )
            count += 1


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
