from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from src.models.content import ConsumptionStatus, ContentItem, ContentType
from src.models.detail_fields import DETAIL_FIELDS, FieldKind, FieldOwner
from src.storage.field_rebuild import WriterRanks, rebuild_item_fields
from src.storage.field_writes import (
    FieldWrite,
    FieldWriter,
    WriterBand,
    record_field_writes,
)
from src.storage.sqlite_db import SQLiteDB
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
}

_STEAM_PORTRAIT = "https://example.test/steam/library_600x900.jpg"
_RAWG_SCREENSHOT = "https://example.test/rawg/screenshot.jpg"
_IGDB_BOX_ART = "https://example.test/igdb/cover_big.jpg"


def _rebuild(
    db: SQLiteDB, db_id: int, ranks: WriterRanks = WriterRanks()
) -> dict[str, Any]:
    with db.connection() as conn:
        return rebuild_item_fields(conn.cursor(), db_id, ranks)


def _states(db: SQLiteDB, db_id: int, writer: FieldWriter, *writes: FieldWrite) -> None:
    with db.connection() as conn:
        cursor = conn.cursor()
        record_field_writes(cursor, db_id, writer, writes)
        conn.commit()


def _provider_states(
    db: SQLiteDB, db_id: int, provider: str, *writes: FieldWrite
) -> None:
    _states(db, db_id, FieldWriter(WriterBand.PROVIDER, provider), *writes)


def _book(source: str, **metadata: Any) -> ContentItem:
    return ContentItem(
        id=f"{source}-1",
        source=source,
        title="Dune",
        author="Frank Herbert",
        content_type=ContentType.BOOK,
        status=ConsumptionStatus.UNREAD,
        metadata=metadata,
    )


def _stating_every_writer_field(content_type: str) -> ContentItem:
    return ContentItem(
        id=f"cw-{content_type}",
        source="calibre_web",
        title="Dune",
        author="Frank Herbert",
        content_type=ContentType(content_type),
        status=ConsumptionStatus.UNREAD,
        cover_url=_STEAM_PORTRAIT,
        metadata={
            detail_field.metadata_key: _SAMPLE_BY_KEY.get(
                detail_field.metadata_key, _SAMPLE_BY_KIND[detail_field.kind]
            )
            for detail_field in DETAIL_FIELDS[content_type].fields
            if detail_field.owner is FieldOwner.WRITER
        },
    )


def _steam_game_two_providers_matched(
    tmp_path: Path, name: str
) -> tuple[SQLiteDB, int]:
    db = SQLiteDB(tmp_path / f"{name}.db")
    db_id = db.save_content_item(
        ContentItem(
            id="440",
            source="steam",
            title="Portal 2",
            content_type=ContentType.VIDEO_GAME,
            status=ConsumptionStatus.UNREAD,
            cover_url=_STEAM_PORTRAIT,
        )
    )
    _provider_states(db, db_id, "rawg", FieldWrite("cover_url", _RAWG_SCREENSHOT))
    _provider_states(db, db_id, "igdb", FieldWrite("cover_url", _IGDB_BOX_ART))
    return db, db_id


@pytest.mark.parametrize("content_type", sorted(DETAIL_FIELDS))
def test_one_sources_item_rebuilds_to_the_fields_the_library_stored(
    tmp_path: Path, content_type: str
) -> None:
    db = SQLiteDB(tmp_path / f"stored-{content_type}.db")
    db_id = db.save_content_item(_stating_every_writer_field(content_type))
    stored = db.get_content_item(db_id)
    assert stored is not None

    resolved = _rebuild(db, db_id)

    assert resolved["title"] == stored.title
    assert resolved["cover_url"] == stored.cover_url
    for detail_field in DETAIL_FIELDS[content_type].fields:
        key = detail_field.metadata_key
        if detail_field.owner is FieldOwner.USER:
            assert key not in resolved
        elif detail_field.kind is FieldKind.CREATOR:
            assert resolved[key] == stored.author
        else:
            assert resolved[key] == stored.metadata[key]
    assert db.get_content_item(db_id) == stored


def test_a_provider_outranks_the_source_on_the_page_count_and_the_description(
    tmp_path: Path,
) -> None:
    db = SQLiteDB(tmp_path / "provider.db")
    db_id = db.save_content_item(
        _book("goodreads_rss", pages=662, description="A feed blurb.")
    )
    _provider_states(
        db,
        db_id,
        "openlibrary",
        FieldWrite("pages", 722),
        FieldWrite("description", "Set on the desert planet Arrakis."),
    )

    resolved = _rebuild(db, db_id, WriterRanks(providers=("openlibrary",)))

    assert resolved["pages"] == 722
    assert resolved["description"] == "Set on the desert planet Arrakis."


def test_the_cover_comes_from_the_source_rather_than_a_provider_that_matched(
    tmp_path: Path,
) -> None:
    db, db_id = _steam_game_two_providers_matched(tmp_path, "covers")

    resolved = _rebuild(db, db_id, WriterRanks(providers=("rawg", "igdb")))

    assert resolved["cover_url"] == _STEAM_PORTRAIT


def test_pinning_a_provider_lifts_its_cover_above_the_sources(tmp_path: Path) -> None:
    db, db_id = _steam_game_two_providers_matched(tmp_path, "pinned")

    resolved = _rebuild(
        db,
        db_id,
        WriterRanks(providers=("rawg", "igdb"), pinned=frozenset({"igdb"})),
    )

    assert resolved["cover_url"] == _IGDB_BOX_ART


def test_a_cleared_cover_is_never_resolved_however_it_ranks(tmp_path: Path) -> None:
    db, db_id = _steam_game_two_providers_matched(tmp_path, "dead")
    assert db.clear_cover_url(db_id) is True

    resolved = _rebuild(db, db_id, WriterRanks(providers=("rawg", "igdb")))

    assert resolved["cover_url"] == _RAWG_SCREENSHOT


def test_a_buried_cover_is_not_resurrected_when_no_other_writer_offers_one(
    tmp_path: Path,
) -> None:
    db, db_id = _steam_game_two_providers_matched(tmp_path, "exhausted")
    assert db.clear_cover_url(db_id) is True

    assert "cover_url" not in _rebuild(db, db_id)


def test_genres_and_tags_combine_rather_than_the_top_ranked_writer_winning(
    tmp_path: Path,
) -> None:
    db = SQLiteDB(tmp_path / "lists.db")
    db_id = db.save_content_item(
        _book("goodreads_rss", genres=["Science Fiction"], tags=["owned"])
    )
    _provider_states(
        db,
        db_id,
        "openlibrary",
        FieldWrite("genres", ["Adventure"]),
        FieldWrite("tags", ["classic"]),
    )

    resolved = _rebuild(db, db_id, WriterRanks(providers=("openlibrary",)))

    assert resolved["genres"] == ["Adventure", "Science Fiction"]
    assert resolved["tags"] == ["classic", "owned"]


def test_a_disabled_provider_drops_out_and_returns_when_it_is_enabled_again(
    tmp_path: Path,
) -> None:
    db = SQLiteDB(tmp_path / "disabled.db")
    db_id = db.save_content_item(_book("goodreads_rss", pages=662))
    _provider_states(db, db_id, "openlibrary", FieldWrite("pages", 722))

    assert _rebuild(db, db_id)["pages"] == 662
    assert _rebuild(db, db_id, WriterRanks(providers=("openlibrary",)))["pages"] == 722


def test_the_series_ordinal_follows_authority_rather_than_writer_rank(
    tmp_path: Path,
) -> None:
    db = SQLiteDB(tmp_path / "authority.db")
    db_id = db.save_content_item(
        _book(
            "calibre_web",
            series_name="Dune",
            series_position=1.0,
            series_position_authority=SeriesAuthority.LIBRARY.value,
        )
    )
    _provider_states(
        db,
        db_id,
        "openlibrary",
        FieldWrite("series_position", 2.0, SeriesAuthority.STATED.value),
    )

    resolved = _rebuild(db, db_id, WriterRanks(providers=("openlibrary",)))

    assert resolved["series_position"] == 1.0
    assert resolved["series_position_authority"] == SeriesAuthority.LIBRARY.value


def test_an_ordinal_past_the_bounds_check_loses_to_one_inside_it(
    tmp_path: Path,
) -> None:
    db = SQLiteDB(tmp_path / "bounds.db")
    db_id = db.save_content_item(
        _book(
            "calibre_web",
            series_name="Dune",
            series_position=99999,
            series_position_authority=SeriesAuthority.LIBRARY.value,
        )
    )
    _provider_states(
        db,
        db_id,
        "openlibrary",
        FieldWrite("series_position", 3.0, SeriesAuthority.AUTHORED.value),
    )

    resolved = _rebuild(db, db_id, WriterRanks(providers=("openlibrary",)))

    assert resolved["series_position"] == 3.0
    assert resolved["series_position_authority"] == SeriesAuthority.AUTHORED.value


def test_seasons_and_episodes_take_the_higher_count_of_the_two_writers(
    tmp_path: Path,
) -> None:
    db = SQLiteDB(tmp_path / "counts.db")
    db_id = db.save_content_item(
        ContentItem(
            id="plex-1",
            source="plex",
            title="Severance",
            content_type=ContentType.TV_SHOW,
            status=ConsumptionStatus.UNREAD,
            metadata={"seasons": 2, "episodes": 20},
        )
    )
    _provider_states(
        db, db_id, "tmdb", FieldWrite("seasons", 3), FieldWrite("episodes", 18)
    )

    resolved = _rebuild(db, db_id, WriterRanks(providers=("tmdb",)))

    assert resolved["seasons"] == 3
    assert resolved["episodes"] == 20


def test_a_legacy_row_ranks_below_the_source_that_claimed_the_field(
    tmp_path: Path,
) -> None:
    db = SQLiteDB(tmp_path / "legacy.db")
    db_id = db.save_content_item(_book("goodreads_rss", description="A feed blurb."))
    _states(
        db,
        db_id,
        FieldWriter(WriterBand.LEGACY, ""),
        FieldWrite("description", "Whatever the column already held."),
    )

    resolved = _rebuild(db, db_id)

    assert resolved["description"] == "A feed blurb."
