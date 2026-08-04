"""One declaration of the fields each content type stores.

:data:`DETAIL_FIELDS` is the single place a detail-table field is described.
The joined SELECT and the detail-table read and write paths in
``src/storage/sqlite_db.py``, the import templates in
``src/ingestion/sources/generic_csv`` and the library export in
``src/web/export.py`` all derive their column lists from it, so adding or
renaming a field is one edit rather than five.

The ``CREATE TABLE`` statements in ``src/storage/schema.py`` are deliberately
*not* generated from this declaration — generating them would put the
migration path at risk for no gain. ``tests/test_detail_fields.py`` reads
``PRAGMA table_info`` instead and fails when the two disagree.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any

from src.models.content import ContentType


def unchanged(value: Any) -> Any:
    """Return the value as it stands, for columns needing no conversion."""
    return value


#: What a text column can hold: a JSON scalar, whose ``str()`` is the value
#: itself rather than a Python repr. A bool is an int here. Named as what is
#: allowed rather than what is not, because a plugin builds its metadata by
#: hand and can reach for a container no JSON document produces — a tuple, a
#: set, bytes — and a list of names to refuse would let each of those through.
_TEXT_SCALARS: tuple[type, ...] = (str, int, float)


def to_text(value: Any) -> str | None:
    """Flatten a metadata value into the one string its column holds.

    A source hands over a list where the column takes a single name — GOG's
    ``developers`` against ``developer`` — so a list is joined the way TMDB
    already joins several directors into one ``director``. A None among the
    entries is dropped rather than joined in as the text "None".

    A value naming nothing — an empty string as much as an empty list — is
    None rather than "". Every text column is fill-only in
    ``SQLiteDB._save_detail_table``, which tests ``is not None``, so an empty
    string written once would lock the column against every later sync.

    Raises:
        TypeError: For anything but a scalar in :data:`_TEXT_SCALARS`, at the
            top level or inside a list. Its ``str()`` is a Python repr, and a
            text column is neither mergeable nor monotonic, so the repr would
            be written once and never corrected by anything. Failing the write
            is the only outcome a caller can act on.
            :meth:`DetailField.store` adds the field it came from.
    """
    if value is None:
        return None
    entries = value if isinstance(value, list) else [value]
    for entry in entries:
        if entry is not None and not isinstance(entry, _TEXT_SCALARS):
            raise TypeError(f"a text column cannot hold a {type(entry).__name__}")
    return ", ".join(str(entry) for entry in entries if entry is not None) or None


def text_names(value: Any) -> list[str]:
    """Keep the names in *value* that a text column can hold.

    An entry naming itself in an object — GOG's ``{"name": "CD Projekt Red"}``
    — is read for that name, and anything with no text form is dropped rather
    than stringified into a repr. A caller reducing a shape it knows uses
    this; :func:`to_text` stays the backstop for a shape nobody reduced.
    """
    entries = value if isinstance(value, list) else [value]
    names = [
        entry.get("name") if isinstance(entry, Mapping) else entry for entry in entries
    ]
    return [str(name) for name in names if isinstance(name, _TEXT_SCALARS) and name]


def to_int(value: Any) -> int | None:
    """Coerce a metadata value to an int, or None when it is not a number."""
    if value is None or isinstance(value, int):
        return value
    try:
        return int(value)
    except (ValueError, TypeError):
        return None


def to_json_array(value: Any) -> str | None:
    """Serialize a metadata value as the JSON array its column holds.

    A bare string is wrapped, because a single genre stored as ``"Drama"``
    rather than ``'["Drama"]'`` reads back as a string nothing downstream
    parses. A string that already looks like an array is passed through.
    """
    if value is None:
        return None
    if isinstance(value, str):
        return value if value.startswith("[") else json.dumps([value])
    if isinstance(value, list):
        return json.dumps(value)
    return json.dumps([value])


def parse_json_array(value: Any) -> list[Any] | None:
    """Read a JSON array column back into a list."""
    if value is None:
        return None
    if isinstance(value, list):
        return value
    try:
        parsed = json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return [value] if value else None
    return parsed if isinstance(parsed, list) else [parsed]


class FieldKind(Enum):
    """What a field holds, and so how it crosses the column boundary.

    ``CREATOR`` is the type's creator — author, director, creators,
    developer — which crosses as ``ContentItem.author`` rather than a
    metadata key. ``FREE_FORM`` has no column of its own and lives in the
    detail row's metadata blob.
    """

    CREATOR = "creator"
    TEXT = "text"
    INTEGER = "integer"
    STRING_LIST = "string_list"
    FREE_FORM = "free_form"


@dataclass(frozen=True)
class FieldCodec:
    """How one kind of value is written to and read back from its column."""

    store: Callable[[Any], Any]
    load: Callable[[Any], Any]


_CODECS: dict[FieldKind, FieldCodec] = {
    FieldKind.CREATOR: FieldCodec(store=to_text, load=unchanged),
    FieldKind.TEXT: FieldCodec(store=to_text, load=unchanged),
    FieldKind.INTEGER: FieldCodec(store=to_int, load=unchanged),
    FieldKind.STRING_LIST: FieldCodec(store=to_json_array, load=parse_json_array),
}


@dataclass(frozen=True)
class DetailField:
    """One value a content type stores.

    Args:
        metadata_key: Key ``ContentItem.metadata`` carries the value under.
        kind: What the value is, which selects its codec.
        column: Detail-table column, or None when the metadata blob holds it.
        select_alias: Name the column takes in the joined SELECT, where the
            bare column name would collide with another detail table's.
        aliases: Further metadata keys accepted on write, because the plugins
            do not all agree on the spelling ("genre" for "genres").
        template_column: Column the CSV/JSON templates and the export call
            this field, when they carry it at all.
    """

    metadata_key: str
    kind: FieldKind
    column: str | None = None
    select_alias: str | None = None
    aliases: tuple[str, ...] = ()
    template_column: str | None = None

    def __post_init__(self) -> None:
        """Reject a field whose kind and column disagree, at import time."""
        if self.column is None:
            if self.kind is not FieldKind.FREE_FORM:
                raise ValueError(
                    f"{self.metadata_key!r} has no column, so its kind must be"
                    f" FREE_FORM, not {self.kind}"
                )
            if self.select_alias is not None or self.aliases:
                raise ValueError(
                    f"{self.metadata_key!r} has no column to alias or to read"
                    " alias keys into"
                )
        elif self.kind not in _CODECS:
            raise ValueError(f"{self.kind} cannot be stored in a column")

    @property
    def codec(self) -> FieldCodec:
        """The codec for this field's kind."""
        return _CODECS[self.kind]

    @property
    def metadata_keys(self) -> tuple[str, ...]:
        """The canonical key first, then the aliases accepted for it."""
        return (self.metadata_key, *self.aliases)

    def store(self, value: Any) -> Any:
        """Convert *value* for this field's column, naming the field if it cannot.

        A codec sees a value and no field, so its refusal names a type and
        nothing to act on: every CREATOR and TEXT column shares
        :func:`to_text`. The metadata key is all that is added — a provider's
        value must not reach an exception message, a log line or an API body.

        Raises:
            TypeError: What the codec raised, naming this field's key.
        """
        try:
            return self.codec.store(value)
        except TypeError as error:
            raise TypeError(f"{self.metadata_key!r}: {error}") from error

    def value_from(self, metadata: Mapping[str, Any]) -> Any:
        """Take this field's value out of an item's metadata.

        An alias is consulted only when the key before it says nothing, so an
        item carrying both spellings keeps the canonical one.
        """
        value = None
        for key in self.metadata_keys:
            value = metadata.get(key)
            if value:
                return value
        return value


@dataclass(frozen=True)
class ContentTypeFields:
    """Everything one content type stores, in template order.

    Args:
        table: Detail table holding the columns.
        table_alias: Alias the table takes in the joined SELECT.
        metadata_alias: Alias that table's free-form blob column takes there.
        fields: The fields, ordered as the templates and export list them.
            Reordering them reorders the columns of every CSV and JSON file
            the app exports, including the ones users already hold, so it is
            never a readability edit. ``TestExportLayoutIsStable`` in
            ``tests/web/test_export.py`` pins the resulting layout.
    """

    table: str
    table_alias: str
    metadata_alias: str
    fields: tuple[DetailField, ...]

    def __post_init__(self) -> None:
        """Reject a type not naming its creator exactly once, at import time.

        Import and export both take the creator off ``ContentItem.author``
        and find its template column here, so a type declaring none would
        export a column nothing fills, and one declaring two would export
        whichever field came first.
        """
        columns = self._creator_columns()
        if len(columns) != 1:
            raise ValueError(
                f"{self.table} declares {len(columns)} creator template"
                " columns, and needs exactly one"
            )

    def _creator_columns(self) -> list[str]:
        """Every template column this type marks as carrying its creator."""
        return [
            field.template_column
            for field in self.fields
            if field.kind is FieldKind.CREATOR and field.template_column is not None
        ]

    @property
    def creator_column(self) -> str:
        """Template column carrying the creator: "director", "creator"...

        Its value is ``ContentItem.author`` rather than a metadata key, so
        import and export both take it off the item's author attribute.
        """
        return self._creator_columns()[0]

    @property
    def columns(self) -> tuple[str, ...]:
        """The detail-table columns, in declaration order."""
        return tuple(field.column for field in self.fields if field.column is not None)

    @property
    def known_keys(self) -> frozenset[str]:
        """Metadata keys the columns consume, aliases included.

        Storage drops these from the leftover metadata blob, so a value is
        never both in its column and duplicated into the JSON beside it.
        """
        return frozenset(
            key
            for field in self.fields
            if field.column is not None
            for key in field.metadata_keys
        )

    @property
    def template_columns(self) -> dict[str, str]:
        """Template column to the metadata key the library stores it under.

        The two are not always the same word — a template says "year", the
        library stores "release_year" — and import and export both read this,
        so a column is written and read back under one name.
        """
        return {
            field.template_column: field.metadata_key
            for field in self.fields
            if field.template_column is not None
        }


DETAIL_FIELDS: dict[str, ContentTypeFields] = {
    "book": ContentTypeFields(
        table="book_details",
        table_alias="bd",
        metadata_alias="book_metadata",
        fields=(
            DetailField(
                "author",
                FieldKind.CREATOR,
                column="author",
                select_alias="book_author",
                template_column="author",
            ),
            DetailField("isbn", FieldKind.TEXT, column="isbn", template_column="isbn"),
            DetailField(
                "pages", FieldKind.INTEGER, column="pages", template_column="pages"
            ),
            DetailField(
                "year_published",
                FieldKind.INTEGER,
                column="year_published",
                select_alias="book_year",
                template_column="year_published",
            ),
            DetailField(
                "genres",
                FieldKind.STRING_LIST,
                column="genres",
                select_alias="book_genres",
                aliases=("genre",),
                template_column="genre",
            ),
            DetailField("isbn13", FieldKind.TEXT, column="isbn13"),
            DetailField("publisher", FieldKind.TEXT, column="publisher"),
            DetailField(
                "tags", FieldKind.STRING_LIST, column="tags", select_alias="book_tags"
            ),
            DetailField(
                "description",
                FieldKind.TEXT,
                column="description",
                select_alias="book_description",
            ),
        ),
    ),
    "movie": ContentTypeFields(
        table="movie_details",
        table_alias="md",
        metadata_alias="movie_metadata",
        fields=(
            DetailField(
                "director",
                FieldKind.CREATOR,
                column="director",
                template_column="director",
            ),
            DetailField(
                "release_year",
                FieldKind.INTEGER,
                column="release_year",
                select_alias="movie_year",
                aliases=("year",),
                template_column="year",
            ),
            DetailField(
                "runtime",
                FieldKind.INTEGER,
                column="runtime",
                aliases=("runtime_minutes",),
                template_column="runtime_minutes",
            ),
            DetailField(
                "genres",
                FieldKind.STRING_LIST,
                column="genres",
                select_alias="movie_genres",
                aliases=("genre",),
                template_column="genre",
            ),
            DetailField("studio", FieldKind.TEXT, column="studio"),
            DetailField(
                "tags", FieldKind.STRING_LIST, column="tags", select_alias="movie_tags"
            ),
            DetailField(
                "description",
                FieldKind.TEXT,
                column="description",
                select_alias="movie_description",
            ),
        ),
    ),
    "tv_show": ContentTypeFields(
        table="tv_show_details",
        table_alias="td",
        metadata_alias="tv_metadata",
        fields=(
            # The template says "creator" and the library stores "creators".
            DetailField(
                "creators",
                FieldKind.CREATOR,
                column="creators",
                aliases=("creator",),
                template_column="creator",
            ),
            DetailField(
                "seasons_watched",
                FieldKind.FREE_FORM,
                template_column="seasons_watched",
            ),
            DetailField(
                "seasons",
                FieldKind.INTEGER,
                column="seasons",
                aliases=("total_seasons",),
                template_column="total_seasons",
            ),
            DetailField(
                "release_year",
                FieldKind.INTEGER,
                column="release_year",
                select_alias="tv_year",
                aliases=("year",),
                template_column="year",
            ),
            DetailField(
                "genres",
                FieldKind.STRING_LIST,
                column="genres",
                select_alias="tv_genres",
                aliases=("genre",),
                template_column="genre",
            ),
            DetailField("episodes", FieldKind.INTEGER, column="episodes"),
            DetailField("network", FieldKind.TEXT, column="network"),
            DetailField(
                "tags", FieldKind.STRING_LIST, column="tags", select_alias="tv_tags"
            ),
            DetailField(
                "description",
                FieldKind.TEXT,
                column="description",
                select_alias="tv_description",
            ),
        ),
    ),
    "video_game": ContentTypeFields(
        table="video_game_details",
        table_alias="vgd",
        metadata_alias="game_metadata",
        fields=(
            DetailField(
                "developer",
                FieldKind.CREATOR,
                column="developer",
                aliases=("developers",),
                template_column="developer",
            ),
            DetailField(
                "platforms",
                FieldKind.STRING_LIST,
                column="platforms",
                aliases=("platform",),
                template_column="platform",
            ),
            DetailField(
                "genres",
                FieldKind.STRING_LIST,
                column="genres",
                select_alias="game_genres",
                aliases=("genre",),
                template_column="genre",
            ),
            DetailField(
                "playtime_hours",
                FieldKind.FREE_FORM,
                template_column="hours_played",
            ),
            DetailField(
                "publisher",
                FieldKind.TEXT,
                column="publisher",
                select_alias="game_publisher",
                aliases=("publishers",),
            ),
            DetailField(
                "release_year",
                FieldKind.INTEGER,
                column="release_year",
                select_alias="game_year",
            ),
            DetailField(
                "tags", FieldKind.STRING_LIST, column="tags", select_alias="game_tags"
            ),
            DetailField(
                "description",
                FieldKind.TEXT,
                column="description",
                select_alias="game_description",
            ),
        ),
    ),
}


def _assert_select_aliases_are_unique() -> None:
    """Fail at import when two detail tables would share a SELECT alias.

    The joined read hands every detail column to one ``sqlite3.Row``, which
    resolves a repeated name to whichever came first, so a collision reads one
    table's value as another's rather than raising.
    """
    seen: set[str] = set()
    for spec in DETAIL_FIELDS.values():
        aliases = [spec.metadata_alias]
        for field in spec.fields:
            if field.column is not None:
                aliases.append(field.select_alias or field.column)
        for alias in aliases:
            if alias in seen:
                raise ValueError(f"Detail select alias declared twice: {alias!r}")
            seen.add(alias)


def _assert_every_content_type_is_declared() -> None:
    """Fail at import when a content type has no declaration, or vice versa.

    Callers index this mapping by content type and expect a hit — the CLI's
    creator label and the export column order both do. Without this an added
    ``ContentType`` raises ``KeyError`` wherever it is first read, which is
    exactly the write-but-never-read class of failure the declaration exists
    to stop.
    """
    declared = set(DETAIL_FIELDS)
    known = {content_type.value for content_type in ContentType}
    if undeclared := known - declared:
        raise ValueError(
            f"Content types with no field declaration: {sorted(undeclared)}"
        )
    if unknown := declared - known:
        raise ValueError(
            f"Field declarations for unknown content types: {sorted(unknown)}"
        )


_assert_select_aliases_are_unique()
_assert_every_content_type_is_declared()
