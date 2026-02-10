"""Tests for database schema and user management."""

import sqlite3
from pathlib import Path

import pytest

from src.storage.schema import (
    clear_cached_preference_interpretations,
    create_schema,
    create_user,
    get_all_users,
    get_cached_preference_interpretation,
    get_default_user_id,
    get_user_by_id,
    get_user_by_username,
    save_cached_preference_interpretation,
    update_user_settings,
)


@pytest.fixture
def temp_db(tmp_path: Path) -> sqlite3.Connection:
    """Create a temporary database connection for testing."""
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(db_path)
    yield conn
    conn.close()


def test_create_schema(temp_db: sqlite3.Connection) -> None:
    """Test schema creation."""
    create_schema(temp_db)

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
    assert "preference_interpretation_cache" in tables


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


def test_create_schema_is_idempotent(temp_db: sqlite3.Connection) -> None:
    """Test that create_schema can be called multiple times safely."""
    create_schema(temp_db)
    create_schema(temp_db)  # Should not raise

    # Verify default user still exists (not duplicated)
    user = get_user_by_id(temp_db, 1)
    assert user is not None


def test_get_all_users_default_only(temp_db: sqlite3.Connection) -> None:
    """Test get_all_users returns only the default user when no others exist."""
    create_schema(temp_db)

    users = get_all_users(temp_db)
    assert len(users) == 1
    assert users[0]["id"] == 1
    assert users[0]["username"] == "default"
    assert users[0]["display_name"] == "Default User"


def test_get_all_users_multiple(temp_db: sqlite3.Connection) -> None:
    """Test get_all_users returns all users ordered by id."""
    create_schema(temp_db)

    create_user(temp_db, username="alice", display_name="Alice")
    create_user(temp_db, username="bob", display_name="Bob")

    users = get_all_users(temp_db)
    assert len(users) == 3
    assert users[0]["username"] == "default"
    assert users[1]["username"] == "alice"
    assert users[2]["username"] == "bob"


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
    cursor.execute(
        """
        INSERT INTO content_items (user_id, external_id, title, content_type, status)
        VALUES (1, 'ext123', 'Test Book', 'book', 'unread')
        """
    )

    # Same external_id for same user and type should fail
    with pytest.raises(sqlite3.IntegrityError):
        cursor.execute(
            """
            INSERT INTO content_items (user_id, external_id, title, content_type, status)
            VALUES (1, 'ext123', 'Another Book', 'book', 'unread')
            """
        )

    # Same external_id for different user should work
    create_user(temp_db, "user2")
    cursor.execute(
        """
        INSERT INTO content_items (user_id, external_id, title, content_type, status)
        VALUES (2, 'ext123', 'Test Book', 'book', 'unread')
        """
    )

    # Same external_id for different content type should work
    cursor.execute(
        """
        INSERT INTO content_items (user_id, external_id, title, content_type, status)
        VALUES (1, 'ext123', 'Test Movie', 'movie', 'unread')
        """
    )

    temp_db.commit()


# Preference interpretation cache tests


def test_preference_interpretation_cache_table_exists(
    temp_db: sqlite3.Connection,
) -> None:
    """Test that preference_interpretation_cache table is created."""
    create_schema(temp_db)

    cursor = temp_db.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    tables = [row[0] for row in cursor.fetchall()]

    assert "preference_interpretation_cache" in tables


def test_save_and_get_cached_interpretation(temp_db: sqlite3.Connection) -> None:
    """Test saving and retrieving cached interpretations."""
    create_schema(temp_db)

    cache_key = "test_key_123"
    interpretation_json = '{"genre_boosts": {"horror": 1.0}}'

    # Initially empty
    result = get_cached_preference_interpretation(temp_db, cache_key)
    assert result is None

    # Save
    save_cached_preference_interpretation(temp_db, cache_key, interpretation_json)

    # Retrieve
    result = get_cached_preference_interpretation(temp_db, cache_key)
    assert result == interpretation_json


def test_save_cached_interpretation_overwrites(temp_db: sqlite3.Connection) -> None:
    """Test that saving with same key overwrites previous value."""
    create_schema(temp_db)

    cache_key = "test_key"
    save_cached_preference_interpretation(temp_db, cache_key, "original")
    save_cached_preference_interpretation(temp_db, cache_key, "updated")

    result = get_cached_preference_interpretation(temp_db, cache_key)
    assert result == "updated"


def test_clear_cached_interpretations(temp_db: sqlite3.Connection) -> None:
    """Test clearing all cached interpretations."""
    create_schema(temp_db)

    # Add some entries
    save_cached_preference_interpretation(temp_db, "key1", "value1")
    save_cached_preference_interpretation(temp_db, "key2", "value2")
    save_cached_preference_interpretation(temp_db, "key3", "value3")

    # Clear
    deleted = clear_cached_preference_interpretations(temp_db)
    assert deleted == 3

    # Verify empty
    assert get_cached_preference_interpretation(temp_db, "key1") is None
    assert get_cached_preference_interpretation(temp_db, "key2") is None
