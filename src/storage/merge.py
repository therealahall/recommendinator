"""Shared merge helpers for content-item deduplication.

These functions are used by both ``sqlite_db.SQLiteDB._merge_duplicate_into``
(runtime dedup) and ``schema._merge_duplicate_row`` (migration dedup).
Extracting them into a neutral module breaks the circular import between
``sqlite_db`` and ``schema``.
"""

import json
import re
import sqlite3
from typing import Any

from src.utils.list_merge import merge_string_lists

# ---------------------------------------------------------------------------
# SQL identifier validation
# ---------------------------------------------------------------------------

_SAFE_IDENTIFIER_RE = re.compile(r"^[a-z_][a-z0-9_]*$")


def _assert_safe_identifier(name: str) -> None:
    """Validate that *name* is a safe SQL identifier (lowercase, no spaces).

    Raises ValueError if the name does not match ``^[a-z_][a-z0-9_]*$``.
    """
    if not _SAFE_IDENTIFIER_RE.match(name):
        raise ValueError(f"Unsafe SQL identifier: {name!r}")


# ---------------------------------------------------------------------------
# JSON helpers
# ---------------------------------------------------------------------------


def _parse_json_list(raw: str | None) -> list[str]:
    """Parse a JSON array string into a Python list of strings.

    Args:
        raw: JSON array string, or None.

    Returns:
        List of strings (empty if *raw* is None or unparseable).
    """
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            return [str(item) for item in parsed]
    except (json.JSONDecodeError, TypeError):
        pass
    return []


# ---------------------------------------------------------------------------
# Detail table constants
# ---------------------------------------------------------------------------

# Detail table columns for merge operations.  Kept in sync with
# SQLiteDB._DETAIL_TABLE_CONFIG — enforced by TestDetailTableColumnsConsistency.
# Used by _merge_detail_tables so that column names are never read from the
# live database schema at runtime.
_DETAIL_TABLE_COLUMNS: dict[str, tuple[str, ...]] = {
    "book_details": (
        "author",
        "pages",
        "isbn",
        "isbn13",
        "publisher",
        "year_published",
        "genres",
        "tags",
        "description",
    ),
    "movie_details": (
        "director",
        "runtime",
        "release_year",
        "genres",
        "studio",
        "tags",
        "description",
    ),
    "tv_show_details": (
        "creators",
        "seasons",
        "episodes",
        "network",
        "release_year",
        "genres",
        "tags",
        "description",
    ),
    "video_game_details": (
        "developer",
        "publisher",
        "platforms",
        "genres",
        "release_year",
        "tags",
        "description",
    ),
}

# Derived from _DETAIL_TABLE_COLUMNS so there is no independent list to keep
# in sync.  Used by _save_detail_table to validate table names from
# _DETAIL_TABLE_CONFIG before SQL identifier interpolation.
_ALLOWED_DETAIL_TABLES: frozenset[str] = frozenset(_DETAIL_TABLE_COLUMNS.keys())

# Columns merged additively (union of both rows' lists) during dedup.
_MERGEABLE_DETAIL_COLUMNS: frozenset[str] = frozenset({"genres", "tags"})

# Columns that can only increase (e.g. TV show gaining new seasons).
_MONOTONIC_DETAIL_COLUMNS: frozenset[str] = frozenset({"seasons", "episodes"})


# ---------------------------------------------------------------------------
# Title normalisation
# ---------------------------------------------------------------------------


def normalize_title_for_matching(title: str) -> str:
    """Normalize a title for duplicate detection.

    Removes common variations to match items from different sources:
    - Lowercases
    - Removes trademark/copyright symbols (™, ®, ©)
    - Removes articles (the, a, an)
    - Removes edition/remaster suffixes
    - Converts Roman numerals to Arabic (I->1, II->2, etc.)
    - Removes punctuation and extra whitespace

    Args:
        title: Original title

    Returns:
        Normalized title for comparison
    """
    if not title:
        return ""

    normalized = title.lower().strip()

    # Remove trademark/copyright symbols early
    normalized = re.sub(r"[™®©]", "", normalized)

    # Remove common suffixes
    suffixes_to_remove = [
        r"\s*[:\-–]\s*remastered\s*$",
        r"\s*remastered\s*$",
        r"\s*[:\-–]\s*definitive edition\s*$",
        r"\s*definitive edition\s*$",
        r"\s*[:\-–]\s*game of the year edition\s*$",
        r"\s*goty edition\s*$",
        r"\s*[:\-–]\s*anniversary edition\s*$",
        r"\s*anniversary edition\s*$",
        r"\s*[:\-–]\s*special edition\s*$",
        r"\s*special edition\s*$",
        r"\s*[:\-–]\s*ultimate edition\s*$",
        r"\s*ultimate edition\s*$",
        r"\s*[:\-–]\s*complete edition\s*$",
        r"\s*complete edition\s*$",
        r"\s*[:\-–]\s*deluxe edition\s*$",
        r"\s*deluxe edition\s*$",
        r"\s*\(remastered\)\s*$",
        r"\s*\(remaster\)\s*$",
    ]

    for suffix in suffixes_to_remove:
        normalized = re.sub(suffix, "", normalized, flags=re.IGNORECASE)

    # Remove leading articles
    normalized = re.sub(r"^(the|a|an)\s+", "", normalized)

    # Convert hyphens to spaces before removing punctuation
    # This handles "Year-One" vs "Year One"
    normalized = re.sub(r"-", " ", normalized)

    # Remove punctuation except spaces
    normalized = re.sub(r"[^\w\s]", "", normalized)

    # Convert Roman numerals to Arabic (at word boundaries)
    # Order matters - check longer numerals first
    roman_map = [
        (r"\bviii\b", "8"),
        (r"\bvii\b", "7"),
        (r"\bvi\b", "6"),
        (r"\biv\b", "4"),
        (r"\bv\b", "5"),
        (r"\biii\b", "3"),
        (r"\bii\b", "2"),
        (r"\bi\b", "1"),
        (r"\bix\b", "9"),
        (r"\bx\b", "10"),
    ]
    for roman, arabic in roman_map:
        normalized = re.sub(roman, arabic, normalized)

    # Collapse whitespace
    normalized = re.sub(r"\s+", " ", normalized).strip()

    return normalized


# ---------------------------------------------------------------------------
# Merge operations
# ---------------------------------------------------------------------------


def _merge_scalar_columns(cursor: sqlite3.Cursor, keep_id: int, delete_id: int) -> None:
    """Merge rating, review, and date_completed from duplicate into kept row.

    Shared by ``SQLiteDB._merge_duplicate_into`` (runtime) and
    ``schema._merge_duplicate_row`` (migration) to avoid duplicating
    the merge rules.

    Rules:
    - rating/review: fill from duplicate only if kept is null
    - date_completed: keep the later date

    Note:
        Requires the connection to use ``row_factory = sqlite3.Row`` so
        that rows can be accessed by column name.

    Args:
        cursor: Database cursor (within an active transaction).
        keep_id: Database ID of the row to keep.
        delete_id: Database ID of the duplicate row to delete.
    """
    # Fetch both rows to determine whether the merge would produce any
    # actual data change.  We skip the UPDATE entirely when no delta exists
    # to avoid bumping updated_at (a user-facing sort key) spuriously.
    cursor.execute(
        "SELECT rating, review, date_completed FROM content_items WHERE id = ?",
        (keep_id,),
    )
    keep_row = cursor.fetchone()
    cursor.execute(
        "SELECT rating, review, date_completed FROM content_items WHERE id = ?",
        (delete_id,),
    )
    dup_row = cursor.fetchone()
    if keep_row is None or dup_row is None:
        return

    will_change_rating = keep_row["rating"] is None and dup_row["rating"] is not None
    will_change_review = keep_row["review"] is None and dup_row["review"] is not None
    will_change_date = dup_row["date_completed"] is not None and (
        keep_row["date_completed"] is None
        or dup_row["date_completed"] > keep_row["date_completed"]
    )
    if not (will_change_rating or will_change_review or will_change_date):
        return

    # Fully static parameterized query — no dynamic SQL construction.
    # The CASE expressions duplicate the will_change guards intentionally:
    # the Python guard skips the UPDATE to avoid bumping updated_at; the
    # CASE expressions ensure correct data even if the guard logic has a bug.
    cursor.execute(
        """UPDATE content_items
           SET rating = CASE WHEN rating IS NULL THEN ? ELSE rating END,
               review = CASE WHEN review IS NULL THEN ? ELSE review END,
               date_completed = CASE
                   WHEN date_completed IS NULL THEN ?
                   WHEN ? IS NOT NULL AND ? > date_completed THEN ?
                   ELSE date_completed
               END,
               updated_at = CURRENT_TIMESTAMP
           WHERE id = ?""",
        (
            dup_row["rating"],
            dup_row["review"],
            dup_row["date_completed"],
            dup_row["date_completed"],
            dup_row["date_completed"],
            dup_row["date_completed"],
            keep_id,
        ),
    )


def _merge_detail_metadata(
    keep_detail: sqlite3.Row, dup_detail: sqlite3.Row
) -> str | None:
    """Merge metadata JSON from duplicate into kept detail row.

    Returns the merged JSON string, or None if the merge should be skipped
    (e.g. duplicate has no metadata, or either side has unparseable/non-dict
    metadata — in which case we preserve the kept row's data as-is).

    Precondition: both arguments must be non-None sqlite3.Row objects.
    The caller (_merge_detail_tables) guards against None before calling.

    Merge rule: existing keys take precedence; incoming fills gaps.
    """
    assert keep_detail is not None and dup_detail is not None
    dup_meta_raw = dup_detail["metadata"]
    if dup_meta_raw is None:
        return None
    try:
        dup_meta = json.loads(dup_meta_raw)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(dup_meta, dict) or not dup_meta:
        return None  # Empty or non-dict metadata — nothing to merge

    keep_meta: dict[str, Any] = {}
    keep_meta_raw = keep_detail["metadata"]
    if keep_meta_raw is not None:
        try:
            parsed = json.loads(keep_meta_raw)
        except (json.JSONDecodeError, TypeError):
            return None  # Kept metadata unparseable — skip to avoid data loss
        if not isinstance(parsed, dict):
            return None  # Kept metadata non-dict — skip to preserve it
        keep_meta = parsed

    # Existing keys take precedence; incoming fills gaps
    merged = {**dup_meta, **keep_meta}
    return json.dumps(merged)


def _merge_detail_tables(cursor: sqlite3.Cursor, keep_id: int, delete_id: int) -> None:
    """Merge detail table rows from duplicate into kept row.

    For each detail table (book_details, movie_details, etc.):
    - If only the duplicate has a row, move it to the kept item.
    - If both have rows, merge genres/tags additively and fill nulls.
    - Metadata JSON is merged additively (existing keys preserved).

    Column names are sourced from the compile-time ``_DETAIL_TABLE_COLUMNS``
    constant — never from live database schema enumeration.

    Note:
        This function intentionally does not bump ``updated_at`` on the
        ``content_items`` row.  Detail-table changes (genres, tags, metadata)
        are internal bookkeeping from dedup — they are not user-visible edits
        and should not alter the item's modification timestamp.

    Note:
        Requires the connection to use ``row_factory = sqlite3.Row``.

    Args:
        cursor: Database cursor (within an active transaction).
        keep_id: Database ID of the row to keep.
        delete_id: Database ID of the duplicate row to delete.
    """
    for table, columns in _DETAIL_TABLE_COLUMNS.items():
        # table comes from _DETAIL_TABLE_COLUMNS.keys() (compile-time constant),
        # which is the source of _ALLOWED_DETAIL_TABLES — no runtime check needed.
        # Column names are validated individually below as defense-in-depth.
        cursor.execute(
            f"SELECT * FROM {table} WHERE content_item_id = ?",
            (keep_id,),
        )
        keep_detail = cursor.fetchone()
        cursor.execute(
            f"SELECT * FROM {table} WHERE content_item_id = ?",
            (delete_id,),
        )
        dup_detail = cursor.fetchone()
        if dup_detail is None:
            continue
        if keep_detail is None:
            # Move the duplicate's detail row to the kept item
            cursor.execute(
                f"UPDATE {table} SET content_item_id = ? WHERE content_item_id = ?",
                (keep_id, delete_id),
            )
            continue

        # Both have detail rows — merge using compile-time column list
        detail_updates: list[str] = []
        detail_params: list[Any] = []

        for col in columns:
            _assert_safe_identifier(col)
            if col in _MERGEABLE_DETAIL_COLUMNS:
                # Genres/tags: additive merge
                keep_list = _parse_json_list(keep_detail[col])
                dup_list = _parse_json_list(dup_detail[col])
                if dup_list:
                    merged = merge_string_lists(keep_list, dup_list)
                    detail_updates.append(f"{col} = ?")
                    detail_params.append(json.dumps(merged))
            elif col in _MONOTONIC_DETAIL_COLUMNS:
                # Seasons/episodes: take the higher value
                keep_val = keep_detail[col]
                dup_val = dup_detail[col]
                try:
                    if dup_val is not None and (
                        keep_val is None or int(dup_val) > int(keep_val)
                    ):
                        detail_updates.append(f"{col} = ?")
                        detail_params.append(int(dup_val))
                except (ValueError, TypeError):
                    pass  # Non-integer value — skip monotonic merge
            elif keep_detail[col] is None and dup_detail[col] is not None:
                # Fill-only: use duplicate's value if kept is null
                detail_updates.append(f"{col} = ?")
                detail_params.append(dup_detail[col])

        # Merge metadata JSON additively (existing keys preserved).
        merged_meta_json = _merge_detail_metadata(keep_detail, dup_detail)
        if merged_meta_json is not None:
            detail_updates.append("metadata = ?")
            detail_params.append(merged_meta_json)

        if detail_updates:
            detail_clause = ", ".join(detail_updates)
            detail_params.append(keep_id)
            cursor.execute(
                f"UPDATE {table} SET {detail_clause} WHERE content_item_id = ?",
                detail_params,
            )
