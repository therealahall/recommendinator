"""SQLite database manager for content items."""

import json
import re
import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from src.models.content import (
    ConsumptionStatus,
    ContentItem,
    ContentType,
    get_enum_value,
)
from src.storage.schema import create_schema, get_default_user_id
from src.utils.list_merge import merge_string_lists

# Status ordering for forward-only progression.
# A status can only be overwritten by a status later in this sequence.
_STATUS_ORDER: dict[str, int] = {
    "unread": 0,
    "currently_consuming": 1,
    "completed": 2,
}


def _resolve_status_forward(existing_status: str | None, incoming_status: str) -> str:
    """Return the later of two statuses (forward-only progression).

    Status can only advance: unread → currently_consuming → completed.
    A re-sync with an earlier status does not revert.

    Args:
        existing_status: Current status in the database (may be None).
        incoming_status: Status from the incoming sync.

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
        """Save or update a content item.

        Args:
            item: ContentItem to save
            user_id: User ID (defaults to item.user_id or default user)

        Returns:
            Database ID of the saved item
        """
        # Use provided user_id, fall back to item's user_id, then default
        effective_user_id = user_id or item.user_id or get_default_user_id()

        with self.connection() as conn:
            cursor = conn.cursor()

            content_type_value = get_enum_value(item.content_type)

            # Check if item exists (by user_id, external_id, and content_type)
            existing_id = None
            if item.id:
                cursor.execute(
                    """SELECT id FROM content_items
                       WHERE user_id = ? AND external_id = ? AND content_type = ?""",
                    (effective_user_id, item.id, content_type_value),
                )
                row = cursor.fetchone()
                if row:
                    existing_id = row["id"]

            # Fallback: check by normalized title to merge items from different sources
            if existing_id is None and item.title:
                normalized_title = normalize_title_for_matching(item.title)
                if normalized_title:
                    # Find items with matching normalized title
                    cursor.execute(
                        """SELECT id, title FROM content_items
                           WHERE user_id = ? AND content_type = ?""",
                        (effective_user_id, content_type_value),
                    )
                    for row in cursor.fetchall():
                        if (
                            normalize_title_for_matching(row["title"])
                            == normalized_title
                        ):
                            existing_id = row["id"]
                            break

            if existing_id:
                # Update existing item in base table.
                # Rules:
                #   - rating/review: set once — never overwrite existing values
                #   - status: forward-only (unread → consuming → completed)
                #   - date_completed: only if incoming is later than existing
                #   - ignored: only when source explicitly sets it
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

                # Source: update if incoming is not None
                if item.source is not None:
                    set_parts.append("source = ?")
                    params.append(item.source)

                # Status: only advance forward
                existing_status = existing_row["status"] if existing_row else None
                resolved_status = _resolve_status_forward(
                    existing_status, get_enum_value(item.status)
                )
                set_parts.append("status = ?")
                params.append(resolved_status)

                # Rating: set once — only set if existing is None
                existing_rating = existing_row["rating"] if existing_row else None
                if existing_rating is None and item.rating is not None:
                    set_parts.append("rating = ?")
                    params.append(item.rating)

                # Review: set once — only set if existing is None
                existing_review = existing_row["review"] if existing_row else None
                if existing_review is None and item.review is not None:
                    set_parts.append("review = ?")
                    params.append(item.review)

                # Date completed: only if incoming is not None and later
                if item.date_completed is not None:
                    incoming_date_str = item.date_completed.isoformat()
                    existing_date_str = (
                        existing_row["date_completed"] if existing_row else None
                    )
                    if (
                        existing_date_str is None
                        or incoming_date_str > existing_date_str
                    ):
                        set_parts.append("date_completed = ?")
                        params.append(incoming_date_str)

                # Ignored: only when source explicitly sets it (existing behavior)
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
                    (user_id, external_id, title, content_type, status, rating, review,
                     date_completed, source, ignored)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        effective_user_id,
                        item.id,
                        item.title,
                        content_type_value,
                        get_enum_value(item.status),
                        item.rating,
                        item.review,
                        (
                            item.date_completed.isoformat()
                            if item.date_completed
                            else None
                        ),
                        item.source,
                        1 if item.ignored else 0,
                    ),
                )
                db_id = cursor.lastrowid

            # Save to type-specific detail table
            self._save_detail_table(cursor, db_id, item, content_type_value)

            conn.commit()
            return db_id  # type: ignore

    # Configuration for type-specific detail tables.
    # Each entry maps content_type value to:
    #   table: SQL table name
    #   columns: list of (column_name, metadata_key, converter) tuples
    #            converter is "str" (default), "int" (safe_int), "json" (to_json_array),
    #            or "author" (special: use item.author or metadata)
    #   alias_keys: metadata keys that are aliases (e.g. "genre" -> "genres")
    _DETAIL_TABLE_CONFIG: dict[str, dict[str, Any]] = {
        "book": {
            "table": "book_details",
            "columns": [
                ("author", "author", "author"),
                ("pages", "pages", "int"),
                ("isbn", "isbn", "str"),
                ("isbn13", "isbn13", "str"),
                ("publisher", "publisher", "str"),
                ("year_published", "year_published", "int"),
                ("genres", "genres", "json_or_genre"),
                ("tags", "tags", "json"),
                ("description", "description", "str"),
            ],
            "known_keys": {
                "author",
                "pages",
                "isbn",
                "isbn13",
                "publisher",
                "year_published",
                "genres",
                "genre",
                "tags",
                "description",
            },
        },
        "movie": {
            "table": "movie_details",
            "columns": [
                ("director", "director", "str"),
                ("runtime", "runtime", "int"),
                ("release_year", "release_year", "int"),
                ("genres", "genres", "json_or_genre"),
                ("studio", "studio", "str"),
                ("tags", "tags", "json"),
                ("description", "description", "str"),
            ],
            "known_keys": {
                "director",
                "runtime",
                "release_year",
                "genres",
                "genre",
                "studio",
                "tags",
                "description",
            },
        },
        "tv_show": {
            "table": "tv_show_details",
            "columns": [
                ("creators", "creators", "str"),
                ("seasons", "seasons", "int"),
                ("episodes", "episodes", "int"),
                ("network", "network", "str"),
                ("release_year", "release_year", "int"),
                ("genres", "genres", "json_or_genre"),
                ("tags", "tags", "json"),
                ("description", "description", "str"),
            ],
            "known_keys": {
                "creators",
                "seasons",
                "episodes",
                "network",
                "release_year",
                "genres",
                "genre",
                "tags",
                "description",
            },
        },
        "video_game": {
            "table": "video_game_details",
            "columns": [
                ("developer", "developer", "str"),
                ("publisher", "publisher", "str"),
                ("platforms", "platforms", "json_or_platform"),
                ("genres", "genres", "json_or_genre"),
                ("release_year", "release_year", "int"),
                ("tags", "tags", "json"),
                ("description", "description", "str"),
            ],
            "known_keys": {
                "developer",
                "publisher",
                "platforms",
                "platform",
                "genres",
                "genre",
                "release_year",
                "tags",
                "description",
            },
        },
    }

    @staticmethod
    def _safe_int(val: Any) -> int | None:
        """Safely convert a value to int."""
        if val is None:
            return None
        if isinstance(val, int):
            return val
        try:
            return int(val)
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _to_json_array(val: Any) -> str | None:
        """Convert a value to a JSON array string.

        Bare strings are wrapped in a JSON array.  Strings that already
        look like a JSON array (start with ``[``) are returned as-is.

        Args:
            val: Value to convert (str, list, or other).

        Returns:
            JSON array string, or None if *val* is None.
        """
        if val is None:
            return None
        if isinstance(val, str):
            if val.startswith("["):
                return val  # Already JSON array
            return json.dumps([val])  # Wrap bare string
        if isinstance(val, list):
            return json.dumps(val)
        return json.dumps([val])

    @staticmethod
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

    # Columns where values should be merged additively instead of replaced.
    _MERGEABLE_COLUMNS: set[str] = {"genres", "tags"}

    def _save_detail_table(
        self, cursor: sqlite3.Cursor, db_id: int, item: ContentItem, content_type: str
    ) -> None:
        """Save item to appropriate type-specific detail table.

        For existing rows, enrichment is the source of truth:
        - Genres/tags: merged additively (new + existing)
        - All other columns: fill-only (only set if existing value is None)
        - Remaining metadata JSON: merged additively (existing keys preserved)

        For new rows, all data from ingestion is used as-is.

        Args:
            cursor: Database cursor
            db_id: Content item database ID
            item: ContentItem to save
            content_type: Content type as string
        """
        config = self._DETAIL_TABLE_CONFIG.get(content_type)
        if not config:
            return

        metadata = item.metadata or {}
        table = config["table"]
        columns = config["columns"]
        known_keys = config["known_keys"]

        # Check for an existing row
        cursor.execute(
            f"SELECT * FROM {table} WHERE content_item_id = ?",  # noqa: S608
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

        for col_name, meta_key, converter in columns:
            # Compute the incoming value from metadata
            new_value: Any
            if converter == "author":
                new_value = item.author or metadata.get(meta_key)
            elif converter == "int":
                new_value = self._safe_int(metadata.get(meta_key))
            elif converter == "json":
                new_value = self._to_json_array(metadata.get(meta_key))
            elif converter == "json_or_genre":
                raw = metadata.get("genres") or metadata.get("genre")
                new_value = self._to_json_array(raw)
            elif converter == "json_or_platform":
                raw = metadata.get("platforms") or metadata.get("platform")
                new_value = self._to_json_array(raw)
            else:
                new_value = metadata.get(meta_key)

            # Decide final value based on existing data
            if col_name in self._MERGEABLE_COLUMNS and existing_data:
                # Genres/tags: additive merge
                existing_list = self._parse_json_list(existing_data.get(col_name))
                new_list = self._parse_json_list(new_value)
                merged = merge_string_lists(existing_list, new_list)
                values.append(json.dumps(merged) if merged else new_value)
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
            metadata_json = json.dumps(merged_remaining) if merged_remaining else None
        else:
            metadata_json = (
                json.dumps(remaining_metadata) if remaining_metadata else None
            )
        col_names.append("metadata")
        values.append(metadata_json)

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
                f"UPDATE {table} SET {set_clauses} WHERE content_item_id = ?",  # noqa: S608
                update_values,
            )
        else:
            cursor.execute(
                f"INSERT INTO {table} ({col_list}) VALUES ({placeholders})",  # noqa: S608
                values,
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
            query = """
                SELECT ci.*,
                       bd.author as book_author, bd.pages, bd.isbn, bd.isbn13,
                       bd.publisher, bd.year_published as book_year,
                       bd.genres as book_genres, bd.tags as book_tags,
                       bd.description as book_description, bd.metadata as book_metadata,
                       md.director, md.runtime, md.release_year as movie_year,
                       md.genres as movie_genres, md.studio,
                       md.tags as movie_tags, md.description as movie_description,
                       md.metadata as movie_metadata,
                       td.creators, td.seasons, td.episodes, td.network,
                       td.release_year as tv_year, td.genres as tv_genres,
                       td.tags as tv_tags, td.description as tv_description,
                       td.metadata as tv_metadata,
                       vgd.developer, vgd.publisher as game_publisher,
                       vgd.platforms, vgd.genres as game_genres,
                       vgd.release_year as game_year,
                       vgd.tags as game_tags, vgd.description as game_description,
                       vgd.metadata as game_metadata
                FROM content_items ci
                LEFT JOIN book_details bd ON ci.id = bd.content_item_id
                LEFT JOIN movie_details md ON ci.id = md.content_item_id
                LEFT JOIN tv_show_details td ON ci.id = td.content_item_id
                LEFT JOIN video_game_details vgd ON ci.id = vgd.content_item_id
                WHERE ci.id = ?
            """
            params: list[Any] = [db_id]

            if user_id is not None:
                query += " AND ci.user_id = ?"
                params.append(user_id)

            cursor.execute(query, params)
            row = cursor.fetchone()
            if row:
                return self._row_to_content_item(row)
            return None

    def get_content_items(
        self,
        user_id: int | None = None,
        content_type: ContentType | None = None,
        status: ConsumptionStatus | None = None,
        min_rating: int | None = None,
        limit: int | None = None,
        offset: int = 0,
        sort_by: str = "title",
    ) -> list[ContentItem]:
        """Get content items with optional filters.

        Args:
            user_id: Filter by user ID (defaults to default user)
            content_type: Filter by content type
            status: Filter by consumption status
            min_rating: Minimum rating (inclusive)
            limit: Maximum number of results
            offset: Number of results to skip (for pagination)
            sort_by: Sort order - "title" (default, ignores articles),
                "updated_at", "rating", or "created_at"

        Returns:
            List of ContentItem objects
        """
        # Default to default user if not specified
        effective_user_id = user_id if user_id is not None else get_default_user_id()

        with self.connection() as conn:
            cursor = conn.cursor()
            query = """
                SELECT ci.*,
                       bd.author as book_author, bd.pages, bd.isbn, bd.isbn13,
                       bd.publisher, bd.year_published as book_year,
                       bd.genres as book_genres, bd.tags as book_tags,
                       bd.description as book_description, bd.metadata as book_metadata,
                       md.director, md.runtime, md.release_year as movie_year,
                       md.genres as movie_genres, md.studio,
                       md.tags as movie_tags, md.description as movie_description,
                       md.metadata as movie_metadata,
                       td.creators, td.seasons, td.episodes, td.network,
                       td.release_year as tv_year, td.genres as tv_genres,
                       td.tags as tv_tags, td.description as tv_description,
                       td.metadata as tv_metadata,
                       vgd.developer, vgd.publisher as game_publisher,
                       vgd.platforms, vgd.genres as game_genres,
                       vgd.release_year as game_year,
                       vgd.tags as game_tags, vgd.description as game_description,
                       vgd.metadata as game_metadata
                FROM content_items ci
                LEFT JOIN book_details bd ON ci.id = bd.content_item_id
                LEFT JOIN movie_details md ON ci.id = md.content_item_id
                LEFT JOIN tv_show_details td ON ci.id = td.content_item_id
                LEFT JOIN video_game_details vgd ON ci.id = vgd.content_item_id
                WHERE ci.user_id = ?
            """
            params: list[Any] = [effective_user_id]

            if content_type:
                query += " AND ci.content_type = ?"
                content_type_value = get_enum_value(content_type)
                params.append(content_type_value)

            if status:
                query += " AND ci.status = ?"
                status_value = get_enum_value(status)
                params.append(status_value)

            if min_rating is not None:
                query += " AND ci.rating >= ?"
                params.append(min_rating)

            # Apply SQL-level sorting for non-title sorts
            # Title sorting is done in Python to handle article stripping
            if sort_by == "updated_at":
                query += " ORDER BY ci.updated_at DESC"
            elif sort_by == "created_at":
                query += " ORDER BY ci.created_at DESC"
            elif sort_by == "rating":
                query += " ORDER BY ci.rating DESC NULLS LAST, ci.title ASC"
            # For "title" sort, we do it in Python after fetching

            if sort_by != "title":
                # Apply SQL LIMIT/OFFSET for non-title sorts
                if limit:
                    query += " LIMIT ?"
                    params.append(limit)
                if offset > 0:
                    query += " OFFSET ?"
                    params.append(offset)

            cursor.execute(query, params)
            rows = cursor.fetchall()
            items = [self._row_to_content_item(row) for row in rows]

            # Apply title sorting in Python (ignoring articles)
            if sort_by == "title":
                from src.utils.sorting import get_sort_title

                items.sort(key=lambda item: get_sort_title(item.title))
                # Apply offset and limit after sorting
                if offset > 0:
                    items = items[offset:]
                if limit:
                    items = items[:limit]

            return items

    def get_unconsumed_items(
        self,
        user_id: int | None = None,
        content_type: ContentType | None = None,
        limit: int | None = None,
    ) -> list[ContentItem]:
        """Get unconsumed items (status = UNREAD).

        Args:
            user_id: Filter by user ID
            content_type: Filter by content type
            limit: Maximum number of results

        Returns:
            List of unconsumed ContentItem objects
        """
        return self.get_content_items(
            user_id=user_id,
            content_type=content_type,
            status=ConsumptionStatus.UNREAD,
            limit=limit,
        )

    def get_completed_items(
        self,
        user_id: int | None = None,
        content_type: ContentType | None = None,
        min_rating: int | None = None,
        limit: int | None = None,
    ) -> list[ContentItem]:
        """Get completed items with optional minimum rating.

        Args:
            user_id: Filter by user ID
            content_type: Filter by content type
            min_rating: Minimum rating (inclusive)
            limit: Maximum number of results

        Returns:
            List of completed ContentItem objects
        """
        return self.get_content_items(
            user_id=user_id,
            content_type=content_type,
            status=ConsumptionStatus.COMPLETED,
            min_rating=min_rating,
            limit=limit,
        )

    # Configuration for reading detail table columns back into metadata.
    # Each entry: (row_column, metadata_key, parse_type)
    # parse_type: "str", "json_array", "remaining_json"
    _READ_DETAIL_CONFIG: dict[str, dict[str, Any]] = {
        "book": {
            "author_column": "book_author",
            "fields": [
                ("pages", "pages", "str"),
                ("isbn", "isbn", "str"),
                ("isbn13", "isbn13", "str"),
                ("publisher", "publisher", "str"),
                ("book_year", "year_published", "str"),
                ("book_genres", "genres", "json_array"),
                ("book_tags", "tags", "json_array"),
                ("book_description", "description", "str"),
                ("book_metadata", None, "remaining_json"),
            ],
        },
        "movie": {
            "author_column": None,
            "fields": [
                ("director", "director", "str"),
                ("runtime", "runtime", "str"),
                ("movie_year", "release_year", "str"),
                ("movie_genres", "genres", "json_array"),
                ("studio", "studio", "str"),
                ("movie_tags", "tags", "json_array"),
                ("movie_description", "description", "str"),
                ("movie_metadata", None, "remaining_json"),
            ],
        },
        "tv_show": {
            "author_column": None,
            "fields": [
                ("creators", "creators", "str"),
                ("seasons", "seasons", "str"),
                ("episodes", "episodes", "str"),
                ("network", "network", "str"),
                ("tv_year", "release_year", "str"),
                ("tv_genres", "genres", "json_array"),
                ("tv_tags", "tags", "json_array"),
                ("tv_description", "description", "str"),
                ("tv_metadata", None, "remaining_json"),
            ],
        },
        "video_game": {
            "author_column": None,
            "fields": [
                ("developer", "developer", "str"),
                ("game_publisher", "publisher", "str"),
                ("platforms", "platforms", "json_array"),
                ("game_genres", "genres", "json_array"),
                ("game_year", "release_year", "str"),
                ("game_tags", "tags", "json_array"),
                ("game_description", "description", "str"),
                ("game_metadata", None, "remaining_json"),
            ],
        },
    }

    @staticmethod
    def _get_row_value(row: sqlite3.Row, key: str, default: Any = None) -> Any:
        """Safely get value from sqlite3.Row."""
        try:
            value = row[key]
            return value if value is not None else default
        except (KeyError, IndexError):
            return default

    @staticmethod
    def _parse_json_array(val: Any) -> list[str] | None:
        """Parse a JSON array value from the database."""
        if val is None:
            return None
        if isinstance(val, list):
            return val
        try:
            parsed = json.loads(val)
            if isinstance(parsed, list):
                return parsed
            return [parsed]
        except (json.JSONDecodeError, TypeError):
            return [val] if val else None

    def _row_to_content_item(self, row: sqlite3.Row) -> ContentItem:
        """Convert a database row to ContentItem.

        Args:
            row: Database row (may include joined detail table columns)

        Returns:
            ContentItem object
        """
        content_type = ContentType(row["content_type"])
        metadata: dict[str, Any] = {}
        author: str | None = None

        config = self._READ_DETAIL_CONFIG.get(content_type.value)
        if config:
            # Get author from dedicated column if configured
            if config["author_column"]:
                author = self._get_row_value(row, config["author_column"])

            # Build metadata from detail table fields
            for row_column, meta_key, parse_type in config["fields"]:
                if parse_type == "remaining_json":
                    raw = self._get_row_value(row, row_column)
                    if raw:
                        try:
                            remaining = json.loads(raw)
                            metadata.update(remaining)
                        except (json.JSONDecodeError, TypeError):
                            pass
                elif parse_type == "json_array":
                    parsed = self._parse_json_array(
                        self._get_row_value(row, row_column)
                    )
                    if parsed:
                        metadata[meta_key] = parsed
                else:
                    value = self._get_row_value(row, row_column)
                    if value:
                        metadata[meta_key] = value

        # Parse date_completed
        date_completed = None
        if date_completed_str := self._get_row_value(row, "date_completed"):
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
            source=self._get_row_value(row, "source"),
            ignored=bool(self._get_row_value(row, "ignored")),
            metadata=metadata,
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

        Returns:
            List of (db_id, ContentItem) tuples for items needing enrichment
        """
        effective_user_id = user_id if user_id is not None else get_default_user_id()

        with self.connection() as conn:
            cursor = conn.cursor()

            # Find items without enrichment status or with needs_enrichment=TRUE
            # Optionally also include items with enrichment_quality='not_found'
            not_found_clause = ""
            if include_not_found:
                not_found_clause = " OR es.enrichment_quality = 'not_found'"

            query = f"""
                SELECT ci.id
                FROM content_items ci
                LEFT JOIN enrichment_status es ON ci.id = es.content_item_id
                WHERE ci.user_id = ?
                  AND (es.content_item_id IS NULL OR es.needs_enrichment = 1{not_found_clause})
            """
            params: list[Any] = [effective_user_id]

            if content_type:
                query += " AND ci.content_type = ?"
                content_type_value = get_enum_value(content_type)
                params.append(content_type_value)

            query += " ORDER BY ci.id LIMIT ?"
            params.append(limit)

            cursor.execute(query, params)
            rows = cursor.fetchall()

            # Fetch full items for each ID
            results = []
            for row in rows:
                db_id = row["id"]
                item = self.get_content_item(db_id, user_id=effective_user_id)
                if item:
                    results.append((db_id, item))

            return results

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
