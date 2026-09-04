"""The ``CREATE TABLE`` statements in ``src/storage/schema.py`` are deliberately
*not* generated from this declaration — generating them would put the
migration path at risk for no gain.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any

from src.models.content import ContentType


def unchanged(value: Any) -> Any:
    return value


#: What a text column can hold: a JSON scalar, whose ``str()`` is the value
#: itself rather than a Python repr. A bool is an int here.
_TEXT_SCALARS: tuple[type, ...] = (str, int, float)


def to_text(value: Any) -> str | None:
    """Every text column is fill-only in ``SQLiteDB._save_detail_table``, which
    tests ``is not None``, so an empty string written once would lock the column
    against every later sync.
    """
    if value is None:
        return None
    entries = value if isinstance(value, list) else [value]
    for entry in entries:
        if entry is not None and not isinstance(entry, _TEXT_SCALARS):
            raise TypeError(f"a text column cannot hold a {type(entry).__name__}")
    names = (str(entry).strip() for entry in entries if entry is not None)
    return ", ".join(name for name in names if name) or None


def text_names(value: Any) -> list[str]:
    entries = value if isinstance(value, list) else [value]
    names = [
        entry.get("name") if isinstance(entry, Mapping) else entry for entry in entries
    ]
    return [str(name) for name in names if isinstance(name, _TEXT_SCALARS) and name]


def to_int(value: Any) -> int | None:
    if value is None or isinstance(value, int):
        return value
    try:
        return int(value)
    except (ValueError, TypeError):
        return None


def to_json_array(value: Any) -> str | None:
    """A bare string is wrapped: ``"Drama"`` alone reads back as a string
    nothing parses.
    """
    if value is None:
        return None
    if isinstance(value, str):
        return value if value.startswith("[") else json.dumps([value])
    entries = value if isinstance(value, list) else [value]
    for entry in entries:
        if entry is not None and not isinstance(entry, _TEXT_SCALARS):
            raise TypeError(f"a list column cannot hold a {type(entry).__name__}")
    return json.dumps(entries)


def parse_json_array(value: Any) -> list[str] | None:
    """A row filled before :func:`to_json_array` refused an object still holds
    one, and every reader of the metadata key treats an element as a name.
    """
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            pass
    return text_names(value)


class FieldKind(Enum):
    CREATOR = "creator"
    TEXT = "text"
    INTEGER = "integer"
    STRING_LIST = "string_list"
    FREE_FORM = "free_form"


@dataclass(frozen=True)
class FieldCodec:
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
    metadata_key: str
    kind: FieldKind
    column: str | None = None
    select_alias: str | None = None
    aliases: tuple[str, ...] = ()
    template_column: str | None = None

    def __post_init__(self) -> None:
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
        return _CODECS[self.kind]

    @property
    def metadata_keys(self) -> tuple[str, ...]:
        return (self.metadata_key, *self.aliases)

    def store(self, value: Any) -> Any:
        """The metadata key is all that is added — a provider's value must not reach an
        exception message, a log line or an API body.
        """
        try:
            return self.codec.store(value)
        except TypeError as error:
            raise TypeError(f"{self.metadata_key!r}: {error}") from error

    def value_from(self, metadata: Mapping[str, Any]) -> Any:
        value = None
        for key in self.metadata_keys:
            value = metadata.get(key)
            if value:
                return value
        return value


@dataclass(frozen=True)
class ContentTypeFields:
    """The order is presentational: the importer and the exporter both
    key by column name, so reordering changes how an exported file
    reads and nothing about what it means.
    """

    table: str
    table_alias: str
    metadata_alias: str
    fields: tuple[DetailField, ...]

    def __post_init__(self) -> None:
        columns = self._creator_columns()
        if len(columns) != 1:
            raise ValueError(
                f"{self.table} declares {len(columns)} creator template"
                " columns, and needs exactly one"
            )

    def _creator_columns(self) -> list[str]:
        return [
            field.template_column
            for field in self.fields
            if field.kind is FieldKind.CREATOR and field.template_column is not None
        ]

    @property
    def creator_column(self) -> str:
        """Its value is ``ContentItem.author`` rather than a metadata key, so
        import and export both take it off the item's author attribute.
        """
        return self._creator_columns()[0]

    @property
    def columns(self) -> tuple[str, ...]:
        return tuple(field.column for field in self.fields if field.column is not None)

    @property
    def known_keys(self) -> frozenset[str]:
        """Storage drops these from the leftover metadata blob, so a value is
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
            DetailField("series", FieldKind.FREE_FORM, template_column="series"),
            DetailField(
                "series_index", FieldKind.FREE_FORM, template_column="series_index"
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
                "seasons_watched_dates",
                FieldKind.FREE_FORM,
                template_column="seasons_watched_dates",
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


#: The field each type carries its creator in, where ``author`` is not set.
CREATOR_FIELDS: dict[str, DetailField] = {
    content_type: field
    for content_type, spec in DETAIL_FIELDS.items()
    for field in spec.fields
    if field.kind is FieldKind.CREATOR
}

#: The field each type states its work's release year in. A book declares none
#: on purpose: ``year_published`` is the edition's year, so a 1965 Dune and a
#: 2011 reprint would read as two works.
RELEASE_YEAR_FIELDS: dict[str, DetailField] = {
    content_type: field
    for content_type, spec in DETAIL_FIELDS.items()
    for field in spec.fields
    if field.metadata_key == "release_year"
}


def _assert_select_aliases_are_unique() -> None:
    """The joined read hands every detail column to one ``sqlite3.Row``, which
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
    """Without this an added ``ContentType`` raises ``KeyError`` wherever it is
    first read, which is exactly the write-but-never-read class of failure the
    declaration exists to stop.
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
