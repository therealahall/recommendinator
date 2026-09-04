"""``src/models/detail_fields.py`` is the one place a detail-table field is
described, and storage, the import templates and the export all derive their
column lists from it."""

import json
import sqlite3
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from src.models import detail_fields
from src.models.content import ConsumptionStatus, ContentItem, ContentType
from src.models.detail_fields import (
    DETAIL_FIELDS,
    DetailField,
    FieldKind,
)
from src.storage import sqlite_db
from src.storage.schema import create_schema
from src.storage.sqlite_db import SQLiteDB

_UNDECLARED_COLUMNS = {"content_item_id", "metadata"}

_SAMPLES: dict[FieldKind, tuple[Any, Any]] = {
    FieldKind.CREATOR: ("Sample Creator", "Sample Creator"),
    FieldKind.TEXT: ("Sample Text", "Sample Text"),
    FieldKind.INTEGER: (7, 7),
    FieldKind.STRING_LIST: ("Noir", '["Noir"]'),
}

_READ_BACK: dict[FieldKind, Any] = {
    FieldKind.CREATOR: "Sample Creator",
    FieldKind.TEXT: "Sample Text",
    FieldKind.INTEGER: 7,
    FieldKind.STRING_LIST: ["Noir"],
}

_UNCLAIMED_KEY = "übersetzung"
_UNCLAIMED_VALUE = "Ursula K. Le Guin ✨"


def _live_columns(tmp_path: Path, table: str) -> dict[str, str]:
    conn = sqlite3.connect(tmp_path / "schema.db")
    try:
        create_schema(conn)
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    finally:
        conn.close()
    return {row["name"]: row["type"] for row in rows}


class TestDeclarationMatchesLiveSchema:
    @pytest.mark.parametrize("content_type", sorted(DETAIL_FIELDS))
    def test_declared_columns_are_the_live_columns(
        self, tmp_path: Path, content_type: str
    ) -> None:
        spec = DETAIL_FIELDS[content_type]

        live = _live_columns(tmp_path, spec.table)

        assert set(live) == set(spec.columns) | _UNDECLARED_COLUMNS


class TestAliasesReachTheirColumns:
    """A value arriving under a spelling storage does not know for that column is
    dropped into the leftover JSON blob, where nothing reads it back."""

    def test_canonical_key_wins_over_its_alias(self, tmp_path: Path) -> None:
        """An enriched movie can arrive with the provider's ``year`` beside the
        library's ``release_year``, and only one of them reaches the column."""
        db = SQLiteDB(tmp_path / "precedence.db")

        db_id = db.save_content_item(
            ContentItem(
                id="both-spellings",
                title="Arrival",
                content_type=ContentType.MOVIE,
                status=ConsumptionStatus.UNREAD,
                metadata={"release_year": 2016, "year": 1999},
            )
        )

        with db.connection() as conn:
            row = conn.execute(
                "SELECT release_year, metadata FROM movie_details"
                " WHERE content_item_id = ?",
                (db_id,),
            ).fetchone()

        assert row["release_year"] == 2016
        assert json.loads(row["metadata"] or "{}") == {}

        stored = db.get_content_item(db_id)
        assert stored is not None
        assert stored.metadata["release_year"] == 2016
        assert "year" not in stored.metadata

    def test_alias_fills_in_when_the_canonical_key_is_empty(
        self, tmp_path: Path
    ) -> None:
        db = SQLiteDB(tmp_path / "fallback.db")

        db_id = db.save_content_item(
            ContentItem(
                id="empty-canonical",
                title="Arrival",
                content_type=ContentType.MOVIE,
                status=ConsumptionStatus.UNREAD,
                metadata={"release_year": None, "year": 2016},
            )
        )

        stored = db.get_content_item(db_id)

        assert stored is not None
        assert stored.metadata["release_year"] == 2016


class TestDeclaringOneFieldIsEnough:
    def test_added_field_round_trips_through_save_and_read(
        self, tmp_path: Path
    ) -> None:
        """The join is rebuilt only because it is derived once at import, before
        this test patches anything."""
        db = SQLiteDB(tmp_path / "library.db")
        with db.connection() as conn:
            conn.execute("ALTER TABLE book_details ADD COLUMN translator TEXT")
            conn.commit()

        extended = replace(
            DETAIL_FIELDS["book"],
            fields=(
                *DETAIL_FIELDS["book"].fields,
                DetailField(
                    "translator",
                    FieldKind.TEXT,
                    column="translator",
                    select_alias="book_translator",
                ),
            ),
        )

        with patch.dict(DETAIL_FIELDS, {"book": extended}):
            with patch.object(
                sqlite_db,
                "_CONTENT_ITEM_SELECT",
                sqlite_db._build_content_item_select(),
            ):
                db_id = db.save_content_item(
                    ContentItem(
                        id="new-field",
                        title="Kalpa Imperial",
                        content_type=ContentType.BOOK,
                        status=ConsumptionStatus.UNREAD,
                        metadata={"translator": "Ursula K. Le Guin"},
                    )
                )
                stored = db.get_content_item(db_id)

        assert stored is not None
        assert stored.metadata["translator"] == "Ursula K. Le Guin"

        with db.connection() as conn:
            blob = conn.execute(
                "SELECT metadata FROM book_details WHERE content_item_id = ?",
                (db_id,),
            ).fetchone()["metadata"]

        assert "translator" not in json.loads(blob or "{}")


class TestSingleStringColumnsCoerceWhatTheyAreGiven:
    """``to_text`` is the codec behind every CREATOR and TEXT column, and a plugin
    is free to hand over a number where the column holds text."""

    def test_a_number_is_stored_as_its_own_text(self, tmp_path: Path) -> None:
        db = SQLiteDB(tmp_path / "isbn.db")

        db_id = db.save_content_item(
            ContentItem(
                id="numeric-isbn",
                title="Dune",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.UNREAD,
                metadata={"isbn": 9780441013593},
            )
        )
        stored = db.get_content_item(db_id)

        assert stored is not None
        assert stored.metadata["isbn"] == "9780441013593"

    def test_an_empty_entry_never_reaches_the_column(self, tmp_path: Path) -> None:
        """``generic_json`` forwards a list value entry by entry, so a user's file
        can hand over ``["", "CD Projekt Red"]``."""
        db = SQLiteDB(tmp_path / "empty-entry.db")

        db_id = db.save_content_item(
            ContentItem(
                id="1207658924",
                title="Cyberpunk 2077",
                content_type=ContentType.VIDEO_GAME,
                status=ConsumptionStatus.UNREAD,
                metadata={"developer": ["", "CD Projekt Red"]},
            )
        )

        with db.connection() as conn:
            row = conn.execute(
                "SELECT developer FROM video_game_details WHERE content_item_id = ?",
                (db_id,),
            ).fetchone()
        assert row["developer"] == "CD Projekt Red"


class TestATextColumnRefusesAValueItCannotHold:
    """``to_text`` used to have a string for every input, ``str()`` of a dict
    included, and it sits behind every CREATOR and TEXT column."""

    def test_an_imported_object_never_reaches_the_column(self, tmp_path: Path) -> None:
        """``generic_json`` forwards a raw value, so a user can send one."""
        db = SQLiteDB(tmp_path / "object-isbn.db")

        with pytest.raises(TypeError, match="text column cannot hold"):
            db.save_content_item(
                ContentItem(
                    id="object-isbn",
                    title="Dune",
                    content_type=ContentType.BOOK,
                    status=ConsumptionStatus.UNREAD,
                    metadata={"isbn": {"value": "9780441013593"}},
                )
            )


class TestAListColumnRefusesAValueItCannotHold:
    """``to_json_array`` used to wrap whatever it was handed, and genres merge
    rather than being replaced, so the shape never left the column again."""

    def test_an_imported_object_never_reaches_the_column(self, tmp_path: Path) -> None:
        """``generic_json`` forwards a raw value, so a user's file can send one."""
        db = SQLiteDB(tmp_path / "object-genre.db")

        with pytest.raises(TypeError, match="'genres': a list column cannot hold"):
            db.save_content_item(
                ContentItem(
                    id="object-genre",
                    title="Fargo",
                    content_type=ContentType.MOVIE,
                    status=ConsumptionStatus.UNREAD,
                    metadata={"genre": [{"id": 80, "name": "Crime"}]},
                )
            )

        assert db.get_content_items() == []


class TestWhatAListColumnStillAccepts:
    """Every source that has genres writes them, so a guard reaching one shape too
    far would fail a sync rather than an object."""

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (["RPG"], '["RPG"]'),
            ("Drama", '["Drama"]'),
            ('["Drama"]', '["Drama"]'),
            (None, None),
            ([], "[]"),
        ],
        ids=[
            "list_of_names",
            "bare_name",
            "array_already",
            "null",
            "empty_list",
        ],
    )
    def test_a_value_a_list_column_can_hold_is_serialised_as_before(
        self, value: Any, expected: str | None
    ) -> None:
        """None stored as "[null]" would merge into every later sync's genres."""
        assert detail_fields.to_json_array(value) == expected


class TestWhatATextColumnStillAccepts:
    """``to_text`` is the codec behind every CREATOR and TEXT column and every
    shipped plugin writes at least one of them, so a guard that reached one shape
    too far would fail a sync rather than a repr."""

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ([], None),
            (["Joel Coen", None, "Ethan Coen"], "Joel Coen, Ethan Coen"),
            (["", "Ethan Coen"], "Ethan Coen"),
            (["  Ethan Coen  "], "Ethan Coen"),
            ("", None),
            (7, "7"),
        ],
        ids=[
            "empty_list",
            "names_around_a_null",
            "empty_string_among_names",
            "padded_name",
            "bare_empty_string",
            "number",
        ],
    )
    def test_a_value_with_a_text_form_is_still_flattened(
        self, value: Any, expected: str | None
    ) -> None:
        """Naming nothing is None whichever shape it arrives in, and an entry naming
        nothing is dropped from the join rather than left as a comma in front of the
        next name."""
        assert detail_fields.to_text(value) == expected


class TestReducingAKnownShapeToNames:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("CD Projekt Red", ["CD Projekt Red"]),
            ([{"name": "CD Projekt Red"}], ["CD Projekt Red"]),
            ([{"slug": "cdpr"}], []),
            ([["CD Projekt Red"]], []),
            (None, []),
        ],
        ids=[
            "bare_name",
            "list_of_objects",
            "object_naming_nothing",
            "nested_list",
            "null",
        ],
    )
    def test_only_a_name_a_text_column_can_hold_survives(
        self, value: Any, expected: list[str]
    ) -> None:
        assert detail_fields.text_names(value) == expected

    def test_a_reduced_shape_reaches_no_column_as_a_repr(self, tmp_path: Path) -> None:
        db = SQLiteDB(tmp_path / "no-repr.db")

        db_id = db.save_content_item(
            ContentItem(
                id="1207658924",
                title="Cyberpunk 2077",
                content_type=ContentType.VIDEO_GAME,
                status=ConsumptionStatus.UNREAD,
                metadata={
                    "developer": detail_fields.text_names(
                        [["Nested"], {"name": {"text": "Deep"}}, "Valve"]
                    )
                },
            )
        )

        with db.connection() as conn:
            row = conn.execute(
                "SELECT developer, metadata FROM video_game_details"
                " WHERE content_item_id = ?",
                (db_id,),
            ).fetchone()
        assert row["developer"] == "Valve"
        assert "Nested" not in (row["metadata"] or "")
        assert "Deep" not in (row["metadata"] or "")


class TestReadingAListColumnBack:
    """The read codec answers with names, whatever the column was filled with."""

    @pytest.mark.parametrize(
        ("stored", "expected"),
        [
            ('["Crime", "Drama"]', ["Crime", "Drama"]),
            ('[{"id": 80, "name": "Crime"}, "Drama"]', ["Crime", "Drama"]),
            ('[{"id": 80}]', []),
        ],
        ids=["names", "object_naming_itself", "object_naming_nothing"],
    )
    def test_an_element_is_read_back_as_the_name_it_carries(
        self, stored: str, expected: list[str]
    ) -> None:
        assert detail_fields.parse_json_array(stored) == expected


class TestARefusedWriteLeavesNothingBehind:
    """``_save_detail_table`` runs after the ``content_items`` row is written and
    inside the same connection, so the raise crosses a transaction that has
    already inserted."""

    def test_a_new_item_is_not_left_without_its_detail_row(
        self, tmp_path: Path
    ) -> None:
        db = SQLiteDB(tmp_path / "refused.db")

        with pytest.raises(TypeError, match="text column cannot hold"):
            db.save_content_item(
                ContentItem(
                    id="object-isbn",
                    title="Dune",
                    content_type=ContentType.BOOK,
                    status=ConsumptionStatus.UNREAD,
                    metadata={"isbn": {"value": "9780441013593"}},
                )
            )

        with db.connection() as conn:
            items = conn.execute("SELECT COUNT(*) AS n FROM content_items").fetchone()
            details = conn.execute("SELECT COUNT(*) AS n FROM book_details").fetchone()
        assert (items["n"], details["n"]) == (0, 0)

    def test_a_stored_item_is_untouched_by_a_refused_re_sync(
        self, tmp_path: Path
    ) -> None:
        db = SQLiteDB(tmp_path / "refused-resync.db")
        db_id = db.save_content_item(
            ContentItem(
                id="dune",
                title="Dune",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.UNREAD,
                metadata={"isbn": "9780441013593"},
            )
        )

        with pytest.raises(TypeError, match="text column cannot hold"):
            db.save_content_item(
                ContentItem(
                    id="dune",
                    title="Dune Reprint",
                    content_type=ContentType.BOOK,
                    status=ConsumptionStatus.COMPLETED,
                    metadata={"isbn": {"value": "9780441013593"}},
                )
            )

        stored = db.get_content_item(db_id)
        assert stored is not None
        assert stored.title == "Dune"
        assert stored.status == ConsumptionStatus.UNREAD
        assert stored.metadata["isbn"] == "9780441013593"


class TestNullInAListOfNamesRegression:
    """Bug reported: a movie whose director list carried a null — any plugin list
    can — stored "Joel Coen, None" and exported that into the director cell, so
    "None" read as part of the name."""

    def test_a_null_among_the_creators_is_dropped_regression(
        self, tmp_path: Path
    ) -> None:
        db = SQLiteDB(tmp_path / "null-creator.db")

        db_id = db.save_content_item(
            ContentItem(
                id="movie-null-director",
                title="Fargo",
                content_type=ContentType.MOVIE,
                status=ConsumptionStatus.UNREAD,
                metadata={"director": ["Joel Coen", None]},
            )
        )
        stored = db.get_content_item(db_id)

        assert stored is not None
        assert stored.author == "Joel Coen"

    def test_a_list_holding_only_nulls_stores_nothing_regression(
        self, tmp_path: Path
    ) -> None:
        db = SQLiteDB(tmp_path / "all-null-creator.db")

        db_id = db.save_content_item(
            ContentItem(
                id="movie-all-null-directors",
                title="Anonymous",
                content_type=ContentType.MOVIE,
                status=ConsumptionStatus.UNREAD,
                metadata={"director": [None]},
            )
        )
        stored = db.get_content_item(db_id)

        assert stored is not None
        assert stored.author is None


class TestSelectAliasesDoNotShadowContentItems:
    """A detail alias spelled like a content_items column resolves to whichever the
    driver saw first rather than raising, so ``_row_to_content_item`` would read
    a title or a rating where it asked for a detail value."""

    def test_no_detail_alias_matches_a_content_items_column(
        self, tmp_path: Path
    ) -> None:
        base_columns = set(_live_columns(tmp_path, "content_items"))

        aliases = {
            detail_field.select_alias or detail_field.column
            for spec in DETAIL_FIELDS.values()
            for detail_field in spec.fields
            if detail_field.column is not None
        } | {spec.metadata_alias for spec in DETAIL_FIELDS.values()}

        assert aliases & base_columns == set()


class TestEveryDeclaredColumnRoundTrips:
    """The write path, the joined SELECT and the read path are three separate
    derivations of one declaration."""

    @pytest.mark.parametrize("content_type", sorted(DETAIL_FIELDS))
    def test_all_columns_survive_save_and_read(
        self, tmp_path: Path, content_type: str
    ) -> None:
        spec = DETAIL_FIELDS[content_type]
        db = SQLiteDB(tmp_path / f"round-trip-{content_type}.db")
        metadata: dict[str, Any] = {
            detail_field.metadata_key: _SAMPLES[detail_field.kind][0]
            for detail_field in spec.fields
            if detail_field.column is not None
        }
        metadata[_UNCLAIMED_KEY] = _UNCLAIMED_VALUE

        db_id = db.save_content_item(
            ContentItem(
                id=f"round-trip-{content_type}",
                title="Round Trip",
                content_type=ContentType(content_type),
                status=ConsumptionStatus.UNREAD,
                metadata=metadata,
            )
        )
        stored = db.get_content_item(db_id)

        expected = {_UNCLAIMED_KEY: _UNCLAIMED_VALUE}
        expected_creator: str | None = None
        for detail_field in spec.fields:
            if detail_field.column is None:
                continue
            if detail_field.kind is FieldKind.CREATOR:
                expected_creator = _READ_BACK[detail_field.kind]
                continue
            expected[detail_field.metadata_key] = _READ_BACK[detail_field.kind]

        assert stored is not None
        assert stored.metadata == expected
        assert stored.author == expected_creator

        with db.connection() as conn:
            blob = conn.execute(
                f"SELECT metadata FROM {spec.table} WHERE content_item_id = ?",
                (db_id,),
            ).fetchone()["metadata"]

        assert json.loads(blob) == {_UNCLAIMED_KEY: _UNCLAIMED_VALUE}


class TestLeftoverBlobWinsOverItsColumn:
    """Rows written before a field had a column of its own carry that key in the
    free-form blob, and the read path merges the blob after the columns."""

    def test_blob_value_wins_over_the_column(self, tmp_path: Path) -> None:
        db = SQLiteDB(tmp_path / "legacy-blob.db")
        db_id = db.save_content_item(
            ContentItem(
                id="legacy-blob",
                title="Arrival",
                content_type=ContentType.MOVIE,
                status=ConsumptionStatus.UNREAD,
                metadata={"studio": "Column Pictures"},
            )
        )

        with db.connection() as conn:
            conn.execute(
                "UPDATE movie_details SET metadata = ? WHERE content_item_id = ?",
                (json.dumps({"studio": "Blob Pictures"}), db_id),
            )
            conn.commit()

        stored = db.get_content_item(db_id)

        assert stored is not None
        assert stored.metadata["studio"] == "Blob Pictures"
