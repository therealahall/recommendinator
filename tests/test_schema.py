"""Tests for database schema and user management."""

import sqlite3
from pathlib import Path

import pytest

from src.storage.schema import (
    SCHEMA_VERSION,
    create_schema,
    create_user,
    get_default_user_id,
    get_schema_version,
    get_user_by_id,
    get_user_by_username,
    migrate_schema,
    update_user_settings,
)


@pytest.fixture
def temp_db(tmp_path: Path) -> sqlite3.Connection:
    """Create a temporary database connection for testing."""
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(db_path)
    yield conn
    conn.close()


def test_schema_version_empty_db(temp_db: sqlite3.Connection) -> None:
    """Test that schema version is 0 for empty database."""
    version = get_schema_version(temp_db)
    assert version == 0


def test_create_schema(temp_db: sqlite3.Connection) -> None:
    """Test schema creation."""
    create_schema(temp_db)

    version = get_schema_version(temp_db)
    assert version == SCHEMA_VERSION

    # Verify tables exist
    cursor = temp_db.cursor()

    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    tables = [row[0] for row in cursor.fetchall()]

    assert "users" in tables
    assert "content_items" in tables
    assert "book_details" in tables
    assert "movie_details" in tables
    assert "tv_show_details" in tables
    assert "video_game_details" in tables
    assert "schema_version" in tables


def test_default_user_created(temp_db: sqlite3.Connection) -> None:
    """Test that default user is created with schema."""
    create_schema(temp_db)

    user = get_user_by_id(temp_db, 1)
    assert user is not None
    assert user["username"] == "default"
    assert user["display_name"] == "Default User"


def test_get_default_user_id() -> None:
    """Test default user ID is always 1."""
    assert get_default_user_id() == 1


def test_create_user(temp_db: sqlite3.Connection) -> None:
    """Test creating a new user."""
    create_schema(temp_db)

    user_id = create_user(
        temp_db,
        username="testuser",
        display_name="Test User",
        settings={"ai_enabled": True},
    )

    assert user_id > 1  # Default user is 1

    user = get_user_by_id(temp_db, user_id)
    assert user is not None
    assert user["username"] == "testuser"
    assert user["display_name"] == "Test User"
    assert user["settings"] == {"ai_enabled": True}


def test_get_user_by_username(temp_db: sqlite3.Connection) -> None:
    """Test getting user by username."""
    create_schema(temp_db)

    user = get_user_by_username(temp_db, "default")
    assert user is not None
    assert user["id"] == 1


def test_get_nonexistent_user(temp_db: sqlite3.Connection) -> None:
    """Test getting a user that doesn't exist."""
    create_schema(temp_db)

    user = get_user_by_id(temp_db, 999)
    assert user is None

    user = get_user_by_username(temp_db, "nonexistent")
    assert user is None


def test_update_user_settings(temp_db: sqlite3.Connection) -> None:
    """Test updating user settings."""
    create_schema(temp_db)

    # Update default user settings
    update_user_settings(temp_db, 1, {"ai_enabled": True, "theme": "dark"})

    user = get_user_by_id(temp_db, 1)
    assert user is not None
    assert user["settings"]["ai_enabled"] is True
    assert user["settings"]["theme"] == "dark"

    # Update again - should merge
    update_user_settings(temp_db, 1, {"language": "en"})

    user = get_user_by_id(temp_db, 1)
    assert user["settings"]["ai_enabled"] is True  # Preserved
    assert user["settings"]["theme"] == "dark"  # Preserved
    assert user["settings"]["language"] == "en"  # Added


def test_migrate_schema_fresh_db(temp_db: sqlite3.Connection) -> None:
    """Test migrate_schema on fresh database creates schema."""
    migrate_schema(temp_db)

    version = get_schema_version(temp_db)
    assert version == SCHEMA_VERSION

    # Verify default user exists
    user = get_user_by_id(temp_db, 1)
    assert user is not None


def test_content_items_has_user_id(temp_db: sqlite3.Connection) -> None:
    """Test that content_items table has user_id column."""
    create_schema(temp_db)

    cursor = temp_db.cursor()
    cursor.execute("PRAGMA table_info(content_items)")
    columns = {row[1] for row in cursor.fetchall()}

    assert "user_id" in columns
    assert "external_id" in columns
    assert "title" in columns
    assert "content_type" in columns
    assert "status" in columns
    assert "source" in columns


def test_content_items_unique_constraint(temp_db: sqlite3.Connection) -> None:
    """Test that content_items has correct unique constraint."""
    create_schema(temp_db)

    cursor = temp_db.cursor()

    # Insert a content item
    cursor.execute("""
        INSERT INTO content_items (user_id, external_id, title, content_type, status)
        VALUES (1, 'ext123', 'Test Book', 'book', 'unread')
        """)

    # Same external_id for same user and type should fail
    with pytest.raises(sqlite3.IntegrityError):
        cursor.execute("""
            INSERT INTO content_items (user_id, external_id, title, content_type, status)
            VALUES (1, 'ext123', 'Another Book', 'book', 'unread')
            """)

    # Same external_id for different user should work
    create_user(temp_db, "user2")
    cursor.execute("""
        INSERT INTO content_items (user_id, external_id, title, content_type, status)
        VALUES (2, 'ext123', 'Test Book', 'book', 'unread')
        """)

    # Same external_id for different content type should work
    cursor.execute("""
        INSERT INTO content_items (user_id, external_id, title, content_type, status)
        VALUES (1, 'ext123', 'Test Movie', 'movie', 'unread')
        """)

    temp_db.commit()
