from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import pytest

from src.models.content import ConsumptionStatus, ContentItem, ContentType
from src.models.detail_fields import DETAIL_FIELDS, ContentTypeFields, FieldKind
from src.storage.field_writes import (
    FieldWriter,
    StoredFieldWrite,
    WriterBand,
    read_field_writes,
)
from src.storage.sqlite_db import SaveOutcome, SQLiteDB
from src.utils.series import SeriesAuthority

_SAMPLE_BY_KIND: dict[FieldKind, Any] = {
    FieldKind.CREATOR: "Frank Herbert",
    FieldKind.TEXT: "stated",
    FieldKind.INTEGER: 3,
    FieldKind.STRING_LIST: ["Science Fiction"],
    FieldKind.FREE_FORM: "stated",
}

_SAMPLE_BY_KEY: dict[str, Any] = {
    "series_position": 1.0,
    "series_position_authority": SeriesAuthority.LIBRARY.value,
    "playtime_hours": 12.5,
    "seasons_watched": [1],
    "seasons_watched_dates": {"1": "2026-01-01T00:00:00+00:00"},
}

# Named rather than read off FieldOwner, so a declaration given the wrong owner
# cannot move the expectation along with the behaviour.
_NEVER_RECORDED = frozenset(
    {
        "status",
        "rating",
        "review",
        "ignored",
        "date_completed",
        "seasons_watched",
        "seasons_watched_dates",
    }
)

_RECORDED_PER_TYPE: dict[str, set[str]] = {
    "book": {"title", "cover_url", "isbn", "genres", "series_name", "series_position"},
    "movie": {"title", "cover_url", "director", "release_year", "runtime", "genres"},
    "tv_show": {"title", "cover_url", "creators", "seasons", "episodes", "genres"},
    "video_game": {
        "title",
        "cover_url",
        "developer",
        "platforms",
        "playtime_hours",
        "genres",
    },
}


def _everything_a_source_can_state(spec: ContentTypeFields) -> dict[str, Any]:
    return {
        detail_field.metadata_key: _SAMPLE_BY_KEY.get(
            detail_field.metadata_key, _SAMPLE_BY_KIND[detail_field.kind]
        )
        for detail_field in spec.fields
    }


def _book(source: str, external_id: str, **metadata: Any) -> ContentItem:
    return ContentItem(
        id=external_id,
        source=source,
        title=str(metadata.pop("title", "Dune")),
        author="Frank Herbert",
        content_type=ContentType.BOOK,
        status=ConsumptionStatus.UNREAD,
        metadata=metadata,
    )


def _calibre_book(**overrides: Any) -> ContentItem:
    """The shape a Calibre-Web sync states: an ordinal its own library founds."""
    stated: dict[str, Any] = {
        "description": "A desert planet.",
        "series_name": "Dune",
        "series_position": 1.0,
        "series_position_authority": SeriesAuthority.LIBRARY.value,
    }
    stated.update(overrides)
    return _book("calibre_web", "cw-1", **stated)


def _writes(db: SQLiteDB, db_id: int) -> list[StoredFieldWrite]:
    with db.connection() as conn:
        return read_field_writes(conn.cursor(), db_id)


def _for_field(writes: list[StoredFieldWrite], field: str) -> list[StoredFieldWrite]:
    return [write for write in writes if write.field == field]


def _stored_description(db: SQLiteDB, db_id: int) -> str | None:
    with db.connection() as conn:
        row = conn.execute(
            "SELECT description FROM book_details WHERE content_item_id = ?",
            (db_id,),
        ).fetchone()
    description: str | None = row["description"]
    return description


def test_a_sync_records_one_row_per_field_the_source_stated(tmp_path: Path) -> None:
    db = SQLiteDB(tmp_path / "stated.db")

    db_id = db.save_content_item(_calibre_book())
    writes = _writes(db, db_id)

    assert [write.value for write in _for_field(writes, "title")] == ["Dune"]
    assert [write.value for write in _for_field(writes, "description")] == [
        "A desert planet."
    ]
    assert [write.value for write in _for_field(writes, "series_name")] == ["Dune"]
    assert [write.value for write in _for_field(writes, "series_position")] == [1.0]
    assert {write.writer_kind for write in writes} == {WriterBand.SOURCE}
    assert {write.writer for write in writes} == {"calibre_web"}
    # A field this sync never mentioned states nothing to remember.
    assert _for_field(writes, "isbn") == []


@pytest.mark.parametrize("content_type", sorted(DETAIL_FIELDS))
def test_a_source_stating_every_field_records_only_the_writer_owned_ones(
    tmp_path: Path, content_type: str
) -> None:
    db = SQLiteDB(tmp_path / f"owned-{content_type}.db")

    db_id = db.save_content_item(
        ContentItem(
            id=f"cw-{content_type}",
            source="calibre_web",
            title="Dune",
            content_type=ContentType(content_type),
            status=ConsumptionStatus.COMPLETED,
            rating=5,
            review="A desert planet.",
            date_completed=date(2026, 1, 1),
            ignored=True,
            cover_url="https://example.test/dune.jpg",
            metadata=_everything_a_source_can_state(DETAIL_FIELDS[content_type]),
        )
    )
    fields = {write.field for write in _writes(db, db_id)}
    with db.connection() as conn:
        base_columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(content_items)")
        }

    assert fields & _NEVER_RECORDED == set()
    assert fields & base_columns == {"title", "cover_url"}
    assert _RECORDED_PER_TYPE[content_type] <= fields


def test_the_series_authority_rides_its_ordinal_rather_than_taking_a_row(
    tmp_path: Path,
) -> None:
    db = SQLiteDB(tmp_path / "authority_row.db")

    db_id = db.save_content_item(_calibre_book())
    series_fields = [
        write.field for write in _writes(db, db_id) if write.field.startswith("series")
    ]

    assert series_fields == ["series_name", "series_position"]


def test_one_ordinal_reads_the_same_whether_stated_as_text_or_a_number(
    tmp_path: Path,
) -> None:
    db = SQLiteDB(tmp_path / "ordinals.db")
    db_id = db.save_content_item(
        _book("calibre_web", "cw-1", series_name="Dune", series_position=3.0)
    )

    db.save_content_item(
        _book("storygraph_csv", "sg-1", series_name="Dune", series_position="3")
    )
    ordinals = _for_field(_writes(db, db_id), "series_position")

    assert [(write.writer, write.value) for write in ordinals] == [
        ("calibre_web", 3.0),
        ("storygraph_csv", 3.0),
    ]


def test_the_series_ordinal_carries_the_authority_its_source_claimed(
    tmp_path: Path,
) -> None:
    db = SQLiteDB(tmp_path / "authority.db")

    db_id = db.save_content_item(_calibre_book())
    writes = _writes(db, db_id)

    assert _for_field(writes, "series_position")[0].authority == "library"
    assert _for_field(writes, "series_name")[0].authority is None
    assert _for_field(writes, "title")[0].authority is None


def test_restating_the_same_values_leaves_written_at_untouched(
    tmp_path: Path,
) -> None:
    db = SQLiteDB(tmp_path / "restated.db")

    # written_at is the rebuild's tiebreak between two sources, and a dict
    # restated in another key order states the same thing.
    db_id = db.save_content_item(_calibre_book(series_name={"id": 1, "name": "Dune"}))
    first = {
        (write.writer, write.field): write.written_at for write in _writes(db, db_id)
    }

    db.save_content_item(_calibre_book(series_name={"name": "Dune", "id": 1}))
    second = {
        (write.writer, write.field): write.written_at for write in _writes(db, db_id)
    }

    assert ("calibre_web", "title") in first
    assert ("calibre_web", "series_name") in first
    assert second == first


def test_a_source_correcting_itself_replaces_its_own_row(tmp_path: Path) -> None:
    db = SQLiteDB(tmp_path / "corrected.db")
    db_id = db.save_content_item(_calibre_book())

    db.save_content_item(
        _calibre_book(title="Dune: Book One", description="Arrakis, desert planet.")
    )
    writes = _writes(db, db_id)

    assert [write.value for write in _for_field(writes, "title")] == ["Dune: Book One"]
    # The fill-only column refuses the new description, so the row it would
    # otherwise be read off never moves: the ledger has to carry the correction.
    assert [write.value for write in _for_field(writes, "description")] == [
        "Arrakis, desert planet."
    ]
    assert _stored_description(db, db_id) == "A desert planet."


def test_two_sources_stating_one_field_keep_a_row_each(tmp_path: Path) -> None:
    db = SQLiteDB(tmp_path / "two_writers.db")
    db_id = db.save_content_item(_book("calibre_web", "cw-1"))

    same_item = db.save_content_item(_book("storygraph_csv", "sg-1"))
    writes = _writes(db, db_id)

    assert same_item == db_id
    assert {(write.writer, write.value) for write in _for_field(writes, "title")} == {
        ("calibre_web", "Dune"),
        ("storygraph_csv", "Dune"),
    }


def test_the_fill_only_rules_still_decide_what_the_column_holds(
    tmp_path: Path,
) -> None:
    db = SQLiteDB(tmp_path / "unchanged.db")
    db_id = db.save_content_item(
        _book("calibre_web", "cw-1", description="A desert planet.")
    )

    db.save_content_item(
        _book("storygraph_csv", "sg-1", description="Arrakis, desert planet.")
    )
    writes = _writes(db, db_id)

    assert _stored_description(db, db_id) == "A desert planet."
    assert {
        (write.writer, write.value) for write in _for_field(writes, "description")
    } == {
        ("calibre_web", "A desert planet."),
        ("storygraph_csv", "Arrakis, desert planet."),
    }


def test_a_source_that_stops_stating_a_field_keeps_the_row_it_stated(
    tmp_path: Path,
) -> None:
    db = SQLiteDB(tmp_path / "persisted.db")
    db_id = db.save_content_item(_calibre_book())

    db.save_content_item(_book("calibre_web", "cw-1", description="A desert planet."))
    fields = {write.field for write in _writes(db, db_id)}

    assert fields == {
        "title",
        "author",
        "description",
        "series_name",
        "series_position",
    }


def test_a_provider_correcting_its_answer_keeps_one_row_holding_the_new_value(
    tmp_path: Path,
) -> None:
    db = SQLiteDB(tmp_path / "corrected_provider.db")
    db_id = db.save_content_item(_book("calibre_web", "cw-1"))
    writer = FieldWriter(WriterBand.PROVIDER, "openlibrary")

    for pages in (400, 420):
        db.save_enrichment_metadata(
            db_id,
            _book("calibre_web", "cw-1", pages=pages),
            writer=writer,
            stated={"pages": pages},
        )
    stated = _for_field(_writes(db, db_id), "pages")

    assert [(write.writer_kind, write.writer, write.value) for write in stated] == [
        (WriterBand.PROVIDER, "openlibrary", 420)
    ]


def test_an_enrichment_write_is_not_recorded_as_a_source(tmp_path: Path) -> None:
    db = SQLiteDB(tmp_path / "enriched.db")
    db_id = db.save_content_item(_book("calibre_web", "cw-1"))

    db.save_enrichment_metadata(
        db_id,
        ContentItem(
            title="Dune",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
            metadata={"publisher": "Chilton Books"},
        ),
    )
    writes = _writes(db, db_id)

    assert {write.writer for write in writes} == {"calibre_web"}
    assert _for_field(writes, "publisher") == []


def test_an_aliased_list_records_one_canonical_row_restating_it_moves_nothing(
    tmp_path: Path,
) -> None:
    db = SQLiteDB(tmp_path / "aliased.db")
    db_id = db.save_content_item(_book("calibre_web", "cw-1", genre="Science Fiction"))
    first = _writes(db, db_id)

    db.save_content_item(_book("calibre_web", "cw-1", genres=["Science Fiction"]))

    assert [write.value for write in _for_field(first, "genres")] == [
        ["Science Fiction"]
    ]
    assert _for_field(first, "genre") == []
    assert _writes(db, db_id) == first


def test_a_source_raising_its_own_authority_replaces_the_one_beside_the_ordinal(
    tmp_path: Path,
) -> None:
    db = SQLiteDB(tmp_path / "authority_raised.db")
    db_id = db.save_content_item(
        _calibre_book(series_position_authority=SeriesAuthority.STATED.value)
    )

    db.save_content_item(_calibre_book())
    ordinals = _for_field(_writes(db, db_id), "series_position")

    assert [(write.value, write.authority) for write in ordinals] == [(1.0, "library")]


def test_a_correction_no_column_accepts_survives_a_sync_reporting_unchanged(
    tmp_path: Path,
) -> None:
    db = SQLiteDB(tmp_path / "refused.db")
    db_id = db.save_content_item(
        _book("calibre_web", "cw-1", description="A desert planet.")
    )

    outcome = db.save_content_item_outcome(
        _book("calibre_web", "cw-1", description="Arrakis, desert planet.")
    ).outcome

    assert outcome is SaveOutcome.UNCHANGED
    assert [write.value for write in _for_field(_writes(db, db_id), "description")] == [
        "Arrakis, desert planet."
    ]
    assert _stored_description(db, db_id) == "A desert planet."
