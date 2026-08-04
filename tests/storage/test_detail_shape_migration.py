"""Tests for the one-time migration of stranded detail-row shapes.

Two shapes were written by code that has since been corrected, and neither
self-repairs on a re-sync, so ``create_schema`` rewrites them once on init:

- A show synced before ``total_seasons`` became an alias for the ``seasons``
  column kept its count in the free-form metadata blob. The blob merge lets
  existing keys win, so no later sync removes the duplicate, and
  ``src/utils/series.py`` prefers the blob copy — once a sync raises the
  column, the recommender keeps reading the stale lower number, which shows up
  as a completed show reappearing as in-progress.
- GOG wrote ``platforms`` as a dict of per-platform booleans where every other
  producer writes a list of names. ``platforms`` is neither mergeable nor
  monotonic, so ``_save_detail_table`` is fill-only for it and a column already
  holding the dict keeps it permanently: an export writes a Python repr into
  the platform cell, and re-importing that file stores the repr as a literal
  string.

Every test seeds the pre-fix shape by writing the detail row directly, because
the corrected write path can no longer produce it.
"""

import csv
import io
import json
import sqlite3
from pathlib import Path
from typing import Any

from src.models.content import ConsumptionStatus, ContentItem, ContentType
from src.storage.sqlite_db import SQLiteDB
from src.utils.item_serialization import item_to_dict
from src.utils.series import expand_tv_shows_to_seasons
from src.web.export import export_items_csv

# The pre-fix GOG value: a flag per platform, wrapped in a single-element list
# on the way into the column by ``SQLiteDB._to_json_array``.
_FLAGS_WINDOWS_AND_LINUX = json.dumps([{"windows": True, "mac": False, "linux": True}])
_FLAGS_NOTHING_SUPPORTED = json.dumps(
    [{"windows": False, "mac": False, "linux": False}]
)


def _seed_show(
    db: SQLiteDB,
    *,
    seasons: int | None,
    blob: dict[str, Any],
    item_id: str = "trakt:1388",
    title: str = "Breaking Bad",
) -> int:
    """Save a TV show, then write its detail row into the given shape.

    Takes an open database rather than a path so several rows can be
    stranded before the re-open that runs the migration.
    """
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
    """Write the raw seasons column and metadata blob for a show."""
    with db.connection() as conn:
        conn.execute(
            "UPDATE tv_show_details SET seasons = ?, metadata = ?"
            " WHERE content_item_id = ?",
            (seasons, metadata, db_id),
        )
        conn.commit()


def _insert_show_row(
    cursor: sqlite3.Cursor,
    *,
    external_id: str,
    title: str,
    seasons: int | None,
    metadata: str | None,
) -> int:
    """Insert a show and its detail row directly, bypassing runtime dedup.

    ``save_content_item`` merges a title-matching row on the way in, so two
    rows the migration's dedup pass will merge can only be created in SQL.
    """
    cursor.execute(
        "INSERT INTO content_items"
        " (user_id, external_id, title, content_type, status, source)"
        " VALUES (1, ?, ?, 'tv_show', 'currently_consuming', 'trakt')",
        (external_id, title),
    )
    db_id = cursor.lastrowid
    assert db_id is not None
    cursor.execute(
        "INSERT INTO tv_show_details (content_item_id, seasons, metadata)"
        " VALUES (?, ?, ?)",
        (db_id, seasons, metadata),
    )
    return db_id


def _seed_game(
    db: SQLiteDB,
    *,
    platforms: str | None,
    item_id: str = "1207658924",
    title: str = "The Witcher",
) -> int:
    """Save a GOG game, then write the raw value into its platforms column."""
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
        conn.commit()
    return db_id


def _show_detail(db: SQLiteDB, db_id: int) -> tuple[Any, dict[str, Any] | None]:
    """Return the stored (seasons column, metadata blob) for a show."""
    with db.connection() as conn:
        row = conn.execute(
            "SELECT seasons, metadata FROM tv_show_details WHERE content_item_id = ?",
            (db_id,),
        ).fetchone()
    blob = row["metadata"]
    return row["seasons"], json.loads(blob) if blob else None


def _stored_platforms(db: SQLiteDB, db_id: int) -> Any:
    """Return the raw platforms column for a game."""
    with db.connection() as conn:
        row = conn.execute(
            "SELECT platforms FROM video_game_details WHERE content_item_id = ?",
            (db_id,),
        ).fetchone()
    return row["platforms"]


class TestStrandedTotalSeasonsMigration:
    """The season count ends up on the column, and only on the column."""

    def test_stranded_count_moves_to_the_seasons_column(self, tmp_path: Path) -> None:
        """A pre-alias row loses the blob key and gains the column value."""
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
        """A column raised by a later sync wins over the stale blob copy.

        This is the drift the duplicate causes: the column is monotonic, so a
        migration that folded the blob copy back in would undo a real season
        the show has gained.
        """
        db_path = tmp_path / "test.db"
        db_id = _seed_show(SQLiteDB(db_path), seasons=5, blob={"total_seasons": 3})

        db = SQLiteDB(db_path)

        assert _show_detail(db, db_id) == (5, None)

    def test_second_init_leaves_the_migrated_row_alone(self, tmp_path: Path) -> None:
        """Running the migration twice changes nothing the first pass did."""
        db_path = tmp_path / "test.db"
        db_id = _seed_show(SQLiteDB(db_path), seasons=None, blob={"total_seasons": 5})
        SQLiteDB(db_path)

        db = SQLiteDB(db_path)

        assert _show_detail(db, db_id) == (5, None)

    def test_row_in_the_current_shape_is_untouched(self, tmp_path: Path) -> None:
        """A blob that never carried the duplicate is left exactly as it is."""
        db_path = tmp_path / "test.db"
        db_id = _seed_show(
            SQLiteDB(db_path),
            seasons=5,
            blob={"seasons_watched": [1, 2], "network": "AMC"},
        )

        db = SQLiteDB(db_path)

        assert _show_detail(db, db_id) == (
            5,
            {"seasons_watched": [1, 2], "network": "AMC"},
        )

    def test_a_duplicate_matching_the_column_only_drops_the_key(
        self, tmp_path: Path
    ) -> None:
        """The state most stranded rows are actually in: blob equals column."""
        db_path = tmp_path / "test.db"
        db_id = _seed_show(
            SQLiteDB(db_path),
            seasons=5,
            blob={"total_seasons": 5, "network": "AMC"},
        )

        db = SQLiteDB(db_path)

        assert _show_detail(db, db_id) == (5, {"network": "AMC"})

    def test_the_reader_follows_the_column_once_the_duplicate_is_gone(
        self, tmp_path: Path
    ) -> None:
        """The drift itself: season expansion stops at the stale blob count.

        ``expand_tv_shows_to_seasons`` prefers ``total_seasons`` over
        ``seasons``, so a column raised by a later sync was invisible to it
        while the blob copy survived — the completed show reappearing as
        in-progress.
        """
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

    def test_a_count_stored_as_a_string_lands_as_a_number(self, tmp_path: Path) -> None:
        """A CSV import stranded the count as text, and the column is an INTEGER."""
        db_path = tmp_path / "test.db"
        db_id = _seed_show(SQLiteDB(db_path), seasons=None, blob={"total_seasons": "5"})

        db = SQLiteDB(db_path)

        assert _show_detail(db, db_id) == (5, None)

    def test_a_count_that_is_not_a_number_never_reaches_the_column(
        self, tmp_path: Path
    ) -> None:
        """An unreadable count is dropped rather than written to the column.

        ``seasons`` is read with ``int()`` everywhere, so moving text onto it
        would break every reader instead of one.
        """
        db_path = tmp_path / "test.db"
        db_id = _seed_show(
            SQLiteDB(db_path), seasons=3, blob={"total_seasons": "unknown"}
        )

        db = SQLiteDB(db_path)

        assert _show_detail(db, db_id) == (3, None)

    def test_a_nested_total_seasons_is_left_alone(self, tmp_path: Path) -> None:
        """Only a top-level duplicate is a duplicate of the column."""
        db_path = tmp_path / "test.db"
        blob = {"trakt_raw": {"total_seasons": 9}}
        db_id = _seed_show(SQLiteDB(db_path), seasons=5, blob=blob)

        db = SQLiteDB(db_path)

        assert _show_detail(db, db_id) == (5, blob)

    def test_a_blob_that_is_not_an_object_is_left_alone(self, tmp_path: Path) -> None:
        """A blob that parses to something other than a dict has no key to move."""
        db_path = tmp_path / "test.db"
        seed = SQLiteDB(db_path)
        db_id = _seed_show(seed, seasons=2, blob={})
        _write_show_metadata(
            seed, db_id, seasons=2, metadata=json.dumps(["total_seasons", 5])
        )

        db = SQLiteDB(db_path)

        with db.connection() as conn:
            row = conn.execute(
                "SELECT seasons, metadata FROM tv_show_details"
                " WHERE content_item_id = ?",
                (db_id,),
            ).fetchone()
        assert row["seasons"] == 2
        assert json.loads(row["metadata"]) == ["total_seasons", 5]

    def test_a_later_sync_of_the_alias_does_not_restrand_the_count(
        self, tmp_path: Path
    ) -> None:
        """The repaired row survives the sync that produced the old shape.

        Trakt still writes ``total_seasons``, so a repair that a re-sync undid
        would be worth nothing: the alias has to land on the column and stay
        out of the blob.
        """
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
        """A blob the migration cannot read is skipped, not raised on.

        ``create_schema`` runs on every open, so a row it chokes on would
        make the database unopenable rather than merely unrepaired.
        """
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


class TestStrandedPlatformFlagsMigration:
    """The flag dict becomes the list of names every other producer writes."""

    def test_flag_dict_becomes_the_supported_platform_names(
        self, tmp_path: Path
    ) -> None:
        """Only the platforms flagged as supported survive, as GOG names them."""
        db_path = tmp_path / "test.db"
        db_id = _seed_game(SQLiteDB(db_path), platforms=_FLAGS_WINDOWS_AND_LINUX)

        db = SQLiteDB(db_path)

        assert json.loads(_stored_platforms(db, db_id)) == ["Windows", "Linux"]

    def test_migrated_names_match_what_the_corrected_plugin_writes(
        self, tmp_path: Path
    ) -> None:
        """A repaired row is indistinguishable from a fresh GOG sync.

        The old dict lowercased GOG's keys, so the migration has to restore
        the capitalisation ``worksOn`` uses or a repaired row and a re-synced
        one disagree about the same platform.
        """
        db_path = tmp_path / "test.db"
        db_id = _seed_game(
            SQLiteDB(db_path),
            platforms=json.dumps([{"windows": True, "mac": True, "linux": True}]),
        )

        db = SQLiteDB(db_path)

        assert json.loads(_stored_platforms(db, db_id)) == ["Windows", "Mac", "Linux"]

    def test_game_supported_on_no_platform_stores_nothing(self, tmp_path: Path) -> None:
        """The old dict was truthy even when it named nothing supported."""
        db_path = tmp_path / "test.db"
        db_id = _seed_game(SQLiteDB(db_path), platforms=_FLAGS_NOTHING_SUPPORTED)

        db = SQLiteDB(db_path)

        assert _stored_platforms(db, db_id) is None

    def test_second_init_leaves_the_emptied_column_alone(self, tmp_path: Path) -> None:
        """The row the migration emptied stays empty on the next open."""
        db_path = tmp_path / "test.db"
        db_id = _seed_game(SQLiteDB(db_path), platforms=_FLAGS_NOTHING_SUPPORTED)
        SQLiteDB(db_path)

        db = SQLiteDB(db_path)

        assert _stored_platforms(db, db_id) is None

    def test_a_value_that_is_not_json_cannot_block_startup(
        self, tmp_path: Path
    ) -> None:
        """A platforms value the migration cannot read is skipped, not raised on."""
        db_path = tmp_path / "test.db"
        seed = SQLiteDB(db_path)
        broken_id = _seed_game(seed, platforms="{not json at all")
        stranded_id = _seed_game(
            seed,
            platforms=_FLAGS_WINDOWS_AND_LINUX,
            item_id="1207658925",
            title="Stardew Valley",
        )

        db = SQLiteDB(db_path)

        assert _stored_platforms(db, broken_id) == "{not json at all"
        assert json.loads(_stored_platforms(db, stranded_id)) == ["Windows", "Linux"]

    def test_export_writes_a_platform_name_rather_than_a_repr(
        self, tmp_path: Path
    ) -> None:
        """The user-visible symptom: the platform cell held a Python repr."""
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

    def test_second_init_leaves_the_migrated_row_alone(self, tmp_path: Path) -> None:
        """Running the migration twice changes nothing the first pass did."""
        db_path = tmp_path / "test.db"
        db_id = _seed_game(SQLiteDB(db_path), platforms=_FLAGS_WINDOWS_AND_LINUX)
        SQLiteDB(db_path)

        db = SQLiteDB(db_path)

        assert json.loads(_stored_platforms(db, db_id)) == ["Windows", "Linux"]

    def test_row_in_the_current_shape_is_untouched(self, tmp_path: Path) -> None:
        """A list of names written by any other producer is left as it is."""
        db_path = tmp_path / "test.db"
        db_id = _seed_game(
            SQLiteDB(db_path), platforms=json.dumps(["Windows", "PlayStation 5"])
        )

        db = SQLiteDB(db_path)

        assert json.loads(_stored_platforms(db, db_id)) == ["Windows", "PlayStation 5"]

    def test_a_flag_dict_stored_without_the_list_wrapper_is_rewritten(
        self, tmp_path: Path
    ) -> None:
        """The dict is read whether or not ``_to_json_array`` wrapped it."""
        db_path = tmp_path / "test.db"
        db_id = _seed_game(
            SQLiteDB(db_path),
            platforms=json.dumps({"windows": True, "mac": False, "linux": False}),
        )

        db = SQLiteDB(db_path)

        assert json.loads(_stored_platforms(db, db_id)) == ["Windows"]

    def test_a_platform_name_containing_a_brace_is_left_alone(
        self, tmp_path: Path
    ) -> None:
        """The brace the scan filters on can appear inside a real name.

        A one-name list unwraps to a string rather than a dict, so the
        cheap ``LIKE '%{%'`` filter costs the row a read and nothing more.
        """
        db_path = tmp_path / "test.db"
        db_id = _seed_game(SQLiteDB(db_path), platforms=json.dumps(["PC {Steam}"]))

        db = SQLiteDB(db_path)

        assert json.loads(_stored_platforms(db, db_id)) == ["PC {Steam}"]

    def test_the_emptied_column_accepts_a_later_sync(self, tmp_path: Path) -> None:
        """Clearing the column has to leave it fillable, not an empty list.

        ``platforms`` is fill-only, so a migration that wrote ``[]`` instead
        of NULL would lock the game out of ever recording a platform again.
        """
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

    def test_export_leaves_the_platform_cell_empty_when_nothing_is_supported(
        self, tmp_path: Path
    ) -> None:
        """No platform value means a blank cell, not ``[]`` or ``None``."""
        db_path = tmp_path / "test.db"
        db_id = _seed_game(SQLiteDB(db_path), platforms=_FLAGS_NOTHING_SUPPORTED)

        db = SQLiteDB(db_path)

        item = db.get_content_item(db_id)
        assert item is not None
        rows = list(
            csv.DictReader(
                io.StringIO(export_items_csv([item], ContentType.VIDEO_GAME))
            )
        )
        assert rows[0]["platform"] == ""


class TestMigrationRunsAfterDeduplication:
    """A row the dedup pass merges is repaired in the same open."""

    def test_a_merged_duplicate_is_repaired_and_never_lowered(
        self, tmp_path: Path
    ) -> None:
        """Dedup hands the survivor a stale blob copy, so the repair follows it.

        ``_deduplicate_inline`` merges the two blobs with the kept row's keys
        winning and takes the higher ``seasons``, so the survivor ends up
        carrying the losing row's stale count beside a real column value.
        Repairing before the merge would leave exactly that row stranded.
        """
        db_path = tmp_path / "test.db"
        db = SQLiteDB(db_path)
        with db.connection() as conn:
            cursor = conn.cursor()
            keep_id = _insert_show_row(
                cursor,
                external_id="trakt:1",
                title="The Wire",
                seasons=None,
                metadata=json.dumps({"total_seasons": 3}),
            )
            _insert_show_row(
                cursor,
                external_id="sonarr:1",
                title="Wire",
                seasons=5,
                metadata=None,
            )
            conn.commit()

        db = SQLiteDB(db_path)

        with db.connection() as conn:
            remaining = conn.execute(
                "SELECT COUNT(*) AS total FROM content_items"
            ).fetchone()["total"]
        assert remaining == 1
        assert _show_detail(db, keep_id) == (5, None)


class TestBothShapesInOnePass:
    """One pass over an existing library repairs every stranded row."""

    def test_every_stranded_row_is_repaired_on_one_open(self, tmp_path: Path) -> None:
        """Two shows and a game, all stranded, all corrected by one init."""
        db_path = tmp_path / "test.db"
        seed = SQLiteDB(db_path)
        first_show = _seed_show(seed, seasons=None, blob={"total_seasons": 5})
        second_show = _seed_show(
            seed,
            seasons=None,
            blob={"total_seasons": 5, "network": "HBO"},
            item_id="trakt:222",
            title="The Wire",
        )
        game = _seed_game(seed, platforms=_FLAGS_WINDOWS_AND_LINUX)

        db = SQLiteDB(db_path)

        assert _show_detail(db, first_show) == (5, None)
        assert _show_detail(db, second_show) == (5, {"network": "HBO"})
        assert json.loads(_stored_platforms(db, game)) == ["Windows", "Linux"]
