"""Tests for the one-time migration of stranded detail-row shapes.

Two shapes were written by code that has since been corrected, and neither
self-repairs on a re-sync, so ``create_schema`` rewrites them once on init:

- A row written before a column claimed one of its metadata keys kept the
  value in the free-form blob as well: ``total_seasons`` beside ``seasons``,
  ``year`` beside ``release_year``, ``runtime_minutes`` beside ``runtime``.
  The blob merge lets existing keys win, so no later sync removes the
  duplicate. ``src/utils/series.py`` prefers the blob's ``total_seasons`` over
  the column, so once a sync raises the column the recommender keeps reading
  the stale lower number, which shows up as a completed show reappearing as
  in-progress; the other keys are inert duplication until a reader does the
  same.
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

import pytest

from src.models.content import ConsumptionStatus, ContentItem, ContentType
from src.models.detail_fields import DETAIL_FIELDS, ContentTypeFields, FieldKind
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

# One stranded value per kind of column, so a blob can be built from the
# declaration rather than from a list of keys written out by hand.
_STRANDED_VALUE: dict[FieldKind, Any] = {
    FieldKind.CREATOR: "Stranded",
    FieldKind.TEXT: "Stranded",
    FieldKind.INTEGER: 1,
    FieldKind.STRING_LIST: ["Stranded"],
}


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


def _seed_movie(
    db: SQLiteDB,
    *,
    blob: dict[str, Any],
    release_year: int | None = None,
    runtime: int | None = None,
    genres: str | None = None,
    item_id: str = "tmdb:603",
    title: str = "The Matrix",
) -> int:
    """Save a movie, then write its detail row into the given shape."""
    db_id = db.save_content_item(
        ContentItem(
            id=item_id,
            title=title,
            content_type=ContentType.MOVIE,
            status=ConsumptionStatus.COMPLETED,
            source="trakt",
        )
    )
    with db.connection() as conn:
        conn.execute(
            "UPDATE movie_details"
            " SET release_year = ?, runtime = ?, genres = ?, metadata = ?"
            " WHERE content_item_id = ?",
            (release_year, runtime, genres, json.dumps(blob), db_id),
        )
        conn.commit()
    return db_id


def _movie_detail(
    db: SQLiteDB, db_id: int
) -> tuple[Any, Any, Any, dict[str, Any] | None]:
    """Return the stored (release_year, runtime, genres, blob) for a movie."""
    with db.connection() as conn:
        row = conn.execute(
            "SELECT release_year, runtime, genres, metadata FROM movie_details"
            " WHERE content_item_id = ?",
            (db_id,),
        ).fetchone()
    blob = row["metadata"]
    return (
        row["release_year"],
        row["runtime"],
        row["genres"],
        json.loads(blob) if blob else None,
    )


def _seed_stranded_blob(
    db: SQLiteDB, content_type: ContentType, blob: dict[str, Any]
) -> int:
    """Save an item of any type, then strand *blob* beside its empty columns.

    The columns are left as ``save_content_item`` wrote them — all NULL, since
    the item carries no metadata — which is the shape of a row written while
    the blob was the only place the value had.
    """
    spec = DETAIL_FIELDS[content_type.value]
    db_id = db.save_content_item(
        ContentItem(
            id=f"legacy:{content_type.value}",
            title="Legacy Item",
            content_type=content_type,
            status=ConsumptionStatus.UNREAD,
            source="generic_csv",
        )
    )
    with db.connection() as conn:
        # The table name comes from the field declaration, not from input.
        conn.execute(
            f"UPDATE {spec.table} SET metadata = ? WHERE content_item_id = ?",
            (json.dumps(blob), db_id),
        )
        conn.commit()
    return db_id


def _detail_row(db: SQLiteDB, spec: ContentTypeFields, db_id: int) -> sqlite3.Row:
    """Return the whole detail row for an item of *spec*'s content type."""
    with db.connection() as conn:
        row = conn.execute(
            f"SELECT * FROM {spec.table} WHERE content_item_id = ?",
            (db_id,),
        ).fetchone()
    assert row is not None
    return row


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


class TestStrandedColumnKeysMigration:
    """Every key a column consumes leaves the blob, not just total_seasons."""

    def test_year_and_runtime_move_onto_their_columns(self, tmp_path: Path) -> None:
        """The keys PR-1a claimed for ``release_year`` and ``runtime``.

        A movie written before the columns accepted them kept both in the
        blob, so the blob copy is the only one there is — dropping the key
        without folding it in would lose the year and the runtime.
        """
        db_path = tmp_path / "test.db"
        db_id = _seed_movie(
            SQLiteDB(db_path), blob={"year": 1999, "runtime_minutes": 136}
        )

        db = SQLiteDB(db_path)

        assert _movie_detail(db, db_id) == (1999, 136, None, None)

    def test_a_column_holding_a_value_keeps_it(self, tmp_path: Path) -> None:
        """The blob copy fills an empty column and never replaces a full one.

        Neither column is monotonic or mergeable, so the write path is
        fill-only for both: a stale blob copy loses to whatever the column
        already holds.
        """
        db_path = tmp_path / "test.db"
        db_id = _seed_movie(
            SQLiteDB(db_path),
            blob={"year": 1999, "runtime_minutes": 136},
            release_year=2000,
        )

        db = SQLiteDB(db_path)

        assert _movie_detail(db, db_id) == (2000, 136, None, None)

    def test_free_form_keys_beside_a_stranded_one_survive(self, tmp_path: Path) -> None:
        """Only the keys a column consumes are taken out of the blob."""
        db_path = tmp_path / "test.db"
        db_id = _seed_movie(
            SQLiteDB(db_path), blob={"year": 1999, "tmdb_id": 603, "watched_at": "2024"}
        )

        db = SQLiteDB(db_path)

        assert _movie_detail(db, db_id) == (
            1999,
            None,
            None,
            {"tmdb_id": 603, "watched_at": "2024"},
        )

    def test_a_stranded_genre_joins_the_ones_the_column_holds(
        self, tmp_path: Path
    ) -> None:
        """``genres`` is merged rather than filled, as it is on every sync.

        Taking the column as it stands would drop a genre the blob is the
        only record of.
        """
        db_path = tmp_path / "test.db"
        db_id = _seed_movie(
            SQLiteDB(db_path),
            blob={"genre": ["Science Fiction"]},
            genres=json.dumps(["Action"]),
        )

        db = SQLiteDB(db_path)

        release_year, runtime, genres, blob = _movie_detail(db, db_id)
        assert json.loads(genres) == ["Action", "Science Fiction"]
        assert blob is None

    def test_a_show_strands_a_year_and_a_season_count_together(
        self, tmp_path: Path
    ) -> None:
        """One row can carry several stranded keys, each with its own rule."""
        db_path = tmp_path / "test.db"
        db_id = _seed_show(
            SQLiteDB(db_path),
            seasons=None,
            blob={"total_seasons": 5, "year": 2008, "seasons_watched": [1]},
        )

        db = SQLiteDB(db_path)

        with db.connection() as conn:
            row = conn.execute(
                "SELECT release_year FROM tv_show_details WHERE content_item_id = ?",
                (db_id,),
            ).fetchone()
        assert row["release_year"] == 2008
        assert _show_detail(db, db_id) == (5, {"seasons_watched": [1]})

    def test_second_init_leaves_the_migrated_row_alone(self, tmp_path: Path) -> None:
        """Running the migration twice changes nothing the first pass did."""
        db_path = tmp_path / "test.db"
        db_id = _seed_movie(
            SQLiteDB(db_path), blob={"year": 1999, "runtime_minutes": 136}
        )
        SQLiteDB(db_path)

        db = SQLiteDB(db_path)

        assert _movie_detail(db, db_id) == (1999, 136, None, None)

    def test_row_in_the_current_shape_is_untouched(self, tmp_path: Path) -> None:
        """A blob holding only free-form keys is left exactly as it is."""
        db_path = tmp_path / "test.db"
        blob = {"tmdb_id": 603, "watched_at": "2024-01-01"}
        db_id = _seed_movie(
            SQLiteDB(db_path), blob=blob, release_year=1999, runtime=136
        )

        db = SQLiteDB(db_path)

        assert _movie_detail(db, db_id) == (1999, 136, None, blob)

    def test_the_moved_values_are_the_ones_an_export_writes(
        self, tmp_path: Path
    ) -> None:
        """The user-visible proof nothing was dropped on the way to the column.

        The export reads its ``year`` and ``runtime_minutes`` cells from the
        columns, so a pass that deleted the blob keys instead of folding them
        in would export two empty cells.
        """
        db_path = tmp_path / "test.db"
        db_id = _seed_movie(
            SQLiteDB(db_path), blob={"year": 1999, "runtime_minutes": 136}
        )

        db = SQLiteDB(db_path)

        item = db.get_content_item(db_id)
        assert item is not None
        rows = list(
            csv.DictReader(io.StringIO(export_items_csv([item], ContentType.MOVIE)))
        )
        assert rows[0]["year"] == "1999"
        assert rows[0]["runtime_minutes"] == "136"


class TestEveryDeclaredKeyIsFolded:
    """The pass covers the declaration, not a list of keys written by hand.

    Each test builds its blob out of :data:`DETAIL_FIELDS`, so declaring a new
    column, or a new alias for one, extends these tests by itself — and
    narrowing the migration back to a fixed set of keys fails them.
    """

    @pytest.mark.parametrize("content_type", list(ContentType))
    def test_no_key_a_column_claims_survives_in_the_blob(
        self, tmp_path: Path, content_type: ContentType
    ) -> None:
        """Every canonical key and every alias leaves the blob, for every type."""
        spec = DETAIL_FIELDS[content_type.value]
        blob = {
            key: _STRANDED_VALUE[field.kind]
            for field in spec.fields
            if field.column is not None
            for key in field.metadata_keys
        }
        db_path = tmp_path / "test.db"
        db_id = _seed_stranded_blob(SQLiteDB(db_path), content_type, blob)

        db = SQLiteDB(db_path)

        assert _detail_row(db, spec, db_id)["metadata"] is None

    @pytest.mark.parametrize("content_type", list(ContentType))
    def test_every_column_takes_the_value_stranded_under_its_own_key(
        self, tmp_path: Path, content_type: ContentType
    ) -> None:
        """Nothing is deleted without first landing on the column that owns it."""
        spec = DETAIL_FIELDS[content_type.value]
        columns = [field for field in spec.fields if field.column is not None]
        blob = {field.metadata_key: _STRANDED_VALUE[field.kind] for field in columns}
        db_path = tmp_path / "test.db"
        db_id = _seed_stranded_blob(SQLiteDB(db_path), content_type, blob)

        db = SQLiteDB(db_path)

        row = _detail_row(db, spec, db_id)
        assert {field.column: row[field.column] for field in columns} == {
            field.column: field.codec.store(_STRANDED_VALUE[field.kind])
            for field in columns
        }

    @pytest.mark.parametrize("content_type", list(ContentType))
    def test_a_value_stranded_under_an_alias_reaches_the_column_too(
        self, tmp_path: Path, content_type: ContentType
    ) -> None:
        """The alias spellings — ``year``, ``runtime_minutes``, ``total_seasons``.

        A row predating the column was written by whichever plugin spelled the
        key its own way, so the alias is the spelling most stranded values are
        actually under.
        """
        spec = DETAIL_FIELDS[content_type.value]
        aliased = [field for field in spec.fields if field.aliases]
        blob = {
            alias: _STRANDED_VALUE[field.kind]
            for field in aliased
            for alias in field.aliases
        }
        db_path = tmp_path / "test.db"
        db_id = _seed_stranded_blob(SQLiteDB(db_path), content_type, blob)

        db = SQLiteDB(db_path)

        row = _detail_row(db, spec, db_id)
        assert {field.column: row[field.column] for field in aliased} == {
            field.column: field.codec.store(_STRANDED_VALUE[field.kind])
            for field in aliased
        }
        assert row["metadata"] is None

    @pytest.mark.parametrize("content_type", list(ContentType))
    def test_a_second_open_changes_nothing_the_first_repaired(
        self, tmp_path: Path, content_type: ContentType
    ) -> None:
        """Idempotent for every claimed key, not just the season count.

        ``create_schema`` runs on every open, so a pass that kept finding
        something to rewrite would churn the row for the life of the database.
        """
        spec = DETAIL_FIELDS[content_type.value]
        blob = {
            key: _STRANDED_VALUE[field.kind]
            for field in spec.fields
            if field.column is not None
            for key in field.metadata_keys
        }
        db_path = tmp_path / "test.db"
        db_id = _seed_stranded_blob(SQLiteDB(db_path), content_type, blob)
        repaired = dict(_detail_row(SQLiteDB(db_path), spec, db_id))

        db = SQLiteDB(db_path)

        assert dict(_detail_row(db, spec, db_id)) == repaired

    def test_a_value_that_is_not_a_number_never_fills_an_integer_column(
        self, tmp_path: Path
    ) -> None:
        """An unreadable year is dropped rather than written to an INTEGER column.

        The fill-only path stores through the field's codec, which reads the
        value with ``int()`` — the same refusal the monotonic path makes, on
        the branch that reaches ``release_year`` rather than ``seasons``.
        """
        db_path = tmp_path / "test.db"
        db_id = _seed_movie(SQLiteDB(db_path), blob={"year": "unknown"})

        db = SQLiteDB(db_path)

        assert _movie_detail(db, db_id) == (None, None, None, None)

    def test_the_column_wins_where_the_blob_copy_used_to(self, tmp_path: Path) -> None:
        """The reader's answer changes for a key spelled the column's own way.

        ``_row_to_content_item`` applies the blob over the columns, so a
        duplicate under the canonical key was what every reader saw. Folding it
        in fill-only hands that back to the column — the value a re-sync would
        have kept — and takes the blob copy out of the reader's way for good.
        """
        db_path = tmp_path / "test.db"
        seed = SQLiteDB(db_path)
        db_id = _seed_movie(
            seed,
            blob={"studio": "WB", "release_year": 1998},
            release_year=1999,
        )
        # On the seeding handle, so the row is still stranded when the
        # migration runs on the re-open below.
        with seed.connection() as conn:
            conn.execute(
                "UPDATE movie_details SET studio = ? WHERE content_item_id = ?",
                ("Warner Bros.", db_id),
            )
            conn.commit()

        db = SQLiteDB(db_path)

        item = db.get_content_item(db_id)
        assert item is not None
        assert item.metadata["studio"] == "Warner Bros."
        assert item.metadata["release_year"] == 1999

    def test_a_stranded_value_keeps_its_non_ascii_text(self, tmp_path: Path) -> None:
        """A folded value crosses two JSON round trips and must survive both.

        The director is asserted on ``author`` rather than in the blob: it is a
        creator column, so the read path puts it on the item itself.
        """
        db_path = tmp_path / "test.db"
        db_id = _seed_movie(
            SQLiteDB(db_path),
            blob={"genre": ["Полицейский", "Science‑Fiction"], "director": "황동혁"},
        )

        db = SQLiteDB(db_path)

        item = db.get_content_item(db_id)
        assert item is not None
        assert item.metadata["genres"] == ["Полицейский", "Science‑Fiction"]
        assert item.author == "황동혁"

    def test_neither_monotonic_column_is_lowered_by_the_blob(
        self, tmp_path: Path
    ) -> None:
        """``episodes`` is monotonic as well as ``seasons``, and the pass reaches it.

        The general pass now folds every claimed key, so a rule that held only
        for ``seasons`` would silently undo a real episode count.
        """
        db_path = tmp_path / "test.db"
        seed = SQLiteDB(db_path)
        db_id = _seed_show(seed, seasons=5, blob={"total_seasons": 3, "episodes": 10})
        # On the seeding handle, so the row is still stranded when the
        # migration runs on the re-open below.
        with seed.connection() as conn:
            conn.execute(
                "UPDATE tv_show_details SET episodes = 62 WHERE content_item_id = ?",
                (db_id,),
            )
            conn.commit()

        db = SQLiteDB(db_path)

        with db.connection() as conn:
            row = conn.execute(
                "SELECT seasons, episodes, metadata FROM tv_show_details"
                " WHERE content_item_id = ?",
                (db_id,),
            ).fetchone()
        assert (row["seasons"], row["episodes"], row["metadata"]) == (5, 62, None)

    def test_a_stranded_author_becomes_the_book_export_writes(
        self, tmp_path: Path
    ) -> None:
        """A book's creator column is filled from the blob like any other.

        ``author`` reaches ``ContentItem.author`` rather than metadata, so a
        pass that dropped the key without folding it in would export a book
        with no author at all.
        """
        db_path = tmp_path / "test.db"
        db_id = _seed_stranded_blob(
            SQLiteDB(db_path),
            ContentType.BOOK,
            {"author": "Frank Herbert", "pages": 412},
        )

        db = SQLiteDB(db_path)

        item = db.get_content_item(db_id)
        assert item is not None
        assert item.author == "Frank Herbert"
        rows = list(
            csv.DictReader(io.StringIO(export_items_csv([item], ContentType.BOOK)))
        )
        assert rows[0]["author"] == "Frank Herbert"
        assert rows[0]["pages"] == "412"

    def test_a_key_name_inside_a_value_does_not_strand_the_row(
        self, tmp_path: Path
    ) -> None:
        """The ``LIKE`` prefilter matches text anywhere, and only prefilters.

        A note quoting a claimed key is selected by the scan and must survive
        it: the decision is made on the parsed blob's own keys.
        """
        db_path = tmp_path / "test.db"
        blob = {"notes": 'the "year" is disputed and "genre" is too'}
        db_id = _seed_stranded_blob(SQLiteDB(db_path), ContentType.MOVIE, blob)

        db = SQLiteDB(db_path)

        assert _movie_detail(db, db_id) == (None, None, None, blob)

    def test_a_stranded_flag_dict_is_folded_and_then_rewritten(
        self, tmp_path: Path
    ) -> None:
        """A GOG row could strand the flag dict in the blob rather than the column.

        The two passes have to run in that order: the blob pass folds the dict
        onto the empty column, and only then does the platform pass see a value
        to rewrite. Reversed, the row keeps the dict.
        """
        db_path = tmp_path / "test.db"
        db_id = _seed_stranded_blob(
            SQLiteDB(db_path),
            ContentType.VIDEO_GAME,
            {"platforms": {"windows": True, "mac": False, "linux": True}},
        )

        db = SQLiteDB(db_path)

        assert json.loads(_stored_platforms(db, db_id)) == ["Windows", "Linux"]
        spec = DETAIL_FIELDS[ContentType.VIDEO_GAME.value]
        assert _detail_row(db, spec, db_id)["metadata"] is None


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
        """A show, a movie and a game, all stranded, all corrected by one init."""
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
        movie = _seed_movie(seed, blob={"year": 1999, "runtime_minutes": 136})
        game = _seed_game(seed, platforms=_FLAGS_WINDOWS_AND_LINUX)

        db = SQLiteDB(db_path)

        assert _show_detail(db, first_show) == (5, None)
        assert _show_detail(db, second_show) == (5, {"trakt_id": 222})
        assert _movie_detail(db, movie) == (1999, 136, None, None)
        assert json.loads(_stored_platforms(db, game)) == ["Windows", "Linux"]
