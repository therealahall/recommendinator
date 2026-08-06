"""Tests for database schema and user management."""

import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from src.storage import schema
from src.storage.merge import normalize_title_for_matching
from src.storage.schema import (
    _enrichment_count_query,
    _enrichment_group_query,
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


# The two states the one-time content repair can be in on a given open. Named
# so a test says which it expects rather than spelling out three zeroes.
_EVERY_PASS_ONCE = {"renormalize": 1, "detail_shapes": 1, "deduplicate": 1}
_NO_PASS_AT_ALL = {"renormalize": 0, "detail_shapes": 0, "deduplicate": 0}


def _repair_pass_calls(conn: sqlite3.Connection) -> dict[str, int]:
    """Run ``create_schema``, counting each one-time content-repair pass.

    Every pass skips rows already in the current shape, so a library that is
    unchanged afterwards says nothing about whether the scan ran. Counting the
    calls is what separates a pass that was skipped from one that found
    nothing, and the scan is the cost being guarded against.
    """
    with (
        patch.object(
            schema, "_renormalize_titles", wraps=schema._renormalize_titles
        ) as renormalize,
        patch.object(
            schema,
            "_migrate_stranded_detail_shapes",
            wraps=schema._migrate_stranded_detail_shapes,
        ) as detail_shapes,
        patch.object(
            schema, "_deduplicate_inline", wraps=schema._deduplicate_inline
        ) as deduplicate,
    ):
        create_schema(conn)
    return {
        "renormalize": renormalize.call_count,
        "detail_shapes": detail_shapes.call_count,
        "deduplicate": deduplicate.call_count,
    }


def _seed_a_library_awaiting_the_repair(conn: sqlite3.Connection) -> None:
    """Write two rows an earlier build left, at the version it left them at.

    Both carry the SQL ``lower(title)`` backfill as their normalized title,
    which is what the full Python normalization corrects: it drops the leading
    article, the punctuation and the Roman numeral that keep these two
    spellings of one game apart, exposing the pair the merge then reconciles.
    """
    cursor = conn.cursor()
    cursor.execute(
        """INSERT INTO content_items
           (user_id, external_id, title, normalized_title, content_type,
            status, rating, source)
           VALUES (1, 'steam-witcher', 'The Witcher III: Wild Hunt',
                   'the witcher iii: wild hunt', 'video_game', 'completed',
                   5, 'steam')"""
    )
    cursor.execute(
        """INSERT INTO content_items
           (user_id, external_id, title, normalized_title, content_type,
            status, review, source)
           VALUES (1, 'blog-witcher', 'Witcher 3 - Wild Hunt',
                   'witcher 3 - wild hunt', 'video_game', 'completed',
                   'Great writing', 'blog')"""
    )
    conn.execute("PRAGMA user_version = 0")
    conn.commit()


def _content_rows(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Return every content row, oldest first."""
    cursor = conn.cursor()
    cursor.execute(
        "SELECT external_id, normalized_title, rating, review FROM content_items"
        " ORDER BY id"
    )
    return cursor.fetchall()


def test_create_schema_is_idempotent(temp_db: sqlite3.Connection) -> None:
    """Test that create_schema can be called multiple times safely.

    Safe was the original claim. The second call is also cheap: it runs none
    of the passes that read the whole library.
    """
    create_schema(temp_db)

    assert _repair_pass_calls(temp_db) == _NO_PASS_AT_ALL

    # Verify default user still exists (not duplicated)
    user = get_user_by_id(temp_db, 1)
    assert user is not None


class TestTheOneTimeContentRepair:
    """The three passes that read every content row run once per database.

    ``create_schema`` runs on every open, so leaving them unguarded costs the
    library's size at every start. Idempotence is not enough on its own: a row
    a pass deliberately declines to settle stays in a shape its prefilter
    matches, so an unguarded scan finds it again for the life of the database.
    """

    def test_a_fresh_database_runs_none_of_the_passes(
        self, temp_db: sqlite3.Connection
    ) -> None:
        """``CREATE TABLE`` writes no row any of them could repair."""
        assert _repair_pass_calls(temp_db) == _NO_PASS_AT_ALL

    def test_a_database_written_before_the_guard_runs_each_pass_once(
        self, temp_db: sqlite3.Connection
    ) -> None:
        """The upgrade itself: one open, one run of all three."""
        create_schema(temp_db)
        _seed_a_library_awaiting_the_repair(temp_db)

        assert _repair_pass_calls(temp_db) == _EVERY_PASS_ONCE

    def test_the_upgrade_renormalizes_a_title_the_sql_backfill_got_wrong(
        self, temp_db: sqlite3.Connection
    ) -> None:
        """``lower(title)`` is replaced by the canonical normalization.

        The stored value is compared with ``lower(title)`` as well, because a
        pass that never ran would leave a value that is also "a string".
        """
        create_schema(temp_db)
        _seed_a_library_awaiting_the_repair(temp_db)

        create_schema(temp_db)

        survivor = _content_rows(temp_db)[0]
        assert survivor["normalized_title"] == "witcher 3 wild hunt"
        assert survivor["normalized_title"] != "the witcher iii: wild hunt"

    def test_the_upgrade_merges_the_duplicate_the_renormalization_exposes(
        self, temp_db: sqlite3.Connection
    ) -> None:
        """The pair only matches once both titles are normalized the same way.

        The survivor is the older row, and it keeps its own rating while
        taking the review only the duplicate held — the merge rules the pass
        shares with runtime dedup.
        """
        create_schema(temp_db)
        _seed_a_library_awaiting_the_repair(temp_db)

        create_schema(temp_db)

        rows = _content_rows(temp_db)
        assert [row["external_id"] for row in rows] == ["steam-witcher"]
        assert (rows[0]["rating"], rows[0]["review"]) == (5, "Great writing")

    def test_a_second_open_runs_none_of_the_passes(
        self, temp_db: sqlite3.Connection
    ) -> None:
        """The repair is spent: the open after it scans nothing."""
        create_schema(temp_db)
        _seed_a_library_awaiting_the_repair(temp_db)
        create_schema(temp_db)

        assert _repair_pass_calls(temp_db) == _NO_PASS_AT_ALL

    def test_a_stale_title_written_after_the_upgrade_is_left_alone(
        self, temp_db: sqlite3.Connection
    ) -> None:
        """What the guard costs, in data rather than call counts.

        Only a pre-upgrade build wrote an un-normalized title, so nothing
        re-normalizes one written afterwards. A change to
        ``normalize_title_for_matching`` is therefore a schema version bump,
        not a silent re-run.
        """
        create_schema(temp_db)
        _seed_a_library_awaiting_the_repair(temp_db)
        create_schema(temp_db)
        temp_db.execute(
            "UPDATE content_items SET normalized_title = 'the witcher iii: wild hunt'"
        )
        temp_db.commit()

        create_schema(temp_db)

        assert _content_rows(temp_db)[0]["normalized_title"] == (
            "the witcher iii: wild hunt"
        )
        assert normalize_title_for_matching("The Witcher III: Wild Hunt") == (
            "witcher 3 wild hunt"
        )


class TestTheSchemaVersionStamp:
    """The version is recorded once, and an open with nothing to do is silent.

    ``PRAGMA data_version`` on an observing connection changes when another
    connection commits a modification, so it says whether an open touched the
    file at all — the header included, which is where the version lives.
    """

    @staticmethod
    def _open(db_path: Path) -> None:
        """Run ``create_schema`` over its own connection, as the app does."""
        conn = sqlite3.connect(db_path)
        try:
            create_schema(conn)
        finally:
            conn.close()

    def test_an_open_with_nothing_to_upgrade_writes_nothing(
        self, tmp_path: Path
    ) -> None:
        """The steady state: every guarded step skipped, and no stamp either."""
        db_path = tmp_path / "observed.db"
        self._open(db_path)
        observer = sqlite3.connect(db_path)
        try:
            before = observer.execute("PRAGMA data_version").fetchone()[0]

            self._open(db_path)

            assert observer.execute("PRAGMA data_version").fetchone()[0] == before
        finally:
            observer.close()

    def test_restamping_the_same_version_would_write_to_the_file(
        self, tmp_path: Path
    ) -> None:
        """Why the stamp is guarded rather than issued on every open.

        SQLite does not treat writing the version it already holds as a no-op,
        so the guard is what keeps the test above true. Without this one, that
        one would pass on an unguarded stamp too and the branch would read as
        dead code.
        """
        db_path = tmp_path / "restamped.db"
        self._open(db_path)
        observer = sqlite3.connect(db_path)
        writer = sqlite3.connect(db_path)
        try:
            before = observer.execute("PRAGMA data_version").fetchone()[0]

            writer.execute(f"PRAGMA user_version = {schema._SCHEMA_VERSION}")
            writer.commit()

            assert observer.execute("PRAGMA data_version").fetchone()[0] != before
        finally:
            writer.close()
            observer.close()


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
