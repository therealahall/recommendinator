"""Markdown import plugin using a prescriptive list format per content type."""

import logging
import re
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

# Map section headings to consumption status
SECTION_STATUS_MAP: dict[str, ConsumptionStatus] = {
    "completed": ConsumptionStatus.COMPLETED,
    "in progress": ConsumptionStatus.CURRENTLY_CONSUMING,
    "currently reading": ConsumptionStatus.CURRENTLY_CONSUMING,
    "currently watching": ConsumptionStatus.CURRENTLY_CONSUMING,
    "currently playing": ConsumptionStatus.CURRENTLY_CONSUMING,
    "to read": ConsumptionStatus.UNREAD,
    "to watch": ConsumptionStatus.UNREAD,
    "to play": ConsumptionStatus.UNREAD,
    "wishlist": ConsumptionStatus.UNREAD,
    "backlog": ConsumptionStatus.UNREAD,
}

# Regex for parsing list items:
# - **Title** by Creator | Rating: N | Date: YYYY-MM-DD
_ITEM_PATTERN = re.compile(
    r"^[-*]\s+"  # List marker (- or *)
    r"\*\*(.+?)\*\*"  # **Title** (required)
    r"(?:\s+by\s+(.+?))??"  # by Creator (optional, lazy)
    r"(?:\s*\|\s*(.+))?"  # | metadata tail (optional)
    r"\s*$"
)

# Pattern for extracting key:value pairs from the metadata tail
_METADATA_PAIR_PATTERN = re.compile(r"(\w+)\s*:\s*(.+)")


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
                description="Path to Markdown file in the prescribed format",
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
        """Fetch content items from a Markdown file.

        Args:
            config: Must contain 'markdown_path' and 'content_type'
            progress_callback: Optional callback for progress updates

        Yields:
            ContentItem for each list entry in the file

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

        logger.info(f"Parsing Markdown file: {file_path}")

        try:
            content = file_path.read_text(encoding="utf-8")
        except FileNotFoundError as error:
            raise SourceError(
                self.name, f"Markdown file not found: {file_path}"
            ) from error

        yield from _parse_markdown(
            content, content_type, self.get_source_identifier(config), progress_callback
        )


def _parse_markdown(
    content: str,
    content_type: ContentType,
    source: str,
    progress_callback: ProgressCallback | None = None,
) -> Iterator[ContentItem]:
    """Parse a Markdown file into ContentItem objects.

    Args:
        content: Full markdown content
        content_type: Content type to assign to all items
        source: Source identifier
        progress_callback: Optional callback for progress updates

    Yields:
        ContentItem objects
    """
    # Pre-scan to count items so we can report a real total
    total = sum(1 for line in content.splitlines() if _ITEM_PATTERN.match(line.strip()))
    logger.info(f"Found {total} entries in Markdown file")

    current_status = ConsumptionStatus.UNREAD
    count = 0

    for line in content.splitlines():
        stripped = line.strip()

        # Check for ## heading (status section)
        if stripped.startswith("## "):
            heading_text = stripped[3:].strip().lower()
            matched_status = _match_section_status(heading_text)
            if matched_status is not None:
                current_status = matched_status
            continue

        # Check for list item
        if not stripped.startswith(("- ", "* ")):
            continue

        match = _ITEM_PATTERN.match(stripped)
        if not match:
            continue

        title = match.group(1).strip()
        if not title:
            continue

        creator = match.group(2)
        if creator:
            creator = creator.strip()

        # Parse metadata from the tail (everything after the first |)
        metadata_tail = match.group(3) or ""
        parsed_metadata = _parse_metadata_tail(metadata_tail)

        # Extract rating
        rating = None
        rating_str = parsed_metadata.pop("rating", None)
        if rating_str:
            try:
                rating_val = int(rating_str)
                if rating_val == 0:
                    rating = None
                else:
                    rating = max(1, min(5, rating_val))
            except ValueError:
                pass

        # Extract date
        date_completed = None
        date_str = parsed_metadata.pop("date", None)
        if date_str:
            try:
                date_completed = datetime.strptime(date_str.strip(), "%Y-%m-%d").date()
            except ValueError:
                logger.warning(
                    f"Invalid date format for '{title}': {date_str}. "
                    "Expected YYYY-MM-DD."
                )

        if progress_callback:
            progress_callback(count, total, title)

        yield ContentItem(
            title=title,
            author=creator or None,
            content_type=content_type,
            rating=rating,
            status=current_status,
            date_completed=date_completed,
            metadata=parsed_metadata if parsed_metadata else {},
            source=source,
        )
        count += 1

    logger.info(f"Imported {count} items from Markdown file")


def _match_section_status(heading_text: str) -> ConsumptionStatus | None:
    """Match a section heading to a consumption status.

    Args:
        heading_text: Lowercase heading text (without ##)

    Returns:
        Matched ConsumptionStatus or None if not recognized
    """
    for keyword, status in SECTION_STATUS_MAP.items():
        if keyword in heading_text:
            return status
    return None


def _parse_metadata_tail(tail: str) -> dict[str, str]:
    """Parse the pipe-separated metadata tail of a list item.

    Format: Rating: 5 | Date: 2024-06-15 | Key: Value

    Args:
        tail: The metadata string after the first pipe

    Returns:
        Dict of key-value pairs (keys lowercased)
    """
    result: dict[str, str] = {}
    if not tail:
        return result

    parts = tail.split("|")
    for raw_part in parts:
        part = raw_part.strip()
        match = _METADATA_PAIR_PATTERN.match(part)
        if match:
            key = match.group(1).strip().lower()
            value = match.group(2).strip()
            result[key] = value

    return result
