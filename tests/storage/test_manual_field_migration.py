from __future__ import annotations

import sqlite3
from pathlib import Path

from src.models.content import ConsumptionStatus, ContentItem, ContentType
from src.storage.manager import StorageManager


def _seed_manually_enriched_db(
    path: Path, *, genres: list[str] | None, emptied: bool = False
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
            metadata={"genres": genres} if genres else {},
        ),
        user_id=1,
    )
    if emptied:
        storage.update_item_from_ui(db_id=db_id, genres=[], user_id=1)

    conn = sqlite3.connect(path)
    try:
        conn.execute("DELETE FROM content_item_manual_fields")
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
    assert [
        (held.field, held.value, held.source_value, held.drifted)
        for held in item.manual_fields
    ] == [("genres", "Sci-Fi, Drama", "Sci-Fi, Drama", False)]


def test_the_upgraded_item_rejoins_the_automatic_queue(tmp_path: Path) -> None:
    db_path = tmp_path / "requeued.db"
    db_id = _seed_manually_enriched_db(db_path, genres=["Sci-Fi"])

    storage = StorageManager(sqlite_path=db_path)

    assert storage.enrichment.status(db_id)["enrichment_provider"] is None
    assert [
        item.db_id
        for item in storage.get_content_items(user_id=1, enrichment="not_enriched")
    ] == [db_id]


def test_an_item_whose_fields_are_all_empty_is_held_on_none_of_them(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "empty.db"
    db_id = _seed_manually_enriched_db(db_path, genres=None)

    item = StorageManager(sqlite_path=db_path).get_content_item(db_id, user_id=1)

    assert item is not None
    assert item.manual_fields == []


def test_a_list_the_operator_emptied_is_held_at_empty_not_refilled(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "emptied.db"
    db_id = _seed_manually_enriched_db(db_path, genres=["Sci-Fi"], emptied=True)

    storage = StorageManager(sqlite_path=db_path)
    item = storage.get_content_item(db_id, user_id=1)

    assert item is not None
    assert [held.field for held in item.manual_fields] == ["genres"]

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


def test_the_step_does_not_re_run_after_the_upgrade(tmp_path: Path) -> None:
    db_path = tmp_path / "once.db"
    db_id = _seed_manually_enriched_db(db_path, genres=["Sci-Fi"])
    StorageManager(sqlite_path=db_path).clear_manual_field(db_id, "genres", user_id=1)

    item = StorageManager(sqlite_path=db_path).get_content_item(db_id, user_id=1)

    assert item is not None
    assert item.manual_fields == []
