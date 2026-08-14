"""SQLite database manager for content items.

Rating, review, status, ``date_completed`` and ``ignored`` are user-owned, and
every write to them goes through one of four doors — one sync door and three
explicit-user-action doors. The sync door's rules are not uniform across the
five fields, so read the field you care about rather than a summary of the
door:

- :meth:`SQLiteDB.save_content_item` is the ingestion/sync door. ``rating``
  and ``review`` are fill-only, written only while the stored value is empty,
  so a re-import can never erase either. A blank incoming ``review`` does not
  count as a value to fill from — stored, it would be indistinguishable from
  one the user wrote and would refuse every later value. The rest are weaker.
  ``status`` is forward-only — :func:`resolve_status_forward` never resolves
  backward — but "a sync cannot revert a completion" holds only of that
  resolution: after the upsert, :meth:`_handle_tv_season_change` regresses a
  completed TV show to currently_consuming when the sync raises its season
  count above the seasons the user has checked off, because new seasons mean
  the show is not finished.
  ``date_completed`` is later-date-wins. ``ignored`` counts only a stated
  value, where a real ``True`` or ``False`` wins in either direction, so an
  exported, edited, re-imported library round-trips, while ``None`` — what a
  source sends when the file says nothing about the flag — leaves the stored
  value alone. That last decision is the door's, taken from the value it is
  handed, so a plugin that says nothing cannot clear the user's ignore list by
  accident. It also means the round trip is wholesale rather than selective:
  ``src/utils/export.py`` writes a concrete ``true``/``false`` on every row it
  exports, so re-importing an export states the flag for every item and
  replaces the ignore list with the state it had at export time. The
  blank-cell rule protects a hand-maintained file; it protects nothing about
  this project's own exports.
- The explicit-user-action doors write exactly the fields the caller supplied
  and may overwrite them freely: :meth:`SQLiteDB.update_item_from_ui` for an
  edit (web UI, CLI), :meth:`SQLiteDB.complete_content_item` for a
  completion, which creates the item first when the library does not have it
  yet, and :meth:`SQLiteDB.set_item_ignored`, which writes ``ignored`` alone,
  in either direction. What "not supplied" looks like is not uniform either:
  :meth:`SQLiteDB.update_item_from_ui` spells it three ways — ``status`` is
  required, ``rating`` and ``review`` use :data:`UNSET` because ``None`` has
  to mean "clear it", and the remaining fields use ``None``. Read that
  method's docstring for the argument you are passing.

No door stores a blank ``review``, whichever one it arrives at, because a
stored ``""`` is indistinguishable from one the user wrote and would refuse
every later import for that column. What each door does with one differs, and
follows from what that door is for: the sync door declines to fill from it,
:meth:`SQLiteDB._write_completion` drops it and leaves the stored review
alone — a completion has nothing to clear — and
:meth:`SQLiteDB.update_item_from_ui` clears the column, because that door
exists to overwrite and an emptied review box is a clear.

``date_completed`` is the field no door replaces *silently*: the sync door
takes an incoming date only when it is later than the stored one, and a user
action replaces a stored date only when the caller names one. A completion
carrying no date fills an empty column with today in the host's zone and
leaves a date the item already carries as it is — "I finished this" is not "I
finished this today". A named date is written as given — a correction pointing
backwards is still a correction — provided it is a day that has happened; see
:data:`MAX_COMPLETION_DATE_SKEW`.

**That skew guard is the completion door's alone.** :meth:`SQLiteDB._upsert_content_item`
writes whatever date the source gave it, so an import carrying 2099 lands. It
is a mirror of somebody else's library and one bad row must not fail the sync,
which is what raising from inside a sync would do.
"""

import json
import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, TypeVar

from src.models.content import (
    ConsumptionStatus,
    ContentItem,
    ContentType,
    EnrichmentFilter,
    get_enum_value,
)
from src.models.detail_fields import (
    DETAIL_FIELDS,
    ContentTypeFields,
    FieldKind,
    to_int,
)
from src.storage.derived import write_derived_columns
from src.storage.merge import (
    ALLOWED_DETAIL_TABLES,
    MERGEABLE_DETAIL_COLUMNS,
    MONOTONIC_DETAIL_COLUMNS,
    assert_known_detail_table,
    assert_safe_identifier,
    detail_join,
    merge_detail_tables,
    merge_scalar_columns,
    normalize_title_for_matching,
    parse_json_list,
    resolve_status_forward,
)
from src.storage.schema import (
    create_schema,
    get_default_user_id,
    write_enrichment_complete,
)
from src.utils.dates import local_today, merge_seasons_watched_dates, utc_now
from src.utils.list_merge import merge_string_lists
from src.utils.sorting import normalize_for_search, search_text_matches


class Unset(Enum):
    """Type of the :data:`UNSET` sentinel.

    A single-member enum rather than a bare object so that ``mypy`` narrows
    ``value is not UNSET`` to the argument's real type.
    """

    UNSET = "unset"


#: Marks an argument the caller did not supply, which ``None`` cannot mean
#: on a nullable field: ``None`` clears the value, ``UNSET`` leaves it alone.
UNSET = Unset.UNSET

#: A caller a zone ahead of the server calls tomorrow "today". Further ahead is
#: a day nobody has lived, and an item dated there heads the variety ladder
#: until the date arrives.
MAX_COMPLETION_DATE_SKEW = timedelta(days=1)


class FutureCompletionDateError(ValueError):
    """A completion dated past :data:`MAX_COMPLETION_DATE_SKEW`.

    Distinct from a bare ``ValueError`` so a caller naming a date can tell
    this refusal from a malformed one and say which it hit.
    """

    def __init__(self) -> None:
        super().__init__("A completion date cannot be in the future.")


_T = TypeVar("_T")


def unset_if_none(value: _T | None) -> _T | Unset:
    """Translate a caller's "not supplied" ``None`` into :data:`UNSET`.

    For surfaces whose absence *is* ``None`` and which therefore cannot ask
    for a clear this way — a Click option nobody passed. A surface that can
    tell absent from null (the web, which
    reads ``model_fields_set``) passes its ``None`` through untranslated, so
    an explicit null still clears the field.

    Args:
        value: The value the caller supplied, or None if they supplied none.

    Returns:
        The value, or UNSET when it was None.
    """
    return UNSET if value is None else value


# Chunk size for IN clauses, staying well within SQLite's
# SQLITE_LIMIT_VARIABLE_NUMBER default of 999.
_IN_CLAUSE_CHUNK_SIZE = 500

# ORDER BY clause for each sort_by option get_content_items() accepts. Every
# one ends in ci.id so the ordering is total: a page boundary falling inside a
# tie would let two adjacent pages repeat one row and drop another.
_SORT_ORDER_BY: dict[str, str] = {
    "title": "ci.sort_title ASC, ci.id ASC",
    "updated_at": "ci.updated_at DESC, ci.id ASC",
    "rating": "ci.rating DESC NULLS LAST, ci.title ASC, ci.id ASC",
    "created_at": "ci.created_at DESC, ci.id ASC",
}

# Whitelist of valid sort_by options for get_content_items().
VALID_SORT_OPTIONS: frozenset[str] = frozenset(_SORT_ORDER_BY)

# Columns the enrichment join contributes, read back by _row_is_enriched.
_ENRICHMENT_SELECT_TERMS = (
    "es.content_item_id as enrichment_item_id",
    "es.needs_enrichment",
    "es.enrichment_provider",
    "es.enrichment_quality",
    "es.enrichment_error",
)


def _select_term(table_alias: str, column: str, alias: str) -> str:
    """One aliased column of a detail join, with its identifiers validated."""
    assert_safe_identifier(column)
    assert_safe_identifier(alias)
    if alias == column:
        return f"{table_alias}.{column}"
    return f"{table_alias}.{column} as {alias}"


def _detail_select_terms(spec: ContentTypeFields) -> list[str]:
    """Aliased columns one detail table contributes to the joined SELECT.

    The table name is checked against the fixed allow-list, and every column
    and alias against the identifier pattern, before any of them reaches SQL.
    """
    assert_known_detail_table(spec)

    terms = []
    for detail_field in spec.fields:
        column = detail_field.column
        if column is None:
            continue
        terms.append(
            _select_term(spec.table_alias, column, detail_field.select_alias or column)
        )
    terms.append(_select_term(spec.table_alias, "metadata", spec.metadata_alias))
    return terms


def _build_content_item_from() -> str:
    """Build the FROM clause of the content-item read.

    One five-way join covering every detail table plus the enrichment status.
    Shared by the full read and by the search-candidate projection so a WHERE
    clause built once stays valid against both.
    """
    joins = [detail_join(spec) for spec in DETAIL_FIELDS.values()]
    joins.append("LEFT JOIN enrichment_status es ON ci.id = es.content_item_id")
    join_list = "\n    ".join(joins)
    return f"\n    FROM content_items ci\n    {join_list}\n"


_CONTENT_ITEM_FROM = _build_content_item_from()


def _build_content_item_select() -> str:
    """Build the content-item read query from the field declaration.

    Used by get_content_item, _items_by_db_ids and _fetch_page. Callers append
    their own WHERE clause.
    """
    terms = ["ci.*"]
    for spec in DETAIL_FIELDS.values():
        terms.extend(_detail_select_terms(spec))
    terms.extend(_ENRICHMENT_SELECT_TERMS)
    select_list = ",\n           ".join(terms)
    return f"\n    SELECT {select_list}{_CONTENT_ITEM_FROM}"


_CONTENT_ITEM_SELECT = _build_content_item_select()

# The projection a library search reads: an id and the stored haystack, and no
# detail blob to parse, so a candidate that does not match never costs a
# ContentItem.
_SEARCH_CANDIDATE_SELECT = f"\n    SELECT ci.id, ci.search_text{_CONTENT_ITEM_FROM}"

# An item is enriched when it has a clean enrichment_status row: a real
# provider found a match, no error, and re-enrichment is not pending. Anything
# else (no row, needs_enrichment=1, not_found quality, or a recorded error)
# counts as not enriched. Expressed as a WHERE fragment over the ``es`` alias
# from _CONTENT_ITEM_SELECT so the list filter and the per-row flag stay in
# sync.
_ENRICHED_PREDICATE = (
    "es.content_item_id IS NOT NULL"
    " AND es.needs_enrichment = 0"
    " AND es.enrichment_error IS NULL"
    " AND es.enrichment_provider IS NOT NULL"
    " AND es.enrichment_provider != 'none'"
    " AND es.enrichment_quality != 'not_found'"
)


class SQLiteDB:
    """SQLite database manager for content items."""

    def __init__(self, db_path: Path) -> None:
        """Initialize SQLite database manager.

        Args:
            db_path: Path to SQLite database file
        """
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        # Set WAL mode once during initialization
        init_conn = sqlite3.connect(self.db_path)
        try:
            init_conn.execute("PRAGMA journal_mode = WAL")
        finally:
            init_conn.close()
        self._ensure_schema()

    def _get_connection(self) -> sqlite3.Connection:
        """Get a database connection.

        Returns:
            SQLite connection
        """
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        # Block (rather than raising SQLITE_BUSY) when another writer holds
        # the lock — required for parallel multi-source sync where two
        # workers may attempt BEGIN IMMEDIATE concurrently.
        conn.execute("PRAGMA busy_timeout = 5000")
        return conn

    @contextmanager
    def connection(self) -> Generator[sqlite3.Connection, None, None]:
        """Context manager for database connections.

        Yields a connection and ensures it is closed after use.

        Yields:
            SQLite connection
        """
        conn = self._get_connection()
        try:
            yield conn
        finally:
            conn.close()

    def _ensure_schema(self) -> None:
        """Ensure database schema is created."""
        with self.connection() as conn:
            create_schema(conn)

    def save_content_item(self, item: ContentItem, user_id: int | None = None) -> int:
        """Save or update a content item (the ingestion/sync door).

        Each user-owned field has its own rule, as described in the module
        docstring: ``rating`` and ``review`` are fill-only, so a re-sync can
        never overwrite either; ``status`` is forward-only, bar the TV-season
        regression the module docstring describes;
        ``date_completed`` is later-date-wins; and ``ignored`` follows only a
        stated incoming value.

        Args:
            item: ContentItem to save
            user_id: User ID (defaults to item.user_id or default user)

        Returns:
            Database ID of the saved item
        """
        with self.connection() as conn:
            cursor = conn.cursor()
            db_id = self._upsert_content_item(cursor, item, user_id)
            conn.commit()
            return db_id

    def complete_content_item(
        self, item: ContentItem, user_id: int | None = None
    ) -> int:
        """Record an explicit completion, adding the item if it is new.

        The entry point behind every completion — the ``complete`` CLI
        command and ``POST /api/complete``: it finds or creates the row and
        applies the user's own values in a single
        transaction, so no interruption can leave an item completed carrying
        the rating it had before.

        Completing something is an explicit user action, so a rating, review
        or completion date supplied here wins over what is stored — a date
        included, even one earlier than the stored date, which is how a user
        corrects a completion an import dated too late. A blank review is not
        a supplied one and leaves a stored review alone; see
        :meth:`_write_completion`. A completion carrying no date is the case
        the module docstring describes: an empty column is stamped with today
        in the host's zone, an existing date is kept.

        Args:
            item: ContentItem being completed, created if the library has no
                match by external id or normalized title. Its
                ``date_completed`` is the date the user named, or None when
                they named none.
            user_id: User ID (defaults to item.user_id or default user)

        Returns:
            Database ID of the completed item

        Raises:
            FutureCompletionDateError: ``item.date_completed`` is further
                ahead than :data:`MAX_COMPLETION_DATE_SKEW`. The transaction
                rolls back, so nothing is written.
        """
        with self.connection() as conn:
            cursor = conn.cursor()
            db_id = self._upsert_content_item(cursor, item, user_id)
            self._write_completion(
                cursor,
                db_id,
                item.rating,
                item.review,
                unset_if_none(item.date_completed),
            )
            conn.commit()
            return db_id

    def _write_completion(
        self,
        cursor: sqlite3.Cursor,
        db_id: int,
        rating: int | None,
        review: str | None,
        date_completed: date | Unset,
    ) -> None:
        """Apply the user-owned half of an explicit completion.

        Runs on the caller's cursor so that creating the row and recording
        what the user said about it are one transaction.

        The status is written here rather than left to the sync rules the
        upsert applies: a TV show whose season count has grown is regressed to
        currently_consuming by that pass, and someone who has just said "I
        finished this" outranks the season count. A named date is written here
        for the same reason — the upsert's later-date-wins rule is a sync rule,
        and leaving the date to it would drop a correction pointing backwards.

        Args:
            cursor: Database cursor (within an active transaction).
            db_id: Database ID of the row being completed.
            rating: Rating the user supplied, or None if they supplied none.
            review: Review the user supplied, or None if they supplied none.
                Blank counts as none: this door overwrites, so writing ``""``
                would replace a stored review with a value that reads as one
                the user wrote and stops a later import from filling the
                field. The check is repeated here because it protects a
                different write from the callers' own: the web and CLI
                surfaces refuse a blank outright, and
                :meth:`_upsert_content_item` — which runs first, so this guard
                never sees what it writes — separately declines to fill from
                one.
            date_completed: Completion date the user supplied, written as
                given unless it is further ahead than
                :data:`MAX_COMPLETION_DATE_SKEW`. UNSET fills an empty column
                with today in the host's zone and leaves a stored date alone.

        Raises:
            FutureCompletionDateError: *date_completed* is a day nobody has
                lived yet. Checked here rather than at each surface, so no
                caller can write one.
        """
        if (
            date_completed is not UNSET
            and date_completed > local_today() + MAX_COMPLETION_DATE_SKEW
        ):
            raise FutureCompletionDateError

        set_parts = ["status = 'completed'", "updated_at = CURRENT_TIMESTAMP"]
        params: list[Any] = []
        if date_completed is not UNSET:
            set_parts.append("date_completed = ?")
            params.append(date_completed.isoformat())
        else:
            set_parts.append("date_completed = COALESCE(date_completed, ?)")
            params.append(local_today().isoformat())
        if rating is not None:
            set_parts.append("rating = ?")
            params.append(rating)
        if review is not None and review.strip():
            set_parts.append("review = ?")
            params.append(review)
        params.append(db_id)
        cursor.execute(
            f"UPDATE content_items SET {', '.join(set_parts)} WHERE id = ?",
            params,
        )

    def _upsert_content_item(
        self, cursor: sqlite3.Cursor, item: ContentItem, user_id: int | None
    ) -> int:
        """Insert or update *item*'s row and detail row under the sync rules.

        The shared body of :meth:`save_content_item` and
        :meth:`complete_content_item`: upsert by external id, cross-source
        dedup by normalized title, and the fill-only rules for user-owned
        fields. Runs on the caller's cursor and does not commit, so a caller
        can add its own writes to the same transaction.

        Args:
            cursor: Database cursor (within an active transaction).
            item: ContentItem to save.
            user_id: User ID (defaults to item.user_id or default user).

        Returns:
            Database ID of the saved item.
        """
        # Use provided user_id, fall back to item's user_id, then default
        effective_user_id = (
            user_id
            if user_id is not None
            else (item.user_id if item.user_id is not None else get_default_user_id())
        )

        content_type_value = get_enum_value(item.content_type)

        # A blank review is not a review. This leg fills the column only while
        # it is empty, and a stored blank is indistinguishable from something
        # the user wrote, so filling with one would refuse every later value
        # and block the field for good.
        incoming_review = item.review if item.review and item.review.strip() else None

        # Check if item exists (by user_id, external_id, and content_type)
        existing_id: int | None = None
        if item.id:
            cursor.execute(
                """SELECT id FROM content_items
                   WHERE user_id = ? AND external_id = ? AND content_type = ?""",
                (effective_user_id, item.id, content_type_value),
            )
            row = cursor.fetchone()
            if row:
                existing_id = int(row["id"])

        # Compute normalized title once for both dedup paths below.
        normalized_title = (
            normalize_title_for_matching(item.title) if item.title else ""
        )

        # Cross-source dedup: if we found a row by external_id, check
        # whether a *different* row exists with the same normalized title.
        # This happens when both sources have already been imported and
        # each has its own row.  Merge the duplicate into the kept row.
        if existing_id is not None and normalized_title:
            cursor.execute(
                """SELECT id FROM content_items
                   WHERE user_id = ? AND content_type = ?
                     AND normalized_title = ? AND id != ?""",
                (
                    effective_user_id,
                    content_type_value,
                    normalized_title,
                    existing_id,
                ),
            )
            # Normally at most one match, but loop defensively in case
            # prior dedup ran partially and left multiple duplicates.
            dup_rows = cursor.fetchall()
            for dup_row in dup_rows:
                dup_id = int(dup_row["id"])
                self._merge_duplicate_into(
                    cursor, keep_id=existing_id, delete_id=dup_id
                )

        # Fallback: check by normalized title to merge items from different sources
        if existing_id is None and normalized_title:
            cursor.execute(
                """SELECT id FROM content_items
                       WHERE user_id = ? AND content_type = ?
                         AND normalized_title = ?""",
                (effective_user_id, content_type_value, normalized_title),
            )
            row = cursor.fetchone()
            if row:
                existing_id = int(row["id"])

        if existing_id is not None:
            # Update existing item in base table.
            # This is the sync door: user-owned fields are filled only
            # while they are empty, never overwritten.
            #   - rating/review: only set when the stored value is null
            #   - status: forward-only (unread → consuming → completed)
            #   - date_completed: only if incoming is later than existing
            #   - ignored: only when the incoming value states one
            #   - None incoming values never overwrite existing data
            cursor.execute(
                "SELECT status, rating, review, date_completed"
                " FROM content_items WHERE id = ?",
                (existing_id,),
            )
            existing_row = cursor.fetchone()

            set_parts = ["updated_at = CURRENT_TIMESTAMP"]
            params: list[str | int | None] = []

            # Title: always update (identity field, always present)
            set_parts.append("title = ?")
            params.append(item.title)

            # Keep normalized_title in sync with title
            set_parts.append("normalized_title = ?")
            params.append(normalize_title_for_matching(item.title))

            # Source: update if incoming is not None
            if item.source is not None:
                set_parts.append("source = ?")
                params.append(item.source)

            # Status: only advance forward
            existing_status = existing_row["status"] if existing_row else None
            resolved_status = resolve_status_forward(
                existing_status, get_enum_value(item.status)
            )
            set_parts.append("status = ?")
            params.append(resolved_status)

            # Rating: fill only — never overwrite the user's own value
            existing_rating = existing_row["rating"] if existing_row else None
            if existing_rating is None and item.rating is not None:
                set_parts.append("rating = ?")
                params.append(item.rating)

            # Review: fill only — never overwrite the user's own value
            existing_review = existing_row["review"] if existing_row else None
            if existing_review is None and incoming_review is not None:
                set_parts.append("review = ?")
                params.append(incoming_review)

            # Date completed: only if incoming is not None and later
            if item.date_completed is not None:
                incoming_date_str = item.date_completed.isoformat()
                existing_date_str = (
                    existing_row["date_completed"] if existing_row else None
                )
                if existing_date_str is None or incoming_date_str > existing_date_str:
                    set_parts.append("date_completed = ?")
                    params.append(incoming_date_str)

            # Ignored: only a stated value counts. True and False both win —
            # that is how an edited export un-ignores an item — while None
            # means the source said nothing and the stored flag stands.
            if item.ignored is not None:
                set_parts.append("ignored = ?")
                params.append(1 if item.ignored else 0)

            set_clause = ", ".join(set_parts)
            params.append(existing_id)
            cursor.execute(
                f"UPDATE content_items SET {set_clause} WHERE id = ?",
                params,
            )
            db_id = existing_id
        else:
            # Insert new item into base table
            cursor.execute(
                """
                INSERT INTO content_items
                (user_id, external_id, title, normalized_title, content_type,
                 status, rating, review, date_completed, source, ignored)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    effective_user_id,
                    item.id,
                    item.title,
                    normalize_title_for_matching(item.title),
                    content_type_value,
                    get_enum_value(item.status),
                    item.rating,
                    incoming_review,
                    (item.date_completed.isoformat() if item.date_completed else None),
                    item.source,
                    1 if item.ignored else 0,
                ),
            )
            lastrowid = cursor.lastrowid
            if lastrowid is None:
                raise RuntimeError("INSERT did not return a row ID")
            db_id = lastrowid

        # Save to type-specific detail table
        self._save_detail_table(cursor, db_id, item, content_type_value)

        # After the detail write, so the derived columns read the creator that
        # was actually stored rather than the one this sync offered.
        write_derived_columns(cursor, db_id)

        # For TV shows, check if new seasons should regress status
        if content_type_value == "tv_show":
            self._handle_tv_season_change(cursor, db_id)

        return db_id

    def _save_detail_table(
        self, cursor: sqlite3.Cursor, db_id: int, item: ContentItem, content_type: str
    ) -> None:
        """Save item to appropriate type-specific detail table.

        For existing rows, enrichment is the source of truth:
        - Genres/tags: merged additively (new + existing)
        - All other columns: fill-only (only set if existing value is None)
        - Remaining metadata JSON: merged additively (existing keys preserved),
          except ``seasons_watched_dates``, which merges per season keeping
          the later watch date (so an earlier sync date never overwrites a
          later manual/existing date, but a genuinely newer Trakt watch does
          update it; new seasons are added)

        For new rows, all data from ingestion is used as-is.

        Args:
            cursor: Database cursor
            db_id: Content item database ID
            item: ContentItem to save
            content_type: Content type as string

        Raises:
            KeyError: For a content type with no field declaration, like
                :meth:`_write_manual_metadata` — every type has one, and a
                skipped write would lose the item's detail row in silence.
        """
        spec = DETAIL_FIELDS[content_type]

        metadata = item.metadata or {}
        table = spec.table
        if table not in ALLOWED_DETAIL_TABLES:
            raise ValueError(f"Unknown detail table: {table!r}")
        known_keys = spec.known_keys

        # Check for an existing row
        cursor.execute(
            f"SELECT * FROM {table} WHERE content_item_id = ?",
            (db_id,),
        )
        existing_row = cursor.fetchone()
        existing_col_names = (
            [description[0] for description in cursor.description]
            if existing_row is not None
            else []
        )
        existing_data: dict[str, Any] = (
            dict(zip(existing_col_names, existing_row, strict=True))
            if existing_row is not None
            else {}
        )

        # Build column values
        col_names = ["content_item_id"]
        values: list[Any] = [db_id]

        for detail_field in spec.fields:
            col_name = detail_field.column
            if col_name is None:
                continue

            raw = detail_field.value_from(metadata)
            if detail_field.kind is FieldKind.CREATOR:
                # The item's own author outranks whatever metadata carries.
                raw = item.author or raw
            new_value = detail_field.store(raw)

            # Decide final value based on existing data
            if col_name in MERGEABLE_DETAIL_COLUMNS and existing_data:
                # Genres/tags: additive merge
                existing_list = parse_json_list(existing_data.get(col_name))
                new_list = parse_json_list(new_value)
                merged = merge_string_lists(existing_list, new_list)
                values.append(json.dumps(merged) if merged else new_value)
            elif col_name in MONOTONIC_DETAIL_COLUMNS and existing_data:
                # Seasons/episodes: take the higher value
                existing_val = to_int(existing_data.get(col_name))
                incoming_val = to_int(new_value)
                if existing_val is not None and incoming_val is not None:
                    values.append(max(existing_val, incoming_val))
                elif incoming_val is not None:
                    values.append(incoming_val)
                else:
                    values.append(existing_val)
            elif existing_data and existing_data.get(col_name) is not None:
                # Existing row has data — keep it (enrichment is source of truth)
                values.append(existing_data[col_name])
            else:
                # No existing row, or existing value is None — use incoming
                values.append(new_value)

            col_names.append(col_name)

        # Remaining metadata as JSON — merge additively with existing
        remaining_metadata = {
            key: val for key, val in metadata.items() if key not in known_keys
        }
        if existing_data and existing_data.get("metadata"):
            existing_remaining: dict[str, Any] = {}
            try:
                parsed = json.loads(existing_data["metadata"])
                if isinstance(parsed, dict):
                    existing_remaining = parsed
            except (json.JSONDecodeError, TypeError):
                pass
            # Existing keys take precedence, incoming fills gaps
            merged_remaining = {**remaining_metadata, **existing_remaining}
            # Exception: seasons_watched_dates merges per season, keeping the
            # later watch date — an earlier sync date never overwrites a
            # later manual/existing date, but a genuinely newer Trakt watch
            # does update it, and new seasons are added.
            combined_dates = merge_seasons_watched_dates(
                existing_remaining.get("seasons_watched_dates"),
                remaining_metadata.get("seasons_watched_dates"),
            )
            # A None result (e.g. both sides only had unparseable timestamps)
            # intentionally leaves the general blob-merge result above in place.
            if combined_dates is not None:
                merged_remaining["seasons_watched_dates"] = combined_dates
            metadata_json = json.dumps(merged_remaining) if merged_remaining else None
        else:
            metadata_json = (
                json.dumps(remaining_metadata) if remaining_metadata else None
            )
        col_names.append("metadata")
        values.append(metadata_json)

        for name in col_names:
            assert_safe_identifier(name)
        placeholders = ", ".join("?" for _ in values)
        col_list = ", ".join(col_names)
        if existing_data:
            # UPDATE existing row
            set_clauses = ", ".join(
                f"{name} = ?" for name in col_names if name != "content_item_id"
            )
            update_values = [
                val
                for name, val in zip(col_names, values, strict=True)
                if name != "content_item_id"
            ]
            update_values.append(db_id)
            cursor.execute(
                f"UPDATE {table} SET {set_clauses} WHERE content_item_id = ?",
                update_values,
            )
        else:
            cursor.execute(
                f"INSERT INTO {table} ({col_list}) VALUES ({placeholders})",
                values,
            )

    def _merge_duplicate_into(
        self, cursor: sqlite3.Cursor, keep_id: int, delete_id: int
    ) -> None:
        """Merge data from a duplicate row into the kept row, then delete it.

        Called when two rows represent the same item (same normalized title,
        user, and content type) but have different external_ids from different
        sources.

        Delegates to the module-level ``merge_scalar_columns`` and
        ``merge_detail_tables`` functions so that the same merge logic
        is available to both runtime and migration paths.

        Args:
            cursor: Database cursor (within an active transaction).
            keep_id: Database ID of the row to keep.
            delete_id: Database ID of the duplicate row to delete.
        """
        merge_scalar_columns(cursor, keep_id, delete_id)
        merge_detail_tables(cursor, keep_id, delete_id)
        # The merge can fill the kept row's creator from the duplicate, and
        # deduplicate_items runs it with no save behind it to refresh them.
        write_derived_columns(cursor, keep_id)

        # Delete the duplicate row (cascades to detail tables)
        cursor.execute("DELETE FROM content_items WHERE id = ?", (delete_id,))

    def _handle_tv_season_change(self, cursor: sqlite3.Cursor, db_id: int) -> None:
        """Regress TV show status when new seasons arrive during sync.

        When a sync updates the total season count for a TV show and the
        user had previously watched all seasons (completed via the UI
        season checklist), the status should regress to currently_consuming
        — unless the item is ignored.

        If ignored, the season count still updates (handled by the monotonic
        column logic in _save_detail_table) but status stays as-is.

        This only fires when ``seasons_watched`` metadata exists (i.e. the
        user has used the edit modal's season checklist at least once).

        Args:
            cursor: Database cursor (within an active transaction).
            db_id: Content item database ID.
        """
        cursor.execute(
            "SELECT ci.status, ci.ignored, td.seasons, td.metadata"
            " FROM content_items ci"
            " JOIN tv_show_details td ON ci.id = td.content_item_id"
            " WHERE ci.id = ?",
            (db_id,),
        )
        row = cursor.fetchone()
        if not row:
            return

        status = row["status"]
        ignored = bool(row["ignored"])
        total_seasons = row["seasons"]

        # Only applies when currently completed
        if status != "completed":
            return

        # Parse seasons_watched from metadata
        metadata_raw = row["metadata"]
        if not metadata_raw:
            return
        try:
            metadata = json.loads(metadata_raw)
        except (json.JSONDecodeError, TypeError):
            return

        seasons_watched = metadata.get("seasons_watched")
        if not isinstance(seasons_watched, list):
            return

        # If all seasons are still watched, no regression needed
        if total_seasons is None or len(seasons_watched) >= total_seasons:
            return

        # New seasons available that user hasn't watched.
        # If ignored, leave status alone.
        if ignored:
            return

        # Regress to currently_consuming
        cursor.execute(
            "UPDATE content_items SET status = 'currently_consuming',"
            " updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (db_id,),
        )

    def get_content_item(
        self, db_id: int, user_id: int | None = None
    ) -> ContentItem | None:
        """Get a content item by database ID.

        Args:
            db_id: Database ID
            user_id: Optional user ID filter (for security)

        Returns:
            ContentItem if found, None otherwise
        """
        with self.connection() as conn:
            cursor = conn.cursor()
            query = _CONTENT_ITEM_SELECT + " WHERE ci.id = ?"
            params: list[Any] = [db_id]

            if user_id is not None:
                query += " AND ci.user_id = ?"
                params.append(user_id)

            cursor.execute(query, params)
            row = cursor.fetchone()
            if row:
                return self._row_to_content_item(row)
            return None

    def get_content_items_by_db_ids(self, db_ids: list[int]) -> list[ContentItem]:
        """Get multiple content items by their database IDs in a single query.

        Args:
            db_ids: List of database IDs to fetch

        Returns:
            One ContentItem per id that names a row, in the order asked for.
            An id naming no row is skipped and a repeated id is returned once
            per occurrence, so the result tracks the argument rather than the
            set of distinct ids in it.
        """
        if not db_ids:
            return []

        with self.connection() as conn:
            return self._items_by_db_ids(conn.cursor(), db_ids)

    def _items_by_db_ids(
        self, cursor: sqlite3.Cursor, db_ids: list[int]
    ) -> list[ContentItem]:
        """Load the named items, in the order named, over an open cursor.

        Chunked so the IN clause stays within SQLite's variable limit, and
        re-ordered afterwards because the chunks come back in whatever order
        each query chose.
        """
        rows: dict[int, sqlite3.Row] = {}
        for i in range(0, len(db_ids), _IN_CLAUSE_CHUNK_SIZE):
            chunk = db_ids[i : i + _IN_CLAUSE_CHUNK_SIZE]
            placeholders = ", ".join("?" for _ in chunk)
            query = f"{_CONTENT_ITEM_SELECT} WHERE ci.id IN ({placeholders})"
            cursor.execute(query, chunk)
            rows.update({row["id"]: row for row in cursor.fetchall()})
        return [
            self._row_to_content_item(rows[db_id]) for db_id in db_ids if db_id in rows
        ]

    def get_content_items_by_external_ids(
        self,
        external_ids: list[str],
        user_id: int | None = None,
        content_type: ContentType | None = None,
    ) -> list[ContentItem]:
        """Get multiple content items by their external IDs in a single query.

        Args:
            external_ids: External IDs to fetch.
            user_id: Filter by user ID (defaults to default user).
            content_type: Filter by content type. One external id may name a
                row of each type, since rows are unique per
                ``(user, external id, content type)``.

        Returns:
            One ContentItem per row matching the filters, in no particular
            order. An id naming no such row is skipped.
        """
        if not external_ids:
            return []

        type_clause = " AND ci.content_type = ?" if content_type is not None else ""
        type_params = [get_enum_value(content_type)] if content_type is not None else []
        effective_user_id = user_id if user_id is not None else get_default_user_id()

        items: list[ContentItem] = []
        with self.connection() as conn:
            cursor = conn.cursor()
            for i in range(0, len(external_ids), _IN_CLAUSE_CHUNK_SIZE):
                chunk = external_ids[i : i + _IN_CLAUSE_CHUNK_SIZE]
                placeholders = ", ".join("?" for _ in chunk)
                cursor.execute(
                    f"{_CONTENT_ITEM_SELECT} WHERE ci.user_id = ?"
                    f" AND ci.external_id IN ({placeholders}){type_clause}",
                    [effective_user_id, *chunk, *type_params],
                )
                items.extend(
                    self._row_to_content_item(row) for row in cursor.fetchall()
                )
        return items

    def get_content_items(
        self,
        user_id: int | None = None,
        content_type: ContentType | None = None,
        status: ConsumptionStatus | list[ConsumptionStatus] | None = None,
        min_rating: int | None = None,
        unrated_only: bool = False,
        limit: int | None = None,
        offset: int = 0,
        sort_by: str = "title",
        include_ignored: bool = True,
        enrichment: EnrichmentFilter | None = None,
        search: str | None = None,
    ) -> list[ContentItem]:
        """Get content items with optional filters.

        Args:
            user_id: Filter by user ID (defaults to default user)
            content_type: Filter by content type
            status: Filter by consumption status (single value or list for
                IN-clause filtering)
            min_rating: Minimum rating (inclusive)
            unrated_only: When True, only return items with no rating set
                (rating IS NULL)
            limit: Maximum number of results. None or 0 means no limit.
            offset: Number of results to skip (for pagination). Independent of
                limit: an offset with no limit skips and returns the rest.
            sort_by: Sort order - "title" (default, ignores articles),
                "updated_at", "rating", or "created_at"
            include_ignored: Whether to include ignored items (default True
                for backward compatibility)
            enrichment: Filter by enrichment state ("enriched" or
                "not_enriched"). None returns all items.
            search: Optional search term. When non-empty (after strip),
                results are filtered to items whose title or creator
                (author/director/creators/developer) matches the term via
                exact, substring, or fuzzy matching. ANDs with all other
                filters. Filtering happens before limit/offset so pagination
                pages over the full matched set.

        Returns:
            List of ContentItem objects

        Note:
            A request builds a ContentItem for the rows it returns and no
            others. A sort with no search term orders and pages entirely in
            SQL; a search matches the stored ``search_text`` of each ordered
            candidate in Python, because the fuzzy tier is not expressible in
            SQL, and loads only the page that survives limit/offset.
        """
        # An empty status list matches nothing by definition.
        if isinstance(status, list) and not status:
            return []

        if sort_by not in _SORT_ORDER_BY:
            raise ValueError(f"Invalid sort_by: {sort_by!r}")

        # Normalize the search term; an empty/whitespace term is a no-op.
        search_term = search.strip() if search else ""

        where, params = self._build_item_filters(
            user_id=user_id if user_id is not None else get_default_user_id(),
            content_type=content_type,
            status=status,
            min_rating=min_rating,
            unrated_only=unrated_only,
            include_ignored=include_ignored,
            enrichment=enrichment,
        )
        order_by = _SORT_ORDER_BY[sort_by]

        with self.connection() as conn:
            cursor = conn.cursor()
            if search_term:
                return self._search_page(
                    cursor, where, params, order_by, limit, offset, search_term
                )
            return self._fetch_page(cursor, where, params, order_by, limit, offset)

    @staticmethod
    def _build_item_filters(
        user_id: int,
        content_type: ContentType | None,
        status: ConsumptionStatus | list[ConsumptionStatus] | None,
        min_rating: int | None,
        unrated_only: bool,
        include_ignored: bool,
        enrichment: EnrichmentFilter | None,
    ) -> tuple[str, list[Any]]:
        """Build the WHERE clause the list filters come to, and its parameters.

        Returned apart from the SELECT so the full read and the
        search-candidate projection page over the same filtered set.
        """
        where = " WHERE ci.user_id = ?"
        params: list[Any] = [user_id]

        if content_type is not None:
            where += " AND ci.content_type = ?"
            params.append(get_enum_value(content_type))

        if enrichment == "enriched":
            where += f" AND ({_ENRICHED_PREDICATE})"
        elif enrichment == "not_enriched":
            where += f" AND NOT ({_ENRICHED_PREDICATE})"

        if status is not None:
            if isinstance(status, list):
                placeholders = ", ".join("?" for _ in status)
                where += f" AND ci.status IN ({placeholders})"
                params.extend(get_enum_value(s) for s in status)
            else:
                where += " AND ci.status = ?"
                params.append(get_enum_value(status))

        if min_rating is not None:
            where += " AND ci.rating >= ?"
            params.append(min_rating)

        if unrated_only:
            where += " AND ci.rating IS NULL"

        if not include_ignored:
            where += " AND (ci.ignored = 0 OR ci.ignored IS NULL)"

        return where, params

    @staticmethod
    def _page_clause(limit: int | None, offset: int) -> tuple[str, list[Any]]:
        """Build the LIMIT/OFFSET clause for a page, and its parameters.

        SQLite accepts OFFSET only as a suffix of LIMIT, so an offset with no
        limit uses -1, SQLite's "unbounded" limit. A falsy limit means no
        limit, and a non-positive offset skips nothing.
        """
        if not limit and offset <= 0:
            return "", []
        clause = " LIMIT ?"
        params: list[Any] = [limit or -1]
        if offset > 0:
            clause += " OFFSET ?"
            params.append(offset)
        return clause, params

    def _fetch_page(
        self,
        cursor: sqlite3.Cursor,
        where: str,
        params: list[Any],
        order_by: str,
        limit: int | None,
        offset: int,
    ) -> list[ContentItem]:
        """Load one ordered page of the filtered set."""
        page_clause, page_params = self._page_clause(limit, offset)
        cursor.execute(
            f"{_CONTENT_ITEM_SELECT}{where} ORDER BY {order_by}{page_clause}",
            [*params, *page_params],
        )
        return [self._row_to_content_item(row) for row in cursor.fetchall()]

    def _search_page(
        self,
        cursor: sqlite3.Cursor,
        where: str,
        params: list[Any],
        order_by: str,
        limit: int | None,
        offset: int,
        search_term: str,
    ) -> list[ContentItem]:
        """Load one page of the items matching *search_term*.

        A search has one matched set, whichever tier of
        :func:`~src.utils.sorting.search_text_matches` an item answers on, so
        the offset means one thing throughout and the pages of a search
        concatenate into the unpaged answer. SQL orders the candidates and
        hands each over as an id and a stored search text — no detail blob to
        parse — so a candidate that misses, and a match outside the page,
        never costs a ContentItem. The scan stops as soon as the page is
        filled, because no caller asks how many matches lie beyond it; a falsy
        limit asks for the rest of the set, so it scans every candidate.
        """
        needle = normalize_for_search(search_term)
        # A term of pure punctuation normalizes away, and an empty needle
        # would otherwise be a substring of every stored search text.
        if not needle:
            return []

        # Read the way _page_clause reads them: only a positive limit bounds
        # the page — it hands a negative one to SQLite, where it means
        # unbounded — and a non-positive offset skips nothing.
        start = max(offset, 0)
        page_end = start + limit if limit and limit > 0 else None

        cursor.execute(f"{_SEARCH_CANDIDATE_SELECT}{where} ORDER BY {order_by}", params)
        matched: list[int] = []
        for row in cursor:
            if search_text_matches(row["search_text"], needle):
                matched.append(row["id"])
                if page_end is not None and len(matched) == page_end:
                    break

        page = matched[start:]
        return self._items_by_db_ids(cursor, page) if page else []

    def get_unconsumed_items(
        self,
        user_id: int | None = None,
        content_type: ContentType | None = None,
        limit: int | None = None,
        include_ignored: bool = True,
    ) -> list[ContentItem]:
        """Get unconsumed items (status = UNREAD or CURRENTLY_CONSUMING).

        Args:
            user_id: Filter by user ID
            content_type: Filter by content type
            limit: Maximum number of results
            include_ignored: Whether to include ignored items (default True)

        Returns:
            List of unconsumed ContentItem objects
        """
        return self.get_content_items(
            user_id=user_id,
            content_type=content_type,
            status=[ConsumptionStatus.UNREAD, ConsumptionStatus.CURRENTLY_CONSUMING],
            limit=limit,
            include_ignored=include_ignored,
        )

    def get_completed_items(
        self,
        user_id: int | None = None,
        content_type: ContentType | None = None,
        min_rating: int | None = None,
        limit: int | None = None,
        include_ignored: bool = True,
    ) -> list[ContentItem]:
        """Get completed items (status = COMPLETED or CURRENTLY_CONSUMING).

        Args:
            user_id: Filter by user ID
            content_type: Filter by content type
            min_rating: Minimum rating (inclusive)
            limit: Maximum number of results
            include_ignored: Whether to include ignored items (default True)

        Returns:
            List of completed ContentItem objects
        """
        return self.get_content_items(
            user_id=user_id,
            content_type=content_type,
            status=[ConsumptionStatus.COMPLETED, ConsumptionStatus.CURRENTLY_CONSUMING],
            min_rating=min_rating,
            limit=limit,
            include_ignored=include_ignored,
        )

    def _row_to_content_item(self, row: sqlite3.Row) -> ContentItem:
        """Convert a database row to ContentItem.

        Args:
            row: A row from _CONTENT_ITEM_SELECT, carrying every detail and
                enrichment column that query aliases. A column it does not
                carry raises rather than reading as absent data: the aliases
                are generated from DETAIL_FIELDS, so a name that misses is a
                bug in the declaration and not a value the row lacks.

        Returns:
            ContentItem object

        Raises:
            KeyError: For a content type with no field declaration, like
                :meth:`_save_detail_table` — every type has one, and reading
                the row without it would report a stored item as carrying no
                detail at all.
        """
        content_type = ContentType(row["content_type"])
        metadata: dict[str, Any] = {}
        author: str | None = None

        spec = DETAIL_FIELDS[content_type.value]
        for detail_field in spec.fields:
            column = detail_field.column
            if column is None:
                continue
            raw = row[detail_field.select_alias or column]
            if detail_field.kind is FieldKind.CREATOR:
                author = raw
                continue
            value = detail_field.codec.load(raw)
            if value:
                metadata[detail_field.metadata_key] = value

        # The blob last, so a key it repeats wins over the column that claims
        # it. Storage keeps a column's keys out of the blob it writes, but a
        # row written before a key belonged to a column still carries one —
        # the shape ``_migrate_stranded_detail_shapes`` repairs on open.
        # A blob that is not an object carries no keys, and reaches the
        # reader because the migration leaves such a row alone as well.
        if blob := row[spec.metadata_alias]:
            try:
                leftover = json.loads(blob)
            except (json.JSONDecodeError, TypeError):
                leftover = None
            if isinstance(leftover, dict):
                metadata.update(leftover)

        # Parse date_completed
        date_completed = None
        if date_completed_str := row["date_completed"]:
            try:
                date_completed = datetime.fromisoformat(date_completed_str).date()
            except (ValueError, AttributeError):
                pass

        return ContentItem(
            user_id=row["user_id"],
            id=row["external_id"],
            db_id=row["id"],
            title=row["title"],
            author=author,
            content_type=content_type,
            rating=row["rating"],
            review=row["review"],
            status=ConsumptionStatus(row["status"]),
            date_completed=date_completed,
            source=row["source"],
            ignored=bool(row["ignored"]),
            enriched=self._row_is_enriched(row),
            metadata=metadata,
        )

    @staticmethod
    def _row_is_enriched(row: sqlite3.Row) -> bool:
        """Derive the enriched flag from the joined enrichment_status columns.

        Mirrors ``_ENRICHED_PREDICATE`` so the per-row flag and the list
        filter agree: a clean row (real provider, no error, not pending). Like
        :meth:`_row_to_content_item`, it reads a row from
        ``_CONTENT_ITEM_SELECT`` and raises on a column that query does not
        carry, rather than reporting every item as not enriched.
        """
        if row["enrichment_item_id"] is None:
            return False
        if row["needs_enrichment"]:
            return False
        if row["enrichment_error"] is not None:
            return False
        if row["enrichment_quality"] == "not_found":
            return False
        provider = row["enrichment_provider"]
        return provider is not None and provider != "none"

    def update_item_from_ui(
        self,
        db_id: int,
        status: str,
        rating: int | None | Unset = UNSET,
        review: str | None | Unset = UNSET,
        seasons_watched: list[int] | None = None,
        genres: list[str] | None = None,
        tags: list[str] | None = None,
        description: str | None = None,
        user_id: int | None = None,
    ) -> bool:
        """Update a content item from an explicit user action (unrestricted).

        This is the explicit-user-action door described in the module
        docstring: unlike save_content_item, which fills user-owned fields
        only while they are empty, an edit made here may freely overwrite
        status, rating and review, and status may go backward.

        Only the fields the caller actually supplied are written, but the
        arguments say "not supplied" in three different ways, so read the one
        you are passing:

        - ``status`` is required and always written.
        - ``rating`` and ``review`` use :data:`UNSET` for "leave it alone",
          because they are nullable and ``None`` therefore has to mean
          "clear it". A blank ``review`` clears it as well, so the only two
          things this door can leave in the column are text the user wrote and
          NULL: it overwrites what it is handed, and a stored ``""`` reads as a
          review the user wrote and refuses every later import. Clearing
          rather than ignoring is what the surfaces in front already decide —
          the edit dialog sends null once the box is empty, and ``library
          edit`` spells that instruction ``--clear-review``.
        - ``seasons_watched``, ``genres``, ``tags`` and ``description`` use
          ``None`` for "leave it alone", so sending an explicit null for one
          of them is a no-op, not a clear. The *empty* value is the clear:
          ``[]`` for the three lists and ``""`` for the description are
          supplied values, and they are written as given. That is the ordinary
          path rather than a corner — the web edit dialog sends ``genres`` and
          ``tags`` on every save, so removing the last one there clears the
          stored list.

        For TV shows with seasons_watched provided, status is auto-derived:
        0 watched = unread, all watched = completed, partial = currently_consuming.
        The auto-derived status overrides the status parameter.

        Also stamps ``seasons_watched_dates`` (season -> ISO timestamp) in the
        detail-table metadata: a season newly checked off in this edit (not
        present in the previous ``seasons_watched``) gets the current time; a
        season that already has a date keeps it; a season that was already
        watched but has no date is left undated rather than inventing one; a
        season no longer in the incoming list is dropped. This is the
        recency signal the variety ladder uses to date an ongoing show's
        finished-season completion event.

        Manual genres/tags/description overwrite the detail-table values
        (rather than the additive merge used by sync/enrichment) and mark the
        item enriched via the ``manual`` provider so it drops out of the
        not-enriched filter and is never re-queued for automatic enrichment.

        When the edit moves the status *into* ``completed`` and the row has no
        ``date_completed`` yet, today's date in the host's zone is stamped so
        an in-app completion carries a date for the variety ladder — the same
        calendar an imported date is narrowed to. An item that was
        already completed is left as it is: an import that carried no date
        stays undated rather than being dated today by an unrelated genre or
        review edit, the same rule the season dates above follow. A status
        moving away from completed leaves the stored date alone — it records
        that a completion happened, and dropping it would be the same silent
        loss this door exists to avoid.

        Args:
            db_id: Database ID of the item to update.
            status: New status value (unread, currently_consuming, completed).
            rating: New rating (1-5), None to clear, UNSET to leave unchanged.
            review: New review text, None or blank to clear, UNSET to leave
                unchanged.
            seasons_watched: List of watched season numbers (TV shows only).
                None leaves them as-is, ``[]`` clears them.
            genres: Manual genres to set (overwrite). None leaves them as-is,
                ``[]`` clears them.
            tags: Manual tags to set (overwrite). None leaves them as-is,
                ``[]`` clears them.
            description: Manual description to set. None leaves it as-is,
                ``""`` clears it.
            user_id: Optional user ID filter for authorization.

        Returns:
            True if item was updated, False if not found.
        """
        with self.connection() as conn:
            cursor = conn.cursor()

            # Verify item exists (and belongs to user_id if provided)
            if user_id is not None:
                cursor.execute(
                    "SELECT id, content_type, status FROM content_items"
                    " WHERE id = ? AND user_id = ?",
                    (db_id, user_id),
                )
            else:
                cursor.execute(
                    "SELECT id, content_type, status FROM content_items WHERE id = ?",
                    (db_id,),
                )
            row = cursor.fetchone()
            if not row:
                return False

            content_type = row["content_type"]
            existing_status = row["status"]
            resolved_status = status

            # For TV shows with seasons_watched, auto-derive status
            if content_type == "tv_show" and seasons_watched is not None:
                cursor.execute(
                    "SELECT seasons, metadata FROM tv_show_details"
                    " WHERE content_item_id = ?",
                    (db_id,),
                )
                tv_row = cursor.fetchone()
                if tv_row:
                    total_seasons = tv_row["seasons"] or 0

                    # Auto-derive status from seasons watched
                    watched_count = len(seasons_watched)
                    if watched_count == 0:
                        resolved_status = "unread"
                    elif total_seasons > 0 and watched_count >= total_seasons:
                        resolved_status = "completed"
                    else:
                        resolved_status = "currently_consuming"

                    # Merge seasons_watched into tv_show_details metadata
                    existing_metadata: dict[str, Any] = {}
                    if tv_row["metadata"]:
                        try:
                            parsed = json.loads(tv_row["metadata"])
                            if isinstance(parsed, dict):
                                existing_metadata = parsed
                        except (json.JSONDecodeError, TypeError):
                            pass
                    now_iso = utc_now().isoformat()
                    existing_dates = existing_metadata.get("seasons_watched_dates")
                    if not isinstance(existing_dates, dict):
                        existing_dates = {}
                    previously_watched = existing_metadata.get("seasons_watched")
                    if not isinstance(previously_watched, list):
                        previously_watched = []
                    # A season already dated keeps its date. A season newly
                    # ticked in this edit (not in the previous list) gets
                    # `now`. A season that was already watched but has no
                    # date is left undated rather than inventing one.
                    # Unchecked seasons fall out (rebuilt from the incoming
                    # list only).
                    new_dates: dict[str, str] = {}
                    for season in seasons_watched:
                        key = str(season)
                        if key in existing_dates:
                            new_dates[key] = existing_dates[key]
                        elif season not in previously_watched:
                            new_dates[key] = now_iso
                    existing_metadata["seasons_watched"] = seasons_watched
                    existing_metadata["seasons_watched_dates"] = new_dates
                    cursor.execute(
                        "UPDATE tv_show_details SET metadata = ?"
                        " WHERE content_item_id = ?",
                        (json.dumps(existing_metadata), db_id),
                    )

            # Update the content_items row directly, writing only the fields
            # the caller supplied so a partial edit cannot erase the rest.
            set_parts = ["status = ?", "updated_at = CURRENT_TIMESTAMP"]
            params: list[Any] = [resolved_status]
            if rating is not UNSET:
                set_parts.append("rating = ?")
                params.append(rating)
            if review is not UNSET:
                set_parts.append("review = ?")
                # A blank is not a review. The check lives here rather than
                # only in the callers so the door holds the property itself:
                # every caller today refuses or drops a blank, and the next one
                # inherits NULL instead of a value that reads as the user's.
                params.append(review if review and review.strip() else None)
            if resolved_status == "completed" and existing_status != "completed":
                # Only a transition into completed dates the completion: an
                # item that was already completed but undated is left undated
                # rather than being dated by an unrelated edit. COALESCE fills
                # the empty case without disturbing a date the user (or an
                # import) already recorded.
                set_parts.append("date_completed = COALESCE(date_completed, ?)")
                params.append(local_today().isoformat())
            params.append(db_id)
            cursor.execute(
                f"UPDATE content_items SET {', '.join(set_parts)} WHERE id = ?",
                params,
            )

            if genres is not None or tags is not None or description is not None:
                # _write_manual_metadata raises for an unknown content_type, so
                # the enriched row is only written when metadata actually was.
                self._write_manual_metadata(
                    cursor, db_id, content_type, genres, tags, description
                )
                write_enrichment_complete(cursor, db_id, "manual", "high")

            conn.commit()
            return True

    def _write_manual_metadata(
        self,
        cursor: sqlite3.Cursor,
        db_id: int,
        content_type: str,
        genres: list[str] | None,
        tags: list[str] | None,
        description: str | None,
    ) -> None:
        """Overwrite manual genres/tags/description in the detail table.

        Only the supplied (non-None) fields are written. Genres/tags are
        stored as JSON arrays to match the detail-table read path. The row is
        created if the item has no detail row yet.

        Raises ``ValueError`` for an unknown content_type so the caller never
        marks an item enriched without having written any metadata.
        """
        spec = DETAIL_FIELDS.get(content_type)
        if spec is None:
            raise ValueError(f"Unknown content_type: {content_type!r}")
        table = spec.table
        if table not in ALLOWED_DETAIL_TABLES:
            raise ValueError(f"Unknown detail table: {table!r}")

        updates: dict[str, Any] = {}
        if genres is not None:
            updates["genres"] = json.dumps(genres)
        if tags is not None:
            updates["tags"] = json.dumps(tags)
        if description is not None:
            updates["description"] = description

        cursor.execute(
            f"SELECT 1 FROM {table} WHERE content_item_id = ?",
            (db_id,),
        )
        row_exists = cursor.fetchone() is not None

        columns = list(updates)
        for name in columns:
            assert_safe_identifier(name)

        if row_exists:
            set_clause = ", ".join(f"{name} = ?" for name in columns)
            cursor.execute(
                f"UPDATE {table} SET {set_clause} WHERE content_item_id = ?",
                [*updates.values(), db_id],
            )
        else:
            col_list = ", ".join(["content_item_id", *columns])
            placeholders = ", ".join("?" for _ in range(len(columns) + 1))
            cursor.execute(
                f"INSERT INTO {table} ({col_list}) VALUES ({placeholders})",
                [db_id, *updates.values()],
            )

    def delete_content_item(self, db_id: int, user_id: int | None = None) -> bool:
        """Delete a content item by database ID.

        Args:
            db_id: Database ID
            user_id: Optional user ID filter (for security)

        Returns:
            True if item was deleted, False if not found
        """
        with self.connection() as conn:
            cursor = conn.cursor()
            if user_id is not None:
                cursor.execute(
                    "DELETE FROM content_items WHERE id = ? AND user_id = ?",
                    (db_id, user_id),
                )
            else:
                cursor.execute("DELETE FROM content_items WHERE id = ?", (db_id,))
            conn.commit()
            return cursor.rowcount > 0

    def deduplicate_items(self, user_id: int | None = None) -> int:
        """Find and merge duplicate items by normalized title.

        Scans for groups of rows sharing the same (user_id, content_type,
        normalized_title) and merges each group into a single row, keeping
        the oldest row (lowest id) and merging data from duplicates.

        Args:
            user_id: If specified, only deduplicate items for this user.

        Returns:
            Number of duplicate rows removed.
        """
        merged_count = 0
        with self.connection() as conn:
            cursor = conn.cursor()

            # Find groups with duplicates
            query = """
                SELECT user_id, content_type, normalized_title
                FROM content_items
                WHERE normalized_title IS NOT NULL AND normalized_title != ''
            """
            params: list[Any] = []
            if user_id is not None:
                query += " AND user_id = ?"
                params.append(user_id)
            query += (
                " GROUP BY user_id, content_type, normalized_title"
                " HAVING COUNT(*) > 1"
            )

            cursor.execute(query, params)
            groups = cursor.fetchall()

            for group in groups:
                g_user_id = group["user_id"]
                g_content_type = group["content_type"]
                g_normalized = group["normalized_title"]

                # Get all rows in this group, ordered by id (keep oldest)
                cursor.execute(
                    """SELECT id FROM content_items
                       WHERE user_id = ? AND content_type = ?
                         AND normalized_title = ?
                       ORDER BY id""",
                    (g_user_id, g_content_type, g_normalized),
                )
                rows = cursor.fetchall()
                if len(rows) < 2:
                    continue

                keep_id = int(rows[0]["id"])
                for row in rows[1:]:
                    dup_id = int(row["id"])
                    self._merge_duplicate_into(
                        cursor, keep_id=keep_id, delete_id=dup_id
                    )
                    merged_count += 1

            conn.commit()
        return merged_count

    def set_item_ignored(
        self, db_id: int, ignored: bool, user_id: int | None = None
    ) -> bool:
        """Set the ignored status of a content item.

        Args:
            db_id: Database ID of the item
            ignored: Whether the item should be ignored
            user_id: Optional user ID filter (for security)

        Returns:
            True if item was updated, False if not found
        """
        with self.connection() as conn:
            cursor = conn.cursor()
            if user_id is not None:
                cursor.execute(
                    """UPDATE content_items
                       SET ignored = ?, updated_at = CURRENT_TIMESTAMP
                       WHERE id = ? AND user_id = ?""",
                    (1 if ignored else 0, db_id, user_id),
                )
            else:
                cursor.execute(
                    """UPDATE content_items
                       SET ignored = ?, updated_at = CURRENT_TIMESTAMP
                       WHERE id = ?""",
                    (1 if ignored else 0, db_id),
                )
            conn.commit()
            return cursor.rowcount > 0

    def count_items(
        self,
        user_id: int | None = None,
        content_type: ContentType | None = None,
        status: ConsumptionStatus | None = None,
    ) -> int:
        """Count content items with optional filters.

        Args:
            user_id: Filter by user ID (defaults to default user)
            content_type: Filter by content type
            status: Filter by consumption status

        Returns:
            Number of matching items
        """
        effective_user_id = user_id if user_id is not None else get_default_user_id()

        with self.connection() as conn:
            cursor = conn.cursor()
            query = "SELECT COUNT(*) FROM content_items WHERE user_id = ?"
            params: list[Any] = [effective_user_id]

            if content_type:
                query += " AND content_type = ?"
                content_type_value = get_enum_value(content_type)
                params.append(content_type_value)

            if status:
                query += " AND status = ?"
                status_value = get_enum_value(status)
                params.append(status_value)

            cursor.execute(query, params)
            result = cursor.fetchone()
            return result[0] if result else 0

    def get_content_item_by_external_id(
        self,
        external_id: str,
        content_type: ContentType,
        user_id: int | None = None,
    ) -> ContentItem | None:
        """Get a content item by external ID and content type.

        Args:
            external_id: External ID from source
            content_type: Content type
            user_id: Filter by user ID (defaults to default user)

        Returns:
            ContentItem if found, None otherwise
        """
        effective_user_id = user_id if user_id is not None else get_default_user_id()

        with self.connection() as conn:
            cursor = conn.cursor()
            content_type_value = get_enum_value(content_type)
            cursor.execute(
                """SELECT id FROM content_items
                   WHERE user_id = ? AND external_id = ? AND content_type = ?""",
                (effective_user_id, external_id, content_type_value),
            )
            row = cursor.fetchone()
            if row:
                return self.get_content_item(row["id"], user_id=effective_user_id)
            return None

    def get_items_needing_enrichment(
        self,
        content_type: ContentType | None = None,
        user_id: int | None = None,
        limit: int = 100,
        include_not_found: bool = False,
        after_db_id: int | None = None,
    ) -> list[tuple[int, ContentItem]]:
        """Get content items that need enrichment.

        Returns items where:
        1. No enrichment_status record exists (new items), OR
        2. needs_enrichment = TRUE, OR
        3. enrichment_quality = 'not_found' (if include_not_found is True)

        Args:
            content_type: Optional filter by content type
            user_id: Filter by user ID (defaults to default user)
            limit: Maximum number of items to return
            include_not_found: Also include items previously marked as not_found
            after_db_id: Only return items with a database ID above this one.
                Results are ordered by ID, so a caller walking the queue passes
                the last ID it saw to page past the items it already handled —
                including any it left queued on purpose.

        Returns:
            List of (db_id, ContentItem) tuples for items needing enrichment
        """
        effective_user_id = user_id if user_id is not None else get_default_user_id()

        with self.connection() as conn:
            cursor = conn.cursor()
            query, params = self._build_enrichment_query(
                effective_user_id,
                content_type,
                include_not_found,
                count_only=False,
                after_db_id=after_db_id,
            )
            query += " ORDER BY ci.id LIMIT ?"
            params.append(limit)

            cursor.execute(query, params)
            rows = cursor.fetchall()
            db_ids = [row["id"] for row in rows]

        # Batch-fetch all items in a single query (outside the first connection)
        items = self.get_content_items_by_db_ids(db_ids)
        return [(item.db_id, item) for item in items if item.db_id is not None]

    def count_items_needing_enrichment(
        self,
        content_type: ContentType | None = None,
        user_id: int | None = None,
    ) -> int:
        """Count content items that need enrichment.

        Uses the same filter as :meth:`get_items_needing_enrichment` (with
        ``include_not_found=False``) so the enrichment manager can report a
        total upfront instead of incrementing per batch. Items previously
        marked as ``not_found`` are tracked separately by the manager and are
        intentionally excluded here to avoid double-counting.

        Args:
            content_type: Optional filter by content type
            user_id: Filter by user ID (defaults to default user)

        Returns:
            Number of items matching the enrichment filter.
        """
        effective_user_id = user_id if user_id is not None else get_default_user_id()

        with self.connection() as conn:
            cursor = conn.cursor()
            query, params = self._build_enrichment_query(
                effective_user_id,
                content_type,
                include_not_found=False,
                count_only=True,
            )
            cursor.execute(query, params)
            row = cursor.fetchone()
            return int(row[0]) if row else 0

    @staticmethod
    def _build_enrichment_query(
        user_id: int,
        content_type: ContentType | None,
        include_not_found: bool,
        count_only: bool,
        after_db_id: int | None = None,
    ) -> tuple[str, list[Any]]:
        """Build the shared SELECT for items needing enrichment.

        Returns the query string and the bound parameter list. Callers append
        ORDER BY / LIMIT clauses as needed. The SELECT clause is hardcoded
        based on ``count_only`` rather than accepting an open string, so this
        helper cannot be misused to inject SQL.
        """
        select_clause = "SELECT COUNT(*)" if count_only else "SELECT ci.id"

        if include_not_found:
            status_filter = (
                "(es.content_item_id IS NULL OR es.needs_enrichment = 1"
                " OR es.enrichment_quality = ?)"
            )
        else:
            status_filter = "(es.content_item_id IS NULL OR es.needs_enrichment = 1)"

        query = f"""
            {select_clause}
            FROM content_items ci
            LEFT JOIN enrichment_status es ON ci.id = es.content_item_id
            WHERE ci.user_id = ?
              AND {status_filter}
        """
        params: list[Any] = [user_id]
        if include_not_found:
            params.append("not_found")
        if content_type:
            query += " AND ci.content_type = ?"
            params.append(get_enum_value(content_type))
        if after_db_id is not None:
            query += " AND ci.id > ?"
            params.append(after_db_id)
        return query, params

    def get_content_item_db_id(
        self,
        external_id: str,
        content_type: ContentType,
        user_id: int | None = None,
    ) -> int | None:
        """Get the database ID of a content item by external ID.

        Args:
            external_id: External ID from source
            content_type: Content type
            user_id: Filter by user ID (defaults to default user)

        Returns:
            Database ID if found, None otherwise
        """
        effective_user_id = user_id if user_id is not None else get_default_user_id()

        with self.connection() as conn:
            cursor = conn.cursor()
            content_type_value = get_enum_value(content_type)
            cursor.execute(
                """SELECT id FROM content_items
                   WHERE user_id = ? AND external_id = ? AND content_type = ?""",
                (effective_user_id, external_id, content_type_value),
            )
            row = cursor.fetchone()
            return row["id"] if row else None
