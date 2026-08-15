"""Tests for database schema and user management."""

import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from src.storage import derived, schema
from src.storage.schema import (
    create_schema,
    create_user,
    get_all_users,
    get_user_by_id,
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


def test_default_user_created(temp_db: sqlite3.Connection) -> None:
    """Test that default user is created with schema."""
    create_schema(temp_db)

    user = get_user_by_id(temp_db, 1)
    assert user is not None
    assert user["username"] == "default"
    assert user["display_name"] == "Default User"


def test_create_user(temp_db: sqlite3.Connection) -> None:
    """Test creating a new user."""
    create_schema(temp_db)

    user_id = create_user(
        temp_db,
        username="testuser",
        display_name="Test User",
        settings={"compact_cards": True},
    )

    assert user_id > 1  # Default user is 1

    user = get_user_by_id(temp_db, user_id)
    assert user is not None
    assert user["username"] == "testuser"
    assert user["display_name"] == "Test User"
    assert user["settings"] == {"compact_cards": True}


def test_update_user_settings(temp_db: sqlite3.Connection) -> None:
    """Test updating user settings."""
    create_schema(temp_db)

    # Update default user settings
    update_user_settings(temp_db, 1, {"compact_cards": True, "theme": "dark"})

    user = get_user_by_id(temp_db, 1)
    assert user is not None
    assert user["settings"]["compact_cards"] is True
    assert user["settings"]["theme"] == "dark"

    # Update again - should merge
    update_user_settings(temp_db, 1, {"language": "en"})

    user = get_user_by_id(temp_db, 1)
    assert user["settings"]["compact_cards"] is True  # Preserved
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


class TestTheOneTimeContentRepair:
    """The three passes that read every content row run once per database.

    ``create_schema`` runs on every open, so leaving them unguarded costs the
    library's size at every start. Idempotence is not enough on its own: a row
    a pass deliberately declines to settle stays in a shape its prefilter
    matches, so an unguarded scan finds it again for the life of the database.
    """

    def test_a_database_written_before_the_guard_runs_each_pass_once(
        self, temp_db: sqlite3.Connection
    ) -> None:
        """The upgrade itself: one open, one run of all three."""
        create_schema(temp_db)
        _seed_a_library_awaiting_the_repair(temp_db)

        assert _repair_pass_calls(temp_db) == _EVERY_PASS_ONCE

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
