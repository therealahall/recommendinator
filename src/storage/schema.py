"""Database schema definitions and migrations."""

import sqlite3


SCHEMA_VERSION = 2


def create_schema(conn: sqlite3.Connection) -> None:
    """Create the database schema (legacy v1, kept for migration purposes).

    Args:
        conn: SQLite database connection
    """
    cursor = conn.cursor()

    # Content items table (v1 schema)
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS content_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            external_id TEXT UNIQUE,
            title TEXT NOT NULL,
            author TEXT,
            content_type TEXT NOT NULL,
            status TEXT NOT NULL,
            rating INTEGER CHECK (rating >= 1 AND rating <= 5),
            review TEXT,
            date_completed DATE,
            metadata TEXT,  -- JSON string
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(external_id, content_type)
        )
        """
    )

    # Create indexes for common queries
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_content_type ON content_items(content_type)"
    )
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_status ON content_items(status)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_rating ON content_items(rating)")
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_date_completed ON content_items(date_completed)"
    )

    # Schema version tracking
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_version (
            version INTEGER PRIMARY KEY,
            applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    # Insert initial schema version
    cursor.execute("INSERT OR IGNORE INTO schema_version (version) VALUES (?)", (1,))

    conn.commit()


def get_schema_version(conn: sqlite3.Connection) -> int:
    """Get the current schema version.

    Args:
        conn: SQLite database connection

    Returns:
        Schema version number, or 0 if not initialized
    """
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT MAX(version) FROM schema_version")
        result = cursor.fetchone()
        return result[0] if result and result[0] is not None else 0
    except sqlite3.OperationalError:
        # Table doesn't exist yet
        return 0


def create_schema_v2(conn: sqlite3.Connection) -> None:
    """Create schema version 2 with type-specific detail tables.

    Args:
        conn: SQLite database connection
    """
    cursor = conn.cursor()

    # Base content items table (removed author column)
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS content_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            external_id TEXT,
            title TEXT NOT NULL,
            content_type TEXT NOT NULL,
            status TEXT NOT NULL,
            rating INTEGER CHECK (rating >= 1 AND rating <= 5),
            review TEXT,
            date_completed DATE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(external_id, content_type)
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
            metadata TEXT
        )
        """
    )

    # Movie-specific details
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS movie_details (
            content_item_id INTEGER PRIMARY KEY REFERENCES content_items(id) ON DELETE CASCADE,
            director TEXT,
            runtime INTEGER,
            release_year INTEGER,
            genre TEXT,
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
            platform TEXT,
            genre TEXT,
            release_year INTEGER,
            metadata TEXT
        )
        """
    )

    # Create indexes for common queries
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_content_type ON content_items(content_type)"
    )
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_status ON content_items(status)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_rating ON content_items(rating)")
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_date_completed ON content_items(date_completed)"
    )

    # Indexes for type-specific fields
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_book_author ON book_details(author)")
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_movie_director ON movie_details(director)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_game_developer ON video_game_details(developer)"
    )

    # Schema version tracking
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_version (
            version INTEGER PRIMARY KEY,
            applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    # Insert schema version
    cursor.execute(
        "INSERT OR IGNORE INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,)
    )

    conn.commit()


def migrate_v1_to_v2(conn: sqlite3.Connection) -> None:
    """Migrate from schema version 1 to version 2.

    This migration:
    1. Creates type-specific detail tables
    2. Migrates author data from content_items to book_details
    3. Migrates metadata JSON to appropriate detail tables
    4. Removes author column from content_items (kept for compatibility initially)

    Args:
        conn: SQLite database connection
    """
    import json

    cursor = conn.cursor()

    # Create type-specific detail tables
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
            metadata TEXT
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS movie_details (
            content_item_id INTEGER PRIMARY KEY REFERENCES content_items(id) ON DELETE CASCADE,
            director TEXT,
            runtime INTEGER,
            release_year INTEGER,
            genre TEXT,
            studio TEXT,
            metadata TEXT
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS tv_show_details (
            content_item_id INTEGER PRIMARY KEY REFERENCES content_items(id) ON DELETE CASCADE,
            creators TEXT,
            seasons INTEGER,
            episodes INTEGER,
            network TEXT,
            release_year INTEGER,
            metadata TEXT
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS video_game_details (
            content_item_id INTEGER PRIMARY KEY REFERENCES content_items(id) ON DELETE CASCADE,
            developer TEXT,
            publisher TEXT,
            platform TEXT,
            genre TEXT,
            release_year INTEGER,
            metadata TEXT
        )
        """
    )

    # Create indexes for type-specific fields
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_book_author ON book_details(author)")
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_movie_director ON movie_details(director)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_game_developer ON video_game_details(developer)"
    )

    # Migrate existing data
    cursor.execute("SELECT id, author, content_type, metadata FROM content_items")
    rows = cursor.fetchall()

    for row in rows:
        content_id, author, content_type, metadata_json = row

        if metadata_json:
            try:
                metadata = json.loads(metadata_json)
            except (json.JSONDecodeError, TypeError):
                metadata = {}
        else:
            metadata = {}

        if content_type == "book":
            # Migrate book data
            cursor.execute(
                """
                INSERT OR REPLACE INTO book_details
                (content_item_id, author, pages, isbn, isbn13, publisher, year_published, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    content_id,
                    author,
                    int(metadata.get("pages")) if metadata.get("pages") else None,
                    metadata.get("isbn"),
                    metadata.get("isbn13"),
                    metadata.get("publisher"),
                    (
                        int(metadata.get("year_published"))
                        if metadata.get("year_published")
                        else None
                    ),
                    (
                        json.dumps(
                            {
                                k: v
                                for k, v in metadata.items()
                                if k
                                not in [
                                    "pages",
                                    "isbn",
                                    "isbn13",
                                    "publisher",
                                    "year_published",
                                ]
                            }
                        )
                        if metadata
                        else None
                    ),
                ),
            )
        # For other content types, we'll store remaining metadata in their detail tables
        # when we have data for them

    # Update schema version
    cursor.execute("INSERT OR REPLACE INTO schema_version (version) VALUES (?)", (2,))

    conn.commit()


def migrate_schema(
    conn: sqlite3.Connection, target_version: int = SCHEMA_VERSION
) -> None:
    """Migrate database schema to target version.

    Args:
        conn: SQLite database connection
        target_version: Target schema version
    """
    current_version = get_schema_version(conn)

    if current_version == 0:
        # Initial schema creation - use v2 schema
        create_schema_v2(conn)
        return

    if current_version == 1 and target_version >= 2:
        # Migrate from v1 to v2
        migrate_v1_to_v2(conn)
        current_version = 2

    if current_version < target_version:
        # Future migrations would go here
        pass
