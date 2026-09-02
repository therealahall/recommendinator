import json
import re
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from src.models.content import ConsumptionStatus, ContentItem, ContentType
from src.storage.item_merges import MergeError, MergeEvidence
from src.storage.schema import (
    get_enrichment_stats,
    mark_enrichment_complete,
    mark_enrichment_failed,
    mark_item_needs_enrichment,
    reset_enrichment_status,
)
from src.storage.sqlite_db import SQLiteDB
from src.utils.series import get_series_item_number, get_series_name

PINNED_UPDATE = "2020-01-01 00:00:00"
DEAD = "https://art/gone.jpg"

_ROW_TABLES = (
    ("content_items", "id"),
    ("video_game_details", "content_item_id"),
    ("enrichment_status", "content_item_id"),
    ("content_item_external_ids", "content_item_id"),
    ("content_item_dead_covers", "content_item_id"),
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


def _enrichment_provider(db: SQLiteDB, db_id: int) -> str | None:
    with db.connection() as conn:
        row = conn.execute(
            "SELECT enrichment_provider FROM enrichment_status"
            " WHERE content_item_id = ?",
            (db_id,),
        ).fetchone()
    return None if row is None else str(row["enrichment_provider"])


def _cover(db: SQLiteDB, db_id: int) -> str | None:
    item = db.get_content_item(db_id)
    assert item is not None
    return item.cover_url


def _merged_into(db: SQLiteDB, db_id: int) -> int | None:
    with db.connection() as conn:
        row = conn.execute(
            "SELECT merged_into FROM content_items WHERE id = ?", (db_id,)
        ).fetchone()
    return None if row is None else row["merged_into"]


def _id_pairs(item: ContentItem | None) -> list[tuple[str, str]]:
    assert item is not None
    return [(pair.source, pair.external_id) for pair in item.external_ids]


def _forget_the_carry(db: SQLiteDB, merge_id: int) -> None:
    with db.connection() as conn:
        row = conn.execute(
            "SELECT restore_json FROM content_item_merges WHERE id = ?", (merge_id,)
        ).fetchone()
        state = json.loads(row["restore_json"])
        state.pop("repointed")
        conn.execute(
            "UPDATE content_item_merges SET restore_json = ? WHERE id = ?",
            (json.dumps(state), merge_id),
        )
        conn.commit()


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
    assert survivor.enriched is True
    assert survivor.metadata["genres"] == ["Puzzle"]

    assert db.unmerge_content_items(record.id) == record

    after = {db_id: _snapshot(db, db_id) for db_id in (survivor_id, absorbed_id)}
    assert after == before


def test_merging_a_row_with_art_onto_one_without_carries_the_cover_and_gives_it_back(
    db: SQLiteDB,
) -> None:
    survivor_id = _save(db, "steam", "620", title="Portal 2")
    absorbed_id = _save(
        db, "gog", "1207658961", title="Portal Two", cover_url="https://art/two.jpg"
    )

    record = db.merge_content_items(survivor_id, absorbed_id, MergeEvidence.MANUAL)

    survivor = db.get_content_item(survivor_id)
    assert survivor is not None
    assert survivor.cover_url == "https://art/two.jpg"

    db.unmerge_content_items(record.id)

    unmerged = db.get_content_item(survivor_id)
    assert unmerged is not None
    assert unmerged.cover_url is None


def test_a_merge_leaves_the_survivors_own_cover_in_place(db: SQLiteDB) -> None:
    survivor_id = _save(
        db, "steam", "620", title="Portal 2", cover_url="https://art/kept.jpg"
    )
    absorbed_id = _save(
        db, "gog", "1207658961", title="Portal Two", cover_url="https://art/two.jpg"
    )

    db.merge_content_items(survivor_id, absorbed_id, MergeEvidence.MANUAL)

    survivor = db.get_content_item(survivor_id)
    assert survivor is not None
    assert survivor.cover_url == "https://art/kept.jpg"


def test_a_merge_does_not_refill_a_cover_the_survivor_proved_dead(db: SQLiteDB) -> None:
    survivor_id = _save(db, "steam", "620", title="Portal 2", cover_url=DEAD)
    assert db.clear_cover_url(survivor_id) is True
    absorbed_id = _save(
        db, "gog", "1207658961", title="Portal Two", cover_url=DEAD, rating=5
    )

    db.merge_content_items(survivor_id, absorbed_id, MergeEvidence.MANUAL)

    survivor = db.get_content_item(survivor_id)
    assert survivor is not None
    assert (survivor.cover_url, survivor.rating) == (None, 5)


def test_a_merge_fills_a_cover_the_survivor_never_buried(db: SQLiteDB) -> None:
    survivor_id = _save(db, "steam", "620", title="Portal 2", cover_url=DEAD)
    assert db.clear_cover_url(survivor_id) is True
    absorbed_id = _save(
        db, "gog", "1207658961", title="Portal Two", cover_url="https://art/two.jpg"
    )

    db.merge_content_items(survivor_id, absorbed_id, MergeEvidence.MANUAL)

    assert _cover(db, survivor_id) == "https://art/two.jpg"


def test_a_merge_carries_what_the_absorbed_row_buried_and_an_undo_takes_it_back(
    db: SQLiteDB,
) -> None:
    survivor_id = _save(db, "steam", "620", title="Portal 2")
    absorbed_id = _save(db, "gog", "1207658961", title="Portal Two", cover_url=DEAD)
    assert db.clear_cover_url(absorbed_id) is True

    record = db.merge_content_items(survivor_id, absorbed_id, MergeEvidence.MANUAL)
    _save(db, "steam", "620", title="Portal 2", cover_url=DEAD)
    assert _cover(db, survivor_id) is None

    db.unmerge_content_items(record.id)
    _save(db, "steam", "620", title="Portal 2", cover_url=DEAD)
    assert _cover(db, survivor_id) == DEAD


def test_an_ignored_absorbed_row_does_not_hide_the_survivor(db: SQLiteDB) -> None:
    survivor_id = _save(db, "steam", "620", title="Portal 2")
    absorbed_id = _save(db, "gog", "1207658961", title="Portal Two", ignored=True)

    db.merge_content_items(survivor_id, absorbed_id, MergeEvidence.MANUAL)

    survivor = db.get_content_item(survivor_id)
    assert survivor is not None
    assert survivor.ignored is False
    assert [item.db_id for item in db.get_content_items()] == [survivor_id]


def test_an_absorbed_item_is_hidden_from_every_read_and_kept_in_the_table(
    db: SQLiteDB,
) -> None:
    survivor_id = _save(db, "steam", "620", title="Portal 2")
    absorbed_id = _save(db, "gog", "1207658961", title="Portal Two")

    db.merge_content_items(survivor_id, absorbed_id, MergeEvidence.MANUAL)

    assert [item.db_id for item in db.get_content_items()] == [survivor_id]
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

    assert (stats["total"], stats["enriched"], stats["pending"]) == (1, 1, 0)


def test_every_merge_is_listed_with_what_absorbed_what_and_on_what_evidence(
    db: SQLiteDB,
) -> None:
    survivor_id = _save(db, "steam", "620", title="Portal 2")
    absorbed_id = _save(db, "gog", "1207658961", title="Portal Two")

    record = db.merge_content_items(
        survivor_id, absorbed_id, MergeEvidence.MANUAL, "gog:1207658961"
    )

    assert db.list_content_item_merges() == [record]
    assert (record.survivor_title, record.absorbed_title) == ("Portal 2", "Portal Two")
    assert (record.evidence, record.evidence_detail) == (
        MergeEvidence.MANUAL,
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

    with pytest.raises(MergeError):
        db.merge_content_items(absorbed_id, other_id, MergeEvidence.MANUAL)


def test_absorbing_a_row_that_has_absorbed_two_carries_only_those_and_hands_them_back(
    db: SQLiteDB,
) -> None:
    keeper_id = _save(db, "steam", "620", title="Portal 2")
    held_id = _save(db, "humble", "portal-dos", title="Portal Dos")
    middle_id = _save(db, "gog", "1207658961", title="Portal Two")
    rated_id = _save(
        db,
        "epic",
        "portal-2",
        title="Portal Zwei",
        rating=5,
        metadata={"genres": ["Puzzle"]},
    )
    reviewed_id = _save(db, "itch", "p2", title="Portal Deux", review="Still the best")
    group = (keeper_id, held_id, middle_id, rated_id, reviewed_id)
    _enrich(db, reviewed_id)
    _pin_updated_at(db)
    before = {db_id: _snapshot(db, db_id) for db_id in group}

    already = db.merge_content_items(keeper_id, held_id, MergeEvidence.MANUAL)
    inner = db.merge_content_items(middle_id, rated_id, MergeEvidence.MANUAL)
    also_inner = db.merge_content_items(middle_id, reviewed_id, MergeEvidence.MANUAL)
    outer = db.merge_content_items(keeper_id, middle_id, MergeEvidence.MANUAL)

    keeper = db.get_content_item(keeper_id)
    assert keeper is not None
    assert (keeper.rating, keeper.review) == (5, "Still the best")
    assert (keeper.metadata["genres"], keeper.enriched) == (["Puzzle"], True)
    assert _id_pairs(keeper) == [
        ("epic", "portal-2"),
        ("gog", "1207658961"),
        ("humble", "portal-dos"),
        ("itch", "p2"),
        ("steam", "620"),
    ]
    assert db.count_items() == 1

    for blocked, first in ((already, outer), (inner, also_inner), (also_inner, outer)):
        with pytest.raises(MergeError, match=f"before merge {first.id}"):
            db.unmerge_content_items(blocked.id)

    assert db.unmerge_content_items(outer.id) == outer
    assert _id_pairs(db.get_content_item(keeper_id)) == [
        ("humble", "portal-dos"),
        ("steam", "620"),
    ]
    assert _id_pairs(db.get_content_item(middle_id)) == [
        ("epic", "portal-2"),
        ("gog", "1207658961"),
        ("itch", "p2"),
    ]

    for record in db.list_content_item_merges():
        assert db.unmerge_content_items(record.id) == record
    assert {db_id: _snapshot(db, db_id) for db_id in group} == before
    assert db.count_items() == 5


def test_a_merge_recorded_before_the_carry_existed_still_undoes(db: SQLiteDB) -> None:
    survivor_id = _save(db, "steam", "620", title="Portal 2")
    absorbed_id = _save(db, "gog", "1207658961", title="Portal Two", rating=5)
    pair = (survivor_id, absorbed_id)
    _pin_updated_at(db)
    before = {db_id: _snapshot(db, db_id) for db_id in pair}

    record = db.merge_content_items(survivor_id, absorbed_id, MergeEvidence.MANUAL)
    _forget_the_carry(db, record.id)

    assert db.unmerge_content_items(record.id) == record
    assert {db_id: _snapshot(db, db_id) for db_id in pair} == before


def test_undoing_two_merges_into_one_survivor_newest_first_leaves_it_as_it_began(
    db: SQLiteDB,
) -> None:
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


def test_an_undo_restores_what_the_merge_wrote_and_leaves_a_later_ignore_alone(
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
    )

    record = db.merge_content_items(survivor_id, absorbed_id, MergeEvidence.MANUAL)
    assert db.set_item_ignored(survivor_id, True) is True
    db.unmerge_content_items(record.id)

    survivor = db.get_content_item(survivor_id)
    assert survivor is not None
    assert (survivor.rating, survivor.review, survivor.date_completed) == (
        None,
        None,
        None,
    )
    assert survivor.status == ConsumptionStatus.UNREAD
    assert survivor.ignored is True


def test_an_undo_of_a_merge_that_carried_nothing_keeps_a_rating_written_since(
    db: SQLiteDB,
) -> None:
    survivor_id = _save(db, "steam", "620", title="Portal 2")
    absorbed_id = _save(db, "gog", "1207658961", title="Portal Two")

    record = db.merge_content_items(survivor_id, absorbed_id, MergeEvidence.MANUAL)
    assert (
        db.update_item_from_ui(db_id=survivor_id, rating=5, review="Still the best")
        is True
    )
    db.unmerge_content_items(record.id)

    survivor = db.get_content_item(survivor_id)
    assert survivor is not None
    assert (survivor.rating, survivor.review) == (5, "Still the best")


def test_an_undo_keeps_the_enrichment_that_landed_after_the_merge(
    db: SQLiteDB,
) -> None:
    survivor_id = _save(
        db, "steam", "620", title="Portal 2", metadata={"developer": "Valve"}
    )
    absorbed_id = _save(
        db,
        "gog",
        "1207658961",
        title="Portal Two",
        metadata={"developer": "Valve Corporation"},
    )
    _enrich(db, survivor_id)
    _enrich(db, absorbed_id)

    record = db.merge_content_items(survivor_id, absorbed_id, MergeEvidence.MANUAL)
    db.save_enrichment_metadata(
        survivor_id,
        ContentItem(
            title="Portal 2",
            content_type=ContentType.VIDEO_GAME,
            status=ConsumptionStatus.UNREAD,
            metadata={"description": "Aperture Science", "genres": ["Puzzle"]},
        ),
    )
    with db.connection() as conn:
        mark_enrichment_complete(conn, survivor_id, "giantbomb", "high")
    db.unmerge_content_items(record.id)

    survivor = db.get_content_item(survivor_id)
    assert survivor is not None
    assert survivor.metadata["description"] == "Aperture Science"
    assert survivor.metadata["genres"] == ["Puzzle"]
    assert _enrichment_provider(db, survivor_id) == "giantbomb"


def test_a_reset_neither_requeues_nor_counts_the_row_behind_a_merge(
    db: SQLiteDB,
) -> None:
    survivor_id = _save(db, "steam", "620", title="Portal 2")
    absorbed_id = _save(db, "gog", "1207658961", title="Portal Two")
    _enrich(db, survivor_id)
    _enrich(db, absorbed_id)
    db.merge_content_items(survivor_id, absorbed_id, MergeEvidence.MANUAL)
    before = _snapshot(db, absorbed_id)

    with db.connection() as conn:
        assert reset_enrichment_status(conn) == 1

    assert [db_id for db_id, _item in db.get_items_needing_enrichment()] == [
        survivor_id
    ]
    assert _snapshot(db, absorbed_id) == before


def test_no_write_door_reaches_the_row_behind_a_merge(db: SQLiteDB) -> None:
    survivor_id = _save(db, "steam", "620", title="Portal 2")
    absorbed_id = _save(db, "gog", "1207658961", title="Portal Two")
    before = _snapshot(db, absorbed_id)

    record = db.merge_content_items(survivor_id, absorbed_id, MergeEvidence.MANUAL)

    assert db.set_item_ignored(absorbed_id, True) is False
    assert (
        db.update_item_from_ui(db_id=absorbed_id, status="completed", rating=1) is False
    )
    db.save_enrichment_metadata(
        absorbed_id,
        ContentItem(
            title="Portal Two",
            content_type=ContentType.VIDEO_GAME,
            status=ConsumptionStatus.UNREAD,
            metadata={"developer": "Valve", "genres": ["Puzzle"]},
        ),
    )
    with db.connection() as conn:
        mark_enrichment_complete(conn, absorbed_id, "igdb", "high")
        mark_enrichment_failed(conn, absorbed_id, "provider timed out")
        mark_item_needs_enrichment(conn, absorbed_id)

    db.unmerge_content_items(record.id)
    assert _snapshot(db, absorbed_id) == before


def test_no_door_in_src_deletes_a_content_row() -> None:
    doors = re.compile(r"delete\s+from\s+content_items\b", re.IGNORECASE)
    assert doors.search("DELETE FROM content_items WHERE id = ?")
    src = Path(__file__).resolve().parents[2] / "src"

    offenders = [
        str(module.relative_to(src))
        for module in sorted(src.rglob("*.py"))
        if doors.search(module.read_text(encoding="utf-8"))
    ]

    assert offenders == []


def test_a_survivor_gains_the_series_the_absorbed_row_states(db: SQLiteDB) -> None:
    survivor_id = _save(
        db,
        "generic_csv",
        "1",
        title="All Systems Red",
        content_type=ContentType.BOOK,
    )
    absorbed_id = _save(
        db,
        "calibre_web",
        "2",
        title="All Systems Red: A Murderbot Novella",
        content_type=ContentType.BOOK,
        metadata={"series": "The Murderbot Diaries", "series_index": 1},
    )

    db.merge_content_items(survivor_id, absorbed_id, MergeEvidence.MANUAL)

    survivor = db.get_content_item(survivor_id)
    assert get_series_name(survivor) == "The Murderbot Diaries"
    assert get_series_item_number(survivor) == 1.0
