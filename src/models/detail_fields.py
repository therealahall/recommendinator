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


def unchanged(value: Any) -> Any:
    """Return the value as it stands, for columns needing no conversion."""
    return value


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

    ``CREATOR`` is the item's ``ContentItem.author`` rather than a metadata
    key. ``FREE_FORM`` has no column of its own and lives in the detail row's
    metadata blob.
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
    FieldKind.CREATOR: FieldCodec(store=unchanged, load=unchanged),
    FieldKind.TEXT: FieldCodec(store=unchanged, load=unchanged),
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
        creator_column: Template column carrying the creator, which becomes
            ``ContentItem.author`` rather than metadata.
        fields: The fields, ordered as the templates and export list them.
    """

    table: str
    table_alias: str
    metadata_alias: str
    creator_column: str
    fields: tuple[DetailField, ...]

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
        creator_column="author",
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
        creator_column="director",
        fields=(
            DetailField(
                "director",
                FieldKind.TEXT,
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
        creator_column="creator",
        fields=(
            # The template's creator column has no detail column of its own:
            # only a book's creator reaches one.
            DetailField("creator", FieldKind.FREE_FORM, template_column="creator"),
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
            DetailField("creators", FieldKind.TEXT, column="creators"),
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
        creator_column="developer",
        fields=(
            DetailField(
                "developer",
                FieldKind.TEXT,
                column="developer",
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


_assert_select_aliases_are_unique()
