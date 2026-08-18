"""Row-level reading the CSV, JSON and Markdown importers share."""

from __future__ import annotations

import csv
import io
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from src.ingestion.importers.base import ImporterError
from src.utils.series import MAX_SEASONS
from src.utils.text import sanitize_for_log

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class CsvRow:
    """One record of a CSV, numbered by the file line it ends on."""

    number: int
    fields: dict[str, Any]
    #: Why the record does not fit its header, or None when it does.
    mismatch: str | None


def read_csv_rows(text: str) -> tuple[tuple[str, ...], list[CsvRow]]:
    """Read every record up front, with its line number and how it misfits."""
    # Universal newlines, as the open() this replaced used, so a CRLF export
    # parses the same whichever machine wrote it.
    reader = csv.DictReader(io.StringIO(text, newline=None))
    try:
        rows = [
            CsvRow(
                number=reader.line_num,
                fields=dict(record),
                mismatch=_header_mismatch(record, reader.fieldnames or ()),
            )
            for record in reader
        ]
    except csv.Error as error:
        raise ImporterError(f"Failed to parse CSV: {error}") from error
    return tuple(reader.fieldnames or ()), rows


def _header_mismatch(record: Mapping[Any, Any], columns: Sequence[str]) -> str | None:
    """Name a hand-edited row that lost or gained a field, so it can be fixed."""
    missing = sum(1 for column in columns if record.get(column) is None)
    if missing:
        return f"{_fields(missing)} short of the header"

    # The long row is the silent one: an unquoted comma inside a value shifts
    # every later cell a column left and parks the leftovers under the None
    # restkey, so the row imports mangled rather than crashing.
    extra = len(record.get(None) or ())
    if extra:
        return f"{_fields(extra)} more than the header"

    return None


def _fields(count: int) -> str:
    return f"{count} field" if count == 1 else f"{count} fields"


def csv_field(row: Mapping[str, Any], column: str) -> str:
    """Read a column, tolerating one the file never had."""
    return (row.get(column) or "").strip()


def normalize_rating(raw_rating: Any) -> int | None:
    """Zero and anything unparseable mean unrated; the rest clamps to 1-5."""
    if raw_rating is None:
        return None
    try:
        rating = int(raw_rating)
    except (ValueError, TypeError):
        return None
    if rating == 0:
        return None
    return max(1, min(5, rating))


def parse_completion_date(value: str, title: str) -> date | None:
    """The template's ``YYYY-MM-DD``, or None with a warning naming the row."""
    value = value.strip()
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        logger.warning(
            "Invalid date format for '%s': %s. Expected YYYY-MM-DD.",
            sanitize_for_log(title),
            sanitize_for_log(value),
        )
        return None


def parse_slashed_date(value: str) -> date | None:
    """The ``%Y/%m/%d`` both book-site exports write; anything else is no date."""
    try:
        return datetime.strptime(value.strip(), "%Y/%m/%d").date()
    except ValueError:
        return None


def parse_boolean_field(value: str | bool | int | None) -> bool:
    """Read true/false, yes/no, 1/0, bool or int. Anything else is False."""
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value != 0
    return str(value).strip().lower() in {"true", "yes", "1"}


def parse_ignored_field(row: Mapping[str, Any]) -> bool | None:
    """A missing column, a blank cell and a null all mean the file said nothing,
    which storage reads as "keep what the user set". Otherwise a re-import
    cleared the flag on every row the operator left alone.
    """
    value = row.get("ignored")
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    return parse_boolean_field(value)


def parse_seasons_watched(value: str | int | list[int] | None) -> list[int]:
    """Read a season list from an array, a comma-separated string or a count.

    Seasons outside ``1..MAX_SEASONS`` are dropped and a count is capped there,
    so a malformed cell cannot expand into an unbounded list.
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

    # A bare number is a count, which is what the field held before it took a
    # list, and an export from that era still imports.
    try:
        count = int(text)
    except ValueError:
        return []
    if count <= 0:
        return []
    return list(range(1, min(count, MAX_SEASONS) + 1))
