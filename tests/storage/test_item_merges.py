"""A merge is recorded, hides rather than deletes, and undoes exactly.

The absorbed row is never written, so the round trip below asserts both rows
column for column rather than the fields the merge was expected to move.
"""

from datetime import date
from pathlib import Path
from typing import Any

import pytest

from src.models.content import ConsumptionStatus, ContentItem, ContentType
from src.storage.item_merges import MergeError, MergeEvidence
from src.storage.schema import create_schema, mark_enrichment_complete
from src.storage.sqlite_db import SQLiteDB

# Pinned so restoring a bumped ``updated_at`` is provable: CURRENT_TIMESTAMP
# resolves to the second, and a merge and its undo run inside one.
PINNED_UPDATE = "2020-01-01 00:00:00"

_ROW_TABLES = (
    ("content_items", "id"),
    ("video_game_details", "content_item_id"),
    ("enrichment_status", "content_item_id"),
    ("content_item_external_ids", "content_item_id"),
)


@pytest.fixture
def db(tmp_path: Path) -> SQLiteDB:
    return SQLiteDB(tmp_path / "test.db")


def _save(db: SQLiteDB, source: str, external_id: str, **fields: Any) -> int:
    item = ContentItem(
        id=external_id,
        source=source,
        content_type=fields.pop("content_type", ContentType.VIDEO_GAME),
        status=fields.pop("status", ConsumptionStatus.UNREAD),
        **fields,
    )
    return db.save_content_item(item)


def _enrich(db: SQLiteDB, db_id: int) -> None:
    with db.connection() as conn:
        mark_enrichment_complete(conn, db_id, "igdb", "high")


def _pin_updated_at(db: SQLiteDB) -> None:
    with db.connection() as conn:
        conn.execute("UPDATE content_items SET updated_at = ?", (PINNED_UPDATE,))
        conn.commit()


def _snapshot(db: SQLiteDB, db_id: int) -> dict[str, list[dict[str, Any]]]:
    with db.connection() as conn:
        return {
            table: [
                dict(row)
                for row in conn.execute(
                    f"SELECT * FROM {table} WHERE {key} = ?", (db_id,)
                ).fetchall()
            ]
            for table, key in _ROW_TABLES
        }


def _merged_into(db: SQLiteDB, db_id: int) -> int | None:
    with db.connection() as conn:
        row = conn.execute(
            "SELECT merged_into FROM content_items WHERE id = ?", (db_id,)
        ).fetchone()
    return None if row is None else row["merged_into"]


def test_merging_a_rated_item_into_an_unrated_one_and_unmerging_restores_both(
    db: SQLiteDB,
) -> None:
    survivor_id = _save(db, "steam", "620", title="Portal 2")
    absorbed_id = _save(
        db,
        "gog",
        "1207658961",
        title="Portal Two",
        status=ConsumptionStatus.COMPLETED,
        rating=5,
        review="Still the best",
        date_completed=date(2024, 1, 2),
        ignored=True,
        metadata={"genres": ["Puzzle"]},
    )
    _enrich(db, absorbed_id)
    _pin_updated_at(db)
    before = {db_id: _snapshot(db, db_id) for db_id in (survivor_id, absorbed_id)}

    record = db.merge_content_items(survivor_id, absorbed_id, MergeEvidence.MANUAL)

    survivor = db.get_content_item(survivor_id)
    assert survivor is not None
    assert survivor.rating == 5
    assert survivor.review == "Still the best"
    assert survivor.status == ConsumptionStatus.COMPLETED
    assert survivor.date_completed == date(2024, 1, 2)
    assert survivor.ignored is True
    assert survivor.enriched is True
    assert survivor.metadata["genres"] == ["Puzzle"]

    assert db.unmerge_content_items(record.id) == record

    after = {db_id: _snapshot(db, db_id) for db_id in (survivor_id, absorbed_id)}
    assert after == before


def test_an_absorbed_item_is_hidden_from_every_read_and_kept_in_the_table(
    db: SQLiteDB,
) -> None:
    survivor_id = _save(db, "steam", "620", title="Portal 2")
    absorbed_id = _save(db, "gog", "1207658961", title="Portal Two")

    db.merge_content_items(survivor_id, absorbed_id, MergeEvidence.MANUAL)

    assert [item.db_id for item in db.get_content_items()] == [survivor_id]
    assert db.get_content_item(absorbed_id) is None
    assert db.get_content_items_by_db_ids([absorbed_id]) == []
    assert db.count_items() == 1
    assert _merged_into(db, absorbed_id) == survivor_id


def test_an_enriched_absorbed_item_leaves_the_survivor_enriched(db: SQLiteDB) -> None:
    survivor_id = _save(db, "steam", "620", title="Portal 2")
    absorbed_id = _save(db, "gog", "1207658961", title="Portal Two")
    _enrich(db, absorbed_id)

    db.merge_content_items(survivor_id, absorbed_id, MergeEvidence.MANUAL)

    survivor = db.get_content_item(survivor_id)
    assert survivor is not None
    assert survivor.enriched is True
    assert db.get_items_needing_enrichment() == []


def test_a_resync_of_either_source_lands_on_the_survivor(db: SQLiteDB) -> None:
    survivor_id = _save(db, "steam", "620", title="Portal 2")
    absorbed_id = _save(db, "gog", "1207658961", title="Portal Two")
    db.merge_content_items(survivor_id, absorbed_id, MergeEvidence.MANUAL)

    by_id = _save(db, "gog", "1207658961", title="Portal Two", rating=4)
    by_title = db.save_content_item(
        ContentItem(
            title="Portal Two",
            source="csv",
            content_type=ContentType.VIDEO_GAME,
            status=ConsumptionStatus.UNREAD,
        )
    )

    assert (by_id, by_title) == (survivor_id, survivor_id)
    assert _merged_into(db, absorbed_id) == survivor_id
    survivor = db.get_content_item(survivor_id)
    assert survivor is not None
    assert survivor.rating == 4


def test_every_merge_is_listed_with_what_absorbed_what_and_on_what_evidence(
    db: SQLiteDB,
) -> None:
    survivor_id = _save(db, "steam", "620", title="Portal 2")
    absorbed_id = _save(db, "gog", "1207658961", title="Portal Two")

    record = db.merge_content_items(
        survivor_id, absorbed_id, MergeEvidence.EXTERNAL_ID, "gog:1207658961"
    )

    assert db.list_content_item_merges() == [record]
    assert (record.survivor_title, record.absorbed_title) == ("Portal 2", "Portal Two")
    assert (record.evidence, record.evidence_detail) == (
        MergeEvidence.EXTERNAL_ID,
        "gog:1207658961",
    )

    db.unmerge_content_items(record.id)
    assert db.list_content_item_merges() == []


def test_a_merge_is_refused_when_it_would_chain_or_cross_content_types(
    db: SQLiteDB,
) -> None:
    survivor_id = _save(db, "steam", "620", title="Portal 2")
    absorbed_id = _save(db, "gog", "1207658961", title="Portal Two")
    other_id = _save(db, "epic", "portal", title="Portal")
    book_id = _save(db, "calibre", "9", title="Portal", content_type=ContentType.BOOK)
    db.merge_content_items(survivor_id, absorbed_id, MergeEvidence.MANUAL)

    for refused in (survivor_id, absorbed_id, book_id):
        with pytest.raises(MergeError):
            db.merge_content_items(other_id, refused, MergeEvidence.MANUAL)


def test_the_upgrade_merge_keeps_the_absorbed_row_and_records_what_it_did(
    db: SQLiteDB,
) -> None:
    with db.connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO content_items (user_id, title, content_type, status)"
            " VALUES (1, 'Portal 2', 'video_game', 'unread')"
        )
        keep_id = cursor.lastrowid
        cursor.execute(
            "INSERT INTO content_items (user_id, title, content_type, status, rating)"
            " VALUES (1, 'Portal 2™', 'video_game', 'completed', 5)"
        )
        dup_id = cursor.lastrowid
        conn.execute("PRAGMA user_version = 2")
        conn.commit()

    with db.connection() as conn:
        create_schema(conn)

    assert keep_id is not None and dup_id is not None
    assert db.get_content_item(dup_id) is None
    survivor = db.get_content_item(keep_id)
    assert survivor is not None and survivor.rating == 5

    [merge] = db.list_content_item_merges()
    assert (merge.survivor_id, merge.absorbed_id) == (keep_id, dup_id)
    assert merge.evidence is MergeEvidence.NORMALIZED_TITLE
    assert merge.evidence_detail == "portal 2"

    db.unmerge_content_items(merge.id)

    restored = db.get_content_item(dup_id)
    assert restored is not None and restored.rating == 5
    survivor = db.get_content_item(keep_id)
    assert survivor is not None and survivor.rating is None
