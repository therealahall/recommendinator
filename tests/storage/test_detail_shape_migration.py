"""Tests for the one-time migration of stranded detail-row shapes.

Three shapes were written by code that has since been corrected, and none
self-repairs on a re-sync, so ``create_schema`` rewrites them once on init:

- A show written before the ``seasons`` column accepted ``total_seasons`` kept
  the count in the free-form metadata blob as well. The blob merge lets
  existing keys win, so no later sync removes the duplicate, and
  ``src/utils/series.py`` prefers the blob's copy over the column: once a sync
  raises the column the recommender keeps reading the stale lower number,
  which shows up as a completed show reappearing as in-progress.
- GOG wrote ``developers`` and ``publishers`` before either was an alias of a
  column, so both landed in the blob in whatever shape the API used, objects
  included. The read path merges the blob into the item it returns and a text
  column refuses an object, so re-saving such an item raises for good.
- GOG wrote ``platforms`` as a dict of per-platform booleans where every other
  producer writes a list of names. ``platforms`` is neither mergeable nor
  monotonic, so ``_save_detail_table`` is fill-only for it and a column already
  holding the dict keeps it permanently: an export writes a Python repr into
  the platform cell, and re-importing that file stores the repr as a literal
  string.

Every test seeds the pre-fix shape by writing the detail row directly, because
the corrected write path can no longer produce it — and rewinds the stored
schema version with it, because ``create_schema`` runs the repair only while
the database is below the version that introduced it, and a row in a shape no
current build writes came out of a build that predates the guard too.
"""

import csv
import io
import json
import sqlite3
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from src.ingestion.sources.markdown.markdown import MarkdownImportPlugin
from src.models.content import ConsumptionStatus, ContentItem, ContentType
from src.models.detail_fields import DETAIL_FIELDS, FieldKind
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


def _mark_written_before_the_repair(
    handle: sqlite3.Connection | sqlite3.Cursor,
) -> None:
    """Rewind the stored schema version to a build that predates the repair.

    Version 2 is the one directly below it: rewinding further would re-run the
    settings migrations too, which have nothing to do with the detail rows
    being seeded.

    Takes the handle the caller already holds rather than opening its own:
    several seeders write inside a transaction, and a second connection to the
    same file would block on its write lock.
    """
    handle.execute("PRAGMA user_version = 2")


def _make_the_next_open_repair(db_path: Path) -> None:
    """Rewind the database, building the schema first if there is none yet.

    Covers the two cases the seed helpers above do not: a test that seeds no
    row at all, and one that wants the repair run a second time over rows it
    has already corrected — which is what the retry after a failed open does.
    """
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
        _mark_written_before_the_repair(conn)
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
    _mark_written_before_the_repair(cursor)
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
    _mark_written_before_the_repair(cursor)
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
    """Save a GOG game, then strand *metadata* beside its company columns."""
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
    """Return the stored (developer, publisher, metadata blob) for a game."""
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
    external_id: str,
    title: str,
    metadata: str,
    developer: str | None = None,
    publisher: str | None = None,
    platforms: str | None = None,
) -> int:
    """Insert a game and its pre-alias detail row over a raw connection.

    The rows the company fold exists for were written by a build with no
    ``developers`` alias, so the whole row is written in SQL rather than
    through a write path that can no longer produce it.
    """
    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO content_items"
            " (user_id, external_id, title, content_type, status, source)"
            " VALUES (1, ?, ?, 'video_game', 'unread', 'gog')",
            (external_id, title),
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
    """Read a game's companies over a raw connection, running no migration.

    The companion of :func:`_show_detail_without_opening`, for asserting a
    seeded row really is unrepaired before the open that repairs it.
    """
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


def _strand_blob_key(
    db: SQLiteDB, db_id: int, content_type: str, blob: dict[str, Any]
) -> None:
    """Write *blob* into a saved item's detail row, whatever its type.

    The write path drops a known key from the blob on the way in, so a key
    stranded in front of a column can only be put there in SQL. The table name
    is read from the declaration, never from input.

    The row count is asserted because an UPDATE matching nothing is silent:
    every caller here reads the blob back through a codec and would pass on an
    unstranded row wherever the codec's answer is None.
    """
    table = DETAIL_FIELDS[content_type].table
    with db.connection() as conn:
        cursor = conn.execute(
            f"UPDATE {table} SET metadata = ? WHERE content_item_id = ?",
            (json.dumps(blob), db_id),
        )
        assert cursor.rowcount == 1
        _mark_written_before_the_repair(conn)
        conn.commit()


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
        _mark_written_before_the_repair(conn)
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


def _show_detail_without_opening(db_path: Path, db_id: int) -> tuple[Any, Any]:
    """Read a show's row over a raw connection, running no migration.

    ``SQLiteDB`` migrates on construction, so the helpers above would repair
    the very row a caller is asking to see unrepaired.
    """
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
    """Return the raw platforms column for a game."""
    with db.connection() as conn:
        row = conn.execute(
            "SELECT platforms FROM video_game_details WHERE content_item_id = ?",
            (db_id,),
        ).fetchone()
    return row["platforms"]


def _open_counting_the_repair(db_path: Path) -> int:
    """Open the database, returning how many times the repair pass ran.

    The pass still does its work, because "did it run" and "what did it leave
    behind" are both being asked. Every pass skips a row already in the current
    shape, so an unguarded rescan and a skipped one leave the same library —
    only the count tells them apart.
    """
    with patch.object(
        schema,
        "_migrate_stranded_detail_shapes",
        wraps=schema._migrate_stranded_detail_shapes,
    ) as repair:
        SQLiteDB(db_path)
    return int(repair.call_count)


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
        _make_the_next_open_repair(db_path)

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

        The pass runs inside the open, so a row it chokes on would make the
        database unopenable rather than merely unrepaired.
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


class TestStrandedCompanyNamesMigration:
    """A blob naming the companies folds onto the columns that now claim it."""

    def test_object_shaped_names_land_on_their_columns(self, tmp_path: Path) -> None:
        """The names reach the columns and the stranded keys are gone.

        The blob held the only copy of either name, so this recovers a
        developer the library had and could not read, rather than only
        clearing a duplicate.
        """
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
        """The item read back from storage re-saves without raising.

        Bug reported: a GOG game synced before ``developers`` was an alias of
        the ``developer`` column stayed queued for enrichment for good, every
        run recording the same failure against whichever provider ran.

        Root cause: the blob held GOG's object shape, the read path merges the
        blob into the item it returns, and a text column refuses an object —
        so every write of that item raised before it reached the fill-only
        check that would have left the populated column alone.

        Fix: the names fold onto the columns and the keys are dropped on open,
        so the shape no producer writes any more stops being read either.
        """
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
        """The fold is fill-only, and the stranded key goes either way.

        RAWG writes the singular keys, so a column can already hold a name
        chosen over GOG's — and the blob copy must not overwrite it while
        still ceasing to exist.
        """
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

    def test_bare_names_fold_the_same_way(self, tmp_path: Path) -> None:
        """GOG named companies in strings too, and several of them at once.

        Two developers join into the one name the column holds, the way the
        write path joins them.
        """
        db_path = tmp_path / "test.db"
        db_id = _seed_stranded_companies(
            SQLiteDB(db_path),
            metadata=json.dumps({"developers": ["CD Projekt Red", "Saber"]}),
        )

        db = SQLiteDB(db_path)

        assert _game_companies(db, db_id) == ("CD Projekt Red, Saber", None, None)

    def test_an_object_naming_nothing_leaves_the_column_fillable(
        self, tmp_path: Path
    ) -> None:
        """A key with no name in it is dropped without writing anything.

        ``developer`` is fill-only, so writing an empty string rather than
        leaving it NULL would lock the game out of ever recording one.
        """
        db_path = tmp_path / "test.db"
        db_id = _seed_stranded_companies(
            SQLiteDB(db_path), metadata=json.dumps({"developers": [{"slug": "cdpr"}]})
        )

        db = SQLiteDB(db_path)
        resynced_id = db.save_content_item(
            ContentItem(
                id="1207658924",
                title="The Witcher",
                content_type=ContentType.VIDEO_GAME,
                status=ConsumptionStatus.UNREAD,
                source="gog",
                metadata={"developers": ["CD Projekt Red"]},
            )
        )

        assert resynced_id == db_id
        assert _game_companies(db, db_id) == ("CD Projekt Red", None, None)

    def test_a_later_sync_of_the_alias_does_not_restrand_the_names(
        self, tmp_path: Path
    ) -> None:
        """GOG still writes the plural keys, and they now land on the columns."""
        db_path = tmp_path / "test.db"
        db_id = _seed_stranded_companies(
            SQLiteDB(db_path), metadata=json.dumps({"developers": ["CD Projekt Red"]})
        )

        db = SQLiteDB(db_path)
        db.save_content_item(
            ContentItem(
                id="1207658924",
                title="The Witcher",
                content_type=ContentType.VIDEO_GAME,
                status=ConsumptionStatus.UNREAD,
                source="gog",
                metadata={"publishers": ["CD Projekt"]},
            )
        )

        assert _game_companies(db, db_id) == ("CD Projekt Red", "CD Projekt", None)

    def test_second_init_leaves_the_migrated_row_alone(self, tmp_path: Path) -> None:
        """Running the migration twice changes nothing the first pass did."""
        db_path = tmp_path / "test.db"
        db_id = _seed_stranded_companies(
            SQLiteDB(db_path),
            metadata=json.dumps({"developers": [{"name": "CD Projekt Red"}]}),
        )
        repaired = _whole_detail_row(SQLiteDB(db_path), "video_game_details", db_id)

        _make_the_next_open_repair(db_path)
        db = SQLiteDB(db_path)

        assert repaired["developer"] == "CD Projekt Red"
        assert _whole_detail_row(db, "video_game_details", db_id) == repaired

    def test_the_key_name_inside_a_value_does_not_strand_the_row(
        self, tmp_path: Path
    ) -> None:
        """The ``LIKE`` prefilter matches text anywhere, and only prefilters."""
        db_path = tmp_path / "test.db"
        blob = {"notes": "the publishers of this game are disputed"}
        db_id = _seed_stranded_companies(SQLiteDB(db_path), metadata=json.dumps(blob))

        db = SQLiteDB(db_path)

        assert _game_companies(db, db_id) == (None, None, blob)

    def test_metadata_that_is_not_json_cannot_block_startup(
        self, tmp_path: Path
    ) -> None:
        """A blob the migration cannot read is skipped, not raised on.

        The pass runs inside the open, so a row it chokes on would make the
        database unopenable rather than merely unrepaired.
        """
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
    """The upgrade path: a row a previous release wrote, opened by this one.

    Nothing here goes through the corrected write path on the way in. The
    database is created by an earlier open and the detail row is then written
    in SQL exactly as a build with no ``developers`` alias left it, which is
    the only state the fold has to answer for.
    """

    def test_a_row_written_before_the_aliases_reads_and_saves_regression(
        self, tmp_path: Path
    ) -> None:
        """The item read back out of storage saves, twice, without raising.

        Bug reported: a GOG library synced by an earlier release could not be
        saved again. Enrichment recorded a provider failure for every such
        game on every run, and the game stayed queued for good.

        Root cause: the plural spellings were free-form keys then, so the blob
        holds GOG's object shape. The read path merges the blob into the item
        it returns, and ``to_text`` refuses an object, so the write raised
        before it could ever store anything that would end the cycle.

        Fix: ``_fold_stranded_company_names`` folds the names onto the columns
        and drops the keys when the database is opened, so the item the reader
        hands back no longer carries a shape the writer refuses.
        """
        db_path = tmp_path / "legacy.db"
        SQLiteDB(db_path)
        legacy_blob = {
            "gog_product_id": "1207658924",
            "developers": [{"name": "CD Projekt Red"}],
            "publishers": [{"name": "CD Projekt"}],
        }
        db_id = _insert_legacy_game_row(
            db_path,
            external_id="1207658924",
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

    def test_the_same_row_still_cannot_be_saved_with_the_fold_stubbed_out(
        self, tmp_path: Path
    ) -> None:
        """The fold is what ends the failure, not anything else on the path.

        The test above passes because the fold ran. Held out of one open, the
        row reaches the writer in the shape it was reported in and raises, so
        the repair is doing the work the regression above credits it with.
        """
        db_path = tmp_path / "unfolded.db"
        SQLiteDB(db_path)
        db_id = _insert_legacy_game_row(
            db_path,
            external_id="1207658924",
            title="The Witcher",
            metadata=json.dumps({"developers": [{"name": "CD Projekt Red"}]}),
        )

        with patch.object(schema, "_fold_stranded_company_names"):
            db = SQLiteDB(db_path)
            stored = db.get_content_item(db_id)

        assert stored is not None
        with pytest.raises(TypeError, match="text column cannot hold"):
            db.save_content_item(stored)

    def test_each_company_column_is_filled_on_its_own(self, tmp_path: Path) -> None:
        """A name enrichment wrote stands while the other column still fills.

        The two columns share one statement, so a fold that weighed them
        together would either overwrite the developer or decline the
        publisher.
        """
        db_path = tmp_path / "legacy-mixed.db"
        SQLiteDB(db_path)
        db_id = _insert_legacy_game_row(
            db_path,
            external_id="1207658924",
            title="The Witcher",
            developer="CD Projekt Red",
            metadata=json.dumps(
                {
                    "developers": [{"name": "Stale Studio"}],
                    "publishers": [{"name": "CD Projekt"}],
                }
            ),
        )

        db = SQLiteDB(db_path)

        assert _game_companies(db, db_id) == ("CD Projekt Red", "CD Projekt", None)

    def test_a_row_stranded_in_both_gog_shapes_is_repaired_at_once(
        self, tmp_path: Path
    ) -> None:
        """One legacy GOG row carries both shapes, and one open ends both.

        The same sync wrote the flag dict and the free-form companies, so the
        two passes meet on the row a real upgrade finds rather than on one
        seeded per shape.
        """
        db_path = tmp_path / "legacy-both.db"
        SQLiteDB(db_path)
        db_id = _insert_legacy_game_row(
            db_path,
            external_id="1207658924",
            title="The Witcher",
            platforms=_FLAGS_WINDOWS_AND_LINUX,
            metadata=json.dumps({"developers": ["CD Projekt Red"]}),
        )

        db = SQLiteDB(db_path)

        assert _game_companies(db, db_id) == ("CD Projekt Red", None, None)
        assert json.loads(_stored_platforms(db, db_id)) == ["Windows", "Linux"]


class TestWhatTheCompanyFoldDeclinesToFold:
    """A key it cannot read a name out of still stops being a stranded key.

    The blob is whatever an API said years ago, so the fold meets keys with
    nothing under them and blobs that are not objects at all. Neither may
    write a column and neither may raise, because this runs inside the open.
    """

    @pytest.mark.parametrize(
        "blob",
        [{"developers": None}, {"publishers": []}, {"developers": [{}]}],
        ids=["null", "empty_list", "object_naming_nothing"],
    )
    def test_a_key_with_no_name_under_it_leaves_the_columns_fillable(
        self, tmp_path: Path, blob: dict[str, Any]
    ) -> None:
        """The key goes and both columns stay NULL, not empty strings.

        Both columns are fill-only on the write path, so an empty string
        written here would lock the game out of ever recording a company.
        """
        db_path = tmp_path / "empty-keys.db"
        SQLiteDB(db_path)
        db_id = _insert_legacy_game_row(
            db_path,
            external_id="1207658924",
            title="The Witcher",
            metadata=json.dumps(blob),
        )

        db = SQLiteDB(db_path)

        assert _game_companies(db, db_id) == (None, None, None)

    def test_a_blob_that_is_not_an_object_is_left_exactly_as_it_was(
        self, tmp_path: Path
    ) -> None:
        """A JSON array naming the key has no keys, so there is nothing to fold."""
        db_path = tmp_path / "array-blob.db"
        SQLiteDB(db_path)
        db_id = _insert_legacy_game_row(
            db_path,
            external_id="1207658924",
            title="The Witcher",
            metadata=json.dumps(["developers"]),
        )

        db = SQLiteDB(db_path)

        assert _game_companies(db, db_id) == (None, None, ["developers"])

    def test_two_stranded_duplicates_merge_into_a_row_that_saves(
        self, tmp_path: Path
    ) -> None:
        """Each row folds its own names before the merge weighs them.

        The blob merge lets an existing key win, so a merge running first
        would carry a stranded company key into the surviving row and leave
        the merged game raising on every save — the same ordering the season
        count needs, on the pass that drops a key rather than copying one.
        """
        db_path = tmp_path / "stranded-duplicates.db"
        SQLiteDB(db_path)
        _insert_legacy_game_row(
            db_path,
            external_id="1207658924",
            title="The Witcher",
            metadata=json.dumps({"developers": [{"name": "CD Projekt Red"}]}),
        )
        _insert_legacy_game_row(
            db_path,
            external_id="gog:1207658924",
            title="The Witcher",
            metadata=json.dumps({"publishers": [{"name": "CD Projekt"}]}),
        )

        db = SQLiteDB(db_path)

        games = db.get_content_items(content_type=ContentType.VIDEO_GAME)
        assert len(games) == 1
        assert games[0].author == "CD Projekt Red"
        assert "developers" not in games[0].metadata
        assert db.save_content_item(games[0]) is not None


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
        _make_the_next_open_repair(db_path)

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
        _make_the_next_open_repair(db_path)

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
        ``["Name"]``, destroying the imported value in place.
        ``TestTheRowsTheRepairDeclinesToSettle`` takes the same row from the
        other side: declining it is what leaves it matching the scan's filter.
        """
        db_path = tmp_path / "test.db"
        stored = json.dumps([{"name": "PC"}])
        db_id = _seed_game(SQLiteDB(db_path), platforms=stored)
        _make_the_next_open_repair(db_path)

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


class TestOnlyTheDeclaredShapesAreRepaired:
    """No other blob key moves, and no other detail table is rewritten.

    The pass reaches ``total_seasons`` on ``tv_show_details``, and
    ``developers``, ``publishers`` and ``platforms`` on
    ``video_game_details``. A blob key duplicating any other column is left
    where it is, so a row this pass has no rule for comes back exactly as it
    was written — including on a later open, which is the one an upgrade that
    started rewriting such a row would ruin.
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
        _make_the_next_open_repair(db_path)

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

    def test_a_game_blob_key_other_than_the_companies_is_left_alone(
        self, tmp_path: Path
    ) -> None:
        """Even on the table the company fold reads, only two keys move.

        ``release_year`` duplicates a column of its own, and the row is
        selected by the scan because the blob names a company beside it.
        """
        db_path = tmp_path / "test.db"
        db_id = _seed_stranded_companies(
            SQLiteDB(db_path),
            metadata=json.dumps(
                {"developers": ["CD Projekt Red"], "release_year": 2007}
            ),
        )

        db = SQLiteDB(db_path)

        with db.connection() as conn:
            row = conn.execute(
                "SELECT developer, release_year, metadata FROM video_game_details"
                " WHERE content_item_id = ?",
                (db_id,),
            ).fetchone()
        assert (row["developer"], row["release_year"]) == ("CD Projekt Red", None)
        assert json.loads(row["metadata"]) == {"release_year": 2007}

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
            _mark_written_before_the_repair(conn)
            conn.commit()

        reopened = SQLiteDB(db_path)

        row = _whole_detail_row(reopened, "book_details", db_id)
        assert row["author"] is None
        assert row["pages"] is None
        assert json.loads(row["metadata"]) == blob


class TestASecondOpenRewritesNothing:
    """Every column of a repaired row is the same after the passes run again.

    The version guard means the next open runs none of them, which the last
    test pins on its own. These two rewind the version between the opens, so
    the passes really do read the repaired row a second time: that is the
    retry after a failed open, and a pass still finding something to write
    would churn the row on every one of them. The whole row is compared rather
    than the repaired column, because a second pass could disturb a column the
    first one left alone.
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

        _make_the_next_open_repair(db_path)
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

        _make_the_next_open_repair(db_path)
        db = SQLiteDB(db_path)

        assert json.loads(repaired["platforms"]) == ["Windows", "Linux"]
        assert _whole_detail_row(db, "video_game_details", db_id) == repaired

    def test_the_second_open_does_not_run_the_pass_at_all(self, tmp_path: Path) -> None:
        """Not rewriting is idempotence; not reading is what the guard adds.

        The two tests above rewind the version to make the passes run again,
        so on their own they say nothing about whether an ordinary second open
        reads the library — and re-reading it is the cost being removed.
        """
        db_path = tmp_path / "test.db"
        _seed_show(SQLiteDB(db_path), seasons=3, blob={"total_seasons": 5})

        assert _open_counting_the_repair(db_path) == 1
        assert _open_counting_the_repair(db_path) == 0


class TestTheRowsTheRepairDeclinesToSettle:
    """A row the pass keeps rather than rewrites is read once, not forever.

    ``generic_json`` wraps a non-list ``platform`` in a list, so an imported
    ``{"name": "PC"}`` is stored in exactly the shape GOG's flag dict took. The
    pass declines it rather than reading its keys as names, which would destroy
    the imported value — and that leaves the row in a shape
    ``platforms LIKE '%{%'`` matches, so an unguarded scan finds and re-parses
    it on every open for the life of the database. Keeping the value is the
    behaviour that must not change; the endless re-reading is what the version
    guard ends.
    """

    def test_a_declined_row_is_read_once_and_kept(self, tmp_path: Path) -> None:
        """One open reads it, the next does not, and the value is untouched."""
        db_path = tmp_path / "test.db"
        stored = json.dumps([{"name": "PC"}])
        db_id = _seed_game(SQLiteDB(db_path), platforms=stored)

        reads = (_open_counting_the_repair(db_path), _open_counting_the_repair(db_path))

        assert reads == (1, 0)
        assert _stored_platforms(SQLiteDB(db_path), db_id) == stored


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
        db_path = tmp_path / "test.db"
        _make_the_next_open_repair(db_path)
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
            SQLiteDB(db_path)

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
        row it leaves behind has to be one a re-run of the pass declines —
        otherwise the retry after a failed open would lower the count.
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
        _make_the_next_open_repair(db_path)
        db = SQLiteDB(db_path)

        assert merged["seasons"] == 5
        assert _whole_detail_row(db, "tv_show_details", keep_id) == merged

    def test_titles_are_normalized_before_either_pass(self, tmp_path: Path) -> None:
        """Dedup matches on the normalized title, so it runs third of three.

        The repair moved in front of dedup; the re-normalization has to stay
        in front of both, because the pair dedup merges is only a pair once
        every title has been through it.
        """
        db_path = tmp_path / "test.db"
        _make_the_next_open_repair(db_path)
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
            SQLiteDB(db_path)

        assert calls == ["normalize", "repair", "dedup"]


class TestTheRepairAndTheMergeShareOneTransaction:
    """A failure in the merge discards the repair rather than committing it.

    Repairing before the merge is only correct while the two are one unit of
    work: ``create_schema`` opens an implicit transaction on its first write
    and commits once at the end, and any exception closes the connection with
    nothing committed. A commit added between the passes — or a connection
    running without implicit transactions — would leave a database repaired
    but unmerged, and nothing would say so.
    """

    def test_a_failure_in_the_merge_leaves_the_repair_unapplied(
        self, tmp_path: Path
    ) -> None:
        """The stranded row is exactly as it was before the open that raised."""
        db_path = tmp_path / "test.db"
        db_id = _seed_show(SQLiteDB(db_path), seasons=None, blob={"total_seasons": 5})

        with (
            patch.object(
                schema, "_deduplicate_inline", side_effect=OSError("disk failure")
            ),
            pytest.raises(OSError),
        ):
            SQLiteDB(db_path)

        assert _show_detail_without_opening(db_path, db_id) == (
            None,
            {"total_seasons": 5},
        )

    def test_a_failure_in_the_merge_leaves_the_company_fold_unapplied(
        self, tmp_path: Path
    ) -> None:
        """The fold writes columns and rewrites a blob, and neither survives.

        It is the one pass that drops a key, so a half-applied run would lose
        the only copy of a name rather than merely leave a duplicate.
        """
        db_path = tmp_path / "test.db"
        blob = {"developers": [{"name": "CD Projekt Red"}]}
        SQLiteDB(db_path)
        db_id = _insert_legacy_game_row(
            db_path,
            external_id="1207658924",
            title="The Witcher",
            metadata=json.dumps(blob),
        )

        with (
            patch.object(
                schema, "_deduplicate_inline", side_effect=OSError("disk failure")
            ),
            pytest.raises(OSError),
        ):
            SQLiteDB(db_path)

        assert _game_companies_without_opening(db_path, db_id) == (None, None, blob)

    def test_the_database_still_opens_and_repairs_after_a_failed_open(
        self, tmp_path: Path
    ) -> None:
        """A transient failure costs the repair, not the database.

        Discarding the work is only half of what an operator needs: the open
        that raised must also leave nothing behind — no half-written row, no
        connection still holding the file — or the failure turns a stranded
        row into a library that cannot be started at all.
        """
        db_path = tmp_path / "test.db"
        db_id = _seed_show(SQLiteDB(db_path), seasons=None, blob={"total_seasons": 5})

        with (
            patch.object(
                schema, "_deduplicate_inline", side_effect=OSError("disk failure")
            ),
            pytest.raises(OSError),
        ):
            SQLiteDB(db_path)

        assert _show_detail(SQLiteDB(db_path), db_id) == (5, None)


class TestEveryAliasWasCheckedForTheSameStranding:
    """No alias but the two company keys can strand before a text column.

    The fold is scoped by a reading of the declaration: every other alias this
    branch adds is read by a codec that answers a stranded object without
    raising. That reading is pinned here, so an alias added later to a text
    column fails a test rather than quietly re-opening the defect.
    """

    #: What each column swept here holds once the stranded object has been
    #: through the codec that answers it: the integer codec reads no number out
    #: of it, the list codec serialises it whole, and the additive merge behind
    #: ``genres`` reduces every entry to text on the way past. None of the
    #: three refuses the write, which is the property being swept for.
    #: ``seasons`` never sees the object at all — the season pass drops
    #: ``total_seasons`` on the open, before anything reads it back.
    #: Keyed by column rather than by alias, because what lands is the codec's
    #: doing and the codec is the column's, whichever alias arrived.
    _LANDED_BY_COLUMN: dict[str, Any] = {
        "release_year": None,
        "runtime": None,
        "seasons": None,
        "platforms": '[{"name": "Object"}]',
        "genres": "[\"{'name': 'Object'}\"]",
    }

    @staticmethod
    def _text_column_aliases() -> set[str]:
        """Every alias whose field is stored by ``to_text``."""
        return {
            alias
            for spec in DETAIL_FIELDS.values()
            for field in spec.fields
            if field.kind in (FieldKind.CREATOR, FieldKind.TEXT)
            for alias in field.aliases
        }

    def test_the_fold_covers_every_text_alias_but_the_one_nothing_writes(self) -> None:
        """``creator`` is the exemption, and the value is what earns it.

        A blob can carry the key: the markdown source parses any ``Key:
        Value`` in a list item's tail into a lowercased metadata key, so
        ``Creator: Vince Gilligan`` on a show writes one. Every value it can
        write is a string, which ``to_text`` takes unchanged, so the key folds
        onto ``creators`` on the next save rather than refusing it — a
        duplicate at worst, never a stranding. What holds is the shape the
        producers can write, not the absence of a producer;
        ``TestWhatTheCreatorExemptionCosts`` pins both halves.
        """
        assert (
            schema._STRANDED_COMPANY_COLUMNS.keys()
            == self._text_column_aliases() - {"creator"}
        ), (
            "A new alias in front of a text column needs a repair pass of its"
            " own. _fold_stranded_company_names names developer, publisher and"
            " video_game_details literally, so adding a key to"
            " _STRANDED_COMPANY_COLUMNS pops it out of every game blob and"
            " writes it to nothing."
        )

    @pytest.mark.parametrize(
        ("content_type", "alias", "column"),
        [
            (content_type, alias, field.column)
            for content_type, spec in DETAIL_FIELDS.items()
            for field in spec.fields
            if field.kind not in (FieldKind.CREATOR, FieldKind.TEXT)
            for alias in field.aliases
        ],
    )
    def test_an_object_under_any_other_alias_cannot_fail_a_write(
        self, tmp_path: Path, content_type: str, alias: str, column: str
    ) -> None:
        """The alias the reader hands back is written, not refused.

        The stranding is the reader handing a legacy blob key back to the
        writer, so the loop to exercise is seed-in-SQL, reopen, read back,
        re-save. Saving a fresh item carrying the key proves nothing: the
        write path pops it before any column sees it.

        What landed is asserted rather than that the item still exists,
        because a column the codec declined and a column holding the object's
        repr are both "not None", and only one of those is the sweep's claim.
        """
        db_path = tmp_path / "swept.db"
        seed = SQLiteDB(db_path)
        db_id = seed.save_content_item(
            ContentItem(
                id=f"swept:{alias}",
                title="Swept",
                content_type=ContentType(content_type),
                status=ConsumptionStatus.UNREAD,
            )
        )
        _strand_blob_key(seed, db_id, content_type, {alias: [{"name": "Object"}]})

        db = SQLiteDB(db_path)
        stored = db.get_content_item(db_id)

        assert stored is not None
        assert db.save_content_item(stored) == db_id
        row = _whole_detail_row(db, DETAIL_FIELDS[content_type].table, db_id)
        assert row[column] == self._LANDED_BY_COLUMN[column]


class TestWhatTheCreatorExemptionCosts:
    """``creator`` has no repair pass, and the value is what earns it.

    A blob can carry the key: the markdown source turns any ``Key: Value`` in
    a list item's tail into a lowercased metadata key, so a show written
    ``| Creator: Vince Gilligan`` strands one. Every value that source can
    write is a string, which ``to_text`` takes unchanged, so the key folds
    onto the column instead of refusing the save. The exemption rests on that
    and on nothing else — the price of a producer writing the same key as an
    object is recorded below, because no shipped code would notice.
    """

    #: The refusal in full. It names the canonical key rather than the
    #: ``creator`` alias the blob carried, which is what
    #: ``docs/PLUGIN_DEVELOPMENT.md`` tells a plugin author to look for.
    _REFUSAL = "'creators': a text column cannot hold a dict"

    @staticmethod
    def _strand_a_show_creator(db_path: Path, creator: Any) -> int:
        """Save a show, then strand *creator* under the ``creator`` blob key."""
        db = SQLiteDB(db_path)
        db_id = db.save_content_item(
            ContentItem(
                id="markdown:breaking-bad",
                title="Breaking Bad",
                content_type=ContentType.TV_SHOW,
                status=ConsumptionStatus.COMPLETED,
                source="markdown_import",
            )
        )
        _strand_blob_key(db, db_id, "tv_show", {"creator": creator})
        return db_id

    @pytest.mark.parametrize(
        ("tail", "expected"),
        [
            ("Creator: Vince Gilligan", {"creator": "Vince Gilligan"}),
            ("Creator: 5", {"creator": "5"}),
            (
                "Creator: [{'name': 'Vince Gilligan'}]",
                {"creator": "[{'name': 'Vince Gilligan'}]"},
            ),
            ("Creator: true", {"creator": "true"}),
            ("Creator: A | Creator: B", {"creator": "B"}),
            ("Creator:", {}),
            ("Creator:   | Rating: 5", {}),
        ],
        ids=[
            "name",
            "number",
            "object_literal",
            "boolean_word",
            "repeated_key",
            "no_value",
            "whitespace_value",
        ],
    )
    def test_no_tail_makes_the_key_anything_but_a_string(
        self, tmp_path: Path, tail: str, expected: dict[str, str]
    ) -> None:
        """The exemption's whole premise, swept rather than sampled.

        ``_parse_metadata_tail`` matches ``(\\w+)\\s*:\\s*(.+)`` on each
        segment and returns ``match.group(2).strip()``, so the key is whatever
        the file says and no input turns a value into a list, a dict, a number
        or a bool: a file writing what looks like one gets its text. A tail
        with nothing after the colon writes no key at all, which is why
        ``to_text("")`` never answers this producer. The exemption holds only
        while this does.
        """
        md_file = tmp_path / "shows.md"
        md_file.write_text(f"## Completed\n- **Breaking Bad** | {tail}\n")

        items = list(
            MarkdownImportPlugin().fetch(
                {"path": str(md_file), "content_type": "tv_show"}
            )
        )

        assert [item.metadata for item in items] == [expected]

    def test_a_stranded_string_folds_itself_onto_the_column(
        self, tmp_path: Path
    ) -> None:
        """No pass runs, and the next save repairs the row anyway.

        The blob keeps its copy, the way it keeps any existing key, but that
        copy is a duplicate rather than a stranding: it agrees with the column
        and ``extract_creator`` reads it only when the column says nothing.
        """
        db_path = tmp_path / "string-creator.db"
        db_id = self._strand_a_show_creator(db_path, "Vince Gilligan")

        db = SQLiteDB(db_path)
        stored = db.get_content_item(db_id)

        assert stored is not None
        assert db.save_content_item(stored) == db_id
        row = _whole_detail_row(db, "tv_show_details", db_id)
        assert row["creators"] == "Vince Gilligan"
        assert json.loads(row["metadata"]) == {"creator": "Vince Gilligan"}

    def test_a_stranded_object_costs_the_item_every_save(self, tmp_path: Path) -> None:
        """What the exemption buys, if a producer ever writes an object.

        A plugin writing ``metadata["creator"] = [{"name": ...}]`` for a show
        re-opens exactly the defect the company fold repairs: the reader hands
        the key back, the text column refuses it, and the item can never be
        saved again — with no pass to fold it and no other test to notice.

        The refusal is the permanent half of the contrast with the stranded
        string above, which repairs itself on the save it survives. So the
        blob is read after the first refusal and the save is made again: a
        second raise off an unchanged blob is what "every save" means.
        """
        db_path = tmp_path / "object-creator.db"
        db_id = self._strand_a_show_creator(db_path, [{"name": "Vince Gilligan"}])

        db = SQLiteDB(db_path)
        stored = db.get_content_item(db_id)

        assert stored is not None
        with pytest.raises(TypeError) as refusal:
            db.save_content_item(stored)
        assert str(refusal.value) == self._REFUSAL

        row = _whole_detail_row(db, "tv_show_details", db_id)
        assert row["creators"] is None
        assert json.loads(row["metadata"]) == {"creator": [{"name": "Vince Gilligan"}]}

        with pytest.raises(TypeError) as second_refusal:
            db.save_content_item(stored)
        assert str(second_refusal.value) == self._REFUSAL


class TestEveryShapeInOnePass:
    """One pass over an existing library repairs every stranded row."""

    def test_every_stranded_row_is_repaired_on_one_open(self, tmp_path: Path) -> None:
        """Two shows and two games, all stranded, all corrected by one init."""
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
