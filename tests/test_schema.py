"""Tests for database schema and user management."""

import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from src.storage import derived, schema
from src.storage.merge import normalize_title_for_matching
from src.storage.schema import (
    _enrichment_count_query,
    _enrichment_group_query,
    create_schema,
    create_user,
    get_all_users,
    get_default_user_id,
    get_enrichment_stats,
    get_user_by_id,
    get_user_by_username,
    update_user_identity,
    update_user_settings,
)
from src.utils.sorting import build_search_text, get_sort_title


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


class TestUpdatingAUsersIdentity:
    """Both names are written together, which is how a display name is cleared."""

    def test_both_names_are_written_and_the_settings_are_left_alone(
        self, temp_db: sqlite3.Connection
    ) -> None:
        create_schema(temp_db)
        update_user_settings(temp_db, 1, {"theme": "dark"})

        renamed = update_user_identity(temp_db, 1, "owner", None)

        assert renamed is not None
        assert (renamed["username"], renamed["display_name"]) == ("owner", None)
        assert renamed == get_user_by_id(temp_db, 1)
        assert renamed["settings"] == {"theme": "dark"}

    def test_an_unknown_user_is_reported_rather_than_inserted(
        self, temp_db: sqlite3.Connection
    ) -> None:
        """What ``StorageManager`` turns into ``UnknownUserError``."""
        create_schema(temp_db)

        assert update_user_identity(temp_db, 999, "owner", "The Owner") is None
        assert get_user_by_username(temp_db, "owner") is None

    def test_a_username_another_row_holds_is_refused(
        self, temp_db: sqlite3.Connection
    ) -> None:
        """``users.username`` is UNIQUE, and a rename must not merge two rows."""
        create_schema(temp_db)
        create_user(temp_db, username="second", display_name="Second")

        with pytest.raises(sqlite3.IntegrityError):
            update_user_identity(temp_db, 1, "second", None)

        user = get_user_by_id(temp_db, 1)
        assert user is not None and user["username"] == "default"


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


def _rows_backfilled(conn: sqlite3.Connection) -> int:
    """Run ``create_schema``, counting the rows the derived-column fill wrote.

    The fill runs on every open, so counting the calls to it would say
    nothing; what matters is that it writes only rows that are missing a
    column, and a library where none is costs no write at all. Nothing else
    reaches ``_write_row`` during an open — the duplicate merge deliberately
    leaves the columns to the fill that follows it.
    """
    with patch.object(derived, "_write_row", wraps=derived._write_row) as write_row:
        create_schema(conn)
    return int(write_row.call_count)


def _seed_a_row_missing_the_derived_columns(conn: sqlite3.Connection) -> None:
    """Write a row carrying a title, a creator and neither derived column.

    What every row looked like until the columns were added, and what a build
    that predates them still writes into a database that already has them.
    """
    cursor = conn.cursor()
    cursor.execute(
        """INSERT INTO content_items
           (user_id, external_id, title, normalized_title, content_type, status)
           VALUES (1, 'steam-witcher', 'The Witcher 3', 'witcher 3',
                   'video_game', 'completed')"""
    )
    cursor.execute(
        "INSERT INTO video_game_details (content_item_id, developer)"
        " VALUES (?, 'CD Projekt Red')",
        (cursor.lastrowid,),
    )
    conn.commit()


def _derived_columns(conn: sqlite3.Connection) -> tuple[str, str]:
    """Return the one content row's stored sort title and search text."""
    cursor = conn.cursor()
    cursor.execute("SELECT sort_title, search_text FROM content_items")
    row = cursor.fetchone()
    return row[0], row[1]


class TestTheDerivedColumnBackfill:
    """``sort_title`` and ``search_text`` are filled for whatever row lacks them.

    They are what the library list orders and searches by, so a row carrying
    neither is invisible to search and sorts ahead of the whole library. The
    fill is therefore selected on the columns rather than on the schema
    version — but it must still write nothing when every row already has them,
    since it runs on every open.
    """

    def test_a_fresh_database_has_no_row_to_fill(
        self, temp_db: sqlite3.Connection
    ) -> None:
        """``CREATE TABLE`` writes no row missing either column."""
        assert _rows_backfilled(temp_db) == 0

    def test_a_row_written_before_the_columns_is_filled(
        self, temp_db: sqlite3.Connection
    ) -> None:
        """The upgrade itself: the one row that needs it, written once."""
        create_schema(temp_db)
        _seed_a_row_missing_the_derived_columns(temp_db)

        assert _rows_backfilled(temp_db) == 1

    def test_the_open_after_the_fill_writes_nothing(
        self, temp_db: sqlite3.Connection
    ) -> None:
        """A library that already has the columns costs no write."""
        create_schema(temp_db)
        _seed_a_row_missing_the_derived_columns(temp_db)
        create_schema(temp_db)

        assert _rows_backfilled(temp_db) == 0

    def test_a_row_at_the_current_version_is_filled_all_the_same_regression(
        self, temp_db: sqlite3.Connection
    ) -> None:
        """Defect: a row missing the columns was repaired only once per database.

        Reported: the derived columns can be permanently NULL on a row in a
        database this build has already stamped, leaving that item unfindable
        by search and sorted ahead of every other title. Reachable by
        downgrade-then-upgrade — this build stamps the version, a build that
        predates the columns inserts rows without them, and re-upgrading reads
        the stamp.

        Root cause: the fill was guarded on ``stored_version < 4`` and its
        source select read every row, so the one open that could have repaired
        such a row was the open that had already happened.

        Fix: the guard is gone and the select carries the condition instead,
        so the fill is spent on rows rather than on databases. Nothing here
        rewinds ``user_version``.
        """
        create_schema(temp_db)
        _seed_a_row_missing_the_derived_columns(temp_db)

        assert _rows_backfilled(temp_db) == 1
        assert _derived_columns(temp_db) == (
            get_sort_title("The Witcher 3"),
            build_search_text("The Witcher 3", "CD Projekt Red"),
        )

    def test_the_fill_derives_both_columns_from_the_title_and_the_creator(
        self, temp_db: sqlite3.Connection
    ) -> None:
        """The values, not just the fact that a row was written.

        The creator comes from the detail table, so a fill reading the content
        row alone would leave the developer's name unsearchable.
        """
        create_schema(temp_db)
        _seed_a_row_missing_the_derived_columns(temp_db)

        create_schema(temp_db)

        assert _derived_columns(temp_db) == (
            get_sort_title("The Witcher 3"),
            build_search_text("The Witcher 3", "CD Projekt Red"),
        )

    # external_id -> (title, content_type, detail table, creator column, creator).
    # One row per content type, because each keeps its creator in a column of
    # its own and the fill selects between them on the content type. The titles
    # carry a leading article and non-Latin letters, which SQL's own lower()
    # could not normalize the way the key function does.
    _LIBRARY_OF_EVERY_TYPE = (
        (
            "gr-1",
            "The Left Hand of Darkness",
            "book",
            "book_details",
            "author",
            "Le Guin",
        ),
        ("tmdb-1", "Ångström", "movie", "movie_details", "director", "Roy Andersson"),
        ("tvdb-1", "進撃の巨人", "tv_show", "tv_show_details", "creators", "諫山創"),
        (
            "steam-1",
            "The Witcher 3",
            "video_game",
            "video_game_details",
            "developer",
            "CD Projekt Red",
        ),
    )

    @classmethod
    def _seed_every_content_type(cls, conn: sqlite3.Connection) -> None:
        """Write one row per content type, each missing the derived columns."""
        cursor = conn.cursor()
        for (
            external_id,
            title,
            content_type,
            table,
            column,
            creator,
        ) in cls._LIBRARY_OF_EVERY_TYPE:
            cursor.execute(
                """INSERT INTO content_items
                   (user_id, external_id, title, normalized_title, content_type,
                    status)
                   VALUES (1, ?, ?, ?, ?, 'completed')""",
                (external_id, title, title.lower(), content_type),
            )
            cursor.execute(
                f"INSERT INTO {table} (content_item_id, {column}) VALUES (?, ?)",
                (cursor.lastrowid, creator),
            )
        conn.commit()

    def test_the_fill_reaches_the_creator_of_every_content_type(
        self, temp_db: sqlite3.Connection
    ) -> None:
        """A book's author, a film's director, a show's creators, a game's developer.

        One type missing from the creator expression would leave its rows
        searchable by title only, and nothing about the upgrade would say so —
        the columns are filled, just not with the name.
        """
        create_schema(temp_db)
        self._seed_every_content_type(temp_db)

        create_schema(temp_db)

        cursor = temp_db.cursor()
        cursor.execute("SELECT external_id, sort_title, search_text FROM content_items")
        stored = {
            row["external_id"]: (row["sort_title"], row["search_text"])
            for row in cursor.fetchall()
        }
        assert stored == {
            external_id: (
                get_sort_title(title),
                build_search_text(title, creator),
            )
            for external_id, title, _, _, _, creator in self._LIBRARY_OF_EVERY_TYPE
        }

    def test_a_row_with_no_detail_row_fills_its_title_alone(
        self, temp_db: sqlite3.Connection
    ) -> None:
        """A creator nobody recorded is an empty half, not a skipped row.

        The creator expression selects a column of a table the row may have no
        entry in, so it yields NULL — which has to reach the search text as an
        empty creator rather than making the whole value NULL and taking the
        title down with it.
        """
        create_schema(temp_db)
        temp_db.execute(
            """INSERT INTO content_items
               (user_id, external_id, title, normalized_title, content_type, status)
               VALUES (1, 'orphan', 'A Lonely Row', 'lonely row', 'book',
                       'completed')"""
        )
        temp_db.commit()

        create_schema(temp_db)

        assert _derived_columns(temp_db) == (
            get_sort_title("A Lonely Row"),
            build_search_text("A Lonely Row", None),
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


def test_the_ai_tables_are_no_longer_created(temp_db: sqlite3.Connection) -> None:
    """The AI removal took its tables with it, so a fresh database has none."""
    create_schema(temp_db)

    cursor = temp_db.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = {row[0] for row in cursor.fetchall()}

    assert tables.isdisjoint(
        {"preference_interpretation_cache", "core_memories", "conversation_messages"}
    )
    # Anchors the assertion above: the profile table shares their vintage and
    # is the half that survived.
    assert "preference_profiles" in tables


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
