import sqlite3
from pathlib import Path
from typing import Any

import pytest

from src.models.content import ConsumptionStatus, ContentItem, ContentType
from src.sources.service import is_valid_source_id
from src.storage.manager import MergeEvidence, StorageManager
from src.storage.merge import normalize_title_for_matching
from src.storage.schema import _LEGACY_EXTERNAL_ID_SOURCE, _rebuild_content_items
from src.storage.sqlite_db import SQLiteDB

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

_LIBRARY: tuple[tuple[str, str, str, str | None, str, str, str], ...] = (
    ("Darkness", "book", "12345", "goodreads_csv", "book_details", "author", "Guin"),
    ("Heat", "movie", "1", "trakt", "movie_details", "director", "Mann"),
    ("Andor", "tv_show", "1", "trakt", "tv_show_details", "creators", "Gilroy"),
    ("TF2", "video_game", "440", "gog", "video_game_details", "developer", "Valve"),
    ("Typed", "book", "csv-1", None, "book_details", "author", "Nobody"),
)


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


def test_a_rebuild_whose_children_followed_the_rename_raises_before_committing(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "library.db"
    _stand_up_a_version_seven_library(db_path)

    conn = _connect(db_path)
    try:
        conn.execute("BEGIN")
        with pytest.raises(RuntimeError):
            _rebuild_content_items(conn.cursor())
        conn.rollback()
    finally:
        conn.close()

    assert _creators_by_title(db_path) == {
        title: creator for title, _, _, _, _, _, creator in _LIBRARY
    }


def test_a_sync_after_the_upgrade_lands_on_the_row_holding_the_legacy_id(
    tmp_path: Path,
) -> None:
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


def _tautulli_film(
    external_id: str,
    title: str = "The Drama",
    source: str = "tautulli",
    content_type: ContentType = ContentType.MOVIE,
) -> ContentItem:
    return ContentItem(
        id=external_id,
        title=title,
        content_type=content_type,
        status=ConsumptionStatus.COMPLETED,
        source=source,
    )


def _stamp_version_25(db_path: Path) -> None:
    conn = _connect(db_path)
    try:
        conn.execute("PRAGMA user_version = 25")
        conn.commit()
    finally:
        conn.close()


@pytest.mark.parametrize(
    ("older", "newer", "synced_index"),
    [
        ("tautulli:movie:the drama:2025", "tautulli:movie:the drama:2026", 0),
        ("tautulli:movie:the drama:2026", "tautulli:movie:the drama", 1),
        ("tautulli:movie:the drama", "tautulli:movie:the drama:2026", 0),
    ],
)
def test_a_film_split_by_year_before_the_upgrade_is_offered_and_stays_merged(
    tmp_path: Path, older: str, newer: str, synced_index: int
) -> None:
    db_path = tmp_path / "library.db"
    library = SQLiteDB(db_path)
    rows = [library.save_content_item(_tautulli_film(one)) for one in (older, newer)]
    _stamp_version_25(db_path)
    upgraded = StorageManager(db_path)

    synced = upgraded.save_content_item(_tautulli_film("tautulli:movie:the drama"))
    (offered,) = upgraded.list_duplicate_suggestions().suggestions
    leftover = rows[1 - synced_index]
    upgraded.merge_content_items(leftover, synced, MergeEvidence.MANUAL)
    resynced = upgraded.save_content_item(_tautulli_film("tautulli:movie:the drama"))

    assert synced == rows[synced_index]
    assert [copy.db_id for copy in offered.copies] == rows
    assert resynced == leftover
    assert upgraded.count_items() == 1


def test_the_upgrade_rewrites_only_dated_tautulli_film_ids_and_reruns_as_a_no_op(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "library.db"
    library = SQLiteDB(db_path)
    stored = [
        ("Star Wars: Episode IV", "tautulli:movie:star wars: episode iv:1977"),
        ("1917", "tautulli:movie:1917:2019"),
        ("2046", "tautulli:movie:2046"),
    ]
    for title, external_id in stored:
        library.save_content_item(_tautulli_film(external_id, title))
    library.save_content_item(
        _tautulli_film("tautulli:show:1923", "1923", content_type=ContentType.TV_SHOW)
    )
    library.save_content_item(_tautulli_film("trakt:1984", "Nineteen", "trakt"))
    expected = {
        "Star Wars: Episode IV": ("tautulli", "tautulli:movie:star wars: episode iv"),
        "1917": ("tautulli", "tautulli:movie:1917"),
        "2046": ("tautulli", "tautulli:movie:2046"),
        "1923": ("tautulli", "tautulli:show:1923"),
        "Nineteen": ("trakt", "trakt:1984"),
    }

    for _ in range(2):
        _stamp_version_25(db_path)
        SQLiteDB(db_path)
        assert _ids_by_title(db_path) == expected
