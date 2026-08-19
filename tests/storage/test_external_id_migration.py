"""Moving the external ids off ``content_items``.

They sat under a key with no source in it, so Steam's app 440 and GOG's
product 440 were one game. SQLite can only drop that column by rebuilding.
"""

import sqlite3
from pathlib import Path
from typing import Any

from src.storage.schema import _SCHEMA_VERSION
from src.storage.sqlite_db import SQLiteDB

# The shape version 7 left behind, written out rather than derived: the point
# is to be a build this one no longer is.
_CONTENT_ITEMS_AT_VERSION_SEVEN = """
    CREATE TABLE content_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL DEFAULT 1 REFERENCES users(id) ON DELETE CASCADE,
        external_id TEXT,
        title TEXT NOT NULL,
        normalized_title TEXT,
        sort_title TEXT,
        search_text TEXT,
        content_type TEXT NOT NULL,
        status TEXT NOT NULL,
        rating INTEGER CHECK (rating >= 1 AND rating <= 5),
        review TEXT,
        date_completed DATE,
        source TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(user_id, external_id, content_type)
    )
"""

# (title, content type, external id, source, detail table, creator column,
# creator). One item per content type, so every table cascading off
# ``content_items`` has a row riding on the rebuild.
_LIBRARY: tuple[tuple[str, str, str, str | None, str, str, str], ...] = (
    (
        "The Left Hand of Darkness",
        "book",
        "12345",
        "goodreads_csv",
        "book_details",
        "author",
        "Le Guin",
    ),
    (
        "Arrival",
        "movie",
        "radarr_9",
        "radarr",
        "movie_details",
        "director",
        "Villeneuve",
    ),
    ("Andor", "tv_show", "1", "trakt", "tv_show_details", "creators", "Gilroy"),
    (
        "Team Fortress 2",
        "video_game",
        "440",
        "steam",
        "video_game_details",
        "developer",
        "Valve",
    ),
    # Trakt numbers each content type from one, so this shares an id with
    # Andor above. The old key admitted the pair; the new one does not.
    ("Heat", "movie", "1", "trakt", "movie_details", "director", "Mann"),
    # Sourceless on purpose, which is what a file import leaves.
    ("A Hand-Typed Book", "book", "csv-1", None, "book_details", "author", "Nobody"),
)


def _connect(db_path: Path) -> sqlite3.Connection:
    """Open the database without running ``create_schema`` over it."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _stand_up_a_version_seven_library(db_path: Path) -> None:
    """Build the current schema, then put ``content_items`` back as it was.

    ``ignored`` is added by ALTER because that is how a version-7 database
    carries it, and the rebuild must end with it where the fresh create does.
    """
    SQLiteDB(db_path)
    conn = _connect(db_path)
    try:
        conn.execute("DROP TABLE content_items")
        conn.execute(_CONTENT_ITEMS_AT_VERSION_SEVEN)
        conn.execute("ALTER TABLE content_items ADD COLUMN ignored BOOLEAN DEFAULT 0")
        for (
            title,
            content_type,
            external_id,
            source,
            table,
            column,
            creator,
        ) in _LIBRARY:
            cursor = conn.execute(
                "INSERT INTO content_items"
                " (user_id, external_id, title, content_type, status, source)"
                " VALUES (1, ?, ?, ?, 'completed', ?)",
                (external_id, title, content_type, source),
            )
            conn.execute(
                f"INSERT INTO {table} (content_item_id, {column}) VALUES (?, ?)",
                (cursor.lastrowid, creator),
            )
            conn.execute(
                "INSERT INTO enrichment_status (content_item_id, needs_enrichment)"
                " VALUES (?, 0)",
                (cursor.lastrowid,),
            )
        conn.execute("PRAGMA user_version = 7")
        conn.commit()
    finally:
        conn.close()


def _schema_of(db_path: Path) -> list[tuple[Any, ...]]:
    """Every table and index the database declares, with its stored DDL."""
    conn = _connect(db_path)
    try:
        objects = conn.execute(
            "SELECT type, name, tbl_name, sql FROM sqlite_master"
            " WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name"
        ).fetchall()
        return [tuple(row) for row in objects]
    finally:
        conn.close()


def _user_version(db_path: Path) -> int:
    """The version stamp, read without opening the schema."""
    conn = _connect(db_path)
    try:
        return int(conn.execute("PRAGMA user_version").fetchone()[0])
    finally:
        conn.close()


def _ids_by_title(db_path: Path) -> dict[str, tuple[str, str]]:
    """Each item's recorded ids, as title -> (source, external id)."""
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            "SELECT ci.title, x.source, x.external_id FROM content_item_external_ids x"
            " JOIN content_items ci ON ci.id = x.content_item_id"
        ).fetchall()
        return {row["title"]: (row["source"], row["external_id"]) for row in rows}
    finally:
        conn.close()


def _creators_by_title(db_path: Path) -> dict[str, str | None]:
    """Each item's creator, read back through the detail table it lives in."""
    conn = _connect(db_path)
    try:
        return {
            title: conn.execute(
                f"SELECT d.{column} AS creator FROM {table} d"
                " JOIN content_items ci ON ci.id = d.content_item_id"
                " WHERE ci.title = ?",
                (title,),
            ).fetchone()["creator"]
            for title, _, _, _, table, column, _ in _LIBRARY
        }
    finally:
        conn.close()


def test_a_version_seven_database_and_a_fresh_one_end_at_the_same_schema(
    tmp_path: Path,
) -> None:
    """Two routes to one shape: the rebuild's create, and the fresh one.

    Drift between them — a column in another position, an index the rebuild
    dropped and nothing recreated — leaves every later migration reasoning
    about two schemas.
    """
    upgraded = tmp_path / "upgraded.db"
    _stand_up_a_version_seven_library(upgraded)
    SQLiteDB(upgraded)

    fresh = tmp_path / "fresh.db"
    SQLiteDB(fresh)

    assert _schema_of(upgraded) == _schema_of(fresh)
    assert _user_version(upgraded) == _SCHEMA_VERSION
    assert _user_version(fresh) == _SCHEMA_VERSION


def test_every_external_id_survives_attached_to_the_source_that_issued_it(
    tmp_path: Path,
) -> None:
    """The ids are the library's only handle on what a re-sync has.

    One lost is an item its source adds again next sync, so the move carries
    the id and the source off the row it came from.
    """
    db_path = tmp_path / "library.db"
    _stand_up_a_version_seven_library(db_path)

    SQLiteDB(db_path)

    assert _ids_by_title(db_path) == {
        "The Left Hand of Darkness": ("goodreads_csv", "12345"),
        "Arrival": ("radarr", "radarr_9"),
        "Andor": ("trakt", "1"),
        "Team Fortress 2": ("steam", "440"),
    }


def test_the_rebuild_leaves_every_detail_row_attached_to_its_item(
    tmp_path: Path,
) -> None:
    """Four detail tables and enrichment_status cascade off ``content_items``.

    Dropping it while those references still name it deletes the lot, and
    silently: the items survive, so the library still lists everything, with
    every author, director and developer gone.
    """
    db_path = tmp_path / "library.db"
    _stand_up_a_version_seven_library(db_path)

    SQLiteDB(db_path)

    assert _creators_by_title(db_path) == {
        title: creator for title, _, _, _, _, _, creator in _LIBRARY
    }
    conn = _connect(db_path)
    try:
        attached = conn.execute(
            "SELECT COUNT(*) FROM enrichment_status es"
            " JOIN content_items ci ON ci.id = es.content_item_id"
        ).fetchone()[0]
    finally:
        conn.close()
    assert attached == len(_LIBRARY)


def test_an_id_on_a_row_with_no_source_is_left_behind(tmp_path: Path) -> None:
    """An id with nothing to scope it identifies nothing.

    A file import and a hand-completed item have no source, and a row written
    with a NULL one would fail the new table's NOT NULL, taking the open down.
    """
    db_path = tmp_path / "library.db"
    _stand_up_a_version_seven_library(db_path)

    SQLiteDB(db_path)

    assert "A Hand-Typed Book" not in _ids_by_title(db_path)


def test_one_source_holding_an_id_for_two_types_keeps_it_on_the_older_row(
    tmp_path: Path,
) -> None:
    """The old key was per content type, so a source could issue an id twice.

    The new key is not, so one of the pair gives the id up. Raising instead
    would leave the database unable to open.
    """
    db_path = tmp_path / "library.db"
    _stand_up_a_version_seven_library(db_path)

    SQLiteDB(db_path)

    ids = _ids_by_title(db_path)
    assert ids["Andor"] == ("trakt", "1")
    assert "Heat" not in ids


def test_opening_the_upgraded_database_again_moves_nothing(tmp_path: Path) -> None:
    """The rebuild is guarded on the column it drops, not on the stored version.

    A rewound version, which a rolled-back build leaves, must not run it again
    over a table that no longer has the column.
    """
    db_path = tmp_path / "library.db"
    _stand_up_a_version_seven_library(db_path)
    SQLiteDB(db_path)
    after_the_upgrade = (_schema_of(db_path), _ids_by_title(db_path))

    conn = _connect(db_path)
    try:
        conn.execute("PRAGMA user_version = 0")
        conn.commit()
    finally:
        conn.close()
    SQLiteDB(db_path)

    assert (_schema_of(db_path), _ids_by_title(db_path)) == after_the_upgrade
    assert _user_version(db_path) == _SCHEMA_VERSION
