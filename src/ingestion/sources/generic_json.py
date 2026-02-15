"""Generic JSON/JSONL import plugin with prescriptive templates per content type."""

import json
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
from src.ingestion.sources.generic_csv import (
    CONTENT_TYPE_COLUMNS,
    CREATOR_FIELD,
    STATUS_MAP,
    parse_boolean_field,
    parse_seasons_watched,
)
from src.models.content import ConsumptionStatus, ContentItem, ContentType

logger = logging.getLogger(__name__)


class JsonImportPlugin(SourcePlugin):
    """Plugin for importing content from JSON or JSONL files.

    Supports both .json (array of objects) and .jsonl (one object per line).
    Each content type uses the same field names as the CSV templates.
    Template files are available in the templates/ directory.
    """

    @property
    def name(self) -> str:
        return "json_import"

    @property
    def display_name(self) -> str:
        return "JSON Import"

    @property
    def description(self) -> str:
        return "Import from JSON/JSONL file"

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
                description="Path to JSON or JSONL file matching the template",
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
            errors.append(f"JSON file not found: {path}")

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
        """Fetch content items from a JSON or JSONL file.

        Args:
            config: Must contain 'json_path' and 'content_type'
            progress_callback: Optional callback for progress updates

        Yields:
            ContentItem for each entry in the file

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
            entries = _load_json_or_jsonl(file_path)
        except FileNotFoundError as error:
            raise SourceError(self.name, f"JSON file not found: {file_path}") from error
        except (json.JSONDecodeError, ValueError) as error:
            raise SourceError(self.name, f"Failed to parse JSON: {error}") from error

        yield from _parse_entries(
            entries,
            content_type,
            self.get_source_identifier(config),
            progress_callback,
        )


def _load_json_or_jsonl(file_path: Path) -> list[dict[str, Any]]:
    """Load entries from a JSON array or JSONL file.

    Args:
        file_path: Path to the file

    Returns:
        List of entry dictionaries

    Raises:
        FileNotFoundError: If file doesn't exist
        json.JSONDecodeError: If JSON is malformed
        ValueError: If file format is invalid
    """
    content = file_path.read_text(encoding="utf-8").strip()

    if not content:
        return []

    # Try JSON array first
    if content.startswith("["):
        data = json.loads(content)
        if not isinstance(data, list):
            raise ValueError("JSON file must contain an array of objects")
        return list(data)

    # Try JSONL (one object per line)
    entries: list[dict[str, Any]] = []
    for line_number, line in enumerate(content.splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError(f"Invalid JSON on line {line_number}: {error}") from error
        if not isinstance(entry, dict):
            raise ValueError(
                f"Line {line_number} must be a JSON object, not {type(entry).__name__}"
            )
        entries.append(entry)

    return entries


def _parse_entries(
    entries: list[dict[str, Any]],
    content_type: ContentType,
    source: str,
    progress_callback: ProgressCallback | None = None,
) -> Iterator[ContentItem]:
    """Parse JSON entries into ContentItem objects.

    Args:
        entries: List of entry dictionaries
        content_type: The content type to parse as
        source: Source identifier for the items
        progress_callback: Optional callback for progress updates

    Yields:
        ContentItem objects
    """
    creator_field = CREATOR_FIELD.get(content_type.value)
    total = len(entries)
    count = 0

    for entry in entries:
        title = str(entry.get("title", "")).strip()
        if not title:
            continue

        if progress_callback:
            progress_callback(count, total, title)

        # Parse rating
        raw_rating = entry.get("rating")
        rating = _normalize_json_rating(raw_rating)

        # Parse status
        status_str = str(entry.get("status", "")).strip().lower()
        status = STATUS_MAP.get(status_str, ConsumptionStatus.UNREAD)

        # Parse date completed
        date_completed = None
        date_str = str(entry.get("date_completed", "")).strip()
        if date_str:
            try:
                date_completed = datetime.strptime(date_str, "%Y-%m-%d").date()
            except ValueError:
                logger.warning(
                    f"Invalid date format for '{title}': {date_str}. "
                    "Expected YYYY-MM-DD."
                )

        # Parse review and notes
        review = str(entry.get("review", "")).strip() or None
        notes = str(entry.get("notes", "")).strip() or None

        # Get creator
        author = None
        if creator_field:
            author = str(entry.get(creator_field, "")).strip() or None

        # Parse ignored flag
        ignored = parse_boolean_field(entry.get("ignored"))

        # Build metadata from type-specific fields
        metadata = _build_json_metadata(entry, content_type)
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


def _normalize_json_rating(raw_rating: Any) -> int | None:
    """Normalize a JSON rating value to 1-5 or None.

    Args:
        raw_rating: Raw rating from JSON (int, float, str, or None)

    Returns:
        Normalized rating (1-5) or None
    """
    if raw_rating is None:
        return None

    try:
        rating = int(raw_rating)
        if rating == 0:
            return None
        return max(1, min(5, rating))
    except (ValueError, TypeError):
        return None


def _build_json_metadata(
    entry: dict[str, Any], content_type: ContentType
) -> dict[str, Any]:
    """Build metadata dict from type-specific JSON fields.

    Args:
        entry: JSON entry dict
        content_type: Content type for determining which fields to extract

    Returns:
        Metadata dictionary with non-empty values
    """
    metadata: dict[str, Any] = {}
    type_columns = CONTENT_TYPE_COLUMNS.get(content_type.value, set())

    # Fields that map to ContentItem fields directly
    skip_fields = {"author", "director", "creator", "developer"}

    for column in type_columns:
        if column in skip_fields:
            continue
        value = entry.get(column)
        if value is not None and str(value).strip():
            metadata[column] = value

    return metadata
