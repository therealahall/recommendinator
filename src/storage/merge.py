"""Shared merge helpers for content-item deduplication.

These functions are used by both ``sqlite_db.SQLiteDB._merge_duplicate_into``
(runtime dedup) and ``schema._merge_duplicate_row`` (migration dedup).
Extracting them into a neutral module breaks the circular import between
``sqlite_db`` and ``schema``.

``__all__`` is this module's contract: those names are imported by
``sqlite_db``, ``schema`` and ``derived`` and cannot be renamed or reshaped
without updating all three. Nothing else here is imported by another ``src``
module, so the underscore-prefixed names really are internal.
"""

import json
import re
import sqlite3
from typing import Any

from src.models.detail_fields import ContentTypeFields
from src.utils.dates import merge_seasons_watched_dates
from src.utils.list_merge import merge_string_lists

__all__ = [
    "ALLOWED_DETAIL_TABLES",
    "MERGEABLE_DETAIL_COLUMNS",
    "MONOTONIC_DETAIL_COLUMNS",
    "assert_known_detail_table",
    "assert_safe_identifier",
    "detail_join",
    "merge_detail_tables",
    "merge_scalar_columns",
    "normalize_title_for_matching",
    "parse_json_list",
    "resolve_status_forward",
]

# ---------------------------------------------------------------------------
# SQL identifier validation
# ---------------------------------------------------------------------------

_SAFE_IDENTIFIER_RE = re.compile(r"^[a-z_][a-z0-9_]*$")


def assert_safe_identifier(name: str) -> None:
    """Validate that *name* is a safe SQL identifier (lowercase, no spaces).

    Raises ValueError if the name does not match ``^[a-z_][a-z0-9_]*$``.
    """
    if not _SAFE_IDENTIFIER_RE.match(name):
        raise ValueError(f"Unsafe SQL identifier: {name!r}")


# ---------------------------------------------------------------------------
# JSON helpers
# ---------------------------------------------------------------------------


def parse_json_list(raw: str | None) -> list[str]:
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

# Detail table columns for merge operations.  Deliberately an independent
# hand-written list rather than a derivation of ``models.detail_fields``: it
# is the source of ALLOWED_DETAIL_TABLES, which guards every SQL identifier
# this module and sqlite_db interpolate, so it must not move with the
# declaration it checks.  TestDetailTableColumnsConsistency proves the two
# name the same columns; the order of the tuples below is not compared, because
# it only reaches the order of SET clauses in merge_detail_tables.  Used by
# merge_detail_tables so that column names are never read from the live database
# schema at runtime.
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
# in sync.  Used by SQLiteDB._save_detail_table and by the joined SELECT
# builder to validate table names from the field declaration in
# src/models/detail_fields.py before SQL identifier interpolation.
ALLOWED_DETAIL_TABLES: frozenset[str] = frozenset(_DETAIL_TABLE_COLUMNS.keys())


def assert_known_detail_table(spec: ContentTypeFields) -> None:
    """Validate the table and alias one content type's declaration names.

    Every query built from ``src/models/detail_fields`` interpolates both, so
    the check belongs beside the allow-list it reads rather than at each of
    the three query builders.

    Raises:
        ValueError: If the table is not allow-listed, or the alias is not a
            safe identifier.
    """
    if spec.table not in ALLOWED_DETAIL_TABLES:
        raise ValueError(f"Unknown detail table: {spec.table!r}")
    assert_safe_identifier(spec.table_alias)


def detail_join(spec: ContentTypeFields) -> str:
    """The LEFT JOIN one detail table contributes to a content-item query.

    Shared by the joined read in ``sqlite_db`` and the derived-column source
    select in ``derived``, which both join every detail table onto the same
    ``ci`` alias and must agree on what that join is.
    """
    assert_known_detail_table(spec)
    return (
        f"LEFT JOIN {spec.table} {spec.table_alias}"
        f" ON ci.id = {spec.table_alias}.content_item_id"
    )


# Columns merged additively (union of both rows' lists) during dedup.
MERGEABLE_DETAIL_COLUMNS: frozenset[str] = frozenset({"genres", "tags"})

# Columns that can only increase (e.g. TV show gaining new seasons).
MONOTONIC_DETAIL_COLUMNS: frozenset[str] = frozenset({"seasons", "episodes"})


# ---------------------------------------------------------------------------
# Status ordering
# ---------------------------------------------------------------------------

# Status ordering for forward-only progression.
# A status can only be overwritten by a status later in this sequence.
_STATUS_ORDER: dict[str, int] = {
    "unread": 0,
    "currently_consuming": 1,
    "completed": 2,
}


def resolve_status_forward(existing_status: str | None, incoming_status: str) -> str:
    """Return the later of two statuses (forward-only progression).

    Status can only advance: unread → currently_consuming → completed.
    A re-sync with an earlier status does not revert, and neither does a
    duplicate-row merge.

    Args:
        existing_status: Current status in the database (may be None).
        incoming_status: Status from the incoming sync or duplicate row.

    Returns:
        The resolved status string.
    """
    if existing_status is None:
        return incoming_status
    existing_order = _STATUS_ORDER.get(existing_status, 0)
    incoming_order = _STATUS_ORDER.get(incoming_status, 0)
    if incoming_order >= existing_order:
        return incoming_status
    return existing_status


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


def merge_scalar_columns(cursor: sqlite3.Cursor, keep_id: int, delete_id: int) -> None:
    """Merge the user-owned scalar columns from a duplicate into the kept row.

    Shared by ``SQLiteDB._merge_duplicate_into`` (runtime) and
    ``schema._merge_duplicate_row`` (migration) to avoid duplicating
    the merge rules.

    The duplicate row is deleted right after this runs, so every column a
    user can own has to be carried across or it is lost for good.

    Rules:
    - rating/review: fill from duplicate only if kept is null
    - date_completed: keep the later date
    - status: keep the further-advanced status (forward-only ordering)
    - ignored: an ignore on either row survives

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
    select_sql = (
        "SELECT status, rating, review, date_completed, ignored"
        " FROM content_items WHERE id = ?"
    )
    cursor.execute(select_sql, (keep_id,))
    keep_row = cursor.fetchone()
    cursor.execute(select_sql, (delete_id,))
    dup_row = cursor.fetchone()
    if keep_row is None or dup_row is None:
        return

    merged_status = resolve_status_forward(keep_row["status"], dup_row["status"])
    keep_ignored = 1 if keep_row["ignored"] else 0
    merged_ignored = 1 if (keep_ignored or dup_row["ignored"]) else 0

    will_change_rating = keep_row["rating"] is None and dup_row["rating"] is not None
    will_change_review = keep_row["review"] is None and dup_row["review"] is not None
    will_change_date = dup_row["date_completed"] is not None and (
        keep_row["date_completed"] is None
        or dup_row["date_completed"] > keep_row["date_completed"]
    )
    if not (
        will_change_rating
        or will_change_review
        or will_change_date
        or merged_status != keep_row["status"]
        or merged_ignored != keep_ignored
    ):
        return

    # Fully static parameterized query — no dynamic SQL construction.
    # The CASE expressions duplicate the will_change guards intentionally:
    # the Python guard skips the UPDATE to avoid bumping updated_at; the
    # CASE expressions ensure correct data even if the guard logic has a bug.
    # status/ignored are resolved in Python instead, since their rules are
    # not expressible in SQL without restating the status ordering.
    cursor.execute(
        """UPDATE content_items
           SET rating = CASE WHEN rating IS NULL THEN ? ELSE rating END,
               review = CASE WHEN review IS NULL THEN ? ELSE review END,
               date_completed = CASE
                   WHEN date_completed IS NULL THEN ?
                   WHEN ? IS NOT NULL AND ? > date_completed THEN ?
                   ELSE date_completed
               END,
               status = ?,
               ignored = ?,
               updated_at = CURRENT_TIMESTAMP
           WHERE id = ?""",
        (
            dup_row["rating"],
            dup_row["review"],
            dup_row["date_completed"],
            dup_row["date_completed"],
            dup_row["date_completed"],
            dup_row["date_completed"],
            merged_status,
            merged_ignored,
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
    The caller (merge_detail_tables) guards against None before calling.

    Merge rule: existing keys take precedence; incoming fills gaps — except
    ``seasons_watched_dates``, which merges per season keeping the later
    watch date (see below).
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

    # Exception: seasons_watched_dates merges per season, keeping the later
    # watch date — a season only the duplicate has a date for is folded in,
    # a season both have keeps whichever date is later, and the kept row's
    # date is never overwritten by an earlier duplicate date.
    combined_dates = merge_seasons_watched_dates(
        keep_meta.get("seasons_watched_dates"), dup_meta.get("seasons_watched_dates")
    )
    # A None result (e.g. both sides only had unparseable timestamps)
    # intentionally leaves the general blob-merge result above in place.
    if combined_dates is not None:
        merged["seasons_watched_dates"] = combined_dates

    return json.dumps(merged)


def merge_detail_tables(cursor: sqlite3.Cursor, keep_id: int, delete_id: int) -> None:
    """Merge detail table rows from duplicate into kept row.

    For each detail table (book_details, movie_details, etc.):
    - If only the duplicate has a row, move it to the kept item.
    - If both have rows, merge genres/tags additively and fill nulls.
    - Metadata JSON is merged additively (existing keys preserved), except
      ``seasons_watched_dates``, which merges per season keeping the later
      watch date across the two rows (see ``_merge_detail_metadata``).

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
        # which is the source of ALLOWED_DETAIL_TABLES — no runtime check needed.
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
            assert_safe_identifier(col)
            if col in MERGEABLE_DETAIL_COLUMNS:
                # Genres/tags: additive merge
                keep_list = parse_json_list(keep_detail[col])
                dup_list = parse_json_list(dup_detail[col])
                if dup_list:
                    merged = merge_string_lists(keep_list, dup_list)
                    detail_updates.append(f"{col} = ?")
                    detail_params.append(json.dumps(merged))
            elif col in MONOTONIC_DETAIL_COLUMNS:
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
