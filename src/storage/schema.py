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

    # Enrichment status tracking
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS enrichment_status (
            content_item_id INTEGER PRIMARY KEY
                REFERENCES content_items(id) ON DELETE CASCADE,
            last_enriched_at TIMESTAMP,
            enrichment_provider TEXT,
            enrichment_quality TEXT,
            needs_enrichment BOOLEAN DEFAULT 1,
            enrichment_error TEXT
        )
        """
    )

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

    # Add ignored column to content_items for filtering from recommendations
    _add_column_if_not_exists(cursor, "content_items", "ignored", "BOOLEAN DEFAULT 0")

    # Core memories: significant preference signals
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS core_memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            memory_text TEXT NOT NULL,
            memory_type TEXT NOT NULL,  -- "user_stated" or "inferred"
            source TEXT,  -- "conversation", "rating_pattern", "manual"
            confidence REAL DEFAULT 1.0,  -- 0.0-1.0 for inferred memories
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            is_active BOOLEAN DEFAULT 1  -- User can deactivate inferred memories
        )
    """
    )

    # Conversation history (for context rebuilding)
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS conversation_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            role TEXT NOT NULL,  -- "user" or "assistant"
            content TEXT NOT NULL,
            tool_calls TEXT,  -- JSON array of tool calls made
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """
    )

    # Preference profile snapshots (regenerated periodically)
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS preference_profiles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            profile_json TEXT NOT NULL,  -- Distilled preference summary
            generated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id)  -- One active profile per user
        )
    """
    )

    # Indexes for conversation tables
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_core_memories_user " "ON core_memories(user_id)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_core_memories_active "
        "ON core_memories(user_id, is_active)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_conversation_messages_user "
        "ON conversation_messages(user_id)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_conversation_messages_user_created "
        "ON conversation_messages(user_id, created_at DESC)"
    )

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


def _row_to_user_dict(row: tuple) -> dict:
    """Convert a user row tuple to a user dict.

    Args:
        row: Tuple of (id, username, display_name, created_at, settings)

    Returns:
        User dict with parsed settings
    """
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
    return _row_to_user_dict(row) if row else None


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
    return _row_to_user_dict(row) if row else None


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
    return [_row_to_user_dict(row) for row in cursor.fetchall()]


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
        cursor.execute(
            """UPDATE enrichment_status
               SET needs_enrichment = 1, enrichment_error = NULL"""
        )

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

    # Build reusable query parts for optional user filtering
    user_join = (
        " JOIN content_items ci ON es.content_item_id = ci.id" if user_id else ""
    )
    user_filter = " AND ci.user_id = ?" if user_id else ""
    user_params: tuple[int, ...] = (user_id,) if user_id else ()

    def _count_query(table_prefix: str, where_clause: str) -> int:
        """Execute a COUNT query with optional user filtering."""
        query = f"SELECT COUNT(*) FROM {table_prefix}{user_join} WHERE {where_clause}{user_filter}"
        cursor.execute(query, user_params)
        result: int = cursor.fetchone()[0]
        return result

    # Use 'es' alias when joining, plain table name otherwise
    es_prefix = "enrichment_status es" if user_id else "enrichment_status"

    total_items: int = _count_query(
        "content_items ci" if user_id else "content_items",
        "ci.user_id = ?" if user_id else "1=1",
    )
    # Override: total_items doesn't use the enrichment join
    if user_id:
        cursor.execute(
            "SELECT COUNT(*) FROM content_items WHERE user_id = ?", (user_id,)
        )
        total_items = cursor.fetchone()[0]

    tracked_items: int = _count_query(es_prefix, "1=1")
    needs_enrichment: int = _count_query(
        es_prefix, "es.needs_enrichment = 1" if user_id else "needs_enrichment = 1"
    )
    enriched: int = _count_query(
        es_prefix,
        (
            "es.needs_enrichment = 0 AND es.enrichment_error IS NULL"
            " AND es.enrichment_provider != 'none'"
            if user_id
            else "needs_enrichment = 0 AND enrichment_error IS NULL"
            " AND enrichment_provider != 'none'"
        ),
    )
    failed: int = _count_query(
        es_prefix,
        (
            "es.enrichment_error IS NOT NULL"
            if user_id
            else "enrichment_error IS NOT NULL"
        ),
    )

    untracked = total_items - tracked_items

    def _group_query(select_col: str, where_clause: str) -> dict[str, int]:
        """Execute a GROUP BY query with optional user filtering."""
        col_prefix = f"es.{select_col}" if user_id else select_col
        query = (
            f"SELECT {col_prefix}, COUNT(*) FROM {es_prefix}{user_join}"
            f" WHERE {col_prefix} IS NOT NULL{user_filter}"
            f" GROUP BY {col_prefix}"
        )
        cursor.execute(query, user_params)
        return {row[0]: row[1] for row in cursor.fetchall()}

    by_provider = _group_query("enrichment_provider", "")
    by_quality = _group_query("enrichment_quality", "")

    return {
        "total": total_items,
        "enriched": enriched,
        "pending": needs_enrichment + untracked,
        "not_found": by_quality.get("not_found", 0),
        "failed": failed,
        "by_provider": by_provider,
        "by_quality": by_quality,
    }


# Core memory functions


def get_core_memories(
    conn: sqlite3.Connection,
    user_id: int,
    active_only: bool = True,
    memory_type: str | None = None,
) -> list[dict]:
    """Get core memories for a user.

    Args:
        conn: SQLite database connection
        user_id: User ID
        active_only: If True, only return active memories
        memory_type: Filter by type ("user_stated" or "inferred")

    Returns:
        List of memory dicts
    """
    cursor = conn.cursor()
    query = """
        SELECT id, user_id, memory_text, memory_type, source, confidence,
               created_at, updated_at, is_active
        FROM core_memories
        WHERE user_id = ?
    """
    params: list[int | str] = [user_id]

    if active_only:
        query += " AND is_active = 1"

    if memory_type:
        query += " AND memory_type = ?"
        params.append(memory_type)

    query += " ORDER BY created_at DESC"

    cursor.execute(query, params)
    memories = []
    for row in cursor.fetchall():
        memories.append(
            {
                "id": row[0],
                "user_id": row[1],
                "memory_text": row[2],
                "memory_type": row[3],
                "source": row[4],
                "confidence": row[5],
                "created_at": row[6],
                "updated_at": row[7],
                "is_active": bool(row[8]),
            }
        )
    return memories


def save_core_memory(
    conn: sqlite3.Connection,
    user_id: int,
    memory_text: str,
    memory_type: str,
    source: str,
    confidence: float = 1.0,
) -> int:
    """Save a new core memory.

    Args:
        conn: SQLite database connection
        user_id: User ID
        memory_text: The preference statement
        memory_type: "user_stated" or "inferred"
        source: "conversation", "rating_pattern", or "manual"
        confidence: Confidence score (0.0-1.0)

    Returns:
        New memory ID
    """
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO core_memories
        (user_id, memory_text, memory_type, source, confidence)
        VALUES (?, ?, ?, ?, ?)
        """,
        (user_id, memory_text, memory_type, source, confidence),
    )
    conn.commit()
    return cursor.lastrowid  # type: ignore


def update_core_memory(
    conn: sqlite3.Connection,
    memory_id: int,
    memory_text: str | None = None,
    is_active: bool | None = None,
) -> bool:
    """Update a core memory.

    Args:
        conn: SQLite database connection
        memory_id: Memory ID to update
        memory_text: New memory text (optional)
        is_active: New active status (optional)

    Returns:
        True if updated, False if not found
    """
    if memory_text is None and is_active is None:
        return False

    cursor = conn.cursor()
    updates = ["updated_at = CURRENT_TIMESTAMP"]
    params: list[str | int] = []

    if memory_text is not None:
        updates.append("memory_text = ?")
        params.append(memory_text)

    if is_active is not None:
        updates.append("is_active = ?")
        params.append(1 if is_active else 0)

    params.append(memory_id)

    query = f"UPDATE core_memories SET {', '.join(updates)} WHERE id = ?"
    cursor.execute(query, params)
    conn.commit()
    return cursor.rowcount > 0


def delete_core_memory(conn: sqlite3.Connection, memory_id: int) -> bool:
    """Delete a core memory.

    Args:
        conn: SQLite database connection
        memory_id: Memory ID to delete

    Returns:
        True if deleted, False if not found
    """
    cursor = conn.cursor()
    cursor.execute("DELETE FROM core_memories WHERE id = ?", (memory_id,))
    conn.commit()
    return cursor.rowcount > 0


# Conversation message functions


def get_conversation_history(
    conn: sqlite3.Connection,
    user_id: int,
    limit: int = 50,
) -> list[dict]:
    """Get recent conversation history for a user.

    Args:
        conn: SQLite database connection
        user_id: User ID
        limit: Maximum number of messages to return

    Returns:
        List of message dicts ordered by created_at ascending (oldest first)
    """
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT id, user_id, role, content, tool_calls, created_at
        FROM conversation_messages
        WHERE user_id = ?
        ORDER BY id DESC
        LIMIT ?
        """,
        (user_id, limit),
    )
    messages = []
    for row in cursor.fetchall():
        tool_calls = None
        if row[4]:
            try:
                tool_calls = json.loads(row[4])
            except (json.JSONDecodeError, TypeError):
                pass
        messages.append(
            {
                "id": row[0],
                "user_id": row[1],
                "role": row[2],
                "content": row[3],
                "tool_calls": tool_calls,
                "created_at": row[5],
            }
        )
    # Return in chronological order (oldest first)
    return list(reversed(messages))


def save_conversation_message(
    conn: sqlite3.Connection,
    user_id: int,
    role: str,
    content: str,
    tool_calls: list[dict] | None = None,
) -> int:
    """Save a conversation message.

    Args:
        conn: SQLite database connection
        user_id: User ID
        role: "user" or "assistant"
        content: Message content
        tool_calls: Optional list of tool calls made

    Returns:
        New message ID
    """
    cursor = conn.cursor()
    tool_calls_json = json.dumps(tool_calls) if tool_calls else None
    cursor.execute(
        """
        INSERT INTO conversation_messages
        (user_id, role, content, tool_calls)
        VALUES (?, ?, ?, ?)
        """,
        (user_id, role, content, tool_calls_json),
    )
    conn.commit()
    return cursor.lastrowid  # type: ignore


def clear_conversation_history(conn: sqlite3.Connection, user_id: int) -> int:
    """Clear conversation history for a user (the "reset" functionality).

    Args:
        conn: SQLite database connection
        user_id: User ID

    Returns:
        Number of messages deleted
    """
    cursor = conn.cursor()
    cursor.execute(
        "DELETE FROM conversation_messages WHERE user_id = ?",
        (user_id,),
    )
    conn.commit()
    return cursor.rowcount


# Preference profile functions


def get_preference_profile(conn: sqlite3.Connection, user_id: int) -> dict | None:
    """Get the preference profile for a user.

    Args:
        conn: SQLite database connection
        user_id: User ID

    Returns:
        Profile dict or None if not found
    """
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT id, user_id, profile_json, generated_at
        FROM preference_profiles
        WHERE user_id = ?
        """,
        (user_id,),
    )
    row = cursor.fetchone()
    if row:
        try:
            profile_data = json.loads(row[2])
        except (json.JSONDecodeError, TypeError):
            profile_data = {}
        return {
            "id": row[0],
            "user_id": row[1],
            "profile": profile_data,
            "generated_at": row[3],
        }
    return None


def save_preference_profile(
    conn: sqlite3.Connection,
    user_id: int,
    profile_json: str,
) -> int:
    """Save or update a preference profile.

    Uses UPSERT to replace existing profile for the user.

    Args:
        conn: SQLite database connection
        user_id: User ID
        profile_json: JSON string of the profile

    Returns:
        Profile ID
    """
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO preference_profiles (user_id, profile_json, generated_at)
        VALUES (?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(user_id) DO UPDATE SET
            profile_json = excluded.profile_json,
            generated_at = CURRENT_TIMESTAMP
        """,
        (user_id, profile_json),
    )
    conn.commit()
    return cursor.lastrowid  # type: ignore
