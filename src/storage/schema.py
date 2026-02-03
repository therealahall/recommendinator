"""Database schema definitions."""

import json
import sqlite3


def create_schema(conn: sqlite3.Connection) -> None:
    """Create the database schema.

    Includes:
    - Users table for multi-user support
    - Content items with user_id foreign key
    - Type-specific detail tables (books, movies, TV shows, games)
    - Preference interpretation cache

    Args:
        conn: SQLite database connection
    """
    cursor = conn.cursor()

    # Enable foreign keys
    cursor.execute("PRAGMA foreign_keys = ON")

    # Users table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            display_name TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            settings TEXT  -- JSON for per-user settings (AI enabled, weights, etc.)
        )
        """)

    # Create default user
    cursor.execute("""
        INSERT OR IGNORE INTO users (id, username, display_name)
        VALUES (1, 'default', 'Default User')
        """)

    # Base content items table with user_id
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS content_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL DEFAULT 1 REFERENCES users(id) ON DELETE CASCADE,
            external_id TEXT,
            title TEXT NOT NULL,
            content_type TEXT NOT NULL,
            status TEXT NOT NULL,
            rating INTEGER CHECK (rating >= 1 AND rating <= 5),
            review TEXT,
            date_completed DATE,
            source TEXT,  -- Which plugin/source this came from (goodreads, steam, etc.)
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, external_id, content_type)
        )
        """)

    # Book-specific details
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS book_details (
            content_item_id INTEGER PRIMARY KEY REFERENCES content_items(id) ON DELETE CASCADE,
            author TEXT,
            pages INTEGER,
            isbn TEXT,
            isbn13 TEXT,
            publisher TEXT,
            year_published INTEGER,
            genres TEXT,  -- JSON array of genres
            metadata TEXT  -- JSON for additional fields
        )
        """)

    # Movie-specific details
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS movie_details (
            content_item_id INTEGER PRIMARY KEY REFERENCES content_items(id) ON DELETE CASCADE,
            director TEXT,
            runtime INTEGER,  -- minutes
            release_year INTEGER,
            genres TEXT,  -- JSON array of genres
            studio TEXT,
            metadata TEXT
        )
        """)

    # TV Show-specific details
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS tv_show_details (
            content_item_id INTEGER PRIMARY KEY REFERENCES content_items(id) ON DELETE CASCADE,
            creators TEXT,
            seasons INTEGER,
            episodes INTEGER,
            network TEXT,
            release_year INTEGER,
            genres TEXT,  -- JSON array of genres
            metadata TEXT
        )
        """)

    # Video Game-specific details
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS video_game_details (
            content_item_id INTEGER PRIMARY KEY REFERENCES content_items(id) ON DELETE CASCADE,
            developer TEXT,
            publisher TEXT,
            platforms TEXT,  -- JSON array of platforms
            genres TEXT,  -- JSON array of genres
            release_year INTEGER,
            metadata TEXT
        )
        """)

    # Create indexes for common queries
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_content_user ON content_items(user_id)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_content_type ON content_items(content_type)"
    )
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_status ON content_items(status)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_rating ON content_items(rating)")
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_date_completed ON content_items(date_completed)"
    )
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_source ON content_items(source)")
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_user_type ON content_items(user_id, content_type)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_user_status ON content_items(user_id, status)"
    )

    # Indexes for type-specific fields
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_book_author ON book_details(author)")
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_movie_director ON movie_details(director)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_game_developer ON video_game_details(developer)"
    )

    # Preference interpretation cache (for LLM interpretations)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS preference_interpretation_cache (
            cache_key TEXT PRIMARY KEY,
            interpretation_json TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """)

    # Enrichment status tracking
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS enrichment_status (
            content_item_id INTEGER PRIMARY KEY
                REFERENCES content_items(id) ON DELETE CASCADE,
            last_enriched_at TIMESTAMP,
            enrichment_provider TEXT,
            enrichment_quality TEXT,
            needs_enrichment BOOLEAN DEFAULT 1,
            enrichment_error TEXT
        )
        """)

    # Index for finding items that need enrichment
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_enrichment_needs "
        "ON enrichment_status(needs_enrichment)"
    )

    # Add tags and description columns to detail tables if they don't exist
    # Use safe ALTER TABLE that checks for column existence
    _add_column_if_not_exists(cursor, "book_details", "tags", "TEXT")
    _add_column_if_not_exists(cursor, "book_details", "description", "TEXT")
    _add_column_if_not_exists(cursor, "movie_details", "tags", "TEXT")
    _add_column_if_not_exists(cursor, "movie_details", "description", "TEXT")
    _add_column_if_not_exists(cursor, "tv_show_details", "tags", "TEXT")
    _add_column_if_not_exists(cursor, "tv_show_details", "description", "TEXT")
    _add_column_if_not_exists(cursor, "video_game_details", "tags", "TEXT")
    _add_column_if_not_exists(cursor, "video_game_details", "description", "TEXT")

    conn.commit()


def _add_column_if_not_exists(
    cursor: sqlite3.Cursor, table: str, column: str, column_type: str
) -> None:
    """Add a column to a table if it doesn't already exist.

    Args:
        cursor: SQLite cursor
        table: Table name
        column: Column name to add
        column_type: SQL type for the column
    """
    # Check if column exists using PRAGMA table_info
    cursor.execute(f"PRAGMA table_info({table})")
    columns = [row[1] for row in cursor.fetchall()]

    if column not in columns:
        cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {column_type}")


# User management functions


def get_user_by_id(conn: sqlite3.Connection, user_id: int) -> dict | None:
    """Get a user by ID.

    Args:
        conn: SQLite database connection
        user_id: User ID

    Returns:
        User dict or None if not found
    """
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, username, display_name, created_at, settings FROM users WHERE id = ?",
        (user_id,),
    )
    row = cursor.fetchone()
    if row:
        settings = None
        if row[4]:
            try:
                settings = json.loads(row[4])
            except (json.JSONDecodeError, TypeError):
                settings = {}
        return {
            "id": row[0],
            "username": row[1],
            "display_name": row[2],
            "created_at": row[3],
            "settings": settings,
        }
    return None


def get_user_by_username(conn: sqlite3.Connection, username: str) -> dict | None:
    """Get a user by username.

    Args:
        conn: SQLite database connection
        username: Username

    Returns:
        User dict or None if not found
    """
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, username, display_name, created_at, settings FROM users WHERE username = ?",
        (username,),
    )
    row = cursor.fetchone()
    if row:
        settings = None
        if row[4]:
            try:
                settings = json.loads(row[4])
            except (json.JSONDecodeError, TypeError):
                settings = {}
        return {
            "id": row[0],
            "username": row[1],
            "display_name": row[2],
            "created_at": row[3],
            "settings": settings,
        }
    return None


def create_user(
    conn: sqlite3.Connection,
    username: str,
    display_name: str | None = None,
    settings: dict | None = None,
) -> int:
    """Create a new user.

    Args:
        conn: SQLite database connection
        username: Unique username
        display_name: Optional display name
        settings: Optional settings dict

    Returns:
        New user ID
    """
    cursor = conn.cursor()
    settings_json = json.dumps(settings) if settings else None
    cursor.execute(
        "INSERT INTO users (username, display_name, settings) VALUES (?, ?, ?)",
        (username, display_name, settings_json),
    )
    conn.commit()
    return cursor.lastrowid  # type: ignore


def update_user_settings(
    conn: sqlite3.Connection, user_id: int, settings: dict
) -> None:
    """Update user settings.

    Args:
        conn: SQLite database connection
        user_id: User ID
        settings: Settings dict to merge with existing
    """
    cursor = conn.cursor()

    # Get existing settings
    cursor.execute("SELECT settings FROM users WHERE id = ?", (user_id,))
    row = cursor.fetchone()
    if row and row[0]:
        try:
            existing = json.loads(row[0])
        except (json.JSONDecodeError, TypeError):
            existing = {}
    else:
        existing = {}

    # Merge settings
    existing.update(settings)

    cursor.execute(
        "UPDATE users SET settings = ? WHERE id = ?",
        (json.dumps(existing), user_id),
    )
    conn.commit()


def get_all_users(conn: sqlite3.Connection) -> list[dict]:
    """Get all users.

    Args:
        conn: SQLite database connection

    Returns:
        List of user dicts ordered by id
    """
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, username, display_name, created_at, settings FROM users ORDER BY id"
    )
    users = []
    for row in cursor.fetchall():
        settings = None
        if row[4]:
            try:
                settings = json.loads(row[4])
            except (json.JSONDecodeError, TypeError):
                settings = {}
        users.append(
            {
                "id": row[0],
                "username": row[1],
                "display_name": row[2],
                "created_at": row[3],
                "settings": settings,
            }
        )
    return users


def get_default_user_id() -> int:
    """Get the default user ID.

    Returns:
        Default user ID (always 1)
    """
    return 1


# Preference interpretation cache functions


def get_cached_preference_interpretation(
    conn: sqlite3.Connection, cache_key: str
) -> str | None:
    """Get a cached preference interpretation.

    Args:
        conn: SQLite database connection
        cache_key: The cache key to look up

    Returns:
        Cached JSON string or None if not found
    """
    cursor = conn.cursor()
    cursor.execute(
        "SELECT interpretation_json FROM preference_interpretation_cache WHERE cache_key = ?",
        (cache_key,),
    )
    row = cursor.fetchone()
    return row[0] if row else None


def save_cached_preference_interpretation(
    conn: sqlite3.Connection, cache_key: str, interpretation_json: str
) -> None:
    """Save a preference interpretation to the cache.

    Args:
        conn: SQLite database connection
        cache_key: The cache key
        interpretation_json: JSON string of the interpretation
    """
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT OR REPLACE INTO preference_interpretation_cache
        (cache_key, interpretation_json, created_at)
        VALUES (?, ?, CURRENT_TIMESTAMP)
        """,
        (cache_key, interpretation_json),
    )
    conn.commit()


def clear_cached_preference_interpretations(conn: sqlite3.Connection) -> int:
    """Clear all cached preference interpretations.

    Args:
        conn: SQLite database connection

    Returns:
        Number of rows deleted
    """
    cursor = conn.cursor()
    cursor.execute("DELETE FROM preference_interpretation_cache")
    deleted = cursor.rowcount
    conn.commit()
    return deleted


# Enrichment status functions


def get_enrichment_status(
    conn: sqlite3.Connection, content_item_id: int
) -> dict | None:
    """Get enrichment status for a content item.

    Args:
        conn: SQLite database connection
        content_item_id: Content item database ID

    Returns:
        Enrichment status dict or None if not found
    """
    cursor = conn.cursor()
    cursor.execute(
        """SELECT content_item_id, last_enriched_at, enrichment_provider,
                  enrichment_quality, needs_enrichment, enrichment_error
           FROM enrichment_status WHERE content_item_id = ?""",
        (content_item_id,),
    )
    row = cursor.fetchone()
    if row:
        return {
            "content_item_id": row[0],
            "last_enriched_at": row[1],
            "enrichment_provider": row[2],
            "enrichment_quality": row[3],
            "needs_enrichment": bool(row[4]),
            "enrichment_error": row[5],
        }
    return None


def mark_enrichment_complete(
    conn: sqlite3.Connection,
    content_item_id: int,
    provider: str,
    quality: str,
) -> None:
    """Mark an item as successfully enriched.

    Args:
        conn: SQLite database connection
        content_item_id: Content item database ID
        provider: Name of the provider that enriched the item
        quality: Match quality ("high", "medium", "not_found")
    """
    cursor = conn.cursor()
    cursor.execute(
        """INSERT OR REPLACE INTO enrichment_status
           (content_item_id, last_enriched_at, enrichment_provider,
            enrichment_quality, needs_enrichment, enrichment_error)
           VALUES (?, CURRENT_TIMESTAMP, ?, ?, 0, NULL)""",
        (content_item_id, provider, quality),
    )
    conn.commit()


def mark_enrichment_failed(
    conn: sqlite3.Connection,
    content_item_id: int,
    error: str,
) -> None:
    """Mark an item's enrichment as failed.

    Args:
        conn: SQLite database connection
        content_item_id: Content item database ID
        error: Error message describing the failure
    """
    cursor = conn.cursor()
    cursor.execute(
        """INSERT OR REPLACE INTO enrichment_status
           (content_item_id, last_enriched_at, enrichment_provider,
            enrichment_quality, needs_enrichment, enrichment_error)
           VALUES (?, CURRENT_TIMESTAMP, NULL, NULL, 0, ?)""",
        (content_item_id, error),
    )
    conn.commit()


def mark_item_needs_enrichment(
    conn: sqlite3.Connection,
    content_item_id: int,
) -> None:
    """Mark an item as needing enrichment.

    Args:
        conn: SQLite database connection
        content_item_id: Content item database ID
    """
    cursor = conn.cursor()
    cursor.execute(
        """INSERT OR REPLACE INTO enrichment_status
           (content_item_id, needs_enrichment)
           VALUES (?, 1)""",
        (content_item_id,),
    )
    conn.commit()


def reset_enrichment_status(
    conn: sqlite3.Connection,
    provider: str | None = None,
    content_type: str | None = None,
    user_id: int | None = None,
) -> int:
    """Reset enrichment status for items to allow re-enrichment.

    Args:
        conn: SQLite database connection
        provider: If specified, only reset items enriched by this provider.
                  If None, reset all items.
        content_type: If specified, only reset items of this content type.
        user_id: If specified, only reset items for this user.

    Returns:
        Number of items reset
    """
    cursor = conn.cursor()
    params: list[str | int] = []

    # Join with content_items for content_type and user_id filtering
    if content_type or user_id:
        base_query = """
            UPDATE enrichment_status
            SET needs_enrichment = 1, enrichment_error = NULL
            WHERE content_item_id IN (
                SELECT es.content_item_id
                FROM enrichment_status es
                JOIN content_items ci ON es.content_item_id = ci.id
                WHERE 1=1
        """
        if provider:
            base_query += " AND es.enrichment_provider = ?"
            params.append(provider)
        if content_type:
            base_query += " AND ci.content_type = ?"
            params.append(content_type)
        if user_id:
            base_query += " AND ci.user_id = ?"
            params.append(user_id)
        base_query += ")"
        cursor.execute(base_query, params)
    elif provider:
        cursor.execute(
            """UPDATE enrichment_status
               SET needs_enrichment = 1, enrichment_error = NULL
               WHERE enrichment_provider = ?""",
            (provider,),
        )
    else:
        cursor.execute("""UPDATE enrichment_status
               SET needs_enrichment = 1, enrichment_error = NULL""")

    updated = cursor.rowcount
    conn.commit()
    return updated


def get_enrichment_stats(
    conn: sqlite3.Connection,
    user_id: int | None = None,
) -> dict[str, int | dict[str, int]]:
    """Get overall enrichment statistics.

    Args:
        conn: SQLite database connection
        user_id: If specified, only count items for this user.

    Returns:
        Dict with enrichment statistics
    """
    cursor = conn.cursor()

    # Build user filter clause
    user_filter = ""
    user_params: tuple[int, ...] = ()
    if user_id:
        user_filter = " AND ci.user_id = ?"
        user_params = (user_id,)

    # Total content items
    if user_id:
        cursor.execute(
            "SELECT COUNT(*) FROM content_items ci WHERE 1=1" + user_filter,
            user_params,
        )
    else:
        cursor.execute("SELECT COUNT(*) FROM content_items")
    total_items: int = cursor.fetchone()[0]

    # Items with enrichment status
    if user_id:
        cursor.execute(
            """SELECT COUNT(*) FROM enrichment_status es
               JOIN content_items ci ON es.content_item_id = ci.id
               WHERE 1=1""" + user_filter,
            user_params,
        )
    else:
        cursor.execute("SELECT COUNT(*) FROM enrichment_status")
    tracked_items: int = cursor.fetchone()[0]

    # Items needing enrichment
    if user_id:
        cursor.execute(
            """SELECT COUNT(*) FROM enrichment_status es
               JOIN content_items ci ON es.content_item_id = ci.id
               WHERE es.needs_enrichment = 1""" + user_filter,
            user_params,
        )
    else:
        cursor.execute(
            "SELECT COUNT(*) FROM enrichment_status WHERE needs_enrichment = 1"
        )
    needs_enrichment: int = cursor.fetchone()[0]

    # Successfully enriched (needs_enrichment = 0 and no error)
    if user_id:
        cursor.execute(
            """SELECT COUNT(*) FROM enrichment_status es
               JOIN content_items ci ON es.content_item_id = ci.id
               WHERE es.needs_enrichment = 0 AND es.enrichment_error IS NULL"""
            + user_filter,
            user_params,
        )
    else:
        cursor.execute("""SELECT COUNT(*) FROM enrichment_status
               WHERE needs_enrichment = 0 AND enrichment_error IS NULL""")
    enriched: int = cursor.fetchone()[0]

    # Failed enrichment
    if user_id:
        cursor.execute(
            """SELECT COUNT(*) FROM enrichment_status es
               JOIN content_items ci ON es.content_item_id = ci.id
               WHERE es.enrichment_error IS NOT NULL""" + user_filter,
            user_params,
        )
    else:
        cursor.execute("""SELECT COUNT(*) FROM enrichment_status
               WHERE enrichment_error IS NOT NULL""")
    failed: int = cursor.fetchone()[0]

    # Items without any enrichment status (new items)
    untracked = total_items - tracked_items

    # Breakdown by provider
    if user_id:
        cursor.execute(
            """SELECT es.enrichment_provider, COUNT(*)
               FROM enrichment_status es
               JOIN content_items ci ON es.content_item_id = ci.id
               WHERE es.enrichment_provider IS NOT NULL"""
            + user_filter
            + """ GROUP BY es.enrichment_provider""",
            user_params,
        )
    else:
        cursor.execute("""SELECT enrichment_provider, COUNT(*)
               FROM enrichment_status
               WHERE enrichment_provider IS NOT NULL
               GROUP BY enrichment_provider""")
    by_provider: dict[str, int] = {row[0]: row[1] for row in cursor.fetchall()}

    # Breakdown by quality
    if user_id:
        cursor.execute(
            """SELECT es.enrichment_quality, COUNT(*)
               FROM enrichment_status es
               JOIN content_items ci ON es.content_item_id = ci.id
               WHERE es.enrichment_quality IS NOT NULL"""
            + user_filter
            + """ GROUP BY es.enrichment_quality""",
            user_params,
        )
    else:
        cursor.execute("""SELECT enrichment_quality, COUNT(*)
               FROM enrichment_status
               WHERE enrichment_quality IS NOT NULL
               GROUP BY enrichment_quality""")
    by_quality: dict[str, int] = {row[0]: row[1] for row in cursor.fetchall()}

    return {
        "total": total_items,
        "enriched": enriched,
        "pending": needs_enrichment + untracked,
        "not_found": by_quality.get("not_found", 0),
        "failed": failed,
        "by_provider": by_provider,
        "by_quality": by_quality,
    }
