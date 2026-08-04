"""Tests for the one-time migration of stranded detail-row shapes.

Two shapes were written by code that has since been corrected, and neither
self-repairs on a re-sync, so ``create_schema`` rewrites them once on init:

- A show written before the ``seasons`` column accepted ``total_seasons`` kept
  the count in the free-form metadata blob as well. The blob merge lets
  existing keys win, so no later sync removes the duplicate, and
  ``src/utils/series.py`` prefers the blob's copy over the column: once a sync
  raises the column the recommender keeps reading the stale lower number,
  which shows up as a completed show reappearing as in-progress.
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
from unittest.mock import patch

from src.models.content import ConsumptionStatus, ContentItem, ContentType
from src.storage import schema
from src.storage.sqlite_db import SQLiteDB
from src.utils.item_serialization import item_to_dict
from src.utils.series import expand_tv_shows_to_seasons
from src.web.export import export_items_csv

# The pre-fix GOG value: a flag per platform, wrapped in a single-element list
# on the way into the column by ``to_json_array``.
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


def _insert_game_row(
    cursor: sqlite3.Cursor,
    *,
    external_id: str,
    title: str,
    platforms: str | None,
) -> int:
    """Insert a game and its detail row directly, bypassing runtime dedup.

    The games counterpart of :func:`_insert_show_row`, for the pair the
    platform repair and the merge both have an opinion about.
    """
    cursor.execute(
        "INSERT INTO content_items"
        " (user_id, external_id, title, content_type, status, source)"
        " VALUES (1, ?, ?, 'video_game', 'unread', 'gog')",
        (external_id, title),
    )
    db_id = cursor.lastrowid
    assert db_id is not None
    cursor.execute(
        "INSERT INTO video_game_details (content_item_id, platforms) VALUES (?, ?)",
        (db_id, platforms),
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


def _seed_movie(db: SQLiteDB, *, blob: dict[str, Any]) -> int:
    """Save a movie, then strand *blob* beside its empty detail columns."""
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
        conn.commit()
    return db_id


def _movie_detail(db: SQLiteDB, db_id: int) -> tuple[Any, Any, dict[str, Any] | None]:
    """Return the stored (release_year, runtime, metadata blob) for a movie."""
    with db.connection() as conn:
        row = conn.execute(
            "SELECT release_year, runtime, metadata FROM movie_details"
            " WHERE content_item_id = ?",
            (db_id,),
        ).fetchone()
    blob = row["metadata"]
    return row["release_year"], row["runtime"], json.loads(blob) if blob else None


def _whole_detail_row(db: SQLiteDB, table: str, db_id: int) -> dict[str, Any]:
    """Return every column of a detail row, for comparing two opens.

    The table name is a literal from the call site, never input.
    """
    with db.connection() as conn:
        row = conn.execute(
            f"SELECT * FROM {table} WHERE content_item_id = ?",
            (db_id,),
        ).fetchone()
    assert row is not None
    return dict(row)


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
        """A blob holding only free-form keys is left exactly as it is."""
        db_path = tmp_path / "test.db"
        db_id = _seed_show(
            SQLiteDB(db_path),
            seasons=5,
            blob={"seasons_watched": [1, 2], "trakt_id": 1388},
        )

        db = SQLiteDB(db_path)

        assert _show_detail(db, db_id) == (
            5,
            {"seasons_watched": [1, 2], "trakt_id": 1388},
        )

    def test_a_duplicate_matching_the_column_only_drops_the_key(
        self, tmp_path: Path
    ) -> None:
        """The state most stranded rows are actually in: blob equals column."""
        db_path = tmp_path / "test.db"
        db_id = _seed_show(
            SQLiteDB(db_path),
            seasons=5,
            blob={"total_seasons": 5, "trakt_id": 1388},
        )

        db = SQLiteDB(db_path)

        assert _show_detail(db, db_id) == (5, {"trakt_id": 1388})

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

    def test_a_null_count_is_dropped_without_touching_the_column(
        self, tmp_path: Path
    ) -> None:
        """A key holding null records no count, so the column keeps its own.

        A plugin is free to write ``total_seasons: null`` for a show it has
        no count for, and the key is still the duplicate this pass clears —
        but folding it in must not blank a column that has a real number.
        """
        db_path = tmp_path / "test.db"
        db_id = _seed_show(
            SQLiteDB(db_path), seasons=5, blob={"total_seasons": None, "trakt_id": 1388}
        )

        db = SQLiteDB(db_path)

        assert _show_detail(db, db_id) == (5, {"trakt_id": 1388})

    def test_a_null_count_beside_an_empty_column_leaves_it_empty(
        self, tmp_path: Path
    ) -> None:
        """Nothing to fold and nothing to fill: the column stays NULL.

        ``seasons`` is read with ``int()`` everywhere, so a null reaching it
        would break the readers the repair exists to serve.
        """
        db_path = tmp_path / "test.db"
        db_id = _seed_show(
            SQLiteDB(db_path), seasons=None, blob={"total_seasons": None}
        )

        db = SQLiteDB(db_path)

        assert _show_detail(db, db_id) == (None, None)

    def test_a_nested_total_seasons_is_left_alone(self, tmp_path: Path) -> None:
        """Only a top-level duplicate is a duplicate of the column."""
        db_path = tmp_path / "test.db"
        blob = {"trakt_raw": {"total_seasons": 9}}
        db_id = _seed_show(SQLiteDB(db_path), seasons=5, blob=blob)

        db = SQLiteDB(db_path)

        assert _show_detail(db, db_id) == (5, blob)

    def test_the_key_name_inside_a_value_does_not_strand_the_row(
        self, tmp_path: Path
    ) -> None:
        """The ``LIKE`` prefilter matches text anywhere, and only prefilters.

        A note quoting the key is selected by the scan and must survive it:
        the decision is made on the parsed blob's own keys.
        """
        db_path = tmp_path / "test.db"
        blob = {"notes": "the total_seasons of this show is disputed"}
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
        """The dict is read whether or not ``to_json_array`` wrapped it."""
        db_path = tmp_path / "test.db"
        db_id = _seed_game(
            SQLiteDB(db_path),
            platforms=json.dumps({"windows": True, "mac": False, "linux": False}),
        )

        db = SQLiteDB(db_path)

        assert json.loads(_stored_platforms(db, db_id)) == ["Windows"]

    def test_an_imported_object_is_not_read_as_flags(self, tmp_path: Path) -> None:
        """Only a dict of booleans is a flag dict, and only one is rewritten.

        ``generic_json`` wraps a non-list ``platform`` in a list, so an entry
        saying ``{"name": "PC"}`` is stored in exactly the shape GOG's old
        value took. Reading its keys as names rewrote the column to
        ``["Name"]``, destroying the imported value in place — and, since the
        pass runs on every open rather than once, doing it again to every such
        import for the life of the database.
        """
        db_path = tmp_path / "test.db"
        stored = json.dumps([{"name": "PC"}])
        db_id = _seed_game(SQLiteDB(db_path), platforms=stored)
        SQLiteDB(db_path)

        db = SQLiteDB(db_path)

        assert _stored_platforms(db, db_id) == stored

    def test_a_dict_mixing_a_flag_with_another_key_is_left_alone(
        self, tmp_path: Path
    ) -> None:
        """The boundary of the guard: one non-boolean value disqualifies it.

        GOG mapped every name to a boolean, so a dict pairing one with
        anything else is some other producer's object and keeps what it
        holds. Checking that any value is a boolean rather than all of them
        would read this one as naming a "Windows" platform and throw the
        rest away.
        """
        db_path = tmp_path / "test.db"
        stored = json.dumps([{"windows": True, "name": "PC"}])
        db_id = _seed_game(SQLiteDB(db_path), platforms=stored)

        db = SQLiteDB(db_path)

        assert _stored_platforms(db, db_id) == stored

    def test_a_dict_of_ones_and_zeroes_is_not_read_as_flags(
        self, tmp_path: Path
    ) -> None:
        """JSON's 1 and 0 are ints, and the guard asks for booleans.

        ``isinstance(True, int)`` holds but not the reverse, so a producer
        writing 1/0 rather than true/false is some other shape and keeps what
        it holds — a row rewritten from it would drop the ``mac`` key with
        nothing having decided that 0 meant unsupported.
        """
        db_path = tmp_path / "test.db"
        stored = json.dumps([{"windows": 1, "mac": 0}])
        db_id = _seed_game(SQLiteDB(db_path), platforms=stored)

        db = SQLiteDB(db_path)

        assert _stored_platforms(db, db_id) == stored

    def test_an_object_naming_nothing_at_all_clears_the_column(
        self, tmp_path: Path
    ) -> None:
        """An empty object has no value to disqualify it, and names nothing.

        It reaches the column the same way the flag dict did, and holds no
        platform either way, so clearing it costs nothing and leaves the
        fill-only column open to a later sync.
        """
        db_path = tmp_path / "test.db"
        db_id = _seed_game(SQLiteDB(db_path), platforms=json.dumps([{}]))

        db = SQLiteDB(db_path)

        assert _stored_platforms(db, db_id) is None

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


class TestOnlyTheTwoShapesAreRepaired:
    """No other blob key moves, and no other detail table is rewritten.

    The pass reaches ``total_seasons`` on ``tv_show_details`` and ``platforms``
    on ``video_game_details``, and nothing else. A blob key duplicating any
    other column is left where it is, so a row this pass has no rule for comes
    back exactly as it was written — including on every later open, since a
    row it declines to repair stays in the shape it keeps seeing.
    """

    def test_a_movie_duplicating_its_columns_in_the_blob_is_left_alone(
        self, tmp_path: Path
    ) -> None:
        """``year`` and ``runtime_minutes`` are not this pass's business."""
        db_path = tmp_path / "test.db"
        blob = {"year": 1999, "runtime_minutes": 136}
        db_id = _seed_movie(SQLiteDB(db_path), blob=blob)

        db = SQLiteDB(db_path)

        assert _movie_detail(db, db_id) == (None, None, blob)

    def test_a_movie_row_is_not_rewritten_on_a_later_open_either(
        self, tmp_path: Path
    ) -> None:
        """The row the pass skips must not start being repaired on open two."""
        db_path = tmp_path / "test.db"
        blob = {"year": 1999, "runtime_minutes": 136}
        db_id = _seed_movie(SQLiteDB(db_path), blob=blob)
        SQLiteDB(db_path)

        db = SQLiteDB(db_path)

        assert _movie_detail(db, db_id) == (None, None, blob)

    def test_a_show_blob_key_other_than_the_season_count_is_left_alone(
        self, tmp_path: Path
    ) -> None:
        """Even on the table the pass does read, only one key moves.

        ``year`` and ``episodes`` duplicate ``tv_show_details`` columns of
        their own, and the row is selected by the scan because the blob names
        the season count beside them.
        """
        db_path = tmp_path / "test.db"
        db_id = _seed_show(
            SQLiteDB(db_path),
            seasons=None,
            blob={"total_seasons": 5, "year": 2008, "episodes": 62},
        )

        db = SQLiteDB(db_path)

        with db.connection() as conn:
            row = conn.execute(
                "SELECT seasons, release_year, episodes, metadata"
                " FROM tv_show_details WHERE content_item_id = ?",
                (db_id,),
            ).fetchone()
        assert (row["seasons"], row["release_year"], row["episodes"]) == (5, None, None)
        assert json.loads(row["metadata"]) == {"year": 2008, "episodes": 62}

    def test_a_book_blob_duplicating_its_columns_is_left_alone(
        self, tmp_path: Path
    ) -> None:
        """A table with no repair at all is never read by the pass."""
        db_path = tmp_path / "test.db"
        blob = {"author": "Frank Herbert", "pages": 412}
        db = SQLiteDB(db_path)
        db_id = db.save_content_item(
            ContentItem(
                id="legacy:book",
                title="Dune",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.UNREAD,
                source="generic_csv",
            )
        )
        with db.connection() as conn:
            conn.execute(
                "UPDATE book_details SET metadata = ? WHERE content_item_id = ?",
                (json.dumps(blob), db_id),
            )
            conn.commit()

        reopened = SQLiteDB(db_path)

        row = _whole_detail_row(reopened, "book_details", db_id)
        assert row["author"] is None
        assert row["pages"] is None
        assert json.loads(row["metadata"]) == blob


class TestASecondOpenRewritesNothing:
    """Every column of a repaired row is the same after the next open.

    ``create_schema`` runs on every open, so a pass still finding something to
    write would churn the row for the life of the database. The whole row is
    compared rather than the repaired column, because a second pass could
    disturb a column the first one left alone.
    """

    def test_a_repaired_show_row_is_unchanged_by_the_next_open(
        self, tmp_path: Path
    ) -> None:
        """The season count landed once, and the leftover blob stays put."""
        db_path = tmp_path / "test.db"
        db_id = _seed_show(
            SQLiteDB(db_path),
            seasons=3,
            blob={"total_seasons": 5, "seasons_watched": [1, 2]},
        )
        repaired = _whole_detail_row(SQLiteDB(db_path), "tv_show_details", db_id)

        db = SQLiteDB(db_path)

        assert repaired["seasons"] == 5
        assert _whole_detail_row(db, "tv_show_details", db_id) == repaired

    def test_a_repaired_game_row_is_unchanged_by_the_next_open(
        self, tmp_path: Path
    ) -> None:
        """The rewritten name list is not a flag dict, so nothing sees it again."""
        db_path = tmp_path / "test.db"
        db_id = _seed_game(SQLiteDB(db_path), platforms=_FLAGS_WINDOWS_AND_LINUX)
        repaired = _whole_detail_row(SQLiteDB(db_path), "video_game_details", db_id)

        db = SQLiteDB(db_path)

        assert json.loads(repaired["platforms"]) == ["Windows", "Linux"]
        assert _whole_detail_row(db, "video_game_details", db_id) == repaired


class TestRepairRunsBeforeDeduplication:
    """Each row folds its own stranded count before the merge weighs them.

    Both rows of a duplicate pair can have been written before ``seasons``
    accepted ``total_seasons``, leaving each row's only count in its own blob.
    ``_merge_detail_metadata`` merges the two blobs with the kept row's keys
    winning outright, so repairing after the merge throws the duplicate's
    count away and folds the kept row's onto the column — lowering a count the
    library held, which is exactly what the repair promises never to do.
    """

    def test_the_survivor_keeps_the_higher_of_two_stranded_counts(
        self, tmp_path: Path
    ) -> None:
        """The kept row holds the lower count, and the higher one still wins."""
        db_path = tmp_path / "test.db"
        db = SQLiteDB(db_path)
        with db.connection() as conn:
            cursor = conn.cursor()
            keep_id = _insert_show_row(
                cursor,
                external_id="trakt:1",
                title="The Wire",
                seasons=None,
                metadata=json.dumps({"total_seasons": 1}),
            )
            _insert_show_row(
                cursor,
                external_id="sonarr:1",
                title="Wire",
                seasons=None,
                metadata=json.dumps({"total_seasons": 5}),
            )
            conn.commit()

        db = SQLiteDB(db_path)

        with db.connection() as conn:
            remaining = conn.execute(
                "SELECT COUNT(*) AS total FROM content_items"
            ).fetchone()["total"]
        assert remaining == 1
        assert _show_detail(db, keep_id) == (5, None)

    def test_the_repair_pass_is_called_before_the_dedup_pass(
        self, tmp_path: Path
    ) -> None:
        """The order itself, which no data fixture can pin on its own.

        Once every row's count is on its column the two passes commute, so a
        library that happens to end up correct proves nothing about which ran
        first. Patching both records the call order directly.
        """
        calls: list[str] = []

        with (
            patch.object(
                schema,
                "_migrate_stranded_detail_shapes",
                lambda cursor: calls.append("repair"),
            ),
            patch.object(
                schema,
                "_deduplicate_inline",
                lambda cursor: calls.append("dedup"),
            ),
        ):
            SQLiteDB(tmp_path / "test.db")

        assert calls == ["repair", "dedup"]

    def test_the_other_order_round_lowers_the_count_on_the_same_two_rows(
        self, tmp_path: Path
    ) -> None:
        """The ordering itself decides the count, on one fixture, both ways.

        The assertion above reads the whole open, so it can only show that
        the order shipped today is right — not that the other one is wrong,
        which is the claim the class docstring makes. Driving the two passes
        by hand over the same pair shows both outcomes: repair first keeps
        the 5 the library held, dedup first keeps the survivor's own 1.
        """
        counts: dict[str, Any] = {}
        orders = {
            "repair_then_dedup": (
                schema._migrate_stranded_detail_shapes,
                schema._deduplicate_inline,
            ),
            "dedup_then_repair": (
                schema._deduplicate_inline,
                schema._migrate_stranded_detail_shapes,
            ),
        }
        for name, passes in orders.items():
            db = SQLiteDB(tmp_path / f"{name}.db")
            with db.connection() as conn:
                cursor = conn.cursor()
                keep_id = _insert_show_row(
                    cursor,
                    external_id="trakt:1",
                    title="The Wire",
                    seasons=None,
                    metadata=json.dumps({"total_seasons": 1}),
                )
                _insert_show_row(
                    cursor,
                    external_id="sonarr:1",
                    title="Wire",
                    seasons=None,
                    metadata=json.dumps({"total_seasons": 5}),
                )
                # Dedup matches on the normalized title, which a direct
                # INSERT leaves NULL, so the pass that fills it runs first
                # here exactly as it does in ``create_schema``.
                schema._renormalize_titles(cursor)
                for run_pass in passes:
                    run_pass(cursor)
                conn.commit()
            counts[name] = _show_detail(db, keep_id)[0]

        assert counts == {"repair_then_dedup": 5, "dedup_then_repair": 1}

    def test_a_stranded_row_merging_with_a_current_shape_row_keeps_the_higher(
        self, tmp_path: Path
    ) -> None:
        """One row stranded, the other already counted on its column.

        The mixed pair the old ordering was written for, kept because moving
        the pass must not cost it: the survivor's blob count is folded onto
        its own column and the merge then takes the duplicate's higher one.
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

        assert _show_detail(db, keep_id) == (5, None)

    def test_the_survivor_takes_a_platform_list_the_flag_dict_would_have_blocked(
        self, tmp_path: Path
    ) -> None:
        """The platform half of the ordering, which is fill-only either way.

        The kept row holds a flag dict naming nothing, which the repair
        clears. Clearing it first leaves the column open for the merge to
        fill from the duplicate; clearing it afterwards finds that the merge
        already declined to fill a column which was not NULL yet, and the
        library ends up with no platform at all. Both ways round, because
        this is the same ordering claim as the season count and it deserves
        the same evidence.
        """
        stored: dict[str, Any] = {}
        orders = {
            "repair_then_dedup": (
                schema._migrate_stranded_detail_shapes,
                schema._deduplicate_inline,
            ),
            "dedup_then_repair": (
                schema._deduplicate_inline,
                schema._migrate_stranded_detail_shapes,
            ),
        }
        for name, passes in orders.items():
            db = SQLiteDB(tmp_path / f"{name}.db")
            with db.connection() as conn:
                cursor = conn.cursor()
                keep_id = _insert_game_row(
                    cursor,
                    external_id="gog:1",
                    title="The Witcher",
                    platforms=_FLAGS_NOTHING_SUPPORTED,
                )
                _insert_game_row(
                    cursor,
                    external_id="steam:1",
                    title="Witcher",
                    platforms=json.dumps(["Windows"]),
                )
                schema._renormalize_titles(cursor)
                for run_pass in passes:
                    run_pass(cursor)
                conn.commit()
            stored[name] = _stored_platforms(db, keep_id)

        assert stored == {
            "repair_then_dedup": json.dumps(["Windows"]),
            "dedup_then_repair": None,
        }

    def test_a_repaired_detail_row_survives_being_moved_to_the_survivor(
        self, tmp_path: Path
    ) -> None:
        """The merge moves a whole detail row when the kept item has none.

        That leg of ``merge_detail_tables`` re-points the duplicate's row
        rather than merging column by column, so the repair has to have
        already run over the row being moved — nothing reads it again.
        """
        db_path = tmp_path / "test.db"
        db = SQLiteDB(db_path)
        with db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO content_items"
                " (user_id, external_id, title, content_type, status, source)"
                " VALUES (1, 'trakt:1', 'The Wire', 'tv_show',"
                " 'currently_consuming', 'trakt')"
            )
            keep_id = cursor.lastrowid
            _insert_show_row(
                cursor,
                external_id="sonarr:1",
                title="Wire",
                seasons=None,
                metadata=json.dumps({"total_seasons": 5, "trakt_id": 222}),
            )
            conn.commit()

        db = SQLiteDB(db_path)

        assert keep_id is not None
        assert _show_detail(db, keep_id) == (5, {"trakt_id": 222})

    def test_a_deduplicated_pair_is_unchanged_by_the_next_open(
        self, tmp_path: Path
    ) -> None:
        """The merged survivor is in the current shape, so nothing repeats.

        The repair now runs over rows the merge is about to delete, so the
        row it leaves behind has to be one the next open's pass declines —
        otherwise the count would be rewritten on every start for good.
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
                metadata=json.dumps({"total_seasons": 1, "trakt_id": 222}),
            )
            _insert_show_row(
                cursor,
                external_id="sonarr:1",
                title="Wire",
                seasons=None,
                metadata=json.dumps({"total_seasons": 5}),
            )
            conn.commit()

        merged = _whole_detail_row(SQLiteDB(db_path), "tv_show_details", keep_id)
        db = SQLiteDB(db_path)

        assert merged["seasons"] == 5
        assert _whole_detail_row(db, "tv_show_details", keep_id) == merged

    def test_titles_are_normalized_before_either_pass(self, tmp_path: Path) -> None:
        """Dedup matches on the normalized title, so it runs third of three.

        The repair moved in front of dedup; the re-normalization has to stay
        in front of both, because the pair dedup merges is only a pair once
        every title has been through it.
        """
        calls: list[str] = []

        with (
            patch.object(
                schema,
                "_renormalize_titles",
                lambda cursor: calls.append("normalize"),
            ),
            patch.object(
                schema,
                "_migrate_stranded_detail_shapes",
                lambda cursor: calls.append("repair"),
            ),
            patch.object(
                schema,
                "_deduplicate_inline",
                lambda cursor: calls.append("dedup"),
            ),
        ):
            SQLiteDB(tmp_path / "test.db")

        assert calls == ["normalize", "repair", "dedup"]


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
            blob={"total_seasons": 5, "trakt_id": 222},
            item_id="trakt:222",
            title="The Wire",
        )
        game = _seed_game(seed, platforms=_FLAGS_WINDOWS_AND_LINUX)

        db = SQLiteDB(db_path)

        assert _show_detail(db, first_show) == (5, None)
        assert _show_detail(db, second_show) == (5, {"trakt_id": 222})
        assert json.loads(_stored_platforms(db, game)) == ["Windows", "Linux"]
