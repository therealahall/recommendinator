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

from src.models.content import ConsumptionStatus, ContentItem, ContentType
from src.storage import schema
from src.storage.sqlite_db import SQLiteDB
from src.utils.export import export_items_csv
from src.utils.item_serialization import item_to_dict
from src.utils.series import expand_tv_shows_to_seasons

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
        """One legacy GOG row carries both shapes, and one open ends both.

        The same sync wrote the flag dict and the free-form companies, so the
        two passes meet on the row a real upgrade finds rather than on one
        seeded per shape.
        """
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

    def test_an_imported_object_is_not_read_as_flags(self, tmp_path: Path) -> None:
        """Only a dict of booleans is a flag dict: reading an imported
        ``{"name": "PC"}`` as keys rewrote the column to ``["Name"]``, in
        place. The list repair behind it keeps the name the entry states.
        """
        db_path = tmp_path / "test.db"
        db_id = _seed_game(SQLiteDB(db_path), platforms=json.dumps([{"name": "PC"}]))
        _make_the_next_open_repair(db_path)

        db = SQLiteDB(db_path)

        assert json.loads(_stored_platforms(db, db_id)) == ["PC"]

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


class TestTheRepairSharesOneTransactionWithTheOpen:
    """A step failing after the repair discards it rather than committing it.

    ``create_schema`` commits once, at the end. A commit added between the
    steps would leave a half-upgraded database with nothing to say so.
    """

    def test_a_failure_after_the_repair_leaves_it_unapplied(
        self, tmp_path: Path
    ) -> None:
        """The stranded row is exactly as it was before the open that raised."""
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


class TestAnObjectStrandedInAListColumnMigration:
    def test_a_stored_row_can_be_saved_again_regression(self, tmp_path: Path) -> None:
        """A row the release before this one wrote reads back and re-saves:
        it refused the object without repairing the rows already holding one."""
        db_path = tmp_path / "test.db"
        seed = SQLiteDB(db_path)
        db_id = _seed_game(seed, platforms=json.dumps([{"name": "PC"}]))
        with seed.connection() as conn:
            conn.execute(f"PRAGMA user_version = {schema._SCHEMA_VERSION - 1}")
            conn.commit()

        db = SQLiteDB(db_path)

        stored = db.get_content_item(db_id)
        assert stored is not None
        assert db.save_content_item(stored) == db_id
        assert json.loads(_stored_platforms(db, db_id)) == ["PC"]

    def test_an_item_an_earlier_reset_requeued_is_counted_once_regression(
        self, tmp_path: Path
    ) -> None:
        """A reset before this one re-queued the item and kept its not_found."""
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
            conn.execute(f"PRAGMA user_version = {schema._SCHEMA_VERSION - 1}")
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
