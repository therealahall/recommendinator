"""Moving the external ids off ``content_items``, which needs a table rebuild."""

import sqlite3
from pathlib import Path
from typing import Any

from src.storage.schema import _SCHEMA_VERSION
from src.storage.sqlite_db import SQLiteDB

# Written out rather than derived: the point is a build this one is not.
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

# One per content type, so every detail table rides the rebuild. Trakt's pair
# shares an id; the last row is sourceless, as a file import leaves it.
_LIBRARY: tuple[tuple[str, str, str, str | None, str, str, str], ...] = (
    ("Darkness", "book", "12345", "goodreads_csv", "book_details", "author", "Guin"),
    ("Heat", "movie", "1", "trakt", "movie_details", "director", "Mann"),
    ("Andor", "tv_show", "1", "trakt", "tv_show_details", "creators", "Gilroy"),
    ("TF2", "video_game", "440", "steam", "video_game_details", "developer", "Valve"),
    ("Typed", "book", "csv-1", None, "book_details", "author", "Nobody"),
)


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _stand_up_a_version_seven_library(db_path: Path) -> None:
    SQLiteDB(db_path)
    conn = _connect(db_path)
    try:
        conn.execute("DROP TABLE content_items")
        conn.execute(_CONTENT_ITEMS_AT_VERSION_SEVEN)
        conn.execute("ALTER TABLE content_items ADD COLUMN ignored BOOLEAN DEFAULT 0")
        for title, kind, external_id, source, table, column, creator in _LIBRARY:
            cursor = conn.execute(
                "INSERT INTO content_items"
                " (user_id, external_id, title, content_type, status, source)"
                " VALUES (1, ?, ?, ?, 'completed', ?)",
                (external_id, title, kind, source),
            )
            conn.execute(
                f"INSERT INTO {table} (content_item_id, {column}) VALUES (?, ?)",
                (cursor.lastrowid, creator),
            )
        conn.execute("PRAGMA user_version = 7")
        conn.commit()
    finally:
        conn.close()


def _schema_of(db_path: Path) -> list[tuple[Any, ...]]:
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
    conn = _connect(db_path)
    try:
        return int(conn.execute("PRAGMA user_version").fetchone()[0])
    finally:
        conn.close()


def _ids_by_title(db_path: Path) -> dict[str, tuple[str, str]]:
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


def test_a_version_seven_database_reaches_the_fresh_schema_and_stays_there(
    tmp_path: Path,
) -> None:
    upgraded = tmp_path / "upgraded.db"
    _stand_up_a_version_seven_library(upgraded)
    SQLiteDB(upgraded)
    ids_after_the_upgrade = _ids_by_title(upgraded)
    SQLiteDB(upgraded)

    fresh = tmp_path / "fresh.db"
    SQLiteDB(fresh)

    assert _schema_of(upgraded) == _schema_of(fresh)
    assert _ids_by_title(upgraded) == ids_after_the_upgrade
    assert _user_version(upgraded) == _SCHEMA_VERSION


def test_the_rebuild_carries_every_id_and_every_detail_row(tmp_path: Path) -> None:
    """A sourceless id is dropped; one a source gave two types is kept twice."""
    db_path = tmp_path / "library.db"
    _stand_up_a_version_seven_library(db_path)

    SQLiteDB(db_path)

    assert _ids_by_title(db_path) == {
        "Darkness": ("goodreads_csv", "12345"),
        "Heat": ("trakt", "1"),
        "Andor": ("trakt", "1"),
        "TF2": ("steam", "440"),
    }
    assert _creators_by_title(db_path) == {
        title: creator for title, _, _, _, _, _, creator in _LIBRARY
    }
