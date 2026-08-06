"""Tests for the single per-content-type field declaration.

``src/models/detail_fields.py`` is the one place a detail-table field is
described, and storage, the import templates and the export all derive their
column lists from it. What that buys is checked here: the declaration agrees
with the hand-written ``CREATE TABLE`` statements, every metadata-key alias a
field accepts reaches its column instead of the leftover JSON blob, and adding
a field is an edit to the declaration alone.
"""

import ast
import importlib.util
import json
import sqlite3
import sys
from dataclasses import replace
from enum import Enum
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from src.ingestion.sources.generic_csv import CREATOR_COLUMNS, LIST_VALUED_COLUMNS
from src.models import detail_fields
from src.models.content import ConsumptionStatus, ContentItem, ContentType
from src.models.detail_fields import (
    DETAIL_FIELDS,
    ContentTypeFields,
    DetailField,
    FieldKind,
    _assert_every_content_type_is_declared,
    _assert_select_aliases_are_unique,
)
from src.storage import derived, sqlite_db
from src.storage.merge import detail_join
from src.storage.schema import create_schema
from src.storage.sqlite_db import SQLiteDB

# Columns every detail table carries whatever the content type: the foreign
# key and the free-form blob holding whatever no column claimed.
_UNDECLARED_COLUMNS = {"content_item_id", "metadata"}

# The SQL type each kind of field is declared with in schema.py.
_SQL_TYPES: dict[FieldKind, str] = {
    FieldKind.CREATOR: "TEXT",
    FieldKind.TEXT: "TEXT",
    FieldKind.INTEGER: "INTEGER",
    FieldKind.STRING_LIST: "TEXT",
}

# A value of each kind, with what its column holds once stored.
_SAMPLES: dict[FieldKind, tuple[Any, Any]] = {
    FieldKind.CREATOR: ("Sample Creator", "Sample Creator"),
    FieldKind.TEXT: ("Sample Text", "Sample Text"),
    FieldKind.INTEGER: (7, 7),
    FieldKind.STRING_LIST: ("Noir", '["Noir"]'),
}

# What each kind's sample reads back as, which is the stored form only for
# the kinds whose codec parses nothing on the way out.
_READ_BACK: dict[FieldKind, Any] = {
    FieldKind.CREATOR: "Sample Creator",
    FieldKind.TEXT: "Sample Text",
    FieldKind.INTEGER: 7,
    FieldKind.STRING_LIST: ["Noir"],
}

# A metadata key no column claims, so it belongs in the free-form blob.
# Non-ASCII on both sides: the blob is JSON, and a key or value mangled by
# the encode/decode round trip would come back as a different key.
_UNCLAIMED_KEY = "übersetzung"
_UNCLAIMED_VALUE = "Ursula K. Le Guin ✨"

# Every ContentTypeFields names exactly one creator, so a spec built here to
# exercise some other guard carries a well-formed one.
_A_CREATOR = DetailField(
    "author",
    FieldKind.CREATOR,
    column="author",
    select_alias="rogue_author",
    template_column="author",
)


def _import_the_declaration_module_afresh() -> None:
    """Execute ``src/models/detail_fields.py`` again, as a separate module.

    A fresh module rather than ``importlib.reload``, which would rebind the
    live one — the module storage, ingestion and export all hold references
    into — to new objects part way through an import that is meant to fail.
    """
    spec = importlib.util.spec_from_file_location(
        "detail_fields_afresh", detail_fields.__file__
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Registered while it executes because the dataclasses in it resolve
    # their annotations through sys.modules, and dropped again after.
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        del sys.modules[spec.name]


def _live_columns(tmp_path: Path, table: str) -> dict[str, str]:
    """Read a freshly created table's columns and SQL types back."""
    conn = sqlite3.connect(tmp_path / "schema.db")
    try:
        create_schema(conn)
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    finally:
        conn.close()
    return {row["name"]: row["type"] for row in rows}


# Every (content type, field, alias) the declaration accepts on write.
_ALIASED_FIELDS: list[tuple[str, DetailField, str]] = [
    (content_type, detail_field, alias)
    for content_type, spec in DETAIL_FIELDS.items()
    for detail_field in spec.fields
    for alias in detail_field.aliases
]

# Every alias the plugins rely on, as (content type, column key, alias).
# Written out rather than derived, because the parametrized alias tests below
# take their cases from the declaration and so cannot notice one going
# missing from it.
_EXPECTED_ALIASES: frozenset[tuple[str, str, str]] = frozenset(
    {
        ("book", "genres", "genre"),
        ("movie", "genres", "genre"),
        ("movie", "release_year", "year"),
        ("movie", "runtime", "runtime_minutes"),
        ("tv_show", "creators", "creator"),
        ("tv_show", "genres", "genre"),
        ("tv_show", "release_year", "year"),
        ("tv_show", "seasons", "total_seasons"),
        ("video_game", "developer", "developers"),
        ("video_game", "genres", "genre"),
        ("video_game", "platforms", "platform"),
        ("video_game", "publisher", "publishers"),
    }
)

# The metadata keys each type's columns consume, aliases included. Written out
# rather than derived, because ``known_keys`` is itself derived now and storage
# drops every key in it from the free-form blob: a key joining the set without
# a column reading it disappears on save, and one leaving it is written twice,
# to its column and to the blob the read path merges last.
_EXPECTED_KNOWN_KEYS: dict[str, frozenset[str]] = {
    "book": frozenset(
        {
            "author",
            "isbn",
            "isbn13",
            "pages",
            "publisher",
            "year_published",
            "genres",
            "genre",
            "tags",
            "description",
        }
    ),
    "movie": frozenset(
        {
            "director",
            "release_year",
            "year",
            "runtime",
            "runtime_minutes",
            "genres",
            "genre",
            "studio",
            "tags",
            "description",
        }
    ),
    "tv_show": frozenset(
        {
            "seasons",
            "total_seasons",
            "release_year",
            "year",
            "genres",
            "genre",
            "creators",
            "creator",
            "episodes",
            "network",
            "tags",
            "description",
        }
    ),
    "video_game": frozenset(
        {
            "developer",
            "developers",
            "platforms",
            "platform",
            "genres",
            "genre",
            "publisher",
            "publishers",
            "release_year",
            "tags",
            "description",
        }
    ),
}


class TestDeclarationMatchesLiveSchema:
    """The declaration and the hand-written CREATE TABLE statements agree.

    The DDL in ``schema.py`` is deliberately not generated from the
    declaration — generating it would put the migration path at risk — so
    this is what catches a field declared against a column that does not
    exist, or a column no field claims.
    """

    @pytest.mark.parametrize("content_type", sorted(DETAIL_FIELDS))
    def test_declared_columns_are_the_live_columns(
        self, tmp_path: Path, content_type: str
    ) -> None:
        """Each detail table holds exactly the declared columns."""
        spec = DETAIL_FIELDS[content_type]

        live = _live_columns(tmp_path, spec.table)

        assert set(live) == set(spec.columns) | _UNDECLARED_COLUMNS

    @pytest.mark.parametrize("content_type", sorted(DETAIL_FIELDS))
    def test_declared_kinds_match_the_live_column_types(
        self, tmp_path: Path, content_type: str
    ) -> None:
        """An INTEGER field is an INTEGER column, and the rest are TEXT."""
        spec = DETAIL_FIELDS[content_type]
        live = _live_columns(tmp_path, spec.table)

        declared = {
            detail_field.column: _SQL_TYPES[detail_field.kind]
            for detail_field in spec.fields
            if detail_field.column is not None
        }

        assert {name: live[name] for name in declared} == declared


class TestAliasesReachTheirColumns:
    """Every metadata key a field accepts lands in that field's column.

    The plugins do not all agree on a spelling — "genre" for "genres",
    "total_seasons" for "seasons", "year" for "release_year" — and a value
    arriving under an alias storage does not know for that column is dropped
    into the leftover JSON blob, where nothing reads it back as that field.
    Driven off the declaration so an alias added later is covered too.
    """

    def test_the_declared_aliases_are_the_expected_ones(self) -> None:
        """No plugin spelling is dropped, and none is invented."""
        declared = {
            (content_type, detail_field.metadata_key, alias)
            for content_type, detail_field, alias in _ALIASED_FIELDS
        }

        assert declared == set(_EXPECTED_ALIASES)

    @pytest.mark.parametrize(
        ("content_type", "detail_field", "alias"),
        _ALIASED_FIELDS,
        ids=[
            f"{content_type}-{alias}" for content_type, _field, alias in _ALIASED_FIELDS
        ],
    )
    def test_alias_populates_the_column(
        self,
        tmp_path: Path,
        content_type: str,
        detail_field: DetailField,
        alias: str,
    ) -> None:
        """A value under the alias reaches the column its canonical key would."""
        spec = DETAIL_FIELDS[content_type]
        written, stored = _SAMPLES[detail_field.kind]
        db = SQLiteDB(tmp_path / f"{content_type}-{alias}.db")

        db_id = db.save_content_item(
            ContentItem(
                id=f"alias-{alias}",
                title="Alias Fixture",
                content_type=ContentType(content_type),
                status=ConsumptionStatus.UNREAD,
                metadata={alias: written},
            )
        )

        with db.connection() as conn:
            row = conn.execute(
                f"SELECT {detail_field.column}, metadata FROM {spec.table}"
                " WHERE content_item_id = ?",
                (db_id,),
            ).fetchone()

        assert row[detail_field.column] == stored
        blob = json.loads(row["metadata"]) if row["metadata"] else {}
        assert alias not in blob

    def test_canonical_key_wins_over_its_alias(self, tmp_path: Path) -> None:
        """An item carrying both spellings keeps the canonical one.

        An enriched movie can arrive with the provider's ``year`` beside the
        library's ``release_year``, and only one of them reaches the column.
        The alias is the fallback, so it is consulted only where the key
        before it says nothing — and the loser is dropped rather than left in
        the blob to be read back as a second, contradictory value.
        """
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
        """A blank canonical value does not shadow a real aliased one."""
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
    """A field added to the declaration alone round-trips through storage."""

    def test_added_field_round_trips_through_save_and_read(
        self, tmp_path: Path
    ) -> None:
        """Declaring a column is all a new field needs to be stored and read.

        Nothing in ``src/storage``, ``src/web`` or ``src/ingestion`` is
        edited: the write path, the joined read and the leftover-blob split
        all follow the declaration. The join is rebuilt only because it is
        derived once at import, before this test patches anything.
        """
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


class TestFieldDeclarationGuards:
    """Malformed declarations fail when the module is imported, not later.

    Each guard fires on a mistake a string-tagged converter table would have
    swallowed: a field whose kind and column disagree used to fall through to
    a default branch, and a repeated SELECT alias used to read one detail
    table's column as another's.
    """

    def test_field_without_a_column_must_be_free_form(self) -> None:
        """A field with no column must say so with FREE_FORM."""
        with pytest.raises(ValueError, match="FREE_FORM"):
            DetailField("orphan", FieldKind.TEXT)

    def test_free_form_field_cannot_claim_a_column(self) -> None:
        """FREE_FORM means the blob holds it, so a column is a contradiction."""
        with pytest.raises(ValueError, match="cannot be stored in a column"):
            DetailField("orphan", FieldKind.FREE_FORM, column="orphan")

    def test_free_form_field_cannot_carry_aliases(self) -> None:
        """Aliases are read into a column, so a blob field cannot have them."""
        with pytest.raises(ValueError, match="no column to alias"):
            DetailField("orphan", FieldKind.FREE_FORM, aliases=("orphaned",))

    def test_repeated_select_alias_is_rejected(self) -> None:
        """Two tables sharing an alias would read as one column, so it raises."""
        clash = replace(DETAIL_FIELDS["movie"], metadata_alias="book_metadata")

        with patch.dict(DETAIL_FIELDS, {"movie": clash}):
            with pytest.raises(ValueError, match="declared twice"):
                _assert_select_aliases_are_unique()

    def test_type_naming_no_creator_column_is_rejected(self) -> None:
        """A type with no creator would export a column nothing fills."""
        with pytest.raises(ValueError, match="creator template columns"):
            replace(
                DETAIL_FIELDS["movie"],
                fields=tuple(
                    detail_field
                    for detail_field in DETAIL_FIELDS["movie"].fields
                    if detail_field.kind is not FieldKind.CREATOR
                ),
            )

    def test_type_naming_two_creator_columns_is_rejected(self) -> None:
        """A second creator would export whichever field came first."""
        with pytest.raises(ValueError, match="creator template columns"):
            replace(
                DETAIL_FIELDS["movie"],
                fields=(
                    *DETAIL_FIELDS["movie"].fields,
                    DetailField(
                        "studio",
                        FieldKind.CREATOR,
                        column="studio",
                        template_column="studio",
                    ),
                ),
            )

    def test_content_type_without_a_declaration_is_rejected(self) -> None:
        """Callers index this mapping expecting a hit, so a gap must not wait.

        The CLI's creator label and the export column order both look a
        content type up directly, so an undeclared one raises KeyError at
        whichever call site reads it first rather than at import.
        """
        without_movie = {
            content_type: spec
            for content_type, spec in DETAIL_FIELDS.items()
            if content_type != "movie"
        }

        with patch.dict(DETAIL_FIELDS, without_movie, clear=True):
            with pytest.raises(ValueError, match="no field declaration"):
                _assert_every_content_type_is_declared()

    def test_declaration_for_an_unknown_content_type_is_rejected(self) -> None:
        """A declaration nothing can reach is a rename that half landed."""
        with patch.dict(DETAIL_FIELDS, {"audiobook": DETAIL_FIELDS["book"]}):
            with pytest.raises(ValueError, match="unknown content types"):
                _assert_every_content_type_is_declared()

    def test_the_declaration_guard_runs_when_the_module_is_imported(self) -> None:
        """The import is what fires it, not a caller remembering to.

        The declaration guard is called by hand above, which stays green
        with the call at the foot of the module deleted, so this executes
        the module itself against a content type nothing declares and
        expects the import to fail. The per-type creator check needs no
        equivalent: it runs in ``ContentTypeFields.__post_init__``, so the
        declaration cannot be built without it.
        """

        class _UndeclaredContentType(Enum):
            AUDIOBOOK = "audiobook"

        with patch("src.models.content.ContentType", _UndeclaredContentType):
            with pytest.raises(ValueError, match="no field declaration"):
                _import_the_declaration_module_afresh()

    def test_every_guard_the_module_defines_is_called_at_import(self) -> None:
        """A guard the foot of the module never calls guards nothing.

        Executing the module only reaches the guard the undeclared content
        type above trips; the alias guard has no such seam, and a guard
        added later would have none either. Reading the module's own top
        level covers every one of them.
        """
        tree = ast.parse(Path(detail_fields.__file__).read_text(encoding="utf-8"))
        defined = {
            node.name
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name.startswith("_assert_")
        }
        called = {
            node.value.func.id
            for node in tree.body
            if isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Name)
        }

        assert not defined - called, f"never called at import: {sorted(defined-called)}"


class TestEveryTypeNamesItsCreator:
    """Each content type states which template column carries its creator.

    ``creator_column`` is derived from the one field marked
    ``FieldKind.CREATOR``, and both the import templates and the export
    header take the creator's name from it, so a rename here renames a
    column in every file users import and export.
    """

    def test_creator_columns_are_the_documented_template_columns(self) -> None:
        """The four creator columns are the ones the templates document."""
        assert {
            content_type: spec.creator_column
            for content_type, spec in DETAIL_FIELDS.items()
        } == {
            "book": "author",
            "movie": "director",
            "tv_show": "creator",
            "video_game": "developer",
        }

    def test_a_creator_column_is_never_a_key_an_import_stores(self) -> None:
        """The word "creator" is a column heading and never a metadata key.

        ``tv_show`` accepts ``creator`` as an alias but the detail-shape
        repair leaves that key alone, on the grounds that nothing writes it.
        This is what that rests on: an importer takes a creator column onto
        ``ContentItem.author`` and skips it when building metadata, and the
        key the library stores the field under is ``creators``.
        """
        creator_columns = {spec.creator_column for spec in DETAIL_FIELDS.values()}
        stored_keys = {
            key
            for spec in DETAIL_FIELDS.values()
            for key in spec.template_columns.values()
        }

        assert creator_columns <= CREATOR_COLUMNS
        assert "creator" not in stored_keys


class TestSingleStringColumnsCoerceWhatTheyAreGiven:
    """A column holding one string takes whatever a plugin hands it.

    ``to_text`` is the codec behind every CREATOR and TEXT column, and a
    plugin is free to hand over a number where the column holds text.
    """

    def test_a_number_is_stored_as_its_own_text(self, tmp_path: Path) -> None:
        """An ISBN arriving as an int reads back as those digits, as a string."""
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
        """A blank name beside a real one is dropped, not joined in.

        ``generic_json`` forwards a list value entry by entry, so a user's
        file can hand over ``["", "CD Projekt Red"]``. The join used to keep
        the blank as a separator and store ``", CD Projekt Red"``, and the
        column is fill-only, so the punctuation would stand for the life of
        the row. Checked on the stored row rather than on the codec, because
        that is where the value could never be corrected.
        """
        db = SQLiteDB(tmp_path / "empty-entry.db")

        db_id = db.save_content_item(
            ContentItem(
                id="1207658924",
                title="Cyberpunk 2077",
                content_type=ContentType.VIDEO_GAME,
                status=ConsumptionStatus.UNREAD,
                metadata={"developers": ["", "CD Projekt Red"]},
            )
        )

        with db.connection() as conn:
            row = conn.execute(
                "SELECT developer FROM video_game_details WHERE content_item_id = ?",
                (db_id,),
            ).fetchone()
        assert row["developer"] == "CD Projekt Red"


class TestATextColumnRefusesAValueItCannotHold:
    """A value with no text form fails the write instead of taking a repr.

    ``to_text`` used to have a string for every input, ``str()`` of a dict
    included, and it sits behind every CREATOR and TEXT column. Those columns
    are neither mergeable nor monotonic, so ``_save_detail_table`` is
    fill-only for them and nothing in the app ever replaces what lands there:
    a Python repr written once is the permanent corruption
    ``_rewrite_platform_flag_dicts`` exists to undo one table over.
    """

    @pytest.mark.parametrize(
        "value",
        [
            {"name": "CD Projekt Red"},
            [{"name": "CD Projekt Red"}],
            [["Joel Coen"]],
            ("Joel Coen", "Ethan Coen"),
            {"Joel Coen"},
            b"Joel Coen",
            [b"Joel Coen"],
        ],
        ids=[
            "mapping",
            "list_of_mapping",
            "list_of_list",
            "tuple",
            "set",
            "bytes",
            "list_of_bytes",
        ],
    )
    def test_a_value_a_text_column_cannot_hold_raises(self, value: Any) -> None:
        """Every container is refused, not only the two JSON produces.

        A plugin builds its metadata dict in Python rather than parsing it, so
        it can hand over a tuple, a set or bytes as easily as a list — and
        each of those has a ``str()`` that is a repr, which is the whole
        reason a mapping is refused.
        """
        with pytest.raises(TypeError, match="text column cannot hold"):
            detail_fields.to_text(value)

    def test_an_imported_object_never_reaches_the_column(self, tmp_path: Path) -> None:
        """``generic_json`` forwards a raw value, so a user can send one.

        It used to be stored: ``str()`` of a dict is a perfectly storable
        string, so the repr landed in the column and stayed there. Refusing it
        makes the import fail where it can be seen and fixed.
        """
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


class TestWhatATextColumnStillAccepts:
    """The refusal is narrow: only a value with no name inside it.

    ``to_text`` is the codec behind every CREATOR and TEXT column and every
    shipped plugin writes at least one of them, so a guard that reached one
    shape too far would fail a sync rather than a repr.
    """

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ([], None),
            ([None], None),
            (["Joel Coen", None, "Ethan Coen"], "Joel Coen, Ethan Coen"),
            (["", "Ethan Coen"], "Ethan Coen"),
            (["Joel Coen", ""], "Joel Coen"),
            (["", ""], None),
            (["", 0], "0"),
            ([0], "0"),
            ([""], None),
            ([" ", "Ethan Coen"], "Ethan Coen"),
            (["   "], None),
            (" ", None),
            (["  Ethan Coen  "], "Ethan Coen"),
            ("", None),
            (7, "7"),
            (True, "True"),
        ],
        ids=[
            "empty_list",
            "list_of_null",
            "names_around_a_null",
            "empty_string_among_names",
            "empty_string_after_a_name",
            "every_entry_empty",
            "zero_beside_an_empty_string",
            "zero",
            "list_of_empty_string",
            "blank_string_among_names",
            "every_entry_blank",
            "bare_blank_string",
            "padded_name",
            "bare_empty_string",
            "number",
            "boolean",
        ],
    )
    def test_a_value_with_a_text_form_is_still_flattened(
        self, value: Any, expected: str | None
    ) -> None:
        """Every non-container a plugin can emit keeps the behaviour it had.

        Naming nothing is None whichever shape it arrives in, and an entry
        naming nothing is dropped from the join rather than left as a comma in
        front of the next name. A bare ``""`` used to be returned unchanged,
        and a text column is fill-only, so the one storing it locked itself
        against every later sync — the same failure the migration's empty-name
        cases exist to prevent.
        """
        assert detail_fields.to_text(value) == expected

    def test_an_empty_object_is_refused_like_any_other(self) -> None:
        """A mapping is refused for having no text form, not for its contents."""
        with pytest.raises(TypeError, match="text column cannot hold"):
            detail_fields.to_text({})

    def test_a_list_nested_two_deep_is_refused(self) -> None:
        """The guard reads one level, and one level is where a container shows."""
        with pytest.raises(TypeError, match="text column cannot hold"):
            detail_fields.to_text([[["Joel Coen"]]])


class TestARefusalNamesTheFieldItRefused:
    """The message carries the metadata key, and nothing else from the item.

    ``to_text`` is the codec behind every CREATOR and TEXT column, so a
    refusal naming only a type leaves an operator with no way to tell which
    import to go and fix.
    The key is the whole of what is added: an exception message reaches the
    log and the sync report, and no user or provider value may travel there.
    """

    @staticmethod
    def _refuse_an_object_developer(db: SQLiteDB) -> str:
        """Save a game naming its developer in an object, and return the refusal."""
        with pytest.raises(TypeError) as refusal:
            db.save_content_item(
                ContentItem(
                    id="1207658924",
                    title="Cyberpunk 2077",
                    content_type=ContentType.VIDEO_GAME,
                    status=ConsumptionStatus.UNREAD,
                    metadata={"developers": [{"name": "CD Projekt Red"}]},
                )
            )
        return str(refusal.value)

    def test_the_refusal_names_the_field(self, tmp_path: Path) -> None:
        """The field is named by its own key, whichever spelling arrived.

        ``developers`` is an alias of ``developer``, and the declaration is
        indexed by the canonical key, so that is the name to look the field up
        under.
        """
        db = SQLiteDB(tmp_path / "named-refusal.db")

        assert "'developer'" in self._refuse_an_object_developer(db)

    def test_the_refusal_carries_no_value_from_the_item(self, tmp_path: Path) -> None:
        """The provider's name is in the value, so it stays out of the message."""
        db = SQLiteDB(tmp_path / "quiet-refusal.db")

        assert "CD Projekt Red" not in self._refuse_an_object_developer(db)

    def test_the_refusal_is_the_key_and_a_type_and_nothing_else(
        self, tmp_path: Path
    ) -> None:
        """Read whole, the message is two constants and no third thing.

        Asserting the name is absent only rules out the name that was looked
        for. The message reaches a log and a sync report, so what it may carry
        is pinned in full rather than one value at a time.
        """
        db = SQLiteDB(tmp_path / "whole-refusal.db")

        assert (
            self._refuse_an_object_developer(db)
            == "'developer': a text column cannot hold a dict"
        )


class TestReducingAKnownShapeToNames:
    """``text_names`` keeps what a text column holds and drops the rest.

    A plugin that knows its API's shape reduces it here rather than
    stringifying it: a reduction that ``str()``\\ d whatever it did not
    recognise would write the repr into the column one layer in front of the
    guard, and ``to_text`` would never see the value it exists to refuse.
    """

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("CD Projekt Red", ["CD Projekt Red"]),
            (["CD Projekt Red", "Valve"], ["CD Projekt Red", "Valve"]),
            ([{"name": "CD Projekt Red"}], ["CD Projekt Red"]),
            ({"name": "CD Projekt Red"}, ["CD Projekt Red"]),
            ([{"slug": "cdpr"}], []),
            ([["CD Projekt Red"]], []),
            ([{"name": {"text": "CD Projekt Red"}}], []),
            ([None, ""], []),
            (None, []),
            ([7], ["7"]),
        ],
        ids=[
            "bare_name",
            "list_of_names",
            "list_of_objects",
            "lone_object",
            "object_naming_nothing",
            "nested_list",
            "object_shaped_name",
            "nothing_to_name",
            "null",
            "number",
        ],
    )
    def test_only_a_name_a_text_column_can_hold_survives(
        self, value: Any, expected: list[str]
    ) -> None:
        """A nested list and an object-shaped name are dropped, not stringified."""
        assert detail_fields.text_names(value) == expected

    def test_what_it_keeps_is_what_the_column_guard_accepts(self) -> None:
        """The reduction and the refusal read one list of types, so they agree."""
        reduced = detail_fields.text_names([{"name": ["Nested"]}, "Valve"])

        assert detail_fields.to_text(reduced) == "Valve"

    def test_a_reduced_shape_reaches_no_column_as_a_repr(self, tmp_path: Path) -> None:
        """Stored, the row holds the one name and no bracket from the rest.

        The two shapes the reduction used to ``str()`` — a nested list and a
        name that is itself an object — are checked where it matters, on the
        stored row: neither the column nor the blob beside it may hold their
        repr.
        """
        db = SQLiteDB(tmp_path / "no-repr.db")

        db_id = db.save_content_item(
            ContentItem(
                id="1207658924",
                title="Cyberpunk 2077",
                content_type=ContentType.VIDEO_GAME,
                status=ConsumptionStatus.UNREAD,
                metadata={
                    "developers": detail_fields.text_names(
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


class TestARefusedWriteLeavesNothingBehind:
    """A codec that raises mid-write must not half-save the item.

    ``_save_detail_table`` runs after the ``content_items`` row is written
    and inside the same connection, so the raise crosses a transaction that
    has already inserted. A commit that never happens is the only thing
    keeping the library from growing an item with no detail row — which no
    later sync would repair, because the upsert would find the row by its
    external id and take the detail path fill-only from there.
    """

    def test_a_new_item_is_not_left_without_its_detail_row(
        self, tmp_path: Path
    ) -> None:
        """Nothing at all is stored for the item whose write raised."""
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
        """The row a good sync wrote survives a later bad one unchanged."""
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

    def test_the_creator_column_refuses_the_same_shapes(self, tmp_path: Path) -> None:
        """A CREATOR column shares the codec, so it shares the refusal.

        The creator is taken from the item's metadata whenever ``author`` is
        empty, which is how GOG's ``developers`` reaches ``developer`` — the
        one alias in the declaration that a remote API returns as objects.
        """
        db = SQLiteDB(tmp_path / "object-developer.db")

        with pytest.raises(TypeError, match="text column cannot hold"):
            db.save_content_item(
                ContentItem(
                    id="1207658924",
                    title="Cyberpunk 2077",
                    content_type=ContentType.VIDEO_GAME,
                    status=ConsumptionStatus.UNREAD,
                    metadata={"developers": [{"name": "CD Projekt Red"}]},
                )
            )

        with db.connection() as conn:
            items = conn.execute("SELECT COUNT(*) AS n FROM content_items").fetchone()
        assert items["n"] == 0


class TestNullInAListOfNamesRegression:
    """A null among a plugin's list of names was stored as the text "None".

    Bug reported: a movie whose director list carried a null — any plugin
    list can — stored "Joel Coen, None" and exported that into the director
    cell, so "None" read as part of the name.
    Root cause: ``to_text`` joins a list into the one name its column holds,
    over ``str(entry)`` for every entry, and ``str(None)`` is "None".
    Fix: the null entries are dropped before the join.
    """

    def test_a_null_among_the_creators_is_dropped_regression(
        self, tmp_path: Path
    ) -> None:
        """The stored creator is the names, with nothing standing in for the null."""
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
        """Dropping every entry leaves no creator, rather than an empty name."""
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
    """No detail alias collides with a column ``ci.*`` already contributes.

    The joined read selects ``ci.*`` alongside every detail column, and hands
    the lot to one ``sqlite3.Row``. A detail alias spelled like a
    content_items column resolves to whichever the driver saw first rather
    than raising, so ``_row_to_content_item`` would read a title or a rating
    where it asked for a detail value. ``_assert_select_aliases_are_unique``
    only compares the detail tables against each other, so this is the check
    for the other half.
    """

    def test_no_detail_alias_matches_a_content_items_column(
        self, tmp_path: Path
    ) -> None:
        """Every alias the joined SELECT introduces is a new name."""
        base_columns = set(_live_columns(tmp_path, "content_items"))

        aliases = {
            detail_field.select_alias or detail_field.column
            for spec in DETAIL_FIELDS.values()
            for detail_field in spec.fields
            if detail_field.column is not None
        } | {spec.metadata_alias for spec in DETAIL_FIELDS.values()}

        assert aliases & base_columns == set()


class TestSelectIdentifiersAreGuarded:
    """Names from the declaration are validated before they reach a query.

    Three queries are built from the declaration rather than written out: the
    joined read, the detail joins it shares with the derived-column source,
    and that source's creator CASE. Every table goes through
    ALLOWED_DETAIL_TABLES and every alias, column and content-type key through
    the identifier pattern, and these hold that guard in place.
    """

    def test_table_outside_the_allow_list_is_rejected(self) -> None:
        """A detail table nobody allow-listed never reaches a FROM clause."""
        rogue = ContentTypeFields(
            table="rogue_details; DROP TABLE content_items; --",
            table_alias="rd",
            metadata_alias="rogue_metadata",
            fields=(
                _A_CREATOR,
                DetailField(
                    "title",
                    FieldKind.TEXT,
                    column="title",
                    select_alias="rogue_title",
                ),
            ),
        )

        with patch.dict(DETAIL_FIELDS, {"rogue": rogue}):
            with pytest.raises(ValueError, match="Unknown detail table"):
                sqlite_db._build_content_item_select()

    def test_unsafe_column_name_is_rejected(self) -> None:
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
                sqlite_db._build_content_item_select()

    def test_unsafe_select_alias_is_rejected(self) -> None:
        """A SELECT alias outside the identifier pattern raises."""
        rogue = replace(
            DETAIL_FIELDS["book"],
            fields=(
                _A_CREATOR,
                DetailField(
                    "isbn",
                    FieldKind.TEXT,
                    column="isbn",
                    select_alias="x, (SELECT credential_value FROM credentials)",
                ),
            ),
        )

        with patch.dict(DETAIL_FIELDS, {"book": rogue}):
            with pytest.raises(ValueError, match="Unsafe SQL identifier"):
                sqlite_db._build_content_item_select()

    def test_unsafe_table_alias_is_rejected(self) -> None:
        """A table alias outside the identifier pattern raises."""
        rogue = replace(DETAIL_FIELDS["book"], table_alias="bd, credentials c")

        with patch.dict(DETAIL_FIELDS, {"book": rogue}):
            with pytest.raises(ValueError, match="Unsafe SQL identifier"):
                sqlite_db._build_content_item_select()

    def test_detail_join_on_a_table_outside_the_allow_list_is_rejected(self) -> None:
        """The shared join builder refuses a table nobody allow-listed.

        Asserted against the builder itself because the joined read takes its
        joins from an import-time constant, which the test above no longer
        rebuilds.
        """
        rogue = replace(
            DETAIL_FIELDS["book"], table="rogue_details; DROP TABLE content_items; --"
        )

        with pytest.raises(ValueError, match="Unknown detail table"):
            detail_join(rogue)

    def test_unsafe_content_type_key_is_rejected(self) -> None:
        """A content-type key outside the identifier pattern never reaches the CASE.

        The key is the one name the declaration hands to SQL as a string
        literal rather than as an identifier, so a quote in it would close the
        literal the creator CASE compares against and leave the rest as SQL.
        The source is built at import, so the builder is called directly:
        patching the declaration cannot reach a constant already assembled.
        """
        rogue_key = "book' THEN (SELECT credential_value FROM credentials) --"

        with patch.dict(DETAIL_FIELDS, {rogue_key: DETAIL_FIELDS["book"]}):
            with pytest.raises(ValueError, match="Unsafe SQL identifier"):
                derived._build_creator_source()

    def test_unsafe_creator_column_is_rejected(self) -> None:
        """A creator column outside the identifier pattern raises."""
        rogue = replace(
            DETAIL_FIELDS["book"],
            fields=(
                DetailField(
                    "author",
                    FieldKind.CREATOR,
                    column="author FROM credentials; --",
                    template_column="author",
                ),
            ),
        )

        with patch.dict(DETAIL_FIELDS, {"book": rogue}):
            with pytest.raises(ValueError, match="Unsafe SQL identifier"):
                derived._build_creator_source()


class TestKnownKeysAreTheKeysTheColumnsClaim:
    """``known_keys`` is computed, and this is what it has to compute to.

    Storage subtracts the set from an item's metadata before writing what is
    left to the free-form blob, so the set is the whole contract for which
    metadata keys a plugin can rely on reaching a column. The parametrized
    alias tests above take their cases from the declaration and so cannot
    notice a canonical key changing spelling; this can.
    """

    @pytest.mark.parametrize("content_type", sorted(DETAIL_FIELDS))
    def test_known_keys_match_the_expected_set(self, content_type: str) -> None:
        """No metadata key quietly joins or leaves a type's claimed set."""
        assert (
            DETAIL_FIELDS[content_type].known_keys == _EXPECTED_KNOWN_KEYS[content_type]
        )

    def test_every_declared_type_is_covered(self) -> None:
        """A content type added later does not slip past the check above."""
        assert set(_EXPECTED_KNOWN_KEYS) == set(DETAIL_FIELDS)


class TestListValuedColumnsAreTheDeclaredLists:
    """``LIST_VALUED_COLUMNS`` is computed, and this is what it has to be.

    An import wraps one of these template cells in a list and an export
    writes the first entry back, so a column joining the set changes the
    shape a value is stored in, and one leaving it stores a bare string
    where every other producer writes a list.
    """

    def test_the_list_valued_columns_are_the_expected_ones(self) -> None:
        """Only the genre and platform cells stand for a list."""
        assert LIST_VALUED_COLUMNS == frozenset({"genre", "platform"})


class TestEveryDeclaredColumnRoundTrips:
    """Every declared column survives a save and a read, under its own key.

    The write path, the joined SELECT and the read path are three separate
    derivations of one declaration. A field whose column, select alias or
    metadata key disagrees between them is dropped silently rather than
    raising — the value is written and the read simply asks for a name the
    row does not carry — so covering one field per type would not find it.
    """

    @pytest.mark.parametrize("content_type", sorted(DETAIL_FIELDS))
    def test_all_columns_survive_save_and_read(
        self, tmp_path: Path, content_type: str
    ) -> None:
        """Each column comes back, and only unclaimed keys reach the blob."""
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

    @pytest.mark.parametrize("content_type", sorted(DETAIL_FIELDS))
    def test_item_with_no_metadata_reads_back_empty(
        self, tmp_path: Path, content_type: str
    ) -> None:
        """An item stating nothing still states nothing after a round trip.

        Every codec is handed None on the way in and every column is NULL on
        the way out, which is the path a bare title from a sparse source
        takes.
        """
        db = SQLiteDB(tmp_path / f"empty-{content_type}.db")

        db_id = db.save_content_item(
            ContentItem(
                id=f"empty-{content_type}",
                title="Nothing Stated",
                content_type=ContentType(content_type),
                status=ConsumptionStatus.UNREAD,
                metadata={},
            )
        )
        stored = db.get_content_item(db_id)

        assert stored is not None
        assert stored.metadata == {}
        assert stored.author is None


class TestLeftoverBlobWinsOverItsColumn:
    """A column-claimed key sitting in an old row's blob still reads back.

    Rows written before a field had a column of its own carry that key in the
    free-form blob, and the read path merges the blob after the columns. The
    blob's copy therefore wins, which is what keeps an existing library's
    values visible rather than reading as None from a column nothing filled.
    """

    def test_blob_value_wins_over_the_column(self, tmp_path: Path) -> None:
        """The later blob merge overrides what the column said."""
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
