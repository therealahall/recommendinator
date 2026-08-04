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

A blob copy the fold cannot account for — one disagreeing with a column that
already holds a value of its own — is left where it is rather than deleted.

Every test seeds the pre-fix shape by writing the detail row directly, because
the corrected write path can no longer produce it.
"""

import csv
import io
import json
import sqlite3
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from src.models.content import ConsumptionStatus, ContentItem, ContentType
from src.models.detail_fields import (
    DETAIL_FIELDS,
    ContentTypeFields,
    DetailField,
    FieldKind,
)
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

# Every ContentTypeFields names exactly one creator, checked when it is built,
# so a spec assembled here to exercise some other guard carries a well-formed
# one rather than tripping that check first.
_A_CREATOR = DetailField(
    "author",
    FieldKind.CREATOR,
    column="author",
    select_alias="rogue_author",
    template_column="author",
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


def _with_extra_alias(
    content_type: ContentType, metadata_key: str, alias: str
) -> dict[str, ContentTypeFields]:
    """Declare one more spelling of a field than the library has today.

    No field carries two aliases yet, so a blob holding three spellings of one
    field is only reachable by patching the declaration the pass reads. The
    fold takes its keys from there, so a rule written as "the canonical one
    and everything else" fails here and nowhere else.
    """
    spec = DETAIL_FIELDS[content_type.value]
    return {
        content_type.value: replace(
            spec,
            fields=tuple(
                (
                    replace(field, aliases=(*field.aliases, alias))
                    if field.metadata_key == metadata_key
                    else field
                )
                for field in spec.fields
            ),
        )
    }


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
        already holds. The ``year`` it lost with stays in the blob — nothing
        folded it in, so deleting it would drop a value outright — while
        ``runtime_minutes``, which the empty column took, does not.
        """
        db_path = tmp_path / "test.db"
        db_id = _seed_movie(
            SQLiteDB(db_path),
            blob={"year": 1999, "runtime_minutes": 136},
            release_year=2000,
        )

        db = SQLiteDB(db_path)

        assert _movie_detail(db, db_id) == (2000, 136, None, {"year": 1999})

    def test_a_copy_matching_its_column_is_dropped(self, tmp_path: Path) -> None:
        """The duplicate the pass exists for: two records of one value.

        The fold has nothing to do and the blob key goes, which is what takes
        the stale copy out of the reader's way for good.
        """
        db_path = tmp_path / "test.db"
        db_id = _seed_movie(SQLiteDB(db_path), blob={"year": 1999}, release_year=1999)

        db = SQLiteDB(db_path)

        assert _movie_detail(db, db_id) == (1999, None, None, None)
        item = db.get_content_item(db_id)
        assert item is not None
        assert item.metadata["release_year"] == 1999

    def test_a_copy_that_disagrees_with_its_column_is_kept(
        self, tmp_path: Path
    ) -> None:
        """A divergent pair is two claims, and the pass settles neither.

        An old CSV import's ``year`` beside a ``release_year`` TMDB filled in
        is not a duplicate of anything. Deleting it would destroy the older
        value on the first start after an upgrade, with no log, no count and
        no backup, so it survives for a human to settle — every start, not
        just the first, since the row stays in a shape the pass keeps seeing.
        """
        db_path = tmp_path / "test.db"
        db_id = _seed_movie(SQLiteDB(db_path), blob={"year": 1999}, release_year=2001)
        SQLiteDB(db_path)

        db = SQLiteDB(db_path)

        assert _movie_detail(db, db_id) == (2001, None, None, {"year": 1999})

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

    def test_a_kept_blob_copy_still_answers_the_reader(self, tmp_path: Path) -> None:
        """Leaving a divergent copy leaves the reader exactly where it was.

        ``_row_to_content_item`` applies the blob over the columns, so a copy
        under the canonical key is what every reader sees, and goes on being
        so once the pass declines to settle the divergence. That is the whole
        point of keeping it: the repair changes no answer it cannot justify,
        and the values are both still there to be looked at.
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
        assert item.metadata["studio"] == "WB"
        assert item.metadata["release_year"] == 1998
        release_year, _, _, blob = _movie_detail(db, db_id)
        assert release_year == 1999
        assert blob == {"studio": "WB", "release_year": 1998}

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


class TestBothSpellingsOfOneFieldInTheBlob:
    """A blob carrying a field's canonical key *and* its alias, disagreeing.

    The fold is decided per key. It used to be decided per field, from the one
    value ``DetailField.value_from`` picks — the canonical key when the blob
    holds it — and then every other key the field claims was deleted on the
    back of that decision, its own value never compared with anything. A
    disagreeing alias was dropped exactly the way the fold is supposed to have
    stopped dropping things.

    Every row here is a legacy shape: the write path takes every key a column
    claims out of the blob, so only a row written before the column claimed
    them can hold two at once.
    """

    def test_a_disagreeing_alias_copy_survives_beside_the_canonical_key(
        self, tmp_path: Path
    ) -> None:
        """``year`` differs from the column, so it is not a duplicate of it.

        The column already holds the canonical ``release_year``, so the fold
        has nothing to do and spends that copy. The ``year`` beside it lost to
        a differing column, which is the case the pass keeps rather than
        settles.
        """
        db_path = tmp_path / "test.db"
        db_id = _seed_movie(
            SQLiteDB(db_path),
            blob={"release_year": 2001, "year": 1999},
            release_year=2001,
        )

        db = SQLiteDB(db_path)

        assert _movie_detail(db, db_id) == (2001, None, None, {"year": 1999})

    def test_the_higher_of_two_spellings_reaches_a_monotonic_column(
        self, tmp_path: Path
    ) -> None:
        """A monotonic column must never end below a count the row held.

        ``seasons`` and ``total_seasons`` are one field, and the column is
        empty, so both counts are candidates for it. Folding only the
        canonical one in leaves the column saying 3 for a show the same row
        recorded 5 seasons of, and deletes the 5.
        """
        db_path = tmp_path / "test.db"
        db_id = _seed_show(
            SQLiteDB(db_path),
            seasons=None,
            blob={"seasons": 3, "total_seasons": 5},
        )

        db = SQLiteDB(db_path)

        assert _show_detail(db, db_id) == (5, None)

    def test_both_spellings_of_a_mergeable_field_join_the_column(
        self, tmp_path: Path
    ) -> None:
        """A mergeable column takes the union across every spelling.

        Folding only the canonical ``genres`` in and deleting the ``genre``
        beside it would drop a genre the blob was the only record of.
        """
        db_path = tmp_path / "test.db"
        db_id = _seed_movie(
            SQLiteDB(db_path),
            blob={"genres": ["Science Fiction"], "genre": ["Cyberpunk"]},
            genres=json.dumps(["Action"]),
        )

        db = SQLiteDB(db_path)

        _, _, genres, blob = _movie_detail(db, db_id)
        assert json.loads(genres) == ["Action", "Science Fiction", "Cyberpunk"]
        assert blob is None

    def test_the_canonical_spelling_fills_an_empty_column(self, tmp_path: Path) -> None:
        """Precedence is unchanged: ``value_from`` still picks what fills it.

        Judging each spelling separately must not promote an alias over the
        canonical key. The alias disagrees with the value that filled the
        column, so it is a claim of its own and stays.
        """
        db_path = tmp_path / "test.db"
        db_id = _seed_movie(
            SQLiteDB(db_path), blob={"release_year": 2001, "year": 1999}
        )

        db = SQLiteDB(db_path)

        assert _movie_detail(db, db_id) == (2001, None, None, {"year": 1999})

    def test_two_spellings_agreeing_are_both_dropped(self, tmp_path: Path) -> None:
        """Agreement is the unremarkable case, and it is unchanged.

        Both copies say what the column ends up saying, so both are spent and
        the blob empties — the behaviour a single-spelling row already had.
        """
        db_path = tmp_path / "test.db"
        db_id = _seed_movie(
            SQLiteDB(db_path), blob={"release_year": 1999, "year": 1999}
        )

        db = SQLiteDB(db_path)

        assert _movie_detail(db, db_id) == (1999, None, None, None)

    def test_one_number_written_two_ways_is_one_claim(self, tmp_path: Path) -> None:
        """A CSV import's ``"2001"`` beside an integer 2001 is not a divergence.

        Each spelling is judged after its codec has stored it, so the text and
        the number are compared as the column would hold them. Comparing the
        raw blob values instead would keep the string forever as a claim of
        its own.
        """
        db_path = tmp_path / "test.db"
        db_id = _seed_movie(
            SQLiteDB(db_path), blob={"release_year": "2001", "year": 2001}
        )

        db = SQLiteDB(db_path)

        assert _movie_detail(db, db_id) == (2001, None, None, None)

    def test_a_null_alias_is_left_where_it_is(self, tmp_path: Path) -> None:
        """A key holding null claims nothing, so nothing folds it in.

        It differs from the value that filled the column, which is the case
        the pass declines to settle, so it stays — and, because the column is
        no longer empty, the next open finds nothing to spend either.
        """
        db_path = tmp_path / "test.db"
        db_id = _seed_movie(
            SQLiteDB(db_path), blob={"release_year": 2001, "year": None}
        )
        SQLiteDB(db_path)

        db = SQLiteDB(db_path)

        assert _movie_detail(db, db_id) == (2001, None, None, {"year": None})

    def test_overlapping_lists_from_two_spellings_are_deduplicated(
        self, tmp_path: Path
    ) -> None:
        """The union is a union: a genre named twice is stored once.

        Both spellings and the column overlap, so a fold that concatenated
        rather than merged would leave the column repeating "Sci-Fi" in two
        capitalisations.
        """
        db_path = tmp_path / "test.db"
        db_id = _seed_movie(
            SQLiteDB(db_path),
            blob={"genres": ["Action", "Sci-Fi"], "genre": ["sci-fi", "Cyberpunk"]},
            genres=json.dumps(["Action"]),
        )

        db = SQLiteDB(db_path)

        _, _, genres, blob = _movie_detail(db, db_id)
        assert json.loads(genres) == ["Action", "Sci-Fi", "Cyberpunk"]
        assert blob is None

    def test_both_spellings_fill_an_empty_mergeable_column_together(
        self, tmp_path: Path
    ) -> None:
        """An empty mergeable column takes the union too, not the first value.

        Filling it from ``value_from`` alone would store only the canonical
        spelling's genres and delete the alias's beside it.
        """
        db_path = tmp_path / "test.db"
        db_id = _seed_movie(
            SQLiteDB(db_path),
            blob={"genres": ["Action"], "genre": ["action", "Drama"]},
        )

        db = SQLiteDB(db_path)

        _, _, genres, blob = _movie_detail(db, db_id)
        assert json.loads(genres) == ["Action", "Drama"]
        assert blob is None

    def test_a_fill_only_list_column_spends_the_spelling_it_matches(
        self, tmp_path: Path
    ) -> None:
        """``platforms`` is neither mergeable nor monotonic, and has an alias.

        A bare string and a one-name list are the same claim once the codec
        has stored them, so both copies go; nothing is left repeating the
        column.
        """
        db_path = tmp_path / "test.db"
        db_id = _seed_stranded_blob(
            SQLiteDB(db_path),
            ContentType.VIDEO_GAME,
            {"platforms": ["Windows"], "platform": "Windows"},
        )

        db = SQLiteDB(db_path)

        spec = DETAIL_FIELDS[ContentType.VIDEO_GAME.value]
        assert json.loads(_stored_platforms(db, db_id)) == ["Windows"]
        assert _detail_row(db, spec, db_id)["metadata"] is None

    def test_a_fill_only_list_column_keeps_the_spelling_it_disagrees_with(
        self, tmp_path: Path
    ) -> None:
        """The same column, and the divergence it must not resolve by deleting.

        ``platform`` names a system the column does not, and the fold has no
        union rule to reach for here, so the copy survives the pass.
        """
        db_path = tmp_path / "test.db"
        db_id = _seed_stranded_blob(
            SQLiteDB(db_path),
            ContentType.VIDEO_GAME,
            {"platforms": ["Windows"], "platform": "Linux"},
        )

        db = SQLiteDB(db_path)

        spec = DETAIL_FIELDS[ContentType.VIDEO_GAME.value]
        blob = _detail_row(db, spec, db_id)["metadata"]
        assert json.loads(_stored_platforms(db, db_id)) == ["Windows"]
        assert json.loads(blob) == {"platform": "Linux"}


class TestThreeSpellingsOfOneField:
    """One field spelled three ways in a blob the fold has to take apart.

    The declaration gives every field at most one alias today, so a fold that
    happened to work only for a canonical key and one other would pass every
    test above. These patch a second alias in, which is what an added
    spelling will look like, and each spelling is judged on its own value.
    """

    def test_only_the_spellings_the_column_took_are_dropped(
        self, tmp_path: Path
    ) -> None:
        """Fill-only, with one spelling repeating the column and one differing.

        ``released`` says what the column ended up saying and goes;
        ``year`` says something else and stays. Deleting every spelling behind
        one decision destroys the 1999.
        """
        db_path = tmp_path / "test.db"
        db_id = _seed_movie(
            SQLiteDB(db_path),
            blob={"release_year": 2001, "year": 1999, "released": 2001},
        )

        with patch.dict(
            DETAIL_FIELDS,
            _with_extra_alias(ContentType.MOVIE, "release_year", "released"),
        ):
            db = SQLiteDB(db_path)

        assert _movie_detail(db, db_id) == (2001, None, None, {"year": 1999})

    def test_the_first_spelling_present_fills_an_empty_column(
        self, tmp_path: Path
    ) -> None:
        """No canonical key at all: two aliases, and the earlier one wins.

        ``value_from`` reads the canonical key then the aliases in order, so a
        blob holding only aliases still fills the column from the first of
        them — and the second is a claim of its own, not a duplicate.
        """
        db_path = tmp_path / "test.db"
        db_id = _seed_movie(SQLiteDB(db_path), blob={"year": 1999, "released": 2001})

        with patch.dict(
            DETAIL_FIELDS,
            _with_extra_alias(ContentType.MOVIE, "release_year", "released"),
        ):
            db = SQLiteDB(db_path)

        assert _movie_detail(db, db_id) == (1999, None, None, {"released": 2001})

    def test_a_monotonic_column_ends_at_the_highest_of_three(
        self, tmp_path: Path
    ) -> None:
        """Every count in the row is weighed, whichever way it is spelled.

        Weighing the canonical ``seasons`` alone settles the column at 3 for a
        row that recorded 5, and deletes both larger counts on the way out.
        """
        db_path = tmp_path / "test.db"
        db_id = _seed_show(
            SQLiteDB(db_path),
            seasons=2,
            blob={"seasons": 3, "total_seasons": 5, "season_count": 4},
        )

        with patch.dict(
            DETAIL_FIELDS,
            _with_extra_alias(ContentType.TV_SHOW, "seasons", "season_count"),
        ):
            db = SQLiteDB(db_path)

        assert _show_detail(db, db_id) == (5, None)

    def test_a_monotonic_column_stays_above_every_spelling(
        self, tmp_path: Path
    ) -> None:
        """A column a later sync raised is still never lowered by a spelling."""
        db_path = tmp_path / "test.db"
        db_id = _seed_show(
            SQLiteDB(db_path),
            seasons=7,
            blob={"seasons": 3, "total_seasons": 5, "season_count": 4},
        )

        with patch.dict(
            DETAIL_FIELDS,
            _with_extra_alias(ContentType.TV_SHOW, "seasons", "season_count"),
        ):
            db = SQLiteDB(db_path)

        assert _show_detail(db, db_id) == (7, None)

    def test_an_unreadable_spelling_does_not_block_a_readable_one(
        self, tmp_path: Path
    ) -> None:
        """A count no reader can use never hides the count beside it.

        ``seasons`` is read with ``int()`` everywhere, so the text is refused
        — but refusing it must not cost the row the 5 spelled another way.
        """
        db_path = tmp_path / "test.db"
        db_id = _seed_show(
            SQLiteDB(db_path),
            seasons=None,
            blob={"seasons": "unknown", "total_seasons": 5, "season_count": None},
        )

        with patch.dict(
            DETAIL_FIELDS,
            _with_extra_alias(ContentType.TV_SHOW, "seasons", "season_count"),
        ):
            db = SQLiteDB(db_path)

        assert _show_detail(db, db_id) == (5, None)

    def test_a_mergeable_column_unions_all_three_spellings(
        self, tmp_path: Path
    ) -> None:
        """The union runs across every spelling, and the column keeps its own.

        Each spelling names a genre the others do not, so a fold that stopped
        at the canonical key would drop two of them.
        """
        db_path = tmp_path / "test.db"
        db_id = _seed_movie(
            SQLiteDB(db_path),
            blob={
                "genres": ["Sci-Fi"],
                "genre": ["Cyberpunk"],
                "categories": ["action", "Noir"],
            },
            genres=json.dumps(["Action"]),
        )

        with patch.dict(
            DETAIL_FIELDS,
            _with_extra_alias(ContentType.MOVIE, "genres", "categories"),
        ):
            db = SQLiteDB(db_path)

        _, _, genres, blob = _movie_detail(db, db_id)
        assert json.loads(genres) == ["Action", "Sci-Fi", "Cyberpunk", "Noir"]
        assert blob is None

    def test_a_second_open_leaves_the_kept_spelling_alone(self, tmp_path: Path) -> None:
        """The row the pass half-repaired must not churn on every later open."""
        db_path = tmp_path / "test.db"
        db_id = _seed_movie(
            SQLiteDB(db_path),
            blob={"release_year": 2001, "year": 1999, "released": 2001},
        )

        with patch.dict(
            DETAIL_FIELDS,
            _with_extra_alias(ContentType.MOVIE, "release_year", "released"),
        ):
            SQLiteDB(db_path)
            db = SQLiteDB(db_path)

        assert _movie_detail(db, db_id) == (2001, None, None, {"year": 1999})


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


class TestMigrationIdentifiersAreGuarded:
    """The pass builds SQL from the declaration, and validates it first.

    ``_fold_stranded_column_keys`` and the UPDATE it drives interpolate a
    table name and column names straight out of ``DETAIL_FIELDS``, the same
    identifier source the joined SELECT guards. Nothing else holds these
    checks in place, so without them the guard could be deleted without a
    test going red. Opening a database is the trigger because the pass runs
    from ``create_schema``.
    """

    def test_table_outside_the_allow_list_is_rejected(self, tmp_path: Path) -> None:
        """A detail table nobody allow-listed never reaches a FROM clause."""
        rogue = ContentTypeFields(
            table="rogue_details; DROP TABLE content_items; --",
            table_alias="rd",
            metadata_alias="rogue_metadata",
            fields=(
                _A_CREATOR,
                DetailField(
                    "title", FieldKind.TEXT, column="title", select_alias="rogue_title"
                ),
            ),
        )

        with patch.dict(DETAIL_FIELDS, {"rogue": rogue}):
            with pytest.raises(ValueError, match="Unknown detail table"):
                SQLiteDB(tmp_path / "test.db")

    def test_unsafe_column_name_is_rejected(self, tmp_path: Path) -> None:
        """A column name outside the identifier pattern raises."""
        rogue = replace(
            DETAIL_FIELDS["book"],
            fields=(
                _A_CREATOR,
                DetailField("evil", FieldKind.TEXT, column="isbn FROM users; --"),
            ),
        )

        with patch.dict(DETAIL_FIELDS, {"book": rogue}):
            with pytest.raises(ValueError, match="Unsafe SQL identifier"):
                SQLiteDB(tmp_path / "test.db")


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
