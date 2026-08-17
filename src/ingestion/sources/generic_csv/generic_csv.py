"""Generic CSV import plugin with prescriptive templates per content type."""

from __future__ import annotations

import csv
import logging
from collections.abc import Iterator, Mapping
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from src.ingestion.paths import PathNotAllowed, resolve_source_path
from src.ingestion.plugin_base import (
    ConfigField,
    ProgressCallback,
    SourceError,
    SourcePlugin,
)
from src.models.content import ConsumptionStatus, ContentItem, ContentType
from src.models.templates import (
    COMMON_COLUMNS,
    CONTENT_TYPE_COLUMNS,
    CREATOR_COLUMNS,
    CREATOR_FIELD,
    LIST_VALUED_COLUMNS,
    STATUS_MAP,
)
from src.utils.csv_formula import strip_csv_formula_guard
from src.utils.series import MAX_SEASONS
from src.utils.text import sanitize_for_log

if TYPE_CHECKING:
    from src.storage.manager import StorageManager

# Without this, the package's star re-export would republish the shared tables above.
__all__ = [
    "CsvImportPlugin",
    "parse_boolean_field",
    "parse_ignored_field",
    "parse_seasons_watched",
]

logger = logging.getLogger(__name__)


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


def parse_ignored_field(row: Mapping[str, Any]) -> bool | None:
    """Read the optional ``ignored`` column/field from an import row.

    Only a real value counts. A missing column, a blank CSV cell and a JSON
    null all mean the file says nothing about the flag, and return None —
    which tells storage to preserve whatever the user set. The blank cell
    matters because ``csv.DictReader`` puts every header key into every row,
    so exporting a library, hand-editing a few rows and re-importing would
    otherwise clear the ignore flag on every row left untouched.

    A stated ``true`` or ``false`` is returned as it reads, in both
    directions, so the same round trip can un-ignore an item on purpose.

    Args:
        row: A CSV row or JSON entry.

    Returns:
        The parsed flag, or None when the source did not state one.
    """
    value = row.get("ignored")
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    return parse_boolean_field(value)


def parse_seasons_watched(value: str | int | list[int] | None) -> list[int]:
    """Parse a seasons_watched value into a list of season numbers.

    Handles multiple formats for backward compatibility:
    - Comma-separated string "1,2,5,6" -> [1, 2, 5, 6]
    - Single integer 5 -> [1, 2, 3, 4, 5] (legacy: treated as count)
    - Array [1, 2, 5, 6] -> pass through
    - Empty/None -> []

    Season numbers outside ``1..MAX_SEASONS`` are dropped, and a count is
    capped at ``MAX_SEASONS`` so a malformed value cannot expand into an
    unbounded list.

    Args:
        value: Raw seasons_watched value

    Returns:
        Sorted list of season numbers
    """
    if value is None:
        return []

    if isinstance(value, list):
        parsed = []
        for entry in value:
            if not str(entry).strip():
                continue
            try:
                season = int(entry)
            except (ValueError, TypeError):
                continue
            if 1 <= season <= MAX_SEASONS:
                parsed.append(season)
        return sorted(parsed)

    if isinstance(value, int):
        if value <= 0:
            return []
        return list(range(1, min(value, MAX_SEASONS) + 1))

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
                    season = int(part)
                except ValueError:
                    continue
                if 1 <= season <= MAX_SEASONS:
                    seasons.append(season)
        return sorted(seasons)

    # Single number — treat as count for backward compatibility
    try:
        count = int(text)
        if count <= 0:
            return []
        return list(range(1, min(count, MAX_SEASONS) + 1))
    except ValueError:
        return []


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
        logger.info("Parsing CSV file: %s", sanitize_for_log(str(file_path)))
        expected_columns = COMMON_COLUMNS | set(
            CONTENT_TYPE_COLUMNS[content_type.value]
        )
        creator_field = CREATOR_FIELD[content_type.value]

        with open(file_path, encoding="utf-8") as csv_file:
            reader = csv.DictReader(csv_file)
            rows = [
                {
                    column: strip_csv_formula_guard(value)
                    for column, value in row.items()
                }
                for row in reader
            ]

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
                    "CSV contains unknown columns that will be ignored: %s",
                    sanitize_for_log(", ".join(sorted(unknown))),
                )

        total = len(rows)
        logger.info("Found %d entries in CSV file", total)
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
                        "Invalid date format for '%s': %s. Expected YYYY-MM-DD.",
                        sanitize_for_log(title),
                        sanitize_for_log(date_str),
                    )

            # Parse review and notes
            review = row.get("review", "").strip() or None
            notes = row.get("notes", "").strip() or None

            # Get creator (author/director/creator/developer)
            author = row.get(creator_field, "").strip() or None

            # Parse ignored flag (absent or blank leaves the stored flag alone)
            ignored = parse_ignored_field(row)

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

        logger.info("Imported %d items from CSV file", count)


def _build_metadata(row: dict[str, str], content_type: ContentType) -> dict[str, Any]:
    """Build metadata dict from type-specific CSV columns.

    Each column is stored under the metadata key the rest of the library uses
    for it, so an imported value reaches its detail-table column instead of
    the free-form metadata blob. An empty cell says nothing about the field
    and is left out entirely.

    Args:
        row: CSV row as dict
        content_type: Content type for determining which columns to extract

    Returns:
        Metadata dictionary with non-empty values
    """
    metadata: dict[str, Any] = {}
    type_columns = CONTENT_TYPE_COLUMNS[content_type.value]

    for column, metadata_key in type_columns.items():
        if column in CREATOR_COLUMNS:
            continue
        value = row.get(column, "").strip()
        if value:
            metadata[metadata_key] = [value] if column in LIST_VALUED_COLUMNS else value

    return metadata
