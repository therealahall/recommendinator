"""SQLite database manager for content items."""

import json
import sqlite3
from pathlib import Path
from typing import Any, Optional, List
from datetime import date, datetime

from src.models.content import ContentItem, ContentType, ConsumptionStatus
from src.storage.schema import (
    create_schema,
    create_schema_v2,
    migrate_schema,
    get_schema_version,
    SCHEMA_VERSION,
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
        self._ensure_schema()

    def _get_connection(self) -> sqlite3.Connection:
        """Get a database connection.

        Returns:
            SQLite connection
        """
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self) -> None:
        """Ensure database schema is created and up to date."""
        conn = self._get_connection()
        try:
            current_version = get_schema_version(conn)
            if current_version == 0:
                # Create v2 schema directly for new databases
                create_schema_v2(conn)
            elif current_version < SCHEMA_VERSION:
                migrate_schema(conn, SCHEMA_VERSION)
        finally:
            conn.close()

    def save_content_item(self, item: ContentItem) -> int:
        """Save or update a content item.

        Args:
            item: ContentItem to save

        Returns:
            Database ID of the saved item
        """
        conn = self._get_connection()
        try:
            cursor = conn.cursor()

            # Helper to get enum/string value
            def get_enum_value(val: Any) -> str:
                """Get string value from enum or string."""
                return val.value if hasattr(val, "value") else str(val)

            content_type_value = get_enum_value(item.content_type)

            # Check if item exists (by external_id and content_type)
            existing_id = None
            if item.id:
                cursor.execute(
                    "SELECT id FROM content_items WHERE external_id = ? AND content_type = ?",
                    (item.id, content_type_value),
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
                        date_completed = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (
                        item.title,
                        get_enum_value(item.status),
                        item.rating,
                        item.review,
                        item.date_completed.isoformat() if item.date_completed else None,
                        existing_id,
                    ),
                )
                db_id = existing_id
            else:
                # Insert new item into base table
                cursor.execute(
                    """
                    INSERT INTO content_items
                    (external_id, title, content_type, status, rating, review, date_completed)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        item.id,
                        item.title,
                        content_type_value,
                        get_enum_value(item.status),
                        item.rating,
                        item.review,
                        item.date_completed.isoformat() if item.date_completed else None,
                    ),
                )
                db_id = cursor.lastrowid

            # Save to type-specific detail table
            self._save_detail_table(cursor, db_id, item, content_type_value)

            conn.commit()
            return db_id
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

        if content_type == "book":
            # Extract book-specific fields from metadata
            author = item.author or metadata.get("author")
            pages = metadata.get("pages")
            if isinstance(pages, str):
                try:
                    pages = int(pages)
                except ValueError:
                    pages = None

            year_published = metadata.get("year_published")
            if isinstance(year_published, str):
                try:
                    year_published = int(year_published)
                except ValueError:
                    year_published = None

            # Store remaining metadata as JSON
            remaining_metadata = {
                k: v
                for k, v in metadata.items()
                if k not in ["author", "pages", "isbn", "isbn13", "publisher", "year_published"]
            }
            metadata_json = json.dumps(remaining_metadata) if remaining_metadata else None

            cursor.execute(
                """
                INSERT OR REPLACE INTO book_details
                (content_item_id, author, pages, isbn, isbn13, publisher, year_published, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    db_id,
                    author,
                    pages,
                    metadata.get("isbn"),
                    metadata.get("isbn13"),
                    metadata.get("publisher"),
                    year_published,
                    metadata_json,
                ),
            )
        elif content_type == "movie":
            remaining_metadata = {
                k: v
                for k, v in metadata.items()
                if k not in ["director", "runtime", "release_year", "genre", "studio"]
            }
            metadata_json = json.dumps(remaining_metadata) if remaining_metadata else None

            runtime = metadata.get("runtime")
            if isinstance(runtime, str):
                try:
                    runtime = int(runtime)
                except ValueError:
                    runtime = None

            release_year = metadata.get("release_year")
            if isinstance(release_year, str):
                try:
                    release_year = int(release_year)
                except ValueError:
                    release_year = None

            cursor.execute(
                """
                INSERT OR REPLACE INTO movie_details
                (content_item_id, director, runtime, release_year, genre, studio, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    db_id,
                    metadata.get("director"),
                    runtime,
                    release_year,
                    metadata.get("genre"),
                    metadata.get("studio"),
                    metadata_json,
                ),
            )
        elif content_type == "tv_show":
            remaining_metadata = {
                k: v
                for k, v in metadata.items()
                if k not in ["creators", "seasons", "episodes", "network", "release_year"]
            }
            metadata_json = json.dumps(remaining_metadata) if remaining_metadata else None

            seasons = metadata.get("seasons")
            if isinstance(seasons, str):
                try:
                    seasons = int(seasons)
                except ValueError:
                    seasons = None

            episodes = metadata.get("episodes")
            if isinstance(episodes, str):
                try:
                    episodes = int(episodes)
                except ValueError:
                    episodes = None

            release_year = metadata.get("release_year")
            if isinstance(release_year, str):
                try:
                    release_year = int(release_year)
                except ValueError:
                    release_year = None

            cursor.execute(
                """
                INSERT OR REPLACE INTO tv_show_details
                (content_item_id, creators, seasons, episodes, network, release_year, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    db_id,
                    metadata.get("creators"),
                    seasons,
                    episodes,
                    metadata.get("network"),
                    release_year,
                    metadata_json,
                ),
            )
        elif content_type == "video_game":
            remaining_metadata = {
                k: v
                for k, v in metadata.items()
                if k not in ["developer", "publisher", "platform", "genre", "release_year"]
            }
            metadata_json = json.dumps(remaining_metadata) if remaining_metadata else None

            release_year = metadata.get("release_year")
            if isinstance(release_year, str):
                try:
                    release_year = int(release_year)
                except ValueError:
                    release_year = None

            cursor.execute(
                """
                INSERT OR REPLACE INTO video_game_details
                (content_item_id, developer, publisher, platform, genre, release_year, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    db_id,
                    metadata.get("developer"),
                    metadata.get("publisher"),
                    metadata.get("platform"),
                    metadata.get("genre"),
                    release_year,
                    metadata_json,
                ),
            )

    def get_content_item(self, db_id: int) -> Optional[ContentItem]:
        """Get a content item by database ID.

        Args:
            db_id: Database ID

        Returns:
            ContentItem if found, None otherwise
        """
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            # Join with appropriate detail table based on content_type
            cursor.execute(
                """
                SELECT ci.*, 
                       bd.author as book_author, bd.pages, bd.isbn, bd.isbn13, bd.publisher, bd.year_published as book_year, bd.metadata as book_metadata,
                       md.director, md.runtime, md.release_year as movie_year, md.genre as movie_genre, md.studio, md.metadata as movie_metadata,
                       td.creators, td.seasons, td.episodes, td.network, td.release_year as tv_year, td.metadata as tv_metadata,
                       vgd.developer, vgd.publisher as game_publisher, vgd.platform, vgd.genre as game_genre, vgd.release_year as game_year, vgd.metadata as game_metadata
                FROM content_items ci
                LEFT JOIN book_details bd ON ci.id = bd.content_item_id
                LEFT JOIN movie_details md ON ci.id = md.content_item_id
                LEFT JOIN tv_show_details td ON ci.id = td.content_item_id
                LEFT JOIN video_game_details vgd ON ci.id = vgd.content_item_id
                WHERE ci.id = ?
                """,
                (db_id,),
            )
            row = cursor.fetchone()
            if row:
                return self._row_to_content_item(row)
            return None
        finally:
            conn.close()

    def get_content_items(
        self,
        content_type: Optional[ContentType] = None,
        status: Optional[ConsumptionStatus] = None,
        min_rating: Optional[int] = None,
        limit: Optional[int] = None,
    ) -> List[ContentItem]:
        """Get content items with optional filters.

        Args:
            content_type: Filter by content type
            status: Filter by consumption status
            min_rating: Minimum rating (inclusive)
            limit: Maximum number of results

        Returns:
            List of ContentItem objects
        """
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            # Join with all detail tables to get complete data
            query = """
                SELECT ci.*, 
                       bd.author as book_author, bd.pages, bd.isbn, bd.isbn13, bd.publisher, bd.year_published as book_year, bd.metadata as book_metadata,
                       md.director, md.runtime, md.release_year as movie_year, md.genre as movie_genre, md.studio, md.metadata as movie_metadata,
                       td.creators, td.seasons, td.episodes, td.network, td.release_year as tv_year, td.metadata as tv_metadata,
                       vgd.developer, vgd.publisher as game_publisher, vgd.platform, vgd.genre as game_genre, vgd.release_year as game_year, vgd.metadata as game_metadata
                FROM content_items ci
                LEFT JOIN book_details bd ON ci.id = bd.content_item_id
                LEFT JOIN movie_details md ON ci.id = md.content_item_id
                LEFT JOIN tv_show_details td ON ci.id = td.content_item_id
                LEFT JOIN video_game_details vgd ON ci.id = vgd.content_item_id
                WHERE 1=1
            """
            params: List[Any] = []

            if content_type:
                query += " AND ci.content_type = ?"
                content_type_value = (
                    content_type.value if hasattr(content_type, "value") else str(content_type)
                )
                params.append(content_type_value)

            if status:
                query += " AND ci.status = ?"
                status_value = status.value if hasattr(status, "value") else str(status)
                params.append(status_value)

            if min_rating:
                query += " AND ci.rating >= ?"
                params.append(min_rating)

            query += " ORDER BY ci.updated_at DESC"

            if limit:
                query += " LIMIT ?"
                params.append(limit)

            cursor.execute(query, params)
            rows = cursor.fetchall()
            return [self._row_to_content_item(row) for row in rows]
        finally:
            conn.close()

    def get_unconsumed_items(
        self, content_type: Optional[ContentType] = None, limit: Optional[int] = None
    ) -> List[ContentItem]:
        """Get unconsumed items (status = UNREAD).

        Args:
            content_type: Filter by content type
            limit: Maximum number of results

        Returns:
            List of unconsumed ContentItem objects
        """
        return self.get_content_items(
            content_type=content_type,
            status=ConsumptionStatus.UNREAD,
            limit=limit,
        )

    def get_completed_items(
        self,
        content_type: Optional[ContentType] = None,
        min_rating: Optional[int] = None,
        limit: Optional[int] = None,
    ) -> List[ContentItem]:
        """Get completed items with optional minimum rating.

        Args:
            content_type: Filter by content type
            min_rating: Minimum rating (inclusive)
            limit: Maximum number of results

        Returns:
            List of completed ContentItem objects
        """
        return self.get_content_items(
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
        metadata = {}

        # Helper to safely get row value
        def get_row_value(key: str, default: Any = None) -> Any:
            """Safely get value from sqlite3.Row."""
            try:
                value = row[key]
                return value if value is not None else default
            except (KeyError, IndexError):
                return default

        # Build metadata from detail table based on content type
        if content_type == ContentType.BOOK:
            author = get_row_value("book_author")
            pages = get_row_value("pages")
            if pages:
                metadata["pages"] = pages
            isbn = get_row_value("isbn")
            if isbn:
                metadata["isbn"] = isbn
            isbn13 = get_row_value("isbn13")
            if isbn13:
                metadata["isbn13"] = isbn13
            publisher = get_row_value("publisher")
            if publisher:
                metadata["publisher"] = publisher
            book_year = get_row_value("book_year")
            if book_year:
                metadata["year_published"] = book_year
            # Add remaining metadata from JSON
            book_metadata = get_row_value("book_metadata")
            if book_metadata:
                try:
                    remaining = json.loads(book_metadata)
                    metadata.update(remaining)
                except (json.JSONDecodeError, TypeError):
                    pass

        elif content_type == ContentType.MOVIE:
            director = get_row_value("director")
            if director:
                metadata["director"] = director
            runtime = get_row_value("runtime")
            if runtime:
                metadata["runtime"] = runtime
            movie_year = get_row_value("movie_year")
            if movie_year:
                metadata["release_year"] = movie_year
            movie_genre = get_row_value("movie_genre")
            if movie_genre:
                metadata["genre"] = movie_genre
            studio = get_row_value("studio")
            if studio:
                metadata["studio"] = studio
            movie_metadata = get_row_value("movie_metadata")
            if movie_metadata:
                try:
                    remaining = json.loads(movie_metadata)
                    metadata.update(remaining)
                except (json.JSONDecodeError, TypeError):
                    pass

        elif content_type == ContentType.TV_SHOW:
            creators = get_row_value("creators")
            if creators:
                metadata["creators"] = creators
            seasons = get_row_value("seasons")
            if seasons:
                metadata["seasons"] = seasons
            episodes = get_row_value("episodes")
            if episodes:
                metadata["episodes"] = episodes
            network = get_row_value("network")
            if network:
                metadata["network"] = network
            tv_year = get_row_value("tv_year")
            if tv_year:
                metadata["release_year"] = tv_year
            tv_metadata = get_row_value("tv_metadata")
            if tv_metadata:
                try:
                    remaining = json.loads(tv_metadata)
                    metadata.update(remaining)
                except (json.JSONDecodeError, TypeError):
                    pass

        elif content_type == ContentType.VIDEO_GAME:
            developer = get_row_value("developer")
            if developer:
                metadata["developer"] = developer
            game_publisher = get_row_value("game_publisher")
            if game_publisher:
                metadata["publisher"] = game_publisher
            platform = get_row_value("platform")
            if platform:
                metadata["platform"] = platform
            game_genre = get_row_value("game_genre")
            if game_genre:
                metadata["genre"] = game_genre
            game_year = get_row_value("game_year")
            if game_year:
                metadata["release_year"] = game_year
            game_metadata = get_row_value("game_metadata")
            if game_metadata:
                try:
                    remaining = json.loads(game_metadata)
                    metadata.update(remaining)
                except (json.JSONDecodeError, TypeError):
                    pass

        # Parse date_completed
        date_completed = None
        date_completed_str = get_row_value("date_completed")
        if date_completed_str:
            try:
                date_completed = datetime.fromisoformat(date_completed_str).date()
            except (ValueError, AttributeError):
                pass

        # Get author (only for books)
        author = None
        if content_type == ContentType.BOOK:
            author = get_row_value("book_author")

        return ContentItem(
            id=row["external_id"],
            title=row["title"],
            author=author,
            content_type=content_type,
            rating=row["rating"],
            review=row["review"],
            status=ConsumptionStatus(row["status"]),
            date_completed=date_completed,
            metadata=metadata,
        )

    def delete_content_item(self, db_id: int) -> bool:
        """Delete a content item by database ID.

        Args:
            db_id: Database ID

        Returns:
            True if item was deleted, False if not found
        """
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM content_items WHERE id = ?", (db_id,))
            conn.commit()
            return cursor.rowcount > 0
        finally:
            conn.close()

    def count_items(
        self,
        content_type: Optional[ContentType] = None,
        status: Optional[ConsumptionStatus] = None,
    ) -> int:
        """Count content items with optional filters.

        Args:
            content_type: Filter by content type
            status: Filter by consumption status

        Returns:
            Number of matching items
        """
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            query = "SELECT COUNT(*) FROM content_items WHERE 1=1"
            params: List[Any] = []

            if content_type:
                query += " AND content_type = ?"
                content_type_value = (
                    content_type.value if hasattr(content_type, "value") else str(content_type)
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
