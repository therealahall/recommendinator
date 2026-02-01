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
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            display_name TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            settings TEXT  -- JSON for per-user settings (AI enabled, weights, etc.)
        )
        """
    )

    # Create default user
    cursor.execute(
        """
        INSERT OR IGNORE INTO users (id, username, display_name)
        VALUES (1, 'default', 'Default User')
        """
    )

    # Base content items table with user_id
    cursor.execute(
        """
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
        """
    )

    # Book-specific details
    cursor.execute(
        """
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
        """
    )

    # Movie-specific details
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS movie_details (
            content_item_id INTEGER PRIMARY KEY REFERENCES content_items(id) ON DELETE CASCADE,
            director TEXT,
            runtime INTEGER,  -- minutes
            release_year INTEGER,
            genres TEXT,  -- JSON array of genres
            studio TEXT,
            metadata TEXT
        )
        """
    )

    # TV Show-specific details
    cursor.execute(
        """
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
        """
    )

    # Video Game-specific details
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS video_game_details (
            content_item_id INTEGER PRIMARY KEY REFERENCES content_items(id) ON DELETE CASCADE,
            developer TEXT,
            publisher TEXT,
            platforms TEXT,  -- JSON array of platforms
            genres TEXT,  -- JSON array of genres
            release_year INTEGER,
            metadata TEXT
        )
        """
    )

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
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS preference_interpretation_cache (
            cache_key TEXT PRIMARY KEY,
            interpretation_json TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    conn.commit()


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
