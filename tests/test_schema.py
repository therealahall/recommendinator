"""Tests for database schema and user management."""

import json
import sqlite3
from datetime import datetime
from pathlib import Path

import pytest

from src.storage.schema import (
    _enrichment_count_query,
    _enrichment_group_query,
    _seed_season_watched_dates,
    clear_cached_preference_interpretations,
    create_schema,
    create_user,
    get_all_users,
    get_cached_preference_interpretation,
    get_default_user_id,
    get_enrichment_stats,
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


class TestEnrichmentSQLWhitelist:
    """Tests for table and column whitelist validation in enrichment queries."""

    def test_valid_columns_accepted_via_get_enrichment_stats(
        self, temp_db: sqlite3.Connection
    ) -> None:
        """get_enrichment_stats passes valid columns without raising."""
        create_schema(temp_db)
        stats = get_enrichment_stats(temp_db)
        assert "by_provider" in stats
        assert "by_quality" in stats

    def test_invalid_column_raises_value_error(
        self, temp_db: sqlite3.Connection
    ) -> None:
        """_enrichment_group_query raises ValueError for unlisted column names.

        Validates the SQL injection defense-in-depth guard.
        """
        create_schema(temp_db)
        cursor = temp_db.cursor()

        with pytest.raises(ValueError, match="Unknown enrichment column"):
            _enrichment_group_query(
                cursor=cursor,
                select_col="malicious_col; DROP TABLE content_items; --",
                table_name="enrichment_status",
                table_alias=None,
                user_join="",
                user_filter="",
                user_params=(),
            )

    def test_empty_string_column_raises_value_error(
        self, temp_db: sqlite3.Connection
    ) -> None:
        """_enrichment_group_query rejects empty string column name."""
        create_schema(temp_db)
        cursor = temp_db.cursor()

        with pytest.raises(ValueError, match="Unknown enrichment column"):
            _enrichment_group_query(
                cursor=cursor,
                select_col="",
                table_name="enrichment_status",
                table_alias=None,
                user_join="",
                user_filter="",
                user_params=(),
            )

    def test_count_query_rejects_unknown_table(
        self, temp_db: sqlite3.Connection
    ) -> None:
        """_enrichment_count_query raises ValueError for unknown table name."""
        create_schema(temp_db)
        cursor = temp_db.cursor()

        with pytest.raises(ValueError, match="Unknown SQL table"):
            _enrichment_count_query(
                cursor=cursor,
                table_name="malicious_table; DROP TABLE users; --",
                table_alias=None,
                where_clause="1=1",
                user_join="",
                user_filter="",
                user_params=(),
            )

    def test_group_query_rejects_unknown_table(
        self, temp_db: sqlite3.Connection
    ) -> None:
        """_enrichment_group_query raises ValueError for unknown table name."""
        create_schema(temp_db)
        cursor = temp_db.cursor()

        with pytest.raises(ValueError, match="Unknown SQL table"):
            _enrichment_group_query(
                cursor=cursor,
                select_col="enrichment_provider",
                table_name="injected_table",
                table_alias=None,
                user_join="",
                user_filter="",
                user_params=(),
            )

    def test_count_query_rejects_unknown_alias(
        self, temp_db: sqlite3.Connection
    ) -> None:
        """_enrichment_count_query raises ValueError for unknown table alias."""
        create_schema(temp_db)
        cursor = temp_db.cursor()

        with pytest.raises(ValueError, match="Unknown SQL table alias"):
            _enrichment_count_query(
                cursor=cursor,
                table_name="enrichment_status",
                table_alias="injected",
                where_clause="1=1",
                user_join="",
                user_filter="",
                user_params=(),
            )

    def test_count_query_accepts_valid_alias(self, temp_db: sqlite3.Connection) -> None:
        """_enrichment_count_query accepts the allowlisted 'es' alias."""
        create_schema(temp_db)
        cursor = temp_db.cursor()

        # Should not raise — "es" is in the allowlist
        result = _enrichment_count_query(
            cursor=cursor,
            table_name="enrichment_status",
            table_alias="es",
            where_clause="1=1",
            user_join="",
            user_filter="",
            user_params=(),
        )

        assert isinstance(result, int)

    def test_count_query_rejects_unknown_where_clause(
        self, temp_db: sqlite3.Connection
    ) -> None:
        """_enrichment_count_query raises ValueError for unknown WHERE clause."""
        create_schema(temp_db)
        cursor = temp_db.cursor()

        with pytest.raises(ValueError, match="Unknown SQL WHERE clause"):
            _enrichment_count_query(
                cursor=cursor,
                table_name="enrichment_status",
                table_alias=None,
                where_clause="1=1; DROP TABLE users; --",
                user_join="",
                user_filter="",
                user_params=(),
            )

    def test_count_query_rejects_unknown_join(
        self, temp_db: sqlite3.Connection
    ) -> None:
        """_enrichment_count_query raises ValueError for unknown JOIN clause."""
        create_schema(temp_db)
        cursor = temp_db.cursor()

        with pytest.raises(ValueError, match="Unknown SQL JOIN clause"):
            _enrichment_count_query(
                cursor=cursor,
                table_name="enrichment_status",
                table_alias=None,
                where_clause="1=1",
                user_join=" JOIN secrets ON 1=1",
                user_filter="",
                user_params=(),
            )

    def test_count_query_rejects_unknown_filter(
        self, temp_db: sqlite3.Connection
    ) -> None:
        """_enrichment_count_query raises ValueError for unknown filter."""
        create_schema(temp_db)
        cursor = temp_db.cursor()

        with pytest.raises(ValueError, match="Unknown SQL filter"):
            _enrichment_count_query(
                cursor=cursor,
                table_name="enrichment_status",
                table_alias=None,
                where_clause="1=1",
                user_join="",
                user_filter=" OR 1=1",
                user_params=(),
            )

    def test_group_query_rejects_unknown_join(
        self, temp_db: sqlite3.Connection
    ) -> None:
        """_enrichment_group_query raises ValueError for unknown JOIN clause."""
        create_schema(temp_db)
        cursor = temp_db.cursor()

        with pytest.raises(ValueError, match="Unknown SQL JOIN clause"):
            _enrichment_group_query(
                cursor=cursor,
                select_col="enrichment_provider",
                table_name="enrichment_status",
                table_alias=None,
                user_join=" JOIN secrets ON 1=1",
                user_filter="",
                user_params=(),
            )

    def test_group_query_rejects_unknown_filter(
        self, temp_db: sqlite3.Connection
    ) -> None:
        """_enrichment_group_query raises ValueError for unknown filter."""
        create_schema(temp_db)
        cursor = temp_db.cursor()

        with pytest.raises(ValueError, match="Unknown SQL filter"):
            _enrichment_group_query(
                cursor=cursor,
                select_col="enrichment_provider",
                table_name="enrichment_status",
                table_alias=None,
                user_join="",
                user_filter=" OR 1=1",
                user_params=(),
            )


# Season watched-dates backfill tests


def _insert_tv_show(
    cursor: sqlite3.Cursor, external_id: str, updated_at: str, metadata: dict
) -> int:
    """Insert a content_item + tv_show_details row for backfill tests."""
    cursor.execute(
        """
        INSERT INTO content_items
            (user_id, external_id, title, content_type, status, updated_at)
        VALUES (1, ?, ?, 'tv_show', 'currently_consuming', ?)
        """,
        (external_id, external_id, updated_at),
    )
    content_item_id = cursor.lastrowid
    cursor.execute(
        "INSERT INTO tv_show_details (content_item_id, metadata) VALUES (?, ?)",
        (content_item_id, json.dumps(metadata)),
    )
    return content_item_id


def test_backfill_seeds_five_most_recent(temp_db: sqlite3.Connection) -> None:
    """Backfill stamps only the 5 most-recently-updated shows with finished seasons."""
    create_schema(temp_db)
    cursor = temp_db.cursor()

    # show-0 is oldest, show-6 is newest. The 5 most-recently-updated are
    # show-2..show-6; show-0 and show-1 are the 2 oldest and must be skipped.
    # A regression that dropped `reverse=True` in _seed_season_watched_dates
    # would instead stamp show-0..show-4 (the oldest 5) — this test would
    # catch that by asserting the exact set of stamped external_ids.
    for i in range(7):
        _insert_tv_show(
            cursor,
            external_id=f"show-{i}",
            # Raw SQLite CURRENT_TIMESTAMP format ("YYYY-MM-DD HH:MM:SS", no
            # "T", no offset) — what a real row's updated_at looks like before
            # _seed_season_watched_dates normalizes it to ISO 8601.
            updated_at=f"2026-01-0{i + 1} 00:00:00",
            metadata={"seasons_watched": [1, 2]},
        )
    temp_db.commit()

    _seed_season_watched_dates(cursor)
    temp_db.commit()

    cursor.execute(
        "SELECT ci.external_id AS external_id, tsd.metadata AS metadata "
        "FROM content_items ci "
        "JOIN tv_show_details tsd ON tsd.content_item_id = ci.id"
    )
    by_id = {
        row["external_id"]: json.loads(row["metadata"]) for row in cursor.fetchall()
    }
    stamped = {
        ext_id for ext_id, meta in by_id.items() if meta.get("seasons_watched_dates")
    }

    assert stamped == {"show-2", "show-3", "show-4", "show-5", "show-6"}
    assert not by_id["show-0"].get("seasons_watched_dates")
    assert not by_id["show-1"].get("seasons_watched_dates")

    for ext_id in stamped:
        dates = by_id[ext_id]["seasons_watched_dates"]
        # stamps only the highest watched season
        assert set(dates.keys()) == {"2"}
        stamp = dates["2"]
        # The stamp is real ISO 8601, not the raw SQLite timestamp — it
        # contains a "T" separator and round-trips through fromisoformat.
        assert "T" in stamp
        datetime.fromisoformat(stamp)


def test_backfill_is_noop_when_dates_exist(temp_db: sqlite3.Connection) -> None:
    """Backfill does nothing once any show already carries seasons_watched_dates."""
    create_schema(temp_db)
    cursor = temp_db.cursor()

    _insert_tv_show(
        cursor,
        external_id="already-dated",
        updated_at="2026-01-01T00:00:00Z",
        metadata={
            "seasons_watched": [1],
            "seasons_watched_dates": {"1": "2025-12-01T00:00:00Z"},
        },
    )
    _insert_tv_show(
        cursor,
        external_id="dateless",
        updated_at="2026-01-02T00:00:00Z",
        metadata={"seasons_watched": [1, 2]},
    )
    temp_db.commit()

    _seed_season_watched_dates(cursor)
    temp_db.commit()

    cursor.execute("SELECT metadata FROM tv_show_details")
    seeded = [json.loads(row["metadata"]) for row in cursor.fetchall()]
    with_dates = [m for m in seeded if m.get("seasons_watched_dates")]

    assert len(with_dates) == 1  # unchanged; guard tripped


def test_backfill_ignores_shows_without_finished_seasons(
    temp_db: sqlite3.Connection,
) -> None:
    """Shows with an empty seasons_watched list are never seeded."""
    create_schema(temp_db)
    cursor = temp_db.cursor()

    for i in range(3):
        _insert_tv_show(
            cursor,
            external_id=f"unwatched-{i}",
            updated_at=f"2026-01-0{i + 1}T00:00:00Z",
            metadata={"seasons_watched": []},
        )
    temp_db.commit()

    _seed_season_watched_dates(cursor)
    temp_db.commit()

    cursor.execute("SELECT metadata FROM tv_show_details")
    seeded = [json.loads(row["metadata"]) for row in cursor.fetchall()]
    assert all(not m.get("seasons_watched_dates") for m in seeded)


def test_backfill_skips_row_with_unparseable_updated_at(
    temp_db: sqlite3.Connection,
) -> None:
    """A candidate whose updated_at fails to parse is skipped, not garbage-stamped.

    Valid candidates alongside it still get stamped.
    """
    create_schema(temp_db)
    cursor = temp_db.cursor()

    _insert_tv_show(
        cursor,
        external_id="unparseable",
        updated_at="not-a-timestamp",
        metadata={"seasons_watched": [1]},
    )
    for i in range(2):
        _insert_tv_show(
            cursor,
            external_id=f"valid-{i}",
            updated_at=f"2026-01-0{i + 1} 00:00:00",
            metadata={"seasons_watched": [1, 2]},
        )
    temp_db.commit()

    _seed_season_watched_dates(cursor)
    temp_db.commit()

    cursor.execute(
        "SELECT ci.external_id AS external_id, tsd.metadata AS metadata "
        "FROM content_items ci "
        "JOIN tv_show_details tsd ON tsd.content_item_id = ci.id"
    )
    by_id = {
        row["external_id"]: json.loads(row["metadata"]) for row in cursor.fetchall()
    }

    assert not by_id["unparseable"].get("seasons_watched_dates")
    assert by_id["valid-0"].get("seasons_watched_dates")
    assert by_id["valid-1"].get("seasons_watched_dates")


def test_backfill_tolerates_malformed_metadata_json(
    temp_db: sqlite3.Connection,
) -> None:
    """A tv_show_details row with non-JSON metadata does not crash the backfill."""
    create_schema(temp_db)
    cursor = temp_db.cursor()

    cursor.execute(
        """
        INSERT INTO content_items
            (user_id, external_id, title, content_type, status, updated_at)
        VALUES (1, 'malformed', 'malformed', 'tv_show', 'currently_consuming',
                '2026-01-01 00:00:00')
        """
    )
    malformed_id = cursor.lastrowid
    cursor.execute(
        "INSERT INTO tv_show_details (content_item_id, metadata) VALUES (?, ?)",
        (malformed_id, "{not valid json"),
    )
    _insert_tv_show(
        cursor,
        external_id="valid",
        updated_at="2026-01-02 00:00:00",
        metadata={"seasons_watched": [1]},
    )
    temp_db.commit()

    _seed_season_watched_dates(cursor)  # Must not raise.
    temp_db.commit()

    cursor.execute(
        "SELECT ci.external_id AS external_id, tsd.metadata AS metadata "
        "FROM content_items ci "
        "JOIN tv_show_details tsd ON tsd.content_item_id = ci.id"
    )
    by_id = {row["external_id"]: row["metadata"] for row in cursor.fetchall()}

    assert by_id["malformed"] == "{not valid json"  # left untouched
    assert json.loads(by_id["valid"]).get("seasons_watched_dates")


def test_backfill_seeds_all_when_fewer_than_five_eligible(
    temp_db: sqlite3.Connection,
) -> None:
    """With fewer than 5 eligible shows, every one of them is seeded."""
    create_schema(temp_db)
    cursor = temp_db.cursor()

    for i in range(3):
        _insert_tv_show(
            cursor,
            external_id=f"show-{i}",
            updated_at=f"2026-01-0{i + 1} 00:00:00",
            metadata={"seasons_watched": [1]},
        )
    temp_db.commit()

    _seed_season_watched_dates(cursor)
    temp_db.commit()

    cursor.execute("SELECT metadata FROM tv_show_details")
    seeded = [json.loads(row["metadata"]) for row in cursor.fetchall()]
    with_dates = [m for m in seeded if m.get("seasons_watched_dates")]
    assert len(with_dates) == 3
