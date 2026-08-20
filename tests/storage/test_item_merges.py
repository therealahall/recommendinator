"""A merge is recorded, hides rather than deletes, and undoes exactly.

The absorbed row is never written, so the round trip below asserts both rows
column for column, not the fields the merge was expected to move.
"""

from datetime import date
from pathlib import Path
from typing import Any

import pytest

from src.models.content import ConsumptionStatus, ContentItem, ContentType
from src.storage.item_merges import MergeError, MergeEvidence
from src.storage.schema import get_enrichment_stats, mark_enrichment_complete
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


def _id_pairs(item: ContentItem | None) -> list[tuple[str, str]]:
    assert item is not None
    return [(pair.source, pair.external_id) for pair in item.external_ids]


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
    # A term matching both titles: the search page filters the candidates it
    # scans, not only the ids it loads.
    assert [item.db_id for item in db.get_content_items(search="Portal")] == [
        survivor_id
    ]
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
    # The re-sync neither un-hides the absorbed row nor adds a third one.
    assert db.count_items() == 1
    survivor = db.get_content_item(survivor_id)
    assert survivor is not None
    assert survivor.rating == 4


def test_the_survivor_reports_both_rows_ids_and_hands_them_back_on_unmerge(
    db: SQLiteDB,
) -> None:
    survivor_id = _save(db, "steam", "620", title="Portal 2")
    absorbed_id = _save(db, "gog", "1207658961", title="Portal Two")

    record = db.merge_content_items(survivor_id, absorbed_id, MergeEvidence.MANUAL)

    merged = db.get_content_item(survivor_id)
    assert merged is not None
    assert _id_pairs(merged) == [("gog", "1207658961"), ("steam", "620")]
    # Its own source's id, so re-saving what was read records no pair the
    # survivor's row does not hold.
    assert merged.id == "620"

    db.unmerge_content_items(record.id)

    assert _id_pairs(db.get_content_item(survivor_id)) == [("steam", "620")]
    assert _id_pairs(db.get_content_item(absorbed_id)) == [("gog", "1207658961")]


def test_the_enrichment_stats_count_the_survivor_and_not_the_row_behind_it(
    db: SQLiteDB,
) -> None:
    survivor_id = _save(db, "steam", "620", title="Portal 2")
    absorbed_id = _save(db, "gog", "1207658961", title="Portal Two")
    _enrich(db, absorbed_id)

    db.merge_content_items(survivor_id, absorbed_id, MergeEvidence.MANUAL)

    with db.connection() as conn:
        stats = get_enrichment_stats(conn, user_id=1)

    # An absorbed row keeps its enrichment row, so counting the tracked rows
    # without the items they belong to drives ``pending`` negative.
    assert (stats["total"], stats["enriched"], stats["pending"]) == (1, 1, 0)


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


def test_a_merge_is_refused_when_a_row_is_hidden_or_they_cross_content_types(
    db: SQLiteDB,
) -> None:
    survivor_id = _save(db, "steam", "620", title="Portal 2")
    absorbed_id = _save(db, "gog", "1207658961", title="Portal Two")
    other_id = _save(db, "epic", "portal", title="Portal")
    book_id = _save(db, "calibre", "9", title="Portal", content_type=ContentType.BOOK)
    db.merge_content_items(survivor_id, absorbed_id, MergeEvidence.MANUAL)

    for refused in (absorbed_id, book_id):
        with pytest.raises(MergeError):
            db.merge_content_items(other_id, refused, MergeEvidence.MANUAL)

    # A hidden survivor would leave what it absorbed resolving, in the one
    # COALESCE every lookup spends, onto a hidden row.
    with pytest.raises(MergeError):
        db.merge_content_items(absorbed_id, other_id, MergeEvidence.MANUAL)


def test_absorbing_a_row_that_has_absorbed_one_carries_the_group_and_hands_it_back(
    db: SQLiteDB,
) -> None:
    keeper_id = _save(db, "steam", "620", title="Portal 2")
    middle_id = _save(db, "gog", "1207658961", title="Portal Two")
    tail_id = _save(db, "epic", "portal-2", title="Portal Zwei", rating=5)

    inner = db.merge_content_items(middle_id, tail_id, MergeEvidence.MANUAL)
    outer = db.merge_content_items(keeper_id, middle_id, MergeEvidence.MANUAL)

    keeper = db.get_content_item(keeper_id)
    assert keeper is not None
    assert keeper.rating == 5
    assert _id_pairs(keeper) == [
        ("epic", "portal-2"),
        ("gog", "1207658961"),
        ("steam", "620"),
    ]
    assert db.count_items() == 1
    # Left pointing at the row it was, the tail would resolve, in the one
    # COALESCE every lookup spends, onto a row no read hands back.
    assert (_merged_into(db, middle_id), _merged_into(db, tail_id)) == (
        keeper_id,
        keeper_id,
    )

    with pytest.raises(MergeError):
        db.unmerge_content_items(inner.id)

    assert db.unmerge_content_items(outer.id) == outer
    assert (_merged_into(db, middle_id), _merged_into(db, tail_id)) == (
        None,
        middle_id,
    )

    assert db.unmerge_content_items(inner.id) == inner
    assert _merged_into(db, tail_id) is None
    assert db.count_items() == 3


def test_undoing_two_merges_into_one_survivor_newest_first_leaves_it_as_it_began(
    db: SQLiteDB,
) -> None:
    """Each merge records the survivor as it stands, and the older record already
    holds what the newer merge moved, so undoing them in the order they were made
    would write an unmerged row's rating back onto the survivor."""
    survivor_id = _save(db, "steam", "620", title="Portal 2")
    rated_id = _save(
        db, "gog", "1207658961", title="Portal Two", rating=5, review="Still the best"
    )
    completed_id = _save(
        db, "epic", "portal-2", title="Portal Zwei", status=ConsumptionStatus.COMPLETED
    )
    _pin_updated_at(db)
    before = _snapshot(db, survivor_id)

    first = db.merge_content_items(survivor_id, rated_id, MergeEvidence.MANUAL)
    second = db.merge_content_items(survivor_id, completed_id, MergeEvidence.MANUAL)

    with pytest.raises(MergeError):
        db.unmerge_content_items(first.id)

    assert db.unmerge_content_items(second.id) == second
    assert db.unmerge_content_items(first.id) == first
    assert _snapshot(db, survivor_id) == before


def test_neither_write_door_reaches_the_row_behind_a_merge(db: SQLiteDB) -> None:
    """Both doors select by id alone, so an id from a stale merge surface wrote a
    row no read hands back, and the undo handed that row back carrying the write."""
    survivor_id = _save(db, "steam", "620", title="Portal 2")
    absorbed_id = _save(db, "gog", "1207658961", title="Portal Two")
    before = _snapshot(db, absorbed_id)

    record = db.merge_content_items(survivor_id, absorbed_id, MergeEvidence.MANUAL)

    assert db.set_item_ignored(absorbed_id, True) is False
    assert (
        db.update_item_from_ui(db_id=absorbed_id, status="completed", rating=1) is False
    )

    db.unmerge_content_items(record.id)
    assert _snapshot(db, absorbed_id) == before
