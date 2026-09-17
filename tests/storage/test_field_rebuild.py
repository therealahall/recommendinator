from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from src.library.rebuild import (
    owe_library_rebuild,
    run_library_rebuild,
    start_library_rebuild,
)
from src.models.content import ConsumptionStatus, ContentItem, ContentType
from src.models.detail_fields import (
    CREATOR_FIELDS,
    DETAIL_FIELDS,
    FieldKind,
    FieldOwner,
)
from src.settings.metadata import PROVIDER_ORDER_KEY
from src.storage.field_rebuild import WriterRanks, rebuild_item_fields
from src.storage.field_writes import (
    MANUAL_WRITER,
    FieldWrite,
    FieldWriter,
    WriterBand,
    record_field_writes,
)
from src.storage.manager import StorageManager
from src.storage.rebuild_jobs import LibraryRebuildRecord
from src.storage.settings_store import SettingsStore
from src.storage.sqlite_db import SQLiteDB
from src.utils.series import SERIES_NAME_KEY, SeriesAuthority

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
        return rebuild_item_fields(conn.cursor(), db_id, ranks).values


def _states(db: SQLiteDB, db_id: int, writer: FieldWriter, *writes: FieldWrite) -> None:
    with db.connection() as conn:
        cursor = conn.cursor()
        record_field_writes(cursor, db_id, writer, writes)
        conn.commit()


def _provider_states(
    db: SQLiteDB, db_id: int, provider: str, *writes: FieldWrite
) -> None:
    _states(db, db_id, FieldWriter(WriterBand.PROVIDER, provider), *writes)


def _book(source: str, title: str = "Dune", **metadata: Any) -> ContentItem:
    return ContentItem(
        id=f"{source}-{title}",
        source=source,
        title=title,
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


def _steam_portal_2() -> ContentItem:
    return ContentItem(
        id="440",
        source="steam",
        title="Portal 2",
        content_type=ContentType.VIDEO_GAME,
        status=ConsumptionStatus.UNREAD,
        cover_url=_STEAM_PORTRAIT,
    )


def _steam_game_two_providers_matched(
    tmp_path: Path, name: str
) -> tuple[SQLiteDB, int]:
    db = SQLiteDB(tmp_path / f"{name}.db")
    db_id = db.save_content_item(_steam_portal_2())
    _provider_states(db, db_id, "rawg", FieldWrite("cover_url", _RAWG_SCREENSHOT))
    _provider_states(db, db_id, "igdb", FieldWrite("cover_url", _IGDB_BOX_ART))
    return db, db_id


@pytest.mark.parametrize("content_type", sorted(DETAIL_FIELDS))
def test_one_sources_item_rebuilds_to_the_fields_the_library_stored(
    tmp_path: Path, content_type: str
) -> None:
    db = SQLiteDB(tmp_path / f"stored-{content_type}.db")
    item = _stating_every_writer_field(content_type)
    db_id = db.save_content_item(item)
    stored = db.get_content_item(db_id)
    assert stored is not None

    resolved = _rebuild(db, db_id)

    assert resolved["title"] == stored.title
    assert resolved["cover_url"] == stored.cover_url
    for detail_field in DETAIL_FIELDS[content_type].fields:
        key = detail_field.metadata_key
        if detail_field.owner is FieldOwner.USER:
            assert key not in resolved
        elif key == SERIES_NAME_KEY:
            assert key not in resolved
            assert stored.metadata[key] == item.metadata[key]
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


def test_no_cover_is_left_to_offer_once_the_operator_has_cleared_every_one(
    tmp_path: Path,
) -> None:
    db, db_id = _steam_game_two_providers_matched(tmp_path, "exhausted")

    for _ in range(3):
        assert db.clear_cover_url(db_id) is True
        db.save_content_item(_steam_portal_2())

    stored = db.get_content_item(db_id)
    assert stored is not None
    assert stored.cover_url is None
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

    switched_off = WriterRanks(disabled=frozenset({"openlibrary"}))

    assert _rebuild(db, db_id, switched_off)["pages"] == 662
    assert _rebuild(db, db_id)["pages"] == 722


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
        FieldWrite("series_name", "Dune"),
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
        FieldWrite("series_name", "Dune"),
        FieldWrite("series_position", 3.0, SeriesAuthority.AUTHORED.value),
    )

    resolved = _rebuild(db, db_id, WriterRanks(providers=("openlibrary",)))

    assert resolved["series_position"] == 3.0
    assert resolved["series_position_authority"] == SeriesAuthority.AUTHORED.value


def test_an_ordinal_from_a_writer_naming_no_series_cannot_renumber_the_item(
    tmp_path: Path,
) -> None:
    db = SQLiteDB(tmp_path / "unnamed_ordinal.db")
    db_id = db.save_content_item(
        _book(
            "calibre_web",
            series_name="The Expanse",
            series_position=2.5,
            series_position_authority=SeriesAuthority.STATED.value,
        )
    )
    _provider_states(
        db,
        db_id,
        "openlibrary",
        FieldWrite("series_position", 1.0, SeriesAuthority.AUTHORED.value),
    )

    resolved = _rebuild(db, db_id, WriterRanks(providers=("openlibrary",)))

    assert resolved["series_position"] == 2.5
    assert resolved["series_position_authority"] == SeriesAuthority.STATED.value


@pytest.mark.parametrize("content_type", sorted(DETAIL_FIELDS))
def test_a_hand_corrected_creator_and_a_stated_one_are_the_same_field(
    tmp_path: Path, content_type: str
) -> None:
    db = SQLiteDB(tmp_path / f"creator-{content_type}.db")
    db_id = db.save_content_item(_stating_every_writer_field(content_type))

    assert db.update_item_from_ui(db_id=db_id, creator="Dan Simmons") is True

    resolved = _rebuild(db, db_id)
    stored = db.get_content_item(db_id)
    assert resolved[CREATOR_FIELDS[content_type].metadata_key] == "Dan Simmons"
    assert "creator" not in resolved
    assert stored is not None
    assert stored.manual_fields == ["creator"]


def test_a_hand_set_ordinal_outranks_the_library_stating_its_own(
    tmp_path: Path,
) -> None:
    db = SQLiteDB(tmp_path / "manual_ordinal.db")
    db_id = db.save_content_item(
        _book(
            "calibre_web",
            series_name="Dune",
            series_position=1.0,
            series_position_authority=SeriesAuthority.LIBRARY.value,
        )
    )
    _states(db, db_id, MANUAL_WRITER, FieldWrite("series_position", 2.0))

    resolved = _rebuild(db, db_id)

    assert resolved["series_position"] == 2.0
    assert resolved["series_position_authority"] == SeriesAuthority.MANUAL.value


def test_a_hand_set_ordinal_past_the_bounds_check_settles_nothing(
    tmp_path: Path,
) -> None:
    db = SQLiteDB(tmp_path / "manual_bounds.db")
    db_id = db.save_content_item(
        _book(
            "calibre_web",
            series_name="Dune",
            series_position=3.0,
            series_position_authority=SeriesAuthority.LIBRARY.value,
        )
    )
    _states(db, db_id, MANUAL_WRITER, FieldWrite("series_position", 99999))

    resolved = _rebuild(db, db_id)

    assert resolved["series_position"] == 3.0
    assert resolved["series_position_authority"] == SeriesAuthority.LIBRARY.value


def test_a_held_title_outranks_the_source_that_keeps_restating_its_own(
    tmp_path: Path,
) -> None:
    db = SQLiteDB(tmp_path / "held_title.db")
    db_id = db.save_content_item(_book("calibre_web"))

    assert db.update_item_from_ui(db_id=db_id, title="Dune: Book One") is True
    db.save_content_item(_book("calibre_web"))

    assert _rebuild(db, db_id)["title"] == "Dune: Book One"


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


def _enriches(
    db: SQLiteDB, db_id: int, provider: str, *, title: str = "Dune", **stated: Any
) -> None:
    """The enrichment door as a run drives it: what the provider stated, filed
    under its own name.
    """
    db.save_enrichment_metadata(
        db_id,
        _book(provider, title, **stated),
        FieldWriter(WriterBand.PROVIDER, provider),
        stated,
    )


def test_the_library_serves_the_provider_over_the_feed_that_first_filled_it(
    tmp_path: Path,
) -> None:
    db = SQLiteDB(tmp_path / "served.db")
    db_id = db.save_content_item(
        _book("goodreads_rss", pages=662, description="A feed blurb.")
    )

    _enriches(
        db,
        db_id,
        "openlibrary",
        pages=722,
        description="Set on the desert planet Arrakis.",
    )

    stored = db.get_content_item(db_id)
    assert stored is not None
    assert stored.metadata["pages"] == 722
    assert stored.metadata["description"] == "Set on the desert planet Arrakis."


def test_the_steam_cover_survives_the_providers_that_matched_the_game(
    tmp_path: Path,
) -> None:
    db, db_id = _steam_game_two_providers_matched(tmp_path, "kept_cover")

    db.save_content_item(_steam_portal_2())

    stored = db.get_content_item(db_id)
    assert stored is not None
    assert stored.cover_url == _STEAM_PORTRAIT


def test_a_description_the_operator_typed_is_untouched_by_a_later_run(
    tmp_path: Path,
) -> None:
    db = SQLiteDB(tmp_path / "held_description.db")
    db_id = db.save_content_item(_book("goodreads_rss", description="A feed blurb."))
    assert db.update_item_from_ui(db_id=db_id, description="As I read it.") is True

    _enriches(db, db_id, "openlibrary", description="Set on Arrakis.")

    stored = db.get_content_item(db_id)
    assert stored is not None
    assert stored.metadata["description"] == "As I read it."


def test_a_provider_switched_off_in_the_settings_stops_being_served(
    tmp_path: Path,
) -> None:
    db = SQLiteDB(tmp_path / "switched_off.db")
    db_id = db.save_content_item(_book("goodreads_rss", pages=662))
    _enriches(db, db_id, "openlibrary", pages=722)

    SettingsStore(db).set("enrichment.providers.openlibrary.enabled", False)
    db.save_content_item(_book("goodreads_rss", pages=662))

    stored = db.get_content_item(db_id)
    assert stored is not None
    assert stored.metadata["pages"] == 662


def test_a_provider_matching_with_an_empty_result_states_nothing(
    tmp_path: Path,
) -> None:
    db = SQLiteDB(tmp_path / "empty_match.db")
    db_id = db.save_content_item(
        _book("goodreads_rss", pages=662, description="A feed blurb.")
    )
    before = db.get_content_item(db_id)

    _enriches(db, db_id, "openlibrary")

    assert db.get_content_item(db_id) == before


def test_a_source_correcting_itself_lands_over_another_sources_older_word(
    tmp_path: Path,
) -> None:
    db = SQLiteDB(tmp_path / "corrected_among_sources.db")
    db_id = db.save_content_item(_book("calibre_web", description="A desert planet."))
    db.save_content_item(_book("storygraph_csv", description="A shelf blurb."))

    db.save_content_item(_book("calibre_web", description="Arrakis, desert planet."))

    stored = db.get_content_item(db_id)
    assert stored is not None
    assert stored.metadata["description"] == "Arrakis, desert planet."


def test_a_genre_the_operator_typed_is_served_alone_rather_than_combined(
    tmp_path: Path,
) -> None:
    db = SQLiteDB(tmp_path / "manual_genres.db")
    db_id = db.save_content_item(_book("goodreads_rss", genres=["Science Fiction"]))
    assert db.update_item_from_ui(db_id=db_id, genres=["Literary Fiction"]) is True

    _enriches(db, db_id, "openlibrary", genres=["Adventure"])

    stored = db.get_content_item(db_id)
    assert stored is not None
    assert stored.metadata["genres"] == ["Literary Fiction"]


def test_pinning_a_provider_moves_the_cover_and_unpinning_moves_it_back(
    tmp_path: Path,
) -> None:
    db, db_id = _steam_game_two_providers_matched(tmp_path, "pin_door")

    db.set_enrichment_pins(db_id, {"rawg": "620"})
    pinned = db.get_content_item(db_id)

    db.set_enrichment_pins(db_id, {})
    cleared = db.get_content_item(db_id)

    assert pinned is not None and pinned.cover_url == _RAWG_SCREENSHOT
    assert cleared is not None and cleared.cover_url == _STEAM_PORTRAIT


def test_reordering_providers_restores_the_higher_ranked_value_without_asking_one(
    tmp_path: Path,
) -> None:
    db = SQLiteDB(tmp_path / "reorder.db")
    db_id = db.save_content_item(_book("goodreads_rss"))
    _enriches(db, db_id, "openlibrary", description="A")
    _enriches(db, db_id, "hardcover", description="B")
    settings = SettingsStore(db)

    settings.set(PROVIDER_ORDER_KEY, ["openlibrary", "hardcover"])
    db.rebuild_item(db_id)
    first = db.get_content_item(db_id)

    settings.set(PROVIDER_ORDER_KEY, ["hardcover", "openlibrary"])
    moved = db.rebuild_item(db_id)

    stored = db.get_content_item(db_id)
    assert first is not None and first.metadata["description"] == "A"
    assert moved is True
    assert stored is not None and stored.metadata["description"] == "B"


def test_switching_a_provider_off_empties_the_field_only_it_stated_until_it_is_back(
    tmp_path: Path,
) -> None:
    db = SQLiteDB(tmp_path / "switched_off.db")
    db_id = db.save_content_item(_book("goodreads_rss"))
    _enriches(db, db_id, "openlibrary", description="Only OpenLibrary said this")
    settings = SettingsStore(db)

    settings.set("enrichment.providers.openlibrary.enabled", False)
    db.rebuild_item(db_id)
    emptied = db.get_content_item(db_id)

    settings.set("enrichment.providers.openlibrary.enabled", True)
    db.rebuild_item(db_id)
    restored = db.get_content_item(db_id)

    assert emptied is not None and emptied.metadata.get("description") is None
    assert restored is not None
    assert restored.metadata["description"] == "Only OpenLibrary said this"


def _stored_description(storage: StorageManager, db_id: int) -> Any:
    item = storage.get_content_item(db_id)
    assert item is not None
    return item.metadata.get("description")


def _finished_rebuild(storage: StorageManager) -> LibraryRebuildRecord:
    for _ in range(500):
        record = storage.rebuild_jobs.read()
        if not record.running and not record.rerun_requested:
            return record
        time.sleep(0.01)
    raise AssertionError("the rebuild never finished")


def test_a_reorder_moves_the_stored_value_on_every_item_the_pass_walks(
    tmp_path: Path,
) -> None:
    storage = StorageManager(sqlite_path=tmp_path / "pass.db")
    storage.settings.set(PROVIDER_ORDER_KEY, ["openlibrary", "hardcover"])
    ids: list[int] = []
    for title in ("Dune", "Emma"):
        db_id = storage.save_content_item(_book("goodreads_rss", title))
        _enriches(storage.sqlite_db, db_id, "openlibrary", title=title, description="A")
        _enriches(storage.sqlite_db, db_id, "hardcover", title=title, description="B")
        ids.append(db_id)
    assert [_stored_description(storage, db_id) for db_id in ids] == ["A", "A"]

    storage.settings.set(PROVIDER_ORDER_KEY, ["hardcover", "openlibrary"])
    assert start_library_rebuild(storage) is not None
    record = _finished_rebuild(storage)

    assert (record.total, record.processed, record.changed) == (2, 2, 2)
    assert (record.completed, record.errors) == (True, [])
    assert [_stored_description(storage, db_id) for db_id in ids] == ["B", "B"]


def test_a_stop_ends_the_pass_after_the_item_in_flight_and_frees_the_claim(
    tmp_path: Path,
) -> None:
    storage = StorageManager(sqlite_path=tmp_path / "stopped.db")
    for title in ("Dune", "Emma"):
        storage.save_content_item(_book("goodreads_rss", title))
    rebuild_one = storage.rebuild_item

    def stop_once_one_is_in_flight(db_id: int) -> bool:
        storage.rebuild_jobs.request_stop()
        return rebuild_one(db_id)

    with patch.object(storage, "rebuild_item", side_effect=stop_once_one_is_in_flight):
        assert start_library_rebuild(storage) is not None
        record = _finished_rebuild(storage)

    assert (record.processed, record.total) == (1, 2)
    assert (record.completed, record.cancelled) == (False, True)
    assert storage.rebuild_jobs.claim() is True


def test_a_stop_leaves_an_owed_rebuild_for_the_next_start_rather_than_restarting(
    tmp_path: Path,
) -> None:
    storage = StorageManager(sqlite_path=tmp_path / "stopped_owing.db")
    ids = [
        storage.save_content_item(_book("goodreads_rss", title))
        for title in ("Dune", "Emma")
    ]
    rebuild_one = storage.rebuild_item
    walked: list[int] = []

    def owe_once_then_stop(db_id: int) -> bool:
        walked.append(db_id)
        if len(walked) == 1:
            owe_library_rebuild(storage)
        storage.rebuild_jobs.request_stop()
        return rebuild_one(db_id)

    assert storage.rebuild_jobs.claim() is True
    with patch.object(storage, "rebuild_item", side_effect=owe_once_then_stop):
        record = run_library_rebuild(storage)

    assert record.cancelled is True
    assert walked == ids[:1]
    assert storage.rebuild_jobs.read().rerun_requested is True


def test_a_pass_that_raised_reports_the_failure_and_leaves_the_change_owed(
    tmp_path: Path,
) -> None:
    storage = StorageManager(sqlite_path=tmp_path / "raised.db")
    owe_library_rebuild(storage)
    assert storage.rebuild_jobs.claim() is True

    with patch.object(
        storage, "rebuildable_items", side_effect=sqlite3.OperationalError("locked")
    ):
        record = run_library_rebuild(storage)

    assert (record.completed, record.cancelled) == (False, False)
    assert record.errors == ["the rebuild stopped on an error"]
    assert storage.rebuild_jobs.rerun_requested() is True
    assert storage.rebuild_jobs.claim() is True


def test_a_precedence_change_during_a_pass_is_walked_before_the_pass_ends(
    tmp_path: Path,
) -> None:
    storage = StorageManager(sqlite_path=tmp_path / "mid_pass.db")
    storage.settings.set(PROVIDER_ORDER_KEY, ["openlibrary", "hardcover"])
    ids: list[int] = []
    for title in ("Dune", "Emma"):
        db_id = storage.save_content_item(_book("goodreads_rss", title))
        _enriches(storage.sqlite_db, db_id, "openlibrary", title=title, description="A")
        _enriches(storage.sqlite_db, db_id, "hardcover", title=title, description="B")
        ids.append(db_id)
    rebuild_one = storage.rebuild_item
    reordered = False

    def reorder_once_one_is_behind_us(db_id: int) -> bool:
        nonlocal reordered
        changed = rebuild_one(db_id)
        if not reordered:
            reordered = True
            storage.settings.set(PROVIDER_ORDER_KEY, ["hardcover", "openlibrary"])
            owe_library_rebuild(storage)
        return changed

    with patch.object(
        storage, "rebuild_item", side_effect=reorder_once_one_is_behind_us
    ):
        assert start_library_rebuild(storage) is not None
        record = _finished_rebuild(storage)

    assert record.completed is True
    assert [_stored_description(storage, db_id) for db_id in ids] == ["B", "B"]


def test_a_precedence_change_landing_as_the_pass_ends_is_still_walked(
    tmp_path: Path,
) -> None:
    storage = StorageManager(sqlite_path=tmp_path / "at_the_end.db")
    storage.settings.set(PROVIDER_ORDER_KEY, ["openlibrary", "hardcover"])
    ids: list[int] = []
    for title in ("Dune", "Emma"):
        db_id = storage.save_content_item(_book("goodreads_rss", title))
        _enriches(storage.sqlite_db, db_id, "openlibrary", title=title, description="A")
        _enriches(storage.sqlite_db, db_id, "hardcover", title=title, description="B")
        ids.append(db_id)
    take_rerun = storage.rebuild_jobs.take_rerun
    reordered = False

    def reorder_once_the_walk_is_past_its_check() -> bool:
        nonlocal reordered
        owed = take_rerun()
        if not owed and not reordered:
            reordered = True
            storage.settings.set(PROVIDER_ORDER_KEY, ["hardcover", "openlibrary"])
            owe_library_rebuild(storage)
            assert start_library_rebuild(storage) is None
        return owed

    with patch.object(
        storage.rebuild_jobs,
        "take_rerun",
        side_effect=reorder_once_the_walk_is_past_its_check,
    ):
        assert start_library_rebuild(storage) is not None
        record = _finished_rebuild(storage)

    assert record.completed is True
    assert [_stored_description(storage, db_id) for db_id in ids] == ["B", "B"]


def test_an_item_that_raises_is_named_in_the_errors_and_the_walk_goes_on(
    tmp_path: Path,
) -> None:
    storage = StorageManager(sqlite_path=tmp_path / "raising_item.db")
    ids = [
        storage.save_content_item(_book("goodreads_rss", title))
        for title in ("Dune", "Emma")
    ]
    rebuild_one = storage.rebuild_item

    def raise_on_the_first(db_id: int) -> bool:
        if db_id == ids[0]:
            raise sqlite3.OperationalError("no such column")
        return rebuild_one(db_id)

    with patch.object(storage, "rebuild_item", side_effect=raise_on_the_first):
        assert start_library_rebuild(storage) is not None
        record = _finished_rebuild(storage)

    assert (record.processed, record.total) == (2, 2)
    assert record.completed is True
    assert record.errors == ["Dune: OperationalError"]


def test_the_shipped_precedence_beats_the_provider_that_enriched_first(
    tmp_path: Path,
) -> None:
    db = SQLiteDB(tmp_path / "shipped_precedence.db")
    db_id = db.save_content_item(_book("goodreads_rss", pages=662))

    _enriches(db, db_id, "openlibrary", pages=722)
    _enriches(db, db_id, "hardcover", pages=700)

    stored = db.get_content_item(db_id)
    assert stored is not None
    assert stored.metadata["pages"] == 700


def test_the_precedence_the_operator_set_decides_between_two_matches(
    tmp_path: Path,
) -> None:
    db = SQLiteDB(tmp_path / "precedence.db")
    db_id = db.save_content_item(_book("goodreads_rss", pages=662))
    _enriches(db, db_id, "openlibrary", pages=722)
    _enriches(db, db_id, "hardcover", pages=700)

    SettingsStore(db).set(PROVIDER_ORDER_KEY, ["hardcover", "openlibrary"])
    db.save_content_item(_book("goodreads_rss", pages=662))

    stored = db.get_content_item(db_id)
    assert stored is not None
    assert stored.metadata["pages"] == 700


def test_a_provider_corrects_the_creator_the_completion_door_typed(
    tmp_path: Path,
) -> None:
    db = SQLiteDB(tmp_path / "completion_door.db")
    db_id = db.complete_content_item(
        ContentItem(
            title="Dune",
            author="frank herbert",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
        )
    )

    _enriches(db, db_id, "openlibrary", author="Frank Herbert")

    stored = db.get_content_item(db_id)
    assert stored is not None
    assert stored.author == "Frank Herbert"


def test_a_source_corrects_the_creator_the_completion_door_typed(
    tmp_path: Path,
) -> None:
    db = SQLiteDB(tmp_path / "completion_then_sync.db")
    db_id = db.complete_content_item(
        ContentItem(
            title="Dune",
            author="frank herbert",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
        )
    )

    synced = db.save_content_item(_book("goodreads_rss"))

    stored = db.get_content_item(db_id)
    assert synced == db_id
    assert stored is not None
    assert stored.author == "Frank Herbert"


def _matrix(**metadata: Any) -> ContentItem:
    return ContentItem(
        id="603",
        source="radarr",
        title="The Matrix",
        content_type=ContentType.MOVIE,
        status=ConsumptionStatus.UNREAD,
        metadata=metadata,
    )


def test_a_films_ordinal_follows_the_same_ladder_a_books_does(
    tmp_path: Path,
) -> None:
    db = SQLiteDB(tmp_path / "film_series.db")
    db_id = db.save_content_item(
        _matrix(
            series_name="The Matrix",
            series_position=9.0,
            series_position_authority=SeriesAuthority.STATED.value,
        )
    )
    _provider_states(
        db,
        db_id,
        "tmdb",
        FieldWrite("series_name", "The Matrix Collection"),
        FieldWrite("series_position", 1.0, SeriesAuthority.AUTHORED.value),
    )

    db.save_content_item(_matrix(series_name="The Matrix"))

    stored = db.get_content_item(db_id)
    assert stored is not None
    assert stored.metadata["series_position"] == 1.0
    assert (
        stored.metadata["series_position_authority"] == SeriesAuthority.AUTHORED.value
    )
