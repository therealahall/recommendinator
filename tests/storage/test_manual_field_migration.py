from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from src.models.content import ConsumptionStatus, ContentItem, ContentType
from src.storage.manager import StorageManager

_HOLDS_TABLE = """
    CREATE TABLE content_item_manual_fields (
        content_item_id INTEGER NOT NULL
            REFERENCES content_items(id) ON DELETE CASCADE,
        field TEXT NOT NULL,
        PRIMARY KEY (content_item_id, field)
    )
"""


def _writes_in_band(
    path: Path, db_id: int, band: str = "manual"
) -> list[tuple[str, Any]]:
    conn = sqlite3.connect(path)
    try:
        return [
            (field, json.loads(value))
            for field, value in conn.execute(
                "SELECT field, value_json FROM content_item_field_writes"
                " WHERE content_item_id = ? AND writer_kind = ?"
                " ORDER BY field",
                (db_id, band),
            ).fetchall()
        ]
    finally:
        conn.close()


def _holds_table_exists(path: Path) -> bool:
    conn = sqlite3.connect(path)
    try:
        return (
            conn.execute(
                "SELECT 1 FROM sqlite_master WHERE name = 'content_item_manual_fields'"
            ).fetchone()
            is not None
        )
    finally:
        conn.close()


def _seed_a_library_holding(
    path: Path,
    *,
    held: list[str],
    genres: list[str] | None = None,
    raw_genres: str | None = None,
    creator: str | None = None,
    stored_version: int = 26,
) -> int:
    """A library on an earlier version, whose holds live in the table this
    upgrade retires."""
    storage = StorageManager(sqlite_path=path)
    db_id = storage.save_content_item(
        ContentItem(
            id="movie-1",
            title="Arrival",
            content_type=ContentType.MOVIE,
            status=ConsumptionStatus.UNREAD,
            author=creator,
            metadata={} if genres is None else {"genres": genres},
        ),
        user_id=1,
    )

    conn = sqlite3.connect(path)
    try:
        conn.execute(_HOLDS_TABLE)
        conn.executemany(
            "INSERT INTO content_item_manual_fields (content_item_id, field)"
            " VALUES (?, ?)",
            [(db_id, field) for field in held],
        )
        if raw_genres is not None:
            conn.execute(
                "UPDATE movie_details SET genres = ? WHERE content_item_id = ?",
                (raw_genres, db_id),
            )
        conn.execute(f"PRAGMA user_version = {int(stored_version)}")
        conn.commit()
    finally:
        conn.close()
    return db_id


def test_a_hold_becomes_a_manual_row_carrying_what_the_field_holds_now(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "held.db"
    db_id = _seed_a_library_holding(
        db_path, held=["genres"], genres=["Sci-Fi", "Drama"]
    )

    item = StorageManager(sqlite_path=db_path).get_content_item(db_id, user_id=1)

    assert item is not None
    assert item.manual_fields == ["genres"]
    assert _writes_in_band(db_path, db_id) == [("genres", ["Sci-Fi", "Drama"])]
    assert not _holds_table_exists(db_path)


def test_a_held_field_gains_a_legacy_row_beside_the_manual_one(
    tmp_path: Path,
) -> None:
    """Suppressing it would lose what the field held before the operator held it."""
    db_path = tmp_path / "held-and-legacy.db"
    db_id = _seed_a_library_holding(
        db_path, held=["genres"], genres=["Sci-Fi", "Drama"]
    )

    item = StorageManager(sqlite_path=db_path).get_content_item(db_id, user_id=1)

    assert item is not None
    assert item.manual_fields == ["genres"]
    assert _writes_in_band(db_path, db_id) == [("genres", ["Sci-Fi", "Drama"])]
    assert _writes_in_band(db_path, db_id, "legacy") == [
        ("genres", ["Sci-Fi", "Drama"]),
        ("title", "Arrival"),
    ]


def test_a_hold_on_an_empty_field_survives_rather_than_being_dropped(
    tmp_path: Path,
) -> None:
    """Losing it would un-protect the field the operator emptied on purpose."""
    db_path = tmp_path / "empty-hold.db"
    db_id = _seed_a_library_holding(db_path, held=["genres"])

    item = StorageManager(sqlite_path=db_path).get_content_item(db_id, user_id=1)

    assert item is not None
    assert item.manual_fields == ["genres"]
    assert _writes_in_band(db_path, db_id) == [("genres", None)]


def test_the_upgrade_records_the_value_the_repairs_left_not_the_one_they_found(
    tmp_path: Path,
) -> None:
    """Carried before the repairs, the row would protect a shape the column no
    longer holds."""
    db_path = tmp_path / "repaired-hold.db"
    db_id = _seed_a_library_holding(
        db_path,
        held=["genres"],
        genres=["Sci-Fi"],
        raw_genres='[{"name": "Sci-Fi"}]',
        stored_version=15,
    )

    item = StorageManager(sqlite_path=db_path).get_content_item(db_id, user_id=1)

    assert item is not None
    assert item.metadata["genres"] == ["Sci-Fi"]
    assert _writes_in_band(db_path, db_id) == [("genres", ["Sci-Fi"])]


def test_a_held_creator_is_carried_under_the_key_its_type_states_it_in(
    tmp_path: Path,
) -> None:
    """Carried under the name the dialog says, it is a field of its own: the
    source's own director row neither outranks it nor is ranked against it."""
    db_path = tmp_path / "held-creator.db"
    db_id = _seed_a_library_holding(
        db_path, held=["creator"], creator="Denis Villeneuve"
    )

    item = StorageManager(sqlite_path=db_path).get_content_item(db_id, user_id=1)

    assert item is not None
    assert item.manual_fields == ["creator"]
    assert _writes_in_band(db_path, db_id) == [("director", "Denis Villeneuve")]


def _spell_a_carried_hold_as_the_dialog_does(path: Path) -> None:
    """A library whose creator hold was recorded before the ledger took the
    type's own key for it."""
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            "UPDATE content_item_field_writes SET field = 'creator'"
            " WHERE writer_kind = 'manual' AND field = 'director'"
        )
        conn.execute("PRAGMA user_version = 28")
        conn.commit()
    finally:
        conn.close()


def test_a_creator_hold_already_carried_is_renamed_on_the_next_upgrade(
    tmp_path: Path,
) -> None:
    """Left as it was, it names a field no writer states and the release door
    can no longer find."""
    db_path = tmp_path / "carried-creator.db"
    storage = StorageManager(sqlite_path=db_path)
    db_id = storage.save_content_item(
        ContentItem(
            id="movie-1",
            title="Arrival",
            content_type=ContentType.MOVIE,
            status=ConsumptionStatus.UNREAD,
        ),
        user_id=1,
    )
    storage.update_item_from_ui(db_id=db_id, creator="Denis Villeneuve", user_id=1)
    _spell_a_carried_hold_as_the_dialog_does(db_path)

    upgraded = StorageManager(sqlite_path=db_path)
    item = upgraded.get_content_item(db_id, user_id=1)

    assert item is not None
    assert item.manual_fields == ["creator"]
    assert _writes_in_band(db_path, db_id) == [("director", "Denis Villeneuve")]
    assert upgraded.clear_manual_field(db_id, "creator", user_id=1) is True


def test_a_held_status_still_refuses_the_next_sync_after_the_upgrade(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "held-status.db"
    db_id = _seed_a_library_holding(db_path, held=["status"])
    storage = StorageManager(sqlite_path=db_path)

    storage.save_content_item(
        ContentItem(
            id="movie-1",
            title="Arrival",
            content_type=ContentType.MOVIE,
            status=ConsumptionStatus.COMPLETED,
        ),
        user_id=1,
    )

    item = storage.get_content_item(db_id, user_id=1)
    assert item is not None
    assert item.status == ConsumptionStatus.UNREAD


def _seed_manually_enriched_db(
    path: Path,
    *,
    genres: list[str],
    emptied: bool = False,
) -> int:
    """A library on the last version before per-field holds, carrying the
    item-level ``manual`` provider an edit used to write."""
    storage = StorageManager(sqlite_path=path)
    db_id = storage.save_content_item(
        ContentItem(
            id="movie-1",
            title="Arrival",
            content_type=ContentType.MOVIE,
            status=ConsumptionStatus.UNREAD,
            metadata={"genres": genres},
        ),
        user_id=1,
    )
    if emptied:
        storage.update_item_from_ui(db_id=db_id, genres=[], user_id=1)

    conn = sqlite3.connect(path)
    try:
        conn.execute(
            "DELETE FROM content_item_field_writes WHERE writer_kind = 'manual'"
        )
        conn.execute(
            "INSERT OR REPLACE INTO enrichment_status (content_item_id,"
            " enrichment_provider, enrichment_quality, needs_enrichment)"
            " VALUES (?, 'manual', 'high', 0)",
            (db_id,),
        )
        conn.execute("PRAGMA user_version = 23")
        conn.commit()
    finally:
        conn.close()
    return db_id


def test_a_past_correction_becomes_a_hold_on_the_fields_it_could_have_touched(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "upgrade.db"
    db_id = _seed_manually_enriched_db(db_path, genres=["Sci-Fi", "Drama"])

    item = StorageManager(sqlite_path=db_path).get_content_item(db_id, user_id=1)

    assert item is not None
    assert item.metadata["genres"] == ["Sci-Fi", "Drama"]
    assert item.manual_fields == ["genres"]


def test_the_upgraded_item_rejoins_the_automatic_queue(tmp_path: Path) -> None:
    db_path = tmp_path / "requeued.db"
    db_id = _seed_manually_enriched_db(db_path, genres=["Sci-Fi"])

    storage = StorageManager(sqlite_path=db_path)

    assert storage.enrichment.status(db_id)["enrichment_provider"] is None
    assert [
        item.db_id
        for item in storage.get_content_items(user_id=1, enrichment="not_enriched")
    ] == [db_id]


def test_a_list_the_operator_emptied_is_held_at_empty_not_refilled(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "emptied.db"
    db_id = _seed_manually_enriched_db(db_path, genres=["Sci-Fi"], emptied=True)

    storage = StorageManager(sqlite_path=db_path)
    item = storage.get_content_item(db_id, user_id=1)

    assert item is not None
    assert item.manual_fields == ["genres"]

    storage.save_enrichment_metadata(
        db_id,
        ContentItem(
            title="Arrival",
            content_type=ContentType.MOVIE,
            status=ConsumptionStatus.UNREAD,
            metadata={"genres": ["Sci-Fi"]},
        ),
    )

    enriched = storage.get_content_item(db_id, user_id=1)
    assert enriched is not None
    assert not enriched.metadata.get("genres")
