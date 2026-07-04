"""The StoryGraph CSV export plugin."""

from __future__ import annotations

import csv
import logging
import math
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

# StoryGraph "Read Status" value -> consumption status. did-not-finish maps to
# completed because a rated-then-abandoned book is a real preference signal; the
# raw status is preserved in metadata so no fidelity is lost.
STATUS_MAP: dict[str, ConsumptionStatus] = {
    "read": ConsumptionStatus.COMPLETED,
    "currently-reading": ConsumptionStatus.CURRENTLY_CONSUMING,
    "to-read": ConsumptionStatus.UNREAD,
    "did-not-finish": ConsumptionStatus.COMPLETED,
}


def _field(row: dict[str, str], column: str) -> str:
    """Read a column defensively, tolerating missing or None values.

    StoryGraph tweaks its export columns over time, and short rows leave later
    columns as ``None`` under ``csv.DictReader``. Both cases collapse to "".
    """
    return (row.get(column) or "").strip()


class StorygraphCsvPlugin(SourcePlugin):
    """Plugin for importing books from The StoryGraph CSV exports.

    The StoryGraph has no public API, so users export their library as a CSV
    from Manage Account -> Manage Your Data -> Export StoryGraph Library and
    point this plugin at the downloaded file.
    """

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
        elif not Path(path).exists():
            errors.append(f"CSV file not found: {path}")
        return errors

    def fetch(
        self,
        config: dict[str, Any],
        progress_callback: ProgressCallback | None = None,
    ) -> Iterator[ContentItem]:
        """Fetch content items from a StoryGraph CSV export.

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
        """Parse a StoryGraph CSV export file.

        Args:
            file_path: Path to the StoryGraph CSV export file
            config: Plugin config dict (used for source identifier resolution)
            progress_callback: Optional callback for progress updates

        Yields:
            ContentItem objects for each book in the export
        """
        source = self.get_source_identifier(config)
        logger.info("Parsing StoryGraph CSV file: %s", file_path)

        with open(file_path, encoding="utf-8") as csv_file:
            reader = csv.DictReader(csv_file)
            rows = list(reader)

        total = len(rows)
        logger.info("Found %d entries in StoryGraph CSV file", total)
        processed_count = 0
        for row in rows:
            title = _field(row, "Title")
            if not title:
                continue

            if progress_callback:
                progress_callback(processed_count, total, title)

            author = _field(row, "Authors") or None
            rating = self._parse_rating(_field(row, "Star Rating"))

            read_status = _field(row, "Read Status").lower()
            status = STATUS_MAP.get(read_status, ConsumptionStatus.UNREAD)

            date_completed = None
            last_date_read = _field(row, "Last Date Read")
            if last_date_read:
                try:
                    date_completed = datetime.strptime(
                        last_date_read, "%Y/%m/%d"
                    ).date()
                except ValueError:
                    pass

            review = _field(row, "Review") or None
            isbn_uid = _field(row, "ISBN/UID") or None

            metadata = {
                "isbn_uid": isbn_uid,
                "contributors": _field(row, "Contributors") or None,
                "format": _field(row, "Format") or None,
                "read_status": read_status or None,
                "read_count": _field(row, "Read Count") or None,
                "date_added": _field(row, "Date Added") or None,
                "last_date_read": last_date_read or None,
                "dates_read": _field(row, "Dates Read") or None,
                "moods": _field(row, "Moods") or None,
                "pace": _field(row, "Pace") or None,
                "character_or_plot_driven": (
                    _field(row, "Character- or Plot-Driven?") or None
                ),
                "strong_character_development": (
                    _field(row, "Strong Character Development?") or None
                ),
                "loveable_characters": _field(row, "Loveable Characters?") or None,
                "diverse_characters": _field(row, "Diverse Characters?") or None,
                "flawed_characters": _field(row, "Flawed Characters?") or None,
                "content_warnings": _field(row, "Content Warnings") or None,
                "content_warning_description": (
                    _field(row, "Content Warning Description") or None
                ),
                "tags": _field(row, "Tags") or None,
                "owned": _field(row, "Owned?") or None,
            }

            yield ContentItem(
                id=isbn_uid,
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

        logger.info("Imported %d items from StoryGraph CSV file", processed_count)

    @staticmethod
    def _parse_rating(raw_rating: str) -> int | None:
        """Convert a StoryGraph 0-5 fractional star rating to an int 1-5.

        StoryGraph rates in quarter-star steps (e.g. ``4.5``, ``3.25``). The
        value is rounded half up and clamped to 1-5. A ``0``, blank, or
        unparseable rating means unrated and returns ``None``.

        This deliberately does not delegate to the base ``normalize_rating``:
        that helper does not round half up (it ``int()``-truncates, dropping
        the fractional star) and clamps negative values up to 1 instead of
        treating them as unrated. Do not "simplify" it back to the base helper.
        """
        if not raw_rating:
            return None
        try:
            value = float(raw_rating)
        except ValueError:
            return None
        if not math.isfinite(value):
            return None
        rounded = math.floor(value + 0.5)
        if rounded <= 0:
            return None
        return min(5, rounded)
