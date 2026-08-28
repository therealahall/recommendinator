import csv
import io
import json
import sqlite3
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from src.models.content import ConsumptionStatus, ContentItem, ContentType
from src.storage import schema
from src.storage.sqlite_db import SQLiteDB
from src.utils.export import export_items_csv
from src.utils.item_serialization import item_to_dict
from src.utils.series import expand_tv_shows_to_seasons

_FLAGS_WINDOWS_AND_LINUX = json.dumps([{"windows": True, "mac": False, "linux": True}])
_FLAGS_NOTHING_SUPPORTED = json.dumps(
    [{"windows": False, "mac": False, "linux": False}]
)

_STAMPED_BY_AN_EARLIER_BUILD = 15


def _mark_written_before_the_repair(
    handle: sqlite3.Connection | sqlite3.Cursor,
) -> None:
    handle.execute("PRAGMA user_version = 2")


def _make_the_next_open_repair(db_path: Path) -> None:
    db = SQLiteDB(db_path)
    with db.connection() as conn:
        _mark_written_before_the_repair(conn)
        conn.commit()


def _seed_show(
    db: SQLiteDB,
    *,
    seasons: int | None,
    blob: dict[str, Any],
    item_id: str = "trakt:1388",
    title: str = "Breaking Bad",
) -> int:
    db_id = db.save_content_item(
        ContentItem(
            id=item_id,
            title=title,
            content_type=ContentType.TV_SHOW,
            status=ConsumptionStatus.CURRENTLY_CONSUMING,
            source="trakt",
        )
    )
    _write_show_metadata(db, db_id, seasons=seasons, metadata=json.dumps(blob))
    return db_id


def _write_show_metadata(
    db: SQLiteDB, db_id: int, *, seasons: int | None, metadata: str | None
) -> None:
    with db.connection() as conn:
        conn.execute(
            "UPDATE tv_show_details SET seasons = ?, metadata = ?"
            " WHERE content_item_id = ?",
            (seasons, metadata, db_id),
        )
        _mark_written_before_the_repair(conn)
        conn.commit()


def _seed_game(
    db: SQLiteDB,
    *,
    platforms: str | None,
    item_id: str = "1207658924",
    title: str = "The Witcher",
) -> int:
    db_id = db.save_content_item(
        ContentItem(
            id=item_id,
            title=title,
            content_type=ContentType.VIDEO_GAME,
            status=ConsumptionStatus.UNREAD,
            source="gog",
        )
    )
    with db.connection() as conn:
        conn.execute(
            "UPDATE video_game_details SET platforms = ? WHERE content_item_id = ?",
            (platforms, db_id),
        )
        _mark_written_before_the_repair(conn)
        conn.commit()
    return db_id


def _seed_stranded_companies(
    db: SQLiteDB,
    *,
    metadata: str,
    developer: str | None = None,
    publisher: str | None = None,
    item_id: str = "1207658924",
    title: str = "The Witcher",
) -> int:
    db_id = db.save_content_item(
        ContentItem(
            id=item_id,
            title=title,
            content_type=ContentType.VIDEO_GAME,
            status=ConsumptionStatus.UNREAD,
            source="gog",
        )
    )
    with db.connection() as conn:
        conn.execute(
            "UPDATE video_game_details SET developer = ?, publisher = ?, metadata = ?"
            " WHERE content_item_id = ?",
            (developer, publisher, metadata, db_id),
        )
        _mark_written_before_the_repair(conn)
        conn.commit()
    return db_id


def _game_companies(db: SQLiteDB, db_id: int) -> tuple[Any, Any, Any]:
    with db.connection() as conn:
        row = conn.execute(
            "SELECT developer, publisher, metadata FROM video_game_details"
            " WHERE content_item_id = ?",
            (db_id,),
        ).fetchone()
    blob = row["metadata"]
    return row["developer"], row["publisher"], json.loads(blob) if blob else None


def _insert_legacy_game_row(
    db_path: Path,
    *,
    title: str,
    metadata: str,
    developer: str | None = None,
    publisher: str | None = None,
    platforms: str | None = None,
) -> int:
    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO content_items"
            " (user_id, title, content_type, status, source)"
            " VALUES (1, ?, 'video_game', 'unread', 'gog')",
            (title,),
        )
        db_id = cursor.lastrowid
        assert db_id is not None
        cursor.execute(
            "INSERT INTO video_game_details"
            " (content_item_id, developer, publisher, platforms, metadata)"
            " VALUES (?, ?, ?, ?, ?)",
            (db_id, developer, publisher, platforms, metadata),
        )
        _mark_written_before_the_repair(conn)
        conn.commit()
    finally:
        conn.close()
    return db_id


def _game_companies_without_opening(db_path: Path, db_id: int) -> tuple[Any, Any, Any]:
    conn = sqlite3.connect(db_path)
    try:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT developer, publisher, metadata FROM video_game_details"
            " WHERE content_item_id = ?",
            (db_id,),
        ).fetchone()
    finally:
        conn.close()
    blob = row["metadata"]
    return row["developer"], row["publisher"], json.loads(blob) if blob else None


def _seed_movie(db: SQLiteDB, *, blob: dict[str, Any]) -> int:
    db_id = db.save_content_item(
        ContentItem(
            id="tmdb:603",
            title="The Matrix",
            content_type=ContentType.MOVIE,
            status=ConsumptionStatus.COMPLETED,
            source="trakt",
        )
    )
    with db.connection() as conn:
        conn.execute(
            "UPDATE movie_details SET metadata = ? WHERE content_item_id = ?",
            (json.dumps(blob), db_id),
        )
        _mark_written_before_the_repair(conn)
        conn.commit()
    return db_id


def _movie_detail(db: SQLiteDB, db_id: int) -> tuple[Any, Any, dict[str, Any] | None]:
    with db.connection() as conn:
        row = conn.execute(
            "SELECT release_year, runtime, metadata FROM movie_details"
            " WHERE content_item_id = ?",
            (db_id,),
        ).fetchone()
    blob = row["metadata"]
    return row["release_year"], row["runtime"], json.loads(blob) if blob else None


def _whole_detail_row(db: SQLiteDB, table: str, db_id: int) -> dict[str, Any]:
    with db.connection() as conn:
        row = conn.execute(
            f"SELECT * FROM {table} WHERE content_item_id = ?",
            (db_id,),
        ).fetchone()
    assert row is not None
    return dict(row)


def _show_detail(db: SQLiteDB, db_id: int) -> tuple[Any, dict[str, Any] | None]:
    with db.connection() as conn:
        row = conn.execute(
            "SELECT seasons, metadata FROM tv_show_details WHERE content_item_id = ?",
            (db_id,),
        ).fetchone()
    blob = row["metadata"]
    return row["seasons"], json.loads(blob) if blob else None


def _show_detail_without_opening(db_path: Path, db_id: int) -> tuple[Any, Any]:
    conn = sqlite3.connect(db_path)
    try:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT seasons, metadata FROM tv_show_details WHERE content_item_id = ?",
            (db_id,),
        ).fetchone()
    finally:
        conn.close()
    blob = row["metadata"]
    return row["seasons"], json.loads(blob) if blob else None


def _stored_platforms(db: SQLiteDB, db_id: int) -> Any:
    with db.connection() as conn:
        row = conn.execute(
            "SELECT platforms FROM video_game_details WHERE content_item_id = ?",
            (db_id,),
        ).fetchone()
    return row["platforms"]


def _open_counting_the_repair(db_path: Path) -> int:
    with patch.object(
        schema,
        "_migrate_stranded_detail_shapes",
        wraps=schema._migrate_stranded_detail_shapes,
    ) as repair:
        SQLiteDB(db_path)
    return int(repair.call_count)


class TestStrandedTotalSeasonsMigration:
    def test_stranded_count_moves_to_the_seasons_column(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        db_id = _seed_show(
            SQLiteDB(db_path),
            seasons=None,
            blob={"total_seasons": 5, "seasons_watched": [1, 2]},
        )

        db = SQLiteDB(db_path)

        seasons, blob = _show_detail(db, db_id)
        assert seasons == 5
        assert blob == {"seasons_watched": [1, 2]}
        item = db.get_content_item(db_id)
        assert item is not None
        assert item_to_dict(item)["total_seasons"] == 5

    def test_column_count_is_never_lowered(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        db_id = _seed_show(SQLiteDB(db_path), seasons=5, blob={"total_seasons": 3})

        db = SQLiteDB(db_path)

        assert _show_detail(db, db_id) == (5, None)

    def test_the_reader_follows_the_column_once_the_duplicate_is_gone(
        self, tmp_path: Path
    ) -> None:
        db_path = tmp_path / "test.db"
        db_id = _seed_show(
            SQLiteDB(db_path),
            seasons=5,
            blob={"total_seasons": 3, "seasons_watched": [1]},
        )

        db = SQLiteDB(db_path)

        item = db.get_content_item(db_id)
        assert item is not None
        assert "total_seasons" not in item.metadata
        assert [season.title for season in expand_tv_shows_to_seasons([item])] == [
            "Breaking Bad (Season 2)",
            "Breaking Bad (Season 3)",
            "Breaking Bad (Season 4)",
            "Breaking Bad (Season 5)",
        ]

    def test_a_later_sync_of_the_alias_does_not_restrand_the_count(
        self, tmp_path: Path
    ) -> None:
        db_path = tmp_path / "test.db"
        db_id = _seed_show(SQLiteDB(db_path), seasons=None, blob={"total_seasons": 5})

        db = SQLiteDB(db_path)
        resynced_id = db.save_content_item(
            ContentItem(
                id="trakt:1388",
                title="Breaking Bad",
                content_type=ContentType.TV_SHOW,
                status=ConsumptionStatus.CURRENTLY_CONSUMING,
                source="trakt",
                metadata={"total_seasons": 6},
            )
        )

        assert resynced_id == db_id
        assert _show_detail(db, db_id) == (6, None)

    def test_metadata_that_is_not_json_cannot_block_startup(
        self, tmp_path: Path
    ) -> None:
        db_path = tmp_path / "test.db"
        seed = SQLiteDB(db_path)
        broken_id = _seed_show(seed, seasons=2, blob={})
        _write_show_metadata(
            seed, broken_id, seasons=2, metadata="not json, but total_seasons is in it"
        )
        stranded_id = _seed_show(
            seed,
            seasons=None,
            blob={"total_seasons": 4},
            item_id="trakt:222",
            title="The Wire",
        )

        db = SQLiteDB(db_path)

        with db.connection() as conn:
            row = conn.execute(
                "SELECT metadata FROM tv_show_details WHERE content_item_id = ?",
                (broken_id,),
            ).fetchone()
        assert row["metadata"] == "not json, but total_seasons is in it"
        assert _show_detail(db, stranded_id) == (4, None)


class TestStrandedCompanyNamesMigration:
    def test_object_shaped_names_land_on_their_columns(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        db_id = _seed_stranded_companies(
            SQLiteDB(db_path),
            metadata=json.dumps(
                {
                    "developers": [{"name": "CD Projekt Red"}],
                    "publishers": [{"name": "CD Projekt"}],
                    "gog_product_id": "1207658924",
                }
            ),
        )

        db = SQLiteDB(db_path)

        assert _game_companies(db, db_id) == (
            "CD Projekt Red",
            "CD Projekt",
            {"gog_product_id": "1207658924"},
        )

    def test_a_stored_game_can_be_saved_again_regression(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        db_id = _seed_stranded_companies(
            SQLiteDB(db_path),
            metadata=json.dumps({"publishers": [{"name": "CD Projekt"}]}),
        )

        db = SQLiteDB(db_path)

        stored = db.get_content_item(db_id)
        assert stored is not None
        assert db.save_content_item(stored) == db_id
        assert _game_companies(db, db_id)[1] == "CD Projekt"

    def test_a_name_enrichment_already_wrote_is_never_replaced(
        self, tmp_path: Path
    ) -> None:
        db_path = tmp_path / "test.db"
        db_id = _seed_stranded_companies(
            SQLiteDB(db_path),
            developer="CD Projekt Red",
            publisher="CD Projekt",
            metadata=json.dumps(
                {"developers": [{"name": "Stale Studio"}], "publishers": ["Stale Co"]}
            ),
        )

        db = SQLiteDB(db_path)

        assert _game_companies(db, db_id) == ("CD Projekt Red", "CD Projekt", None)

    def test_a_row_a_later_build_stamped_over_is_folded_too(
        self, tmp_path: Path
    ) -> None:
        db_path = tmp_path / "test.db"
        SQLiteDB(db_path)
        db_id = _insert_legacy_game_row(
            db_path,
            title="Cyberpunk 2077",
            metadata=json.dumps({"developers": [{"name": "CD Projekt Red"}]}),
        )
        stamping = sqlite3.connect(db_path)
        try:
            stamping.execute(f"PRAGMA user_version = {_STAMPED_BY_AN_EARLIER_BUILD}")
            stamping.commit()
        finally:
            stamping.close()

        db = SQLiteDB(db_path)

        assert _game_companies(db, db_id) == ("CD Projekt Red", None, None)

    def test_metadata_that_is_not_json_cannot_block_startup(
        self, tmp_path: Path
    ) -> None:
        db_path = tmp_path / "test.db"
        seed = SQLiteDB(db_path)
        broken_id = _seed_stranded_companies(
            seed, metadata="not json, but developers is in it"
        )
        stranded_id = _seed_stranded_companies(
            seed,
            metadata=json.dumps({"publishers": ["CD Projekt"]}),
            item_id="1207658925",
            title="Stardew Valley",
        )

        db = SQLiteDB(db_path)

        with db.connection() as conn:
            row = conn.execute(
                "SELECT metadata FROM video_game_details WHERE content_item_id = ?",
                (broken_id,),
            ).fetchone()
        assert row["metadata"] == "not json, but developers is in it"
        assert _game_companies(db, stranded_id) == (None, "CD Projekt", None)


class TestALegacyGogRowSurvivesTheUpgrade:
    def test_a_row_written_before_the_aliases_reads_and_saves_regression(
        self, tmp_path: Path
    ) -> None:
        db_path = tmp_path / "legacy.db"
        SQLiteDB(db_path)
        legacy_blob = {
            "gog_product_id": "1207658924",
            "developers": [{"name": "CD Projekt Red"}],
            "publishers": [{"name": "CD Projekt"}],
        }
        db_id = _insert_legacy_game_row(
            db_path,
            title="The Witcher",
            metadata=json.dumps(legacy_blob),
        )
        assert _game_companies_without_opening(db_path, db_id) == (
            None,
            None,
            legacy_blob,
        )

        db = SQLiteDB(db_path)

        stored = db.get_content_item(db_id)
        assert stored is not None
        assert stored.author == "CD Projekt Red"
        assert db.save_content_item(stored) == db_id
        assert db.save_content_item(db.get_content_item(db_id)) == db_id
        assert _game_companies(db, db_id) == (
            "CD Projekt Red",
            "CD Projekt",
            {"gog_product_id": "1207658924"},
        )

    def test_a_row_stranded_in_both_gog_shapes_is_repaired_at_once(
        self, tmp_path: Path
    ) -> None:
        db_path = tmp_path / "legacy-both.db"
        SQLiteDB(db_path)
        db_id = _insert_legacy_game_row(
            db_path,
            title="The Witcher",
            platforms=_FLAGS_WINDOWS_AND_LINUX,
            metadata=json.dumps({"developers": ["CD Projekt Red"]}),
        )

        db = SQLiteDB(db_path)

        assert _game_companies(db, db_id) == ("CD Projekt Red", None, None)
        assert json.loads(_stored_platforms(db, db_id)) == ["Windows", "Linux"]


class TestStrandedPlatformFlagsMigration:
    def test_flag_dict_becomes_the_supported_platform_names(
        self, tmp_path: Path
    ) -> None:
        db_path = tmp_path / "test.db"
        db_id = _seed_game(SQLiteDB(db_path), platforms=_FLAGS_WINDOWS_AND_LINUX)

        db = SQLiteDB(db_path)

        assert json.loads(_stored_platforms(db, db_id)) == ["Windows", "Linux"]

    def test_migrated_names_match_what_the_corrected_plugin_writes(
        self, tmp_path: Path
    ) -> None:
        db_path = tmp_path / "test.db"
        db_id = _seed_game(
            SQLiteDB(db_path),
            platforms=json.dumps([{"windows": True, "mac": True, "linux": True}]),
        )

        db = SQLiteDB(db_path)

        assert json.loads(_stored_platforms(db, db_id)) == ["Windows", "Mac", "Linux"]

    def test_game_supported_on_no_platform_stores_nothing(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        db_id = _seed_game(SQLiteDB(db_path), platforms=_FLAGS_NOTHING_SUPPORTED)

        db = SQLiteDB(db_path)

        assert _stored_platforms(db, db_id) is None

    def test_export_writes_a_platform_name_rather_than_a_repr(
        self, tmp_path: Path
    ) -> None:
        db_path = tmp_path / "test.db"
        db_id = _seed_game(SQLiteDB(db_path), platforms=_FLAGS_WINDOWS_AND_LINUX)

        db = SQLiteDB(db_path)

        item = db.get_content_item(db_id)
        assert item is not None
        rows = list(
            csv.DictReader(
                io.StringIO(export_items_csv([item], ContentType.VIDEO_GAME))
            )
        )
        assert rows[0]["platform"] == "Windows"

    def test_an_imported_object_is_not_read_as_flags(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        db_id = _seed_game(SQLiteDB(db_path), platforms=json.dumps([{"name": "PC"}]))
        _make_the_next_open_repair(db_path)

        db = SQLiteDB(db_path)

        assert json.loads(_stored_platforms(db, db_id)) == ["PC"]

    def test_the_emptied_column_accepts_a_later_sync(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        db_id = _seed_game(SQLiteDB(db_path), platforms=_FLAGS_NOTHING_SUPPORTED)

        db = SQLiteDB(db_path)
        resynced_id = db.save_content_item(
            ContentItem(
                id="1207658924",
                title="The Witcher",
                content_type=ContentType.VIDEO_GAME,
                status=ConsumptionStatus.UNREAD,
                source="gog",
                metadata={"platforms": ["Windows"]},
            )
        )

        assert resynced_id == db_id
        assert json.loads(_stored_platforms(db, db_id)) == ["Windows"]


class TestOnlyTheDeclaredShapesAreRepaired:
    def test_a_movie_duplicating_its_columns_in_the_blob_is_left_alone(
        self, tmp_path: Path
    ) -> None:
        db_path = tmp_path / "test.db"
        blob = {"year": 1999, "runtime_minutes": 136}
        db_id = _seed_movie(SQLiteDB(db_path), blob=blob)

        db = SQLiteDB(db_path)

        assert _movie_detail(db, db_id) == (None, None, blob)


class TestASecondOpenRewritesNothing:
    def test_a_repaired_show_row_is_unchanged_by_the_next_open(
        self, tmp_path: Path
    ) -> None:
        db_path = tmp_path / "test.db"
        db_id = _seed_show(
            SQLiteDB(db_path),
            seasons=3,
            blob={"total_seasons": 5, "seasons_watched": [1, 2]},
        )
        repaired = _whole_detail_row(SQLiteDB(db_path), "tv_show_details", db_id)

        _make_the_next_open_repair(db_path)
        db = SQLiteDB(db_path)

        assert repaired["seasons"] == 5
        assert _whole_detail_row(db, "tv_show_details", db_id) == repaired

    def test_the_second_open_does_not_run_the_pass_at_all(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        _seed_show(SQLiteDB(db_path), seasons=3, blob={"total_seasons": 5})

        assert _open_counting_the_repair(db_path) == 1
        assert _open_counting_the_repair(db_path) == 0


class TestTheRepairSharesOneTransactionWithTheOpen:
    def test_a_failure_after_the_repair_leaves_it_unapplied(
        self, tmp_path: Path
    ) -> None:
        db_path = tmp_path / "test.db"
        db_id = _seed_show(SQLiteDB(db_path), seasons=None, blob={"total_seasons": 5})

        with (
            patch.object(
                schema, "backfill_derived_columns", side_effect=OSError("disk failure")
            ),
            pytest.raises(OSError),
        ):
            SQLiteDB(db_path)

        assert _show_detail_without_opening(db_path, db_id) == (
            None,
            {"total_seasons": 5},
        )


class TestEveryShapeInOnePass:
    def test_every_stranded_row_is_repaired_on_one_open(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        seed = SQLiteDB(db_path)
        first_show = _seed_show(seed, seasons=None, blob={"total_seasons": 5})
        second_show = _seed_show(
            seed,
            seasons=None,
            blob={"total_seasons": 5, "trakt_id": 222},
            item_id="trakt:222",
            title="The Wire",
        )
        game = _seed_game(seed, platforms=_FLAGS_WINDOWS_AND_LINUX)
        stranded_companies = _seed_stranded_companies(
            seed,
            metadata=json.dumps({"developers": [{"name": "ConcernedApe"}]}),
            item_id="1453375253",
            title="Stardew Valley",
        )

        db = SQLiteDB(db_path)

        assert _show_detail(db, first_show) == (5, None)
        assert _show_detail(db, second_show) == (5, {"trakt_id": 222})
        assert json.loads(_stored_platforms(db, game)) == ["Windows", "Linux"]
        assert _game_companies(db, stranded_companies) == ("ConcernedApe", None, None)


class TestAnObjectStrandedInAListColumnMigration:
    def test_a_stored_row_can_be_saved_again_regression(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        seed = SQLiteDB(db_path)
        db_id = _seed_game(seed, platforms=json.dumps([{"name": "PC"}]))
        with seed.connection() as conn:
            conn.execute(f"PRAGMA user_version = {_STAMPED_BY_AN_EARLIER_BUILD}")
            conn.commit()

        db = SQLiteDB(db_path)

        stored = db.get_content_item(db_id)
        assert stored is not None
        assert db.save_content_item(stored) == db_id
        assert json.loads(_stored_platforms(db, db_id)) == ["PC"]

    def test_an_item_an_earlier_reset_requeued_is_counted_once_regression(
        self, tmp_path: Path
    ) -> None:
        db_path = tmp_path / "test.db"
        seed = SQLiteDB(db_path)
        db_id = _seed_game(seed, platforms=json.dumps(["PC"]))
        with seed.connection() as conn:
            conn.execute(
                "INSERT INTO enrichment_status"
                " (content_item_id, enrichment_provider, enrichment_quality,"
                " needs_enrichment) VALUES (?, 'none', 'not_found', 1)",
                (db_id,),
            )
            conn.execute(f"PRAGMA user_version = {_STAMPED_BY_AN_EARLIER_BUILD}")
            conn.commit()

        db = SQLiteDB(db_path)

        with db.connection() as conn:
            stats = schema.get_enrichment_stats(conn)
        assert stats["not_found"] == 0
        assert stats["pending"] == 1
        assert (
            stats["enriched"] + stats["pending"] + stats["not_found"] + stats["failed"]
            == stats["total"]
        )
