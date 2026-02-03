"""SQLite database manager for content items."""

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from src.models.content import ConsumptionStatus, ContentItem, ContentType
from src.storage.schema import create_schema, get_default_user_id


class SQLiteDB:
    """SQLite database manager for content items."""

    def __init__(self, db_path: Path) -> None:
        """Initialize SQLite database manager.

        Args:
            db_path: Path to SQLite database file
        """
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def _get_connection(self) -> sqlite3.Connection:
        """Get a database connection.

        Returns:
            SQLite connection
        """
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        # Enable foreign keys
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _ensure_schema(self) -> None:
        """Ensure database schema is created."""
        conn = self._get_connection()
        try:
            create_schema(conn)
        finally:
            conn.close()

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

        conn = self._get_connection()
        try:
            cursor = conn.cursor()

            # Helper to get enum/string value
            def get_enum_value(val: Any) -> str:
                """Get string value from enum or string."""
                return val.value if hasattr(val, "value") else str(val)

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

            if existing_id:
                # Update existing item in base table
                cursor.execute(
                    """
                    UPDATE content_items
                    SET title = ?, status = ?, rating = ?, review = ?,
                        date_completed = ?, source = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (
                        item.title,
                        get_enum_value(item.status),
                        item.rating,
                        item.review,
                        (
                            item.date_completed.isoformat()
                            if item.date_completed
                            else None
                        ),
                        item.source,
                        existing_id,
                    ),
                )
                db_id = existing_id
            else:
                # Insert new item into base table
                cursor.execute(
                    """
                    INSERT INTO content_items
                    (user_id, external_id, title, content_type, status, rating, review,
                     date_completed, source)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    ),
                )
                db_id = cursor.lastrowid

            # Save to type-specific detail table
            self._save_detail_table(cursor, db_id, item, content_type_value)

            conn.commit()
            return db_id  # type: ignore
        finally:
            conn.close()

    def _save_detail_table(
        self, cursor: sqlite3.Cursor, db_id: int, item: ContentItem, content_type: str
    ) -> None:
        """Save item to appropriate type-specific detail table.

        Args:
            cursor: Database cursor
            db_id: Content item database ID
            item: ContentItem to save
            content_type: Content type as string
        """
        metadata = item.metadata or {}

        # Helper to safely convert to int
        def safe_int(val: Any) -> int | None:
            if val is None:
                return None
            if isinstance(val, int):
                return val
            try:
                return int(val)
            except (ValueError, TypeError):
                return None

        # Helper to convert list to JSON
        def to_json_array(val: Any) -> str | None:
            if val is None:
                return None
            if isinstance(val, str):
                return val  # Already JSON or single value
            if isinstance(val, list):
                return json.dumps(val)
            return json.dumps([val])

        if content_type == "book":
            author = item.author or metadata.get("author")
            genres = metadata.get("genres") or metadata.get("genre")
            tags = metadata.get("tags")
            description = metadata.get("description")

            # Store remaining metadata as JSON
            remaining_metadata = {
                k: v
                for k, v in metadata.items()
                if k
                not in [
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
                ]
            }
            metadata_json = (
                json.dumps(remaining_metadata) if remaining_metadata else None
            )

            cursor.execute(
                """
                INSERT OR REPLACE INTO book_details
                (content_item_id, author, pages, isbn, isbn13, publisher, year_published,
                 genres, tags, description, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    db_id,
                    author,
                    safe_int(metadata.get("pages")),
                    metadata.get("isbn"),
                    metadata.get("isbn13"),
                    metadata.get("publisher"),
                    safe_int(metadata.get("year_published")),
                    to_json_array(genres),
                    to_json_array(tags),
                    description,
                    metadata_json,
                ),
            )
        elif content_type == "movie":
            genres = metadata.get("genres") or metadata.get("genre")
            tags = metadata.get("tags")
            description = metadata.get("description")
            remaining_metadata = {
                k: v
                for k, v in metadata.items()
                if k
                not in [
                    "director",
                    "runtime",
                    "release_year",
                    "genres",
                    "genre",
                    "studio",
                    "tags",
                    "description",
                ]
            }
            metadata_json = (
                json.dumps(remaining_metadata) if remaining_metadata else None
            )

            cursor.execute(
                """
                INSERT OR REPLACE INTO movie_details
                (content_item_id, director, runtime, release_year, genres, studio,
                 tags, description, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    db_id,
                    metadata.get("director"),
                    safe_int(metadata.get("runtime")),
                    safe_int(metadata.get("release_year")),
                    to_json_array(genres),
                    metadata.get("studio"),
                    to_json_array(tags),
                    description,
                    metadata_json,
                ),
            )
        elif content_type == "tv_show":
            genres = metadata.get("genres") or metadata.get("genre")
            tags = metadata.get("tags")
            description = metadata.get("description")
            remaining_metadata = {
                k: v
                for k, v in metadata.items()
                if k
                not in [
                    "creators",
                    "seasons",
                    "episodes",
                    "network",
                    "release_year",
                    "genres",
                    "genre",
                    "tags",
                    "description",
                ]
            }
            metadata_json = (
                json.dumps(remaining_metadata) if remaining_metadata else None
            )

            cursor.execute(
                """
                INSERT OR REPLACE INTO tv_show_details
                (content_item_id, creators, seasons, episodes, network, release_year,
                 genres, tags, description, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    db_id,
                    metadata.get("creators"),
                    safe_int(metadata.get("seasons")),
                    safe_int(metadata.get("episodes")),
                    metadata.get("network"),
                    safe_int(metadata.get("release_year")),
                    to_json_array(genres),
                    to_json_array(tags),
                    description,
                    metadata_json,
                ),
            )
        elif content_type == "video_game":
            genres = metadata.get("genres") or metadata.get("genre")
            tags = metadata.get("tags")
            description = metadata.get("description")
            platforms = metadata.get("platforms") or metadata.get("platform")
            remaining_metadata = {
                k: v
                for k, v in metadata.items()
                if k
                not in [
                    "developer",
                    "publisher",
                    "platforms",
                    "platform",
                    "genres",
                    "genre",
                    "release_year",
                    "tags",
                    "description",
                ]
            }
            metadata_json = (
                json.dumps(remaining_metadata) if remaining_metadata else None
            )

            cursor.execute(
                """
                INSERT OR REPLACE INTO video_game_details
                (content_item_id, developer, publisher, platforms, genres, release_year,
                 tags, description, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    db_id,
                    metadata.get("developer"),
                    metadata.get("publisher"),
                    to_json_array(platforms),
                    to_json_array(genres),
                    safe_int(metadata.get("release_year")),
                    to_json_array(tags),
                    description,
                    metadata_json,
                ),
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
        conn = self._get_connection()
        try:
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
        finally:
            conn.close()

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

        conn = self._get_connection()
        try:
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
                content_type_value = (
                    content_type.value
                    if hasattr(content_type, "value")
                    else str(content_type)
                )
                params.append(content_type_value)

            if status:
                query += " AND ci.status = ?"
                status_value = status.value if hasattr(status, "value") else str(status)
                params.append(status_value)

            if min_rating:
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
        finally:
            conn.close()

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

    def _row_to_content_item(self, row: sqlite3.Row) -> ContentItem:
        """Convert a database row to ContentItem.

        Args:
            row: Database row (may include joined detail table columns)

        Returns:
            ContentItem object
        """
        content_type = ContentType(row["content_type"])
        metadata: dict[str, Any] = {}

        # Helper to safely get row value
        def get_row_value(key: str, default: Any = None) -> Any:
            """Safely get value from sqlite3.Row."""
            try:
                value = row[key]
                return value if value is not None else default
            except (KeyError, IndexError):
                return default

        # Helper to parse JSON array
        def parse_json_array(val: Any) -> list[str] | None:
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

        # Build metadata from detail table based on content type
        if content_type == ContentType.BOOK:
            author = get_row_value("book_author")
            if pages := get_row_value("pages"):
                metadata["pages"] = pages
            if isbn := get_row_value("isbn"):
                metadata["isbn"] = isbn
            if isbn13 := get_row_value("isbn13"):
                metadata["isbn13"] = isbn13
            if publisher := get_row_value("publisher"):
                metadata["publisher"] = publisher
            if book_year := get_row_value("book_year"):
                metadata["year_published"] = book_year
            if genres := parse_json_array(get_row_value("book_genres")):
                metadata["genres"] = genres
            if tags := parse_json_array(get_row_value("book_tags")):
                metadata["tags"] = tags
            if description := get_row_value("book_description"):
                metadata["description"] = description
            if book_metadata := get_row_value("book_metadata"):
                try:
                    remaining = json.loads(book_metadata)
                    metadata.update(remaining)
                except (json.JSONDecodeError, TypeError):
                    pass

        elif content_type == ContentType.MOVIE:
            author = None
            if director := get_row_value("director"):
                metadata["director"] = director
            if runtime := get_row_value("runtime"):
                metadata["runtime"] = runtime
            if movie_year := get_row_value("movie_year"):
                metadata["release_year"] = movie_year
            if genres := parse_json_array(get_row_value("movie_genres")):
                metadata["genres"] = genres
            if studio := get_row_value("studio"):
                metadata["studio"] = studio
            if tags := parse_json_array(get_row_value("movie_tags")):
                metadata["tags"] = tags
            if description := get_row_value("movie_description"):
                metadata["description"] = description
            if movie_metadata := get_row_value("movie_metadata"):
                try:
                    remaining = json.loads(movie_metadata)
                    metadata.update(remaining)
                except (json.JSONDecodeError, TypeError):
                    pass

        elif content_type == ContentType.TV_SHOW:
            author = None
            if creators := get_row_value("creators"):
                metadata["creators"] = creators
            if seasons := get_row_value("seasons"):
                metadata["seasons"] = seasons
            if episodes := get_row_value("episodes"):
                metadata["episodes"] = episodes
            if network := get_row_value("network"):
                metadata["network"] = network
            if tv_year := get_row_value("tv_year"):
                metadata["release_year"] = tv_year
            if genres := parse_json_array(get_row_value("tv_genres")):
                metadata["genres"] = genres
            if tags := parse_json_array(get_row_value("tv_tags")):
                metadata["tags"] = tags
            if description := get_row_value("tv_description"):
                metadata["description"] = description
            if tv_metadata := get_row_value("tv_metadata"):
                try:
                    remaining = json.loads(tv_metadata)
                    metadata.update(remaining)
                except (json.JSONDecodeError, TypeError):
                    pass

        elif content_type == ContentType.VIDEO_GAME:
            author = None
            if developer := get_row_value("developer"):
                metadata["developer"] = developer
            if game_publisher := get_row_value("game_publisher"):
                metadata["publisher"] = game_publisher
            if platforms := parse_json_array(get_row_value("platforms")):
                metadata["platforms"] = platforms
            if genres := parse_json_array(get_row_value("game_genres")):
                metadata["genres"] = genres
            if game_year := get_row_value("game_year"):
                metadata["release_year"] = game_year
            if tags := parse_json_array(get_row_value("game_tags")):
                metadata["tags"] = tags
            if description := get_row_value("game_description"):
                metadata["description"] = description
            if game_metadata := get_row_value("game_metadata"):
                try:
                    remaining = json.loads(game_metadata)
                    metadata.update(remaining)
                except (json.JSONDecodeError, TypeError):
                    pass
        else:  # pragma: no cover
            author = None  # type: ignore[unreachable]

        # Parse date_completed
        date_completed = None
        if date_completed_str := get_row_value("date_completed"):
            try:
                date_completed = datetime.fromisoformat(date_completed_str).date()
            except (ValueError, AttributeError):
                pass

        # Get author for books
        if content_type == ContentType.BOOK:
            author = get_row_value("book_author")

        return ContentItem(
            user_id=row["user_id"],
            id=row["external_id"],
            title=row["title"],
            author=author,
            content_type=content_type,
            rating=row["rating"],
            review=row["review"],
            status=ConsumptionStatus(row["status"]),
            date_completed=date_completed,
            source=get_row_value("source"),
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
        conn = self._get_connection()
        try:
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
        finally:
            conn.close()

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

        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            query = "SELECT COUNT(*) FROM content_items WHERE user_id = ?"
            params: list[Any] = [effective_user_id]

            if content_type:
                query += " AND content_type = ?"
                content_type_value = (
                    content_type.value
                    if hasattr(content_type, "value")
                    else str(content_type)
                )
                params.append(content_type_value)

            if status:
                query += " AND status = ?"
                status_value = status.value if hasattr(status, "value") else str(status)
                params.append(status_value)

            cursor.execute(query, params)
            result = cursor.fetchone()
            return result[0] if result else 0
        finally:
            conn.close()

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

        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            content_type_value = (
                content_type.value
                if hasattr(content_type, "value")
                else str(content_type)
            )
            cursor.execute(
                """SELECT id FROM content_items
                   WHERE user_id = ? AND external_id = ? AND content_type = ?""",
                (effective_user_id, external_id, content_type_value),
            )
            row = cursor.fetchone()
            if row:
                return self.get_content_item(row["id"], user_id=effective_user_id)
            return None
        finally:
            conn.close()

    def get_items_needing_enrichment(
        self,
        content_type: ContentType | None = None,
        user_id: int | None = None,
        limit: int = 100,
    ) -> list[tuple[int, ContentItem]]:
        """Get content items that need enrichment.

        Returns items where:
        1. No enrichment_status record exists (new items), OR
        2. needs_enrichment = TRUE

        Args:
            content_type: Optional filter by content type
            user_id: Filter by user ID (defaults to default user)
            limit: Maximum number of items to return

        Returns:
            List of (db_id, ContentItem) tuples for items needing enrichment
        """
        effective_user_id = user_id if user_id is not None else get_default_user_id()

        conn = self._get_connection()
        try:
            cursor = conn.cursor()

            # Find items without enrichment status or with needs_enrichment=TRUE
            query = """
                SELECT ci.id
                FROM content_items ci
                LEFT JOIN enrichment_status es ON ci.id = es.content_item_id
                WHERE ci.user_id = ?
                  AND (es.content_item_id IS NULL OR es.needs_enrichment = 1)
            """
            params: list[Any] = [effective_user_id]

            if content_type:
                query += " AND ci.content_type = ?"
                content_type_value = (
                    content_type.value
                    if hasattr(content_type, "value")
                    else str(content_type)
                )
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
        finally:
            conn.close()

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

        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            content_type_value = (
                content_type.value
                if hasattr(content_type, "value")
                else str(content_type)
            )
            cursor.execute(
                """SELECT id FROM content_items
                   WHERE user_id = ? AND external_id = ? AND content_type = ?""",
                (effective_user_id, external_id, content_type_value),
            )
            row = cursor.fetchone()
            return row["id"] if row else None
        finally:
            conn.close()
