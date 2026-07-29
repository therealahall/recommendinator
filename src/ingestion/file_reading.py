"""Reading a user-supplied import file.

Every file-import plugin reads a file the user handed over — a web upload
written to a temp path, or a path passed to ``import --file``. That file can be
anything: a Latin-1 or UTF-16 export, a directory, a file the process cannot
open. Left alone those surface as ``UnicodeDecodeError`` / ``IsADirectoryError``
/ ``PermissionError`` and escape the ingestion pipeline as unhandled 500s, so
the readers here convert them into a ``SourceError`` the import service can
turn into a 4xx with an actionable message.
"""

from __future__ import annotations

import csv
import io
import logging
from typing import TYPE_CHECKING

from src.ingestion.plugin_base import SourceError
from src.utils.text import sanitize_for_log

if TYPE_CHECKING:
    from collections.abc import Collection
    from pathlib import Path

logger = logging.getLogger(__name__)

# utf-8-sig: Excel writes a UTF-8 BOM, which plain utf-8 would leave glued to
# the first column name or in front of the first heading. It decodes plain
# UTF-8 identically.
_IMPORT_ENCODING = "utf-8-sig"

# How many unknown header cells the warning names. A CSV may declare thousands
# of columns, so the rest are counted rather than listed.
_MAX_LOGGED_COLUMNS = 5


def read_import_text(plugin_name: str, file_path: Path, label: str) -> str:
    """Read a user-supplied file as UTF-8 text.

    Args:
        plugin_name: Plugin raising on failure, for the ``SourceError``.
        file_path: The file to read.
        label: Format name used in error messages (e.g. ``"CSV"``).

    Returns:
        The decoded file contents.

    Raises:
        SourceError: If the file is missing, unreadable, or not UTF-8 text.
    """
    try:
        return file_path.read_text(encoding=_IMPORT_ENCODING)
    except FileNotFoundError as error:
        raise SourceError(
            plugin_name, f"{label} file not found: {file_path}"
        ) from error
    except UnicodeDecodeError as error:
        raise SourceError(
            plugin_name,
            f"{label} file is not UTF-8 text. Re-export it, or re-save it as "
            f"UTF-8 (in Excel: 'CSV UTF-8'): {file_path}",
        ) from error
    except OSError as error:
        # Covers IsADirectoryError, PermissionError and every other open()
        # failure. The exception type, not its text, so a system message
        # cannot smuggle server details into the message.
        raise SourceError(
            plugin_name,
            f"{label} file could not be read ({type(error).__name__}): {file_path}",
        ) from error


def read_csv_rows(
    plugin_name: str,
    file_path: Path,
    required_columns: Collection[str] = (),
    known_columns: Collection[str] = (),
) -> list[dict[str, str]]:
    """Read a user-supplied CSV file into rows, checking its header.

    The whole file is materialised: every caller already collected the rows
    into a list before counting them, and reading the text in one step is what
    lets the decode failure be reported as a ``SourceError``.

    The header checks are skipped for a file with no data rows — an empty
    export is an ordinary outcome the import warns about, not a bad file.

    Args:
        plugin_name: Plugin raising on failure, for the ``SourceError``.
        file_path: The CSV file to read.
        required_columns: Columns the file must declare, or it is rejected.
        known_columns: Every column the caller understands. Any column outside
            this set is logged as ignored, which is how a drifted template
            shows up; pass nothing to skip the check.

    Returns:
        The parsed rows.

    Raises:
        SourceError: If the file is missing, unreadable, not UTF-8 text, not
            parseable as CSV, or missing a required column.
    """
    text = read_import_text(plugin_name, file_path, "CSV")
    # newline="": the csv module needs the line endings untranslated so a
    # quoted field spanning lines survives.
    reader = csv.DictReader(io.StringIO(text, newline=""))
    try:
        rows = list(reader)
    except csv.Error as error:
        raise SourceError(plugin_name, f"Failed to parse CSV: {error}") from error

    columns = set(reader.fieldnames or [])
    if not rows or not columns:
        return rows

    missing = set(required_columns) - columns
    if missing:
        raise SourceError(
            plugin_name, f"CSV missing required column: {', '.join(sorted(missing))}"
        )

    unknown = sorted(columns - set(known_columns))
    if known_columns and unknown:
        # A header cell is the uploaded file's own text, and CSV lets a quoted
        # one carry a newline, so each is escaped before it reaches the record
        # (CWE-117) and only the first few are named.
        logger.warning(
            "CSV contains unknown columns that will be ignored: %s (%d in total)",
            ", ".join(
                sanitize_for_log(column) for column in unknown[:_MAX_LOGGED_COLUMNS]
            ),
            len(unknown),
        )
    return rows
