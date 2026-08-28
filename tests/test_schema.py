import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from src.storage import derived, schema
from src.storage.schema import (
    create_schema,
    create_user,
    get_all_users,
    get_source_config,
    get_user_by_id,
    update_user_identity,
    update_user_settings,
)
from src.utils.sorting import build_search_text, get_sort_title


@pytest.fixture
def temp_db(tmp_path: Path) -> sqlite3.Connection:
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(db_path)
    yield conn
    conn.close()


def test_default_user_created(temp_db: sqlite3.Connection) -> None:
    create_schema(temp_db)

    user = get_user_by_id(temp_db, 1)
    assert user is not None
    assert user["username"] == "default"
    assert user["display_name"] == "Default User"


def test_create_user(temp_db: sqlite3.Connection) -> None:
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
    create_schema(temp_db)

    update_user_settings(temp_db, 1, {"compact_cards": True, "theme": "dark"})

    user = get_user_by_id(temp_db, 1)
    assert user is not None
    assert user["settings"]["compact_cards"] is True
    assert user["settings"]["theme"] == "dark"

    update_user_settings(temp_db, 1, {"language": "en"})

    user = get_user_by_id(temp_db, 1)
    assert user["settings"]["compact_cards"] is True
    assert user["settings"]["theme"] == "dark"
    assert user["settings"]["language"] == "en"


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
# so a test says which it expects rather than spelling out the zeroes.
_EVERY_PASS_ONCE = {"renormalize": 1, "detail_shapes": 1}
_NO_PASS_AT_ALL = {"renormalize": 0, "detail_shapes": 0}


def _repair_pass_calls(conn: sqlite3.Connection) -> dict[str, int]:
    """Counting the calls is what separates a pass that was skipped from one that
    found nothing, and the scan is the cost being guarded against."""
    with (
        patch.object(
            schema, "_renormalize_titles", wraps=schema._renormalize_titles
        ) as renormalize,
        patch.object(
            schema,
            "_migrate_stranded_detail_shapes",
            wraps=schema._migrate_stranded_detail_shapes,
        ) as detail_shapes,
    ):
        create_schema(conn)
    return {
        "renormalize": renormalize.call_count,
        "detail_shapes": detail_shapes.call_count,
    }


def _seed_a_library_awaiting_the_repair(conn: sqlite3.Connection) -> None:
    """Two rows an earlier build left, both carrying the SQL ``lower(title)``
    backfill the full Python normalization corrects: one key then names both
    spellings."""
    cursor = conn.cursor()
    cursor.execute("""INSERT INTO content_items
           (user_id, title, normalized_title, content_type, status, rating, source)
           VALUES (1, 'The Witcher III: Wild Hunt',
                   'the witcher iii: wild hunt', 'video_game', 'completed',
                   5, 'steam')""")
    cursor.execute("""INSERT INTO content_items
           (user_id, title, normalized_title, content_type, status, review, source)
           VALUES (1, 'Witcher 3 - Wild Hunt',
                   'witcher 3 - wild hunt', 'video_game', 'completed',
                   'Great writing', 'blog')""")
    conn.execute("PRAGMA user_version = 0")
    conn.commit()


def _content_rows(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    cursor = conn.cursor()
    cursor.execute(
        "SELECT title, normalized_title, rating, review FROM content_items"
        " WHERE merged_into IS NULL ORDER BY id"
    )
    return cursor.fetchall()


class TestTheOneTimeContentRepair:
    """Idempotence is not enough on its own: a row a pass declines to settle keeps
    matching its prefilter, so an unguarded scan re-reads it on every open."""

    def test_a_database_written_before_the_guard_runs_each_pass_once(
        self, temp_db: sqlite3.Connection
    ) -> None:
        create_schema(temp_db)
        _seed_a_library_awaiting_the_repair(temp_db)

        assert _repair_pass_calls(temp_db) == _EVERY_PASS_ONCE

    def test_the_upgrade_keys_both_spellings_of_one_game_the_same(
        self, temp_db: sqlite3.Connection
    ) -> None:
        """The stored key is what the save door matches on, so the pair only reaches
        it once both rows normalize the same."""
        create_schema(temp_db)
        _seed_a_library_awaiting_the_repair(temp_db)

        create_schema(temp_db)

        rows = _content_rows(temp_db)
        assert [row["normalized_title"] for row in rows] == ["witcher 3 wild hunt"] * 2
        assert [(row["rating"], row["review"]) for row in rows] == [
            (5, None),
            (None, "Great writing"),
        ]

    def test_a_second_open_runs_none_of_the_passes(
        self, temp_db: sqlite3.Connection
    ) -> None:
        create_schema(temp_db)
        _seed_a_library_awaiting_the_repair(temp_db)
        create_schema(temp_db)

        assert _repair_pass_calls(temp_db) == _NO_PASS_AT_ALL


def _rows_backfilled(conn: sqlite3.Connection) -> int:
    """The fill runs on every open, so counting the calls to it would say nothing;
    what matters is that it writes only rows that are missing a column, and a
    library where none is costs no write at all."""
    with patch.object(derived, "_write_row", wraps=derived._write_row) as write_row:
        create_schema(conn)
    return int(write_row.call_count)


def _seed_a_row_missing_the_derived_columns(conn: sqlite3.Connection) -> None:
    """What every row looked like until the columns were added, and what a build
    that predates them still writes into a database that already has them."""
    cursor = conn.cursor()
    cursor.execute("""INSERT INTO content_items
           (user_id, title, normalized_title, content_type, status)
           VALUES (1, 'The Witcher 3', 'witcher 3', 'video_game', 'completed')""")
    cursor.execute(
        "INSERT INTO video_game_details (content_item_id, developer)"
        " VALUES (?, 'CD Projekt Red')",
        (cursor.lastrowid,),
    )
    conn.commit()


def _derived_columns(conn: sqlite3.Connection) -> tuple[str, str]:
    cursor = conn.cursor()
    cursor.execute("SELECT sort_title, search_text FROM content_items")
    row = cursor.fetchone()
    return row[0], row[1]


class TestTheDerivedColumnBackfill:
    """``sort_title`` and ``search_text`` are what the library list orders and
    searches by, so a row carrying neither is invisible to search and sorts ahead
    of the whole library."""

    def test_the_open_after_the_fill_writes_nothing(
        self, temp_db: sqlite3.Connection
    ) -> None:
        create_schema(temp_db)
        _seed_a_row_missing_the_derived_columns(temp_db)
        create_schema(temp_db)

        assert _rows_backfilled(temp_db) == 0

    def test_a_row_at_the_current_version_is_filled_all_the_same_regression(
        self, temp_db: sqlite3.Connection
    ) -> None:
        """Defect: a row missing the columns was repaired only once per database."""
        create_schema(temp_db)
        _seed_a_row_missing_the_derived_columns(temp_db)

        assert _rows_backfilled(temp_db) == 1
        assert _derived_columns(temp_db) == (
            get_sort_title("The Witcher 3"),
            build_search_text("The Witcher 3", "CD Projekt Red"),
        )

    # One row per type, each keeping its creator in a column of its own. The
    # titles carry a leading article and non-Latin letters, which SQL's own
    # lower() cannot normalize.
    _LIBRARY_OF_EVERY_TYPE = (
        ("The Left Hand of Darkness", "book", "book_details", "author", "Le Guin"),
        ("Ångström", "movie", "movie_details", "director", "Roy Andersson"),
        ("進撃の巨人", "tv_show", "tv_show_details", "creators", "諫山創"),
        ("The Witcher 3", "video_game", "video_game_details", "developer", "CDPR"),
    )

    @classmethod
    def _seed_every_content_type(cls, conn: sqlite3.Connection) -> None:
        cursor = conn.cursor()
        for (
            title,
            content_type,
            table,
            column,
            creator,
        ) in cls._LIBRARY_OF_EVERY_TYPE:
            cursor.execute(
                """INSERT INTO content_items
                   (user_id, title, normalized_title, content_type, status)
                   VALUES (1, ?, ?, ?, 'completed')""",
                (title, title.lower(), content_type),
            )
            cursor.execute(
                f"INSERT INTO {table} (content_item_id, {column}) VALUES (?, ?)",
                (cursor.lastrowid, creator),
            )
        conn.commit()

    def test_the_fill_reaches_the_creator_of_every_content_type(
        self, temp_db: sqlite3.Connection
    ) -> None:
        """One type missing from the creator expression would leave its rows
        searchable by title only, and nothing about the upgrade would say so — the
        columns are filled, just not with the name."""
        create_schema(temp_db)
        self._seed_every_content_type(temp_db)

        create_schema(temp_db)

        cursor = temp_db.cursor()
        cursor.execute("SELECT title, sort_title, search_text FROM content_items")
        stored = {
            row["title"]: (row["sort_title"], row["search_text"])
            for row in cursor.fetchall()
        }
        assert stored == {
            title: (
                get_sort_title(title),
                build_search_text(title, creator),
            )
            for title, _, _, _, creator in self._LIBRARY_OF_EVERY_TYPE
        }

    def test_a_row_with_no_detail_row_fills_its_title_alone(
        self, temp_db: sqlite3.Connection
    ) -> None:
        """A creator nobody recorded is an empty half, not a skipped row."""
        create_schema(temp_db)
        temp_db.execute("""INSERT INTO content_items
               (user_id, title, normalized_title, content_type, status)
               VALUES (1, 'A Lonely Row', 'lonely row', 'book', 'completed')""")
        temp_db.commit()

        create_schema(temp_db)

        assert _derived_columns(temp_db) == (
            get_sort_title("A Lonely Row"),
            build_search_text("A Lonely Row", None),
        )


_SOURCE_CONFIGS_BEFORE_SYNC_INTERVAL = """
    CREATE TABLE source_configs (
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        source_id TEXT NOT NULL,
        plugin TEXT NOT NULL,
        config_json TEXT NOT NULL,
        enabled INTEGER NOT NULL DEFAULT 1,
        migrated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (user_id, source_id)
    )
"""


class TestOpeningADatabaseThatPredatesSyncScheduling:
    """``sync_interval`` arrives by ALTER; a rebuild would drop migrated rows."""

    def test_a_version_six_database_keeps_its_source_configs(
        self, temp_db: sqlite3.Connection
    ) -> None:
        temp_db.execute(_SOURCE_CONFIGS_BEFORE_SYNC_INTERVAL)
        temp_db.execute(
            "INSERT INTO source_configs (user_id, source_id, plugin, config_json)"
            " VALUES (1, 'steam', 'steam', '{\"vanity_url\": \"myname\"}')"
        )
        temp_db.execute("PRAGMA user_version = 6")
        temp_db.commit()

        create_schema(temp_db)

        stored = get_source_config(temp_db, 1, "steam")
        assert stored is not None
        assert stored["config_json"] == '{"vanity_url": "myname"}'
        assert stored["sync_interval"] is None
        # The run history is a new table, so the upgrade has to create it too.
        temp_db.execute(
            "INSERT INTO sync_runs (user_id, source_id, started_at, finished_at,"
            " status) VALUES (1, 'steam', '2026-03-01T12:00:00.000000+00:00',"
            " '2026-03-01T12:00:30.000000+00:00', 'completed')"
        )


def test_get_all_users_multiple(temp_db: sqlite3.Connection) -> None:
    create_schema(temp_db)

    create_user(temp_db, username="alice", display_name="Alice")
    create_user(temp_db, username="bob", display_name="Bob")

    users = get_all_users(temp_db)
    assert len(users) == 3
    assert users[0]["username"] == "default"
    assert users[1]["username"] == "alice"
    assert users[2]["username"] == "bob"
