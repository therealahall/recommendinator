"""Moving the external ids off ``content_items``, which needs a table rebuild."""

import sqlite3
from pathlib import Path
from typing import Any

from src.models.content import ConsumptionStatus, ContentItem, ContentType
from src.sources.service import is_valid_source_id
from src.storage.merge import normalize_title_for_matching
from src.storage.schema import _LEGACY_EXTERNAL_ID_SOURCE
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
# shares an id, the last row is sourceless as a file import leaves it, and TF2
# holds Steam's app id under the name GOG's later sync stamped.
_LIBRARY: tuple[tuple[str, str, str, str | None, str, str, str], ...] = (
    ("Darkness", "book", "12345", "goodreads_csv", "book_details", "author", "Guin"),
    ("Heat", "movie", "1", "trakt", "movie_details", "director", "Mann"),
    ("Andor", "tv_show", "1", "trakt", "tv_show_details", "creators", "Gilroy"),
    ("TF2", "video_game", "440", "gog", "video_game_details", "developer", "Valve"),
    ("Typed", "book", "csv-1", None, "book_details", "author", "Nobody"),
)


# On the first row: the rebuild is the only thing carrying these across, and
# it drops the old table, so a column it forgets is gone for good.
_OPERATOR_OWNED: dict[str, object] = {
    "rating": 4,
    "review": "Read it twice",
    "date_completed": "2019-07-04",
    "ignored": 1,
}


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
            normalized = normalize_title_for_matching(title)
            cursor = conn.execute(
                "INSERT INTO content_items (user_id, external_id, title,"
                " normalized_title, content_type, status, source)"
                " VALUES (1, ?, ?, ?, ?, 'completed', ?)",
                (external_id, title, normalized, kind, source),
            )
            conn.execute(
                f"INSERT INTO {table} (content_item_id, {column}) VALUES (?, ?)",
                (cursor.lastrowid, creator),
            )
        assignments = ", ".join(f"{column} = ?" for column in _OPERATOR_OWNED)
        conn.execute(
            f"UPDATE content_items SET {assignments} WHERE title = ?",
            (*_OPERATOR_OWNED.values(), _LIBRARY[0][0]),
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


def _rows_titled(db_path: Path, title: str) -> list[tuple[int, str, str]]:
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            "SELECT ci.id, x.source, x.external_id FROM content_items ci"
            " JOIN content_item_external_ids x ON x.content_item_id = ci.id"
            " WHERE ci.title = ? ORDER BY ci.id, x.source",
            (title,),
        ).fetchall()
        return [(int(r["id"]), r["source"], r["external_id"]) for r in rows]
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


def test_the_rebuild_files_every_id_under_a_source_no_operator_can_configure(
    tmp_path: Path,
) -> None:
    """The source column names the last syncer, not the id's owner."""
    db_path = tmp_path / "library.db"
    _stand_up_a_version_seven_library(db_path)

    SQLiteDB(db_path)

    assert not is_valid_source_id(_LEGACY_EXTERNAL_ID_SOURCE)
    assert _ids_by_title(db_path) == {
        "Darkness": (_LEGACY_EXTERNAL_ID_SOURCE, "12345"),
        "Heat": (_LEGACY_EXTERNAL_ID_SOURCE, "1"),
        "Andor": (_LEGACY_EXTERNAL_ID_SOURCE, "1"),
        "TF2": (_LEGACY_EXTERNAL_ID_SOURCE, "440"),
        "Typed": (_LEGACY_EXTERNAL_ID_SOURCE, "csv-1"),
    }
    assert _creators_by_title(db_path) == {
        title: creator for title, _, _, _, _, _, creator in _LIBRARY
    }


def test_the_rebuild_carries_the_columns_only_the_operator_could_have_written(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "library.db"
    _stand_up_a_version_seven_library(db_path)

    upgraded = SQLiteDB(db_path)

    with upgraded.connection() as conn:
        row = conn.execute(
            f"SELECT {', '.join(_OPERATOR_OWNED)} FROM content_items WHERE title = ?",
            (_LIBRARY[0][0],),
        ).fetchone()
    assert dict(row) == _OPERATOR_OWNED


def test_a_sync_after_the_upgrade_lands_on_the_row_holding_the_legacy_id(
    tmp_path: Path,
) -> None:
    """The row wears GOG's name over Steam's app id, and GOG syncs its own."""
    db_path = tmp_path / "library.db"
    _stand_up_a_version_seven_library(db_path)
    upgraded = SQLiteDB(db_path)

    db_id = upgraded.save_content_item(
        ContentItem(
            id="1470669032",
            title="TF2",
            content_type=ContentType.VIDEO_GAME,
            status=ConsumptionStatus.UNREAD,
            source="gog",
        )
    )

    assert _rows_titled(db_path, "TF2") == [
        (db_id, _LEGACY_EXTERNAL_ID_SOURCE, "440"),
        (db_id, "gog", "1470669032"),
    ]
