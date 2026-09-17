"""No door stores a blank ``review``, whichever one it arrives at, because a
stored ``""`` is indistinguishable from one the user wrote and would refuse
every later import for that column.
"""

import json
import sqlite3
import threading
from collections.abc import Generator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Literal, TypeVar

from src.enrichment.provider_base import pins_of
from src.ingestion.source_labels import source_label
from src.models.content import (
    ConsumptionStatus,
    ContentItem,
    ContentType,
    EnrichmentFilter,
    ExternalId,
    get_enum_value,
)
from src.models.detail_fields import (
    COVER_FIELD,
    CREATOR_FIELDS,
    DETAIL_FIELDS,
    PIN_KEY,
    RELEASE_YEAR_FIELDS,
    ContentTypeFields,
    FieldKind,
    FieldOwner,
    ledger_field,
)
from src.storage.derived import (
    MatchSignals,
    read_match_signals,
    signals_conflict,
    write_derived_columns,
)
from src.storage.duplicates import (
    DeclinedPair,
    SuggestionPage,
    decline_duplicate,
    find_duplicate_suggestions,
    list_declines,
    undecline_duplicate,
)
from src.storage.field_rebuild import (
    fields_leaving_the_group,
    item_writer_ranks,
    rebuild_item_fields,
)
from src.storage.field_writes import (
    LEGACY_WRITER,
    MANUAL_WRITER,
    FieldWrite,
    FieldWriter,
    WriterBand,
    drop_manual_field,
    parse_manual_fields,
    read_manual_fields,
    record_field_writes,
    stated_writes,
)
from src.storage.item_merges import (
    MergeEvidence,
    MergeRecord,
    absorb_item,
    list_merges,
    unmerge_item,
)
from src.storage.merge import (
    ALLOWED_DETAIL_TABLES,
    assert_known_detail_table,
    detail_join,
    normalize_title_for_matching,
    resolve_status_forward,
    stated_region,
)
from src.storage.schema import REBUILD_FUNCTION, create_schema, get_default_user_id
from src.utils.dates import local_today, merge_seasons_watched_dates, utc_now
from src.utils.series import (
    all_seasons_watched,
    reconcile_seasons,
    seasons_watched_for_completed,
    status_for_seasons_watched,
)
from src.utils.sorting import (
    SearchMatchTier,
    normalize_for_search,
    search_text_match_tier,
)
from src.utils.text import escape_lone_surrogates


class Unset(Enum):
    """A single-member enum rather than a bare object so that ``mypy`` narrows
    ``value is not UNSET`` to the argument's real type.
    """

    UNSET = "unset"


#: Marks an argument the caller did not supply, which ``None`` cannot mean
#: on a nullable field: ``None`` clears the value, ``UNSET`` leaves it alone.
UNSET = Unset.UNSET

#: How an enrichment-queue query treats items that settled as ``not_found``:
#: leave them out, or return them alone.
NotFoundMode = Literal["exclude", "only"]

#: A caller a zone ahead of the server calls tomorrow "today". Further ahead is
#: a day nobody has lived, and an item dated there heads the variety ladder
#: until the date arrives.
MAX_COMPLETION_DATE_SKEW = timedelta(days=1)


class FutureCompletionDateError(ValueError):
    """Distinct from a bare ``ValueError`` so a caller naming a date can tell
    this refusal from a malformed one and say which it hit.
    """

    def __init__(self) -> None:
        super().__init__("A completion date cannot be in the future.")


class UncorrectableFieldError(ValueError):
    """A correction naming a field the content type does not state — its own
    type so a broken ``DETAIL_FIELDS`` is not answered as a bad request too."""


class SaveOutcome(Enum):
    """``UNCHANGED`` means every column already held the value the write carried,
    not merely that the row existed.
    """

    ADDED = "added"
    UPDATED = "updated"
    UNCHANGED = "unchanged"


@dataclass(frozen=True)
class SavedItem:
    db_id: int
    outcome: SaveOutcome


@dataclass
class SaveCounts:
    added: int = 0
    updated: int = 0
    unchanged: int = 0

    def record(self, outcome: SaveOutcome) -> None:
        if outcome is SaveOutcome.ADDED:
            self.added += 1
        elif outcome is SaveOutcome.UPDATED:
            self.updated += 1
        else:
            self.unchanged += 1


_T = TypeVar("_T")


def unset_if_none(value: _T | None) -> _T | Unset:
    """For surfaces whose absence *is* ``None`` and which therefore cannot ask
    for a clear this way — a Click option nobody passed.
    """
    return UNSET if value is None else value


def _without_surrogates(value: Any) -> Any:
    """Recurses because ``metadata`` is free-form and reaches a text column whole."""
    if isinstance(value, str):
        return escape_lone_surrogates(value)
    if isinstance(value, dict):
        return {
            _without_surrogates(key): _without_surrogates(inner)
            for key, inner in value.items()
        }
    if isinstance(value, list):
        return [_without_surrogates(inner) for inner in value]
    return value


def _surrogate_free(item: ContentItem) -> ContentItem:
    """SQLite refuses to bind the surrogate ``surrogateescape`` returns for an
    undecodable byte.
    """
    return item.model_copy(update=_without_surrogates(item.model_dump()))


# Chunk size for IN clauses, staying well within SQLite's
# SQLITE_LIMIT_VARIABLE_NUMBER default of 999.
_IN_CLAUSE_CHUNK_SIZE = 500

# ORDER BY clause for each sort_by option get_content_items() accepts. Every
# one ends in ci.id so the ordering is total: a page boundary falling inside a
# tie would let two adjacent pages repeat one row and drop another.
_SORT_ORDER_BY: dict[str, str] = {
    "title": "ci.sort_title ASC, ci.id ASC",
    "updated_at": "ci.updated_at DESC, ci.id ASC",
    "rating": "ci.rating DESC NULLS LAST, ci.title ASC, ci.id ASC",
    "created_at": "ci.created_at DESC, ci.id ASC",
}

# Whitelist of valid sort_by options for get_content_items().
VALID_SORT_OPTIONS: frozenset[str] = frozenset(_SORT_ORDER_BY)

_MANUAL_FIELDS_TERM = (
    "(SELECT json_group_array(w.field)"
    " FROM content_item_field_writes w"
    " WHERE w.content_item_id = ci.id"
    f" AND w.writer_kind = '{WriterBand.MANUAL.value}') as manual_fields"
)

# Columns the enrichment join contributes, read back by _row_is_enriched.
_ENRICHMENT_SELECT_TERMS = (
    "es.content_item_id as enrichment_item_id",
    "es.needs_enrichment",
    "es.enrichment_provider",
    "es.enrichment_quality",
    "es.enrichment_error",
)


def _select_term(table_alias: str, column: str, alias: str) -> str:
    if alias == column:
        return f"{table_alias}.{column}"
    return f"{table_alias}.{column} as {alias}"


def _detail_select_terms(spec: ContentTypeFields) -> list[str]:
    assert_known_detail_table(spec)

    terms = []
    for detail_field in spec.fields:
        column = detail_field.column
        if column is None:
            continue
        terms.append(
            _select_term(spec.table_alias, column, detail_field.select_alias or column)
        )
    terms.append(_select_term(spec.table_alias, "metadata", spec.metadata_alias))
    return terms


def _build_content_item_from() -> str:
    """Shared by the full read and by the search-candidate projection so a WHERE
    clause built once stays valid against both.
    """
    joins = [detail_join(spec) for spec in DETAIL_FIELDS.values()]
    joins.append("LEFT JOIN enrichment_status es ON ci.id = es.content_item_id")
    join_list = "\n    ".join(joins)
    return f"\n    FROM content_items ci\n    {join_list}\n"


_CONTENT_ITEM_FROM = _build_content_item_from()


# A JSON object, not a delimited string: a source name or id may hold any
# character. The group's ids, as two terms rather than a COALESCE no index seeks.
_EXTERNAL_IDS_TERM = (
    "(SELECT json_group_array(json_object("
    "'source', x.source, 'external_id', x.external_id,"
    " 'named', sc.display_name, 'plugin', sc.plugin))"
    " FROM content_item_external_ids x"
    " LEFT JOIN source_configs sc"
    " ON sc.user_id = x.user_id AND sc.source_id = x.source"
    " WHERE x.content_item_id = ci.id"
    " OR x.content_item_id IN (SELECT owner.id FROM content_items owner"
    " WHERE owner.merged_into = ci.id)) as external_ids"
)

# Steam's app 440 and GOG's product 440 are different games, hence the source.
# ``x.user_id`` repeats ``ci.user_id`` because SQLite carries no equality
# across a join, and an unconstrained id table has no index to seek.
_ITEM_ID_BY_SOURCE_EXTERNAL_ID = (
    "SELECT COALESCE(ci.merged_into, ci.id) AS id FROM content_items ci "
    "JOIN content_item_external_ids x ON x.content_item_id = ci.id "
    "WHERE x.user_id = :user_id AND x.source = :source "
    "AND x.external_id = :external_id "
    "AND ci.user_id = :user_id AND ci.content_type = :content_type"
)

# A merge group holding another id from the incoming source is that source's
# other item; the guard spans the group because the SELECT answers as one.
_TITLE_MATCH_CANDIDATES = """
    SELECT COALESCE(ci.merged_into, ci.id) AS id, ci.title AS title
    FROM content_items ci
    WHERE ci.user_id = ? AND ci.content_type = ? AND ci.normalized_title = ?
      AND NOT EXISTS (
          SELECT 1 FROM content_item_external_ids x
          WHERE (x.content_item_id = COALESCE(ci.merged_into, ci.id)
                 OR x.content_item_id IN (
                     SELECT owner.id FROM content_items owner
                     WHERE owner.merged_into = COALESCE(ci.merged_into, ci.id)))
            AND x.source = ? AND x.external_id != ?
      )
"""


def _incoming_creator(item: ContentItem, content_type_value: str) -> str | None:
    """The creator the item being saved states, as the ledger records it:
    ``author``, or the type's creator key, which is where every game, film and
    show plugin puts it."""
    field = CREATOR_FIELDS.get(content_type_value)
    if item.author or field is None:
        return item.author
    stated: str | None = field.store(field.value_from(item.metadata or {}))
    return stated


def _incoming_signals(item: ContentItem, content_type_value: str) -> MatchSignals:
    return MatchSignals(
        creator=_incoming_creator(item, content_type_value),
        region=stated_region(item.title),
    )


def _spelling(title: str | None) -> str:
    return " ".join((title or "").split()).casefold()


def _metadata_blob(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _corrected_fields(
    *,
    title: str | Unset,
    status: str | Unset,
    rating: int | None | Unset,
    review: str | None | Unset,
    genres: list[str] | None,
    tags: list[str] | None,
    description: str | None,
    release_year: int | None,
    creator: str | None,
) -> dict[str, Any]:
    """What an edit stated, field by field, as the column stores it. A supplied
    ``None`` clears the value and is still a correction, so the two sentinels
    cannot be read as one.
    """
    unsent: dict[str, Any] = {
        "title": title,
        "status": status,
        "rating": rating,
        "review": review,
    }
    absent: dict[str, Any] = {
        "genres": genres,
        "tags": tags,
        "description": description,
        "release_year": release_year,
        "creator": creator,
    }
    stated = {field: value for field, value in unsent.items() if value is not UNSET}
    stated.update(
        {field: value for field, value in absent.items() if value is not None}
    )
    if "review" in stated:
        stated["review"] = (stated["review"] or "").strip() or None
    return stated


def _assert_correctable(
    content_type: str, release_year: int | None, creator: str | None
) -> None:
    """Refused before anything is written: a book states no release year of its
    own, so a correction naming one names a field no column could hold.
    """
    for name, field, value in (
        ("release year", RELEASE_YEAR_FIELDS.get(content_type), release_year),
        ("creator", CREATOR_FIELDS.get(content_type), creator),
    ):
        if value is None:
            continue
        if field is None or field.column is None:
            raise UncorrectableFieldError(f"A {content_type} has no {name} to correct.")


def _title_match(
    cursor: sqlite3.Cursor,
    user_id: int,
    content_type_value: str,
    normalized_title: str,
    item: ContentItem,
) -> int | None:
    """Each candidate is weighed against what it states, so a spelling it rules
    out is not answered to.
    """
    cursor.execute(
        f"{_TITLE_MATCH_CANDIDATES} ORDER BY ci.id",
        (user_id, content_type_value, normalized_title, item.source, item.id),
    )
    signals = _incoming_signals(item, content_type_value)
    allowed: dict[int, set[str]] = {}
    for row in cursor.fetchall():
        candidate_id = int(row["id"])
        if candidate_id in allowed:
            allowed[candidate_id].add(_spelling(row["title"]))
        elif not signals_conflict(signals, read_match_signals(cursor, candidate_id)):
            allowed[candidate_id] = {_spelling(row["title"])}
    if len(allowed) < 2:
        return next(iter(allowed), None)
    spelled = _spelling(item.title)
    return next(
        (one for one, spellings in allowed.items() if spelled in spellings), None
    )


def _build_content_item_select() -> str:
    """Callers append their own WHERE clause."""
    terms = ["ci.*", _EXTERNAL_IDS_TERM, _MANUAL_FIELDS_TERM]
    for spec in DETAIL_FIELDS.values():
        terms.extend(_detail_select_terms(spec))
    terms.extend(_ENRICHMENT_SELECT_TERMS)
    select_list = ",\n           ".join(terms)
    return f"\n    SELECT {select_list}{_CONTENT_ITEM_FROM}"


_CONTENT_ITEM_SELECT = _build_content_item_select()

# Id and haystack only: a candidate that does not match never costs a
# ContentItem parse.
_SEARCH_CANDIDATE_SELECT = f"\n    SELECT ci.id, ci.search_text{_CONTENT_ITEM_FROM}"

# A WHERE fragment over _CONTENT_ITEM_SELECT's ``es`` alias, so the list filter
# and the per-row flag of _row_is_enriched stay in sync.
_ENRICHED_PREDICATE = (
    "es.content_item_id IS NOT NULL"
    " AND es.needs_enrichment = 0"
    " AND es.enrichment_error IS NULL"
    " AND es.enrichment_provider IS NOT NULL"
    " AND es.enrichment_provider != 'none'"
    " AND es.enrichment_quality != 'not_found'"
)


class SQLiteDB:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._local = threading.local()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        init_conn = sqlite3.connect(self.db_path)
        try:
            init_conn.execute("PRAGMA journal_mode = WAL")
        finally:
            init_conn.close()
        self._ensure_schema()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        # Block (rather than raising SQLITE_BUSY) when another writer holds
        # the lock — required for parallel multi-source sync where two
        # workers may attempt BEGIN IMMEDIATE concurrently.
        conn.execute("PRAGMA busy_timeout = 5000")
        # What the schema's triggers ask before allowing a write to a resolved
        # column. Only _write_ledger_fields switches it on, and only for its own
        # thread, so no other door can answer yes.
        conn.create_function(REBUILD_FUNCTION, 0, self._rebuilding_fields)
        return conn

    def _rebuilding_fields(self) -> int:
        """Thread-scoped, not instance-scoped: sync, enrichment and the cover
        backfill are daemon threads on one manager, and one clearing the flag
        between another's set and its guarded write reopens the guard.
        """
        return int(getattr(self._local, "rebuilding", False))

    @contextmanager
    def connection(self) -> Generator[sqlite3.Connection, None, None]:
        conn = self._get_connection()
        try:
            yield conn
        finally:
            conn.close()

    def _ensure_schema(self) -> None:
        with self.connection() as conn:
            create_schema(conn)

    def save_content_item(self, item: ContentItem, user_id: int | None = None) -> int:
        return self.save_content_item_outcome(item, user_id=user_id).db_id

    def save_content_item_outcome(
        self, item: ContentItem, user_id: int | None = None
    ) -> SavedItem:
        with self.connection() as conn:
            cursor = conn.cursor()
            saved = self._upsert_content_item(cursor, item, user_id)
            conn.commit()
            return saved

    def save_enrichment_metadata(
        self,
        db_id: int,
        item: ContentItem,
        writer: FieldWriter,
        stated: Mapping[str, Any],
    ) -> None:
        """A row absorbed since the batch read it is refused rather
        than redirected: the survivor is enriched on its own turn.
        """
        with self.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT content_type FROM content_items"
                " WHERE id = ? AND merged_into IS NULL",
                (db_id,),
            )
            row = cursor.fetchone()
            if row is None:
                return
            content_type = row["content_type"]
            # SQLite refuses to bind the lone surrogate an undecodable byte
            # leaves, and a provider's word reaches a column through the ledger.
            record_field_writes(
                cursor,
                db_id,
                writer,
                stated_writes(
                    DETAIL_FIELDS[content_type], _without_surrogates(dict(stated))
                ),
            )
            self._write_ledger_fields(
                cursor, db_id, content_type, _surrogate_free(item).metadata or {}
            )
            # Both read what was stored: a creator this filled belongs in the
            # search text, and a season count it raised unfinishes the show.
            write_derived_columns(cursor, db_id)
            if content_type == "tv_show":
                self._handle_tv_season_change(cursor, db_id)
            conn.commit()

    def set_enrichment_pins(self, db_id: int, pins: Mapping[str, str]) -> None:
        """The records the operator bound this item to, theirs to replace: a
        cleared pin writes an empty map. A pin is a rank change, so the rebuild
        runs here rather than at the next sync.
        """
        with self.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT content_type FROM content_items"
                " WHERE id = ? AND merged_into IS NULL",
                (db_id,),
            )
            row = cursor.fetchone()
            if row is None:
                return
            content_type = row["content_type"]
            spec = DETAIL_FIELDS[content_type]
            assert_known_detail_table(spec)
            blob = _metadata_blob(self._detail_row(cursor, spec, db_id).get("metadata"))
            blob[PIN_KEY] = dict(pins)
            self._write_detail_columns(
                cursor, db_id, content_type, {"metadata": json.dumps(blob)}
            )
            self._write_ledger_fields(cursor, db_id, content_type, {})
            write_derived_columns(cursor, db_id)
            conn.commit()

    def record_stated_fields(
        self, db_id: int, writer: FieldWriter, stated: Mapping[str, Any]
    ) -> None:
        """Recording what a writer said, touching no column. Saving the item to
        record a refused offer moved the status a season list decides.
        """
        with self.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT content_type FROM content_items"
                " WHERE id = ? AND merged_into IS NULL",
                (db_id,),
            )
            row = cursor.fetchone()
            if row is None:
                return
            record_field_writes(
                cursor,
                db_id,
                writer,
                stated_writes(
                    DETAIL_FIELDS[row["content_type"]],
                    _without_surrogates(dict(stated)),
                ),
            )
            conn.commit()

    def clear_cover_url(self, db_id: int) -> bool:
        """The url is buried before the clear, so the rebuild never resolves it
        again — otherwise the next sync re-offers the Steam guess that 404s.
        """
        with self.connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT cover_url FROM content_items WHERE id = ?", (db_id,))
            row = cursor.fetchone()
            if row is None or row["cover_url"] is None:
                return False
            cursor.execute(
                "INSERT OR IGNORE INTO content_item_dead_covers"
                " (content_item_id, cover_url) VALUES (?, ?)",
                (db_id, row["cover_url"]),
            )
            cursor.execute(
                "UPDATE content_items SET cover_url = NULL WHERE id = ?", (db_id,)
            )
            conn.commit()
            return True

    def complete_content_item(
        self, item: ContentItem, user_id: int | None = None
    ) -> int:
        """A blank review is not a supplied one and leaves a stored review
        alone; see :meth:`_write_completion`.
        """
        # Taken before the upsert takes it again: _write_completion binds this
        # item's own values, so the upsert's escaped copy never reaches it.
        item = _surrogate_free(item)

        with self.connection() as conn:
            cursor = conn.cursor()
            db_id = self._upsert_content_item(cursor, item, user_id).db_id
            self._write_completion(
                cursor,
                db_id,
                item.rating,
                item.review,
                unset_if_none(item.date_completed),
            )
            conn.commit()
            return db_id

    def _write_completion(
        self,
        cursor: sqlite3.Cursor,
        db_id: int,
        rating: int | None,
        review: str | None,
        date_completed: date | Unset,
    ) -> None:
        """Runs on the caller's cursor so that creating the row and recording
        what the user said about it are one transaction.
        """
        if (
            date_completed is not UNSET
            and date_completed > local_today() + MAX_COMPLETION_DATE_SKEW
        ):
            raise FutureCompletionDateError

        set_parts = ["status = 'completed'", "updated_at = CURRENT_TIMESTAMP"]
        params: list[Any] = []
        if date_completed is not UNSET:
            set_parts.append("date_completed = ?")
            params.append(date_completed.isoformat())
        else:
            set_parts.append("date_completed = COALESCE(date_completed, ?)")
            params.append(local_today().isoformat())
        if rating is not None:
            set_parts.append("rating = ?")
            params.append(rating)
        if review is not None and review.strip():
            set_parts.append("review = ?")
            params.append(review)
        params.append(db_id)
        cursor.execute(
            f"UPDATE content_items SET {', '.join(set_parts)} WHERE id = ?",
            params,
        )

    def _upsert_content_item(
        self, cursor: sqlite3.Cursor, item: ContentItem, user_id: int | None
    ) -> SavedItem:
        """Runs on the caller's cursor and does not commit, so a caller can add
        writes to it.
        """
        # The one door every plugin's items pass, so the SQLite text guarantee
        # is taken here rather than in each of them.
        item = _surrogate_free(item)

        effective_user_id = (
            user_id
            if user_id is not None
            else (item.user_id if item.user_id is not None else get_default_user_id())
        )

        content_type_value = get_enum_value(item.content_type)

        # This leg fills the column only while it is empty, and a stored blank
        # is indistinguishable from something the user wrote, so filling with
        # one would refuse every later value and block the field for good.
        incoming_review = item.review if item.review and item.review.strip() else None
        incoming_cover = item.cover_url or None

        existing_id: int | None = None
        if item.id and item.source:
            cursor.execute(
                _ITEM_ID_BY_SOURCE_EXTERNAL_ID,
                {
                    "user_id": effective_user_id,
                    "source": item.source,
                    "external_id": item.id,
                    "content_type": content_type_value,
                },
            )
            row = cursor.fetchone()
            if row:
                existing_id = int(row["id"])

        normalized_title = (
            normalize_title_for_matching(item.title) if item.title else ""
        )

        # Dedup only on first contact, updating the match in place: absorbing a
        # row this sync did not match destroyed the ids its own source held.
        # Oldest first, so a title collision resolves the same way twice.
        if existing_id is None and normalized_title:
            existing_id = _title_match(
                cursor,
                effective_user_id,
                content_type_value,
                normalized_title,
                item,
            )

        if existing_id is not None:
            # The sync door: user-owned fields are filled only while they are
            # empty, never overwritten, and a None incoming value states
            # nothing to fill from.
            cursor.execute(
                "SELECT source, status, rating, review, date_completed, ignored"
                " FROM content_items WHERE id = ?",
                (existing_id,),
            )
            existing_row = cursor.fetchone()

            # The person's own columns alone: title and the cover are stated by
            # writers, so the rebuild below decides them from the ledger.
            offered: dict[str, str | int | None] = {
                "status": resolve_status_forward(
                    existing_row["status"], get_enum_value(item.status)
                ),
            }

            # Fill-only: each sync claiming it reported every shared item updated.
            if existing_row["source"] is None and item.source is not None:
                offered["source"] = item.source

            # Rating and review: fill only — never overwrite the user's own.
            if existing_row["rating"] is None and item.rating is not None:
                offered["rating"] = item.rating
            if existing_row["review"] is None and incoming_review is not None:
                offered["review"] = incoming_review

            # Date completed: fill only from a later incoming date.
            if item.date_completed is not None:
                incoming_date_str = item.date_completed.isoformat()
                existing_date_str = existing_row["date_completed"]
                if existing_date_str is None or incoming_date_str > existing_date_str:
                    offered["date_completed"] = incoming_date_str

            # Ignored: only a stated value counts. True and False both win —
            # that is how an edited export un-ignores an item — while None
            # means the source said nothing and the stored flag stands.
            if item.ignored is not None:
                offered["ignored"] = 1 if item.ignored else 0

            # A held field is the operator's, so the sync withdraws the offer.
            # Only these reach here: no rank protects a column the ledger does
            # not rebuild.
            for field in read_manual_fields(cursor, existing_id):
                offered.pop(field, None)

            # Writing only the columns that actually move is what lets a
            # re-sync report itself as unchanged, and keeps ``updated_at`` — a
            # user-facing sort key — off a row nothing happened to.
            changed = {
                column: value
                for column, value in offered.items()
                if existing_row[column] != value
            }
            if changed:
                set_clause = ", ".join(f"{column} = ?" for column in changed)
                cursor.execute(
                    f"UPDATE content_items SET {set_clause},"
                    " updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    [*changed.values(), existing_id],
                )
            db_id = existing_id
            row_changed = bool(changed)
        else:
            cursor.execute(
                "INSERT INTO content_items "
                "(user_id, title, normalized_title, content_type, "
                "status, rating, review, date_completed, source, ignored, cover_url) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    effective_user_id,
                    item.title,
                    normalized_title,
                    content_type_value,
                    get_enum_value(item.status),
                    item.rating,
                    incoming_review,
                    (item.date_completed.isoformat() if item.date_completed else None),
                    item.source,
                    1 if item.ignored else 0,
                    incoming_cover,
                ),
            )
            lastrowid = cursor.lastrowid
            if lastrowid is None:
                raise RuntimeError("INSERT did not return a row ID")
            db_id = lastrowid
            row_changed = True

        stated = dict(item.metadata or {})
        stated["title"] = item.title
        stated["cover_url"] = incoming_cover
        # An item nobody sourced is the library's own word, which every named
        # writer outranks.
        writer = (
            FieldWriter(WriterBand.SOURCE, item.source)
            if item.source
            else LEGACY_WRITER
        )
        record_field_writes(
            cursor,
            db_id,
            writer,
            stated_writes(DETAIL_FIELDS[content_type_value], stated, item.author),
        )

        # On both paths: a source that lost the dedup race would otherwise never
        # attach its id, and take the title path again on every later sync.
        row_changed |= self._record_external_id(
            cursor, db_id, effective_user_id, item, content_type_value
        )

        detail_changed = self._write_ledger_fields(
            cursor, db_id, content_type_value, item.metadata or {}
        )

        # After the detail write, so the derived columns read the creator that
        # was actually stored rather than the one this sync offered.
        write_derived_columns(cursor, db_id)

        season_status_moved = content_type_value == "tv_show" and (
            self._handle_tv_season_change(cursor, db_id)
        )

        if existing_id is None:
            outcome = SaveOutcome.ADDED
        elif row_changed or detail_changed or season_status_moved:
            outcome = SaveOutcome.UPDATED
        else:
            outcome = SaveOutcome.UNCHANGED
        return SavedItem(db_id=db_id, outcome=outcome)

    @staticmethod
    def _record_external_id(
        cursor: sqlite3.Cursor,
        db_id: int,
        user_id: int,
        item: ContentItem,
        content_type: str,
    ) -> bool:
        """Hold *item*'s id under its source; OR IGNORE, so a re-sync is no change."""
        if not (item.id and item.source):
            return False
        cursor.execute(
            "INSERT OR IGNORE INTO content_item_external_ids "
            "(content_item_id, user_id, source, external_id, content_type) "
            "VALUES (?, ?, ?, ?, ?)",
            (db_id, user_id, item.source, item.id, content_type),
        )
        return cursor.rowcount > 0

    def _write_ledger_fields(
        self,
        cursor: sqlite3.Cursor,
        db_id: int,
        content_type: str,
        stated_metadata: Mapping[str, Any],
        clear_fields: frozenset[str] = frozenset(),
    ) -> bool:
        """Record first, then rebuild: every writer-owned column holds what the
        ledger says by rank, and only one whose value moves is written.
        """
        spec = DETAIL_FIELDS[content_type]
        assert_known_detail_table(spec)
        existing = self._detail_row(cursor, spec, db_id)
        stored_blob = _metadata_blob(existing.get("metadata"))
        resolved = rebuild_item_fields(
            cursor, db_id, item_writer_ranks(cursor, frozenset(pins_of(stored_blob)))
        )
        self._local.rebuilding = True
        try:
            base_changed = self._write_base_columns(
                cursor, db_id, resolved, clear_fields
            )
            detail_changed = self._write_detail_row(
                cursor,
                spec,
                db_id,
                existing,
                stored_blob,
                stated_metadata,
                resolved,
                clear_fields,
            )
        finally:
            self._local.rebuilding = False
        return base_changed or detail_changed

    @staticmethod
    def _detail_row(
        cursor: sqlite3.Cursor, spec: ContentTypeFields, db_id: int
    ) -> dict[str, Any]:
        cursor.execute(
            f"SELECT * FROM {spec.table} WHERE content_item_id = ?", (db_id,)
        )
        row = cursor.fetchone()
        return {} if row is None else dict(row)

    @staticmethod
    def _write_base_columns(
        cursor: sqlite3.Cursor,
        db_id: int,
        resolved: Mapping[str, Any],
        clear_fields: frozenset[str] = frozenset(),
    ) -> bool:
        """Title and the cover are the only ``content_items`` columns a writer
        states; status, rating, review and the rest are the person's own.
        """
        offered: dict[str, Any] = {}
        if (title := resolved.get("title")) is not None:
            offered["title"] = title
            offered["normalized_title"] = normalize_title_for_matching(str(title))
        if (cover := resolved.get(COVER_FIELD)) is not None:
            offered[COVER_FIELD] = cover
        elif COVER_FIELD in clear_fields:
            # Never the title beside it: a row that lost its own would be
            # unfindable in every list this library offers.
            offered[COVER_FIELD] = None
        if not offered:
            return False

        cursor.execute(
            "SELECT title, normalized_title, cover_url FROM content_items WHERE id = ?",
            (db_id,),
        )
        row = cursor.fetchone()
        changed = {
            column: value for column, value in offered.items() if row[column] != value
        }
        if not changed:
            return False
        set_clause = ", ".join(f"{column} = ?" for column in changed)
        cursor.execute(
            f"UPDATE content_items SET {set_clause},"
            " updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            [*changed.values(), db_id],
        )
        return True

    def _write_detail_row(
        self,
        cursor: sqlite3.Cursor,
        spec: ContentTypeFields,
        db_id: int,
        existing: Mapping[str, Any],
        stored_blob: Mapping[str, Any],
        stated_metadata: Mapping[str, Any],
        resolved: Mapping[str, Any],
        clear_fields: frozenset[str] = frozenset(),
    ) -> bool:
        """A field no writer states is left as it stands: saying nothing is not
        a claim that the column is empty. A field in *clear_fields* is one: its
        only writers left with the row an undo sent back.
        """
        values: dict[str, Any] = {}
        for detail_field in spec.fields:
            column = detail_field.column
            if column is None or detail_field.owner is not FieldOwner.WRITER:
                continue
            if detail_field.metadata_key in resolved:
                values[column] = detail_field.store(resolved[detail_field.metadata_key])
            elif detail_field.metadata_key in clear_fields:
                values[column] = None
        values["metadata"] = self._merged_blob(
            spec, stored_blob, stated_metadata, resolved
        )

        if existing:
            # A column already holding the value this write carries is not an
            # update of anything, which is what lets a re-sync report unchanged.
            changed = {
                name: value
                for name, value in values.items()
                if existing.get(name) != value
            }
            if not changed:
                return False
            set_clauses = ", ".join(f"{name} = ?" for name in changed)
            cursor.execute(
                f"UPDATE {spec.table} SET {set_clauses} WHERE content_item_id = ?",
                [*changed.values(), db_id],
            )
            return True

        col_list = ", ".join(["content_item_id", *values])
        placeholders = ", ".join("?" for _ in range(len(values) + 1))
        cursor.execute(
            f"INSERT INTO {spec.table} ({col_list}) VALUES ({placeholders})",
            [db_id, *values.values()],
        )
        return True

    @staticmethod
    def _merged_blob(
        spec: ContentTypeFields,
        stored: Mapping[str, Any],
        stated: Mapping[str, Any],
        resolved: Mapping[str, Any],
    ) -> str | None:
        """What the ledger resolved, over the keys no column and no writer
        claims: a writer's own ids, and the person's own watching.
        """
        incoming = {
            key: value for key, value in stated.items() if key not in spec.known_keys
        }
        # Stored keys take precedence, incoming fills gaps.
        merged: dict[str, Any] = {**incoming, **stored}
        # Only against a statement: with nothing incoming both rules below can
        # merely re-derive the stored blob, re-ticking a season the operator
        # just unticked through the edit door.
        if incoming:
            # Exception: the season fields, which no one source states alone.
            merged.update(reconcile_seasons(stored, incoming))
            # Exception: seasons_watched_dates keeps the later date per season,
            # so an earlier sync never overwrites a later watch. A None result —
            # both sides unparseable — leaves the merge above in place.
            combined_dates = merge_seasons_watched_dates(
                stored.get("seasons_watched_dates"),
                incoming.get("seasons_watched_dates"),
            )
            if combined_dates is not None:
                merged["seasons_watched_dates"] = combined_dates
        for detail_field in spec.fields:
            key = detail_field.metadata_key
            if (
                detail_field.column is None
                and detail_field.owner is FieldOwner.WRITER
                and key in resolved
            ):
                merged[key] = resolved[key]
        return json.dumps(merged) if merged else None

    def _handle_tv_season_change(self, cursor: sqlite3.Cursor, db_id: int) -> bool:
        """Status follows the season list: a show completes when every season of
        a known total is watched, and returns to currently_consuming when a new
        season appears. An ignored show is never touched.
        """
        cursor.execute(
            "SELECT ci.status, ci.ignored, td.seasons, td.metadata"
            " FROM content_items ci"
            " JOIN tv_show_details td ON ci.id = td.content_item_id"
            " WHERE ci.id = ?",
            (db_id,),
        )
        row = cursor.fetchone()
        if not row or row["ignored"]:
            return False

        total_seasons = row["seasons"]
        metadata_raw = row["metadata"]
        if not total_seasons or not metadata_raw:
            return False
        try:
            metadata = json.loads(metadata_raw)
        except (json.JSONDecodeError, TypeError):
            return False

        seasons_watched = metadata.get("seasons_watched")
        if not isinstance(seasons_watched, list):
            return False

        finished = all_seasons_watched(seasons_watched, total_seasons)
        if finished == (row["status"] == "completed"):
            return False

        # The one write a status hold does not refuse: the season list deciding
        # it is held by nobody, so a season the operator has not seen makes the
        # status they set stale rather than a correction.
        cursor.execute(
            "UPDATE content_items SET status = ?,"
            " updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            ("completed" if finished else "currently_consuming", db_id),
        )
        return True

    def get_content_item(
        self, db_id: int, user_id: int | None = None
    ) -> ContentItem | None:
        with self.connection() as conn:
            return self._item_on_cursor(conn.cursor(), db_id, user_id)

    def _item_on_cursor(
        self, cursor: sqlite3.Cursor, db_id: int, user_id: int | None
    ) -> ContentItem | None:
        query = _CONTENT_ITEM_SELECT + " WHERE ci.id = ? AND ci.merged_into IS NULL"
        params: list[Any] = [db_id]

        if user_id is not None:
            query += " AND ci.user_id = ?"
            params.append(user_id)

        cursor.execute(query, params)
        row = cursor.fetchone()
        return self._row_to_content_item(row) if row else None

    def get_content_items_by_db_ids(self, db_ids: list[int]) -> list[ContentItem]:
        """Ids come back in the order asked for; one naming no row is skipped,
        and a repeated one returns once per occurrence.
        """
        if not db_ids:
            return []

        with self.connection() as conn:
            return self._items_by_db_ids(conn.cursor(), db_ids)

    def _items_by_db_ids(
        self, cursor: sqlite3.Cursor, db_ids: list[int]
    ) -> list[ContentItem]:
        """Chunked so the IN clause stays within SQLite's variable limit, and
        re-ordered afterwards because the chunks come back in whatever order
        each query chose.
        """
        rows: dict[int, sqlite3.Row] = {}
        for i in range(0, len(db_ids), _IN_CLAUSE_CHUNK_SIZE):
            chunk = db_ids[i : i + _IN_CLAUSE_CHUNK_SIZE]
            placeholders = ", ".join("?" for _ in chunk)
            query = (
                f"{_CONTENT_ITEM_SELECT} WHERE ci.id IN ({placeholders})"
                " AND ci.merged_into IS NULL"
            )
            cursor.execute(query, chunk)
            rows.update({row["id"]: row for row in cursor.fetchall()})
        return [
            self._row_to_content_item(rows[db_id]) for db_id in db_ids if db_id in rows
        ]

    def get_content_items(
        self,
        user_id: int | None = None,
        content_type: ContentType | None = None,
        status: ConsumptionStatus | list[ConsumptionStatus] | None = None,
        min_rating: int | None = None,
        unrated_only: bool = False,
        limit: int | None = None,
        offset: int = 0,
        sort_by: str = "title",
        include_ignored: bool = True,
        ignored_only: bool = False,
        enrichment: EnrichmentFilter | None = None,
        search: str | None = None,
    ) -> list[ContentItem]:
        """A falsy *limit* is no limit, and *offset* is independent of it: an
        offset with no limit skips and returns the rest.
        """
        # An empty status list matches nothing by definition.
        if isinstance(status, list) and not status:
            return []

        if sort_by not in _SORT_ORDER_BY:
            raise ValueError(f"Invalid sort_by: {sort_by!r}")

        # Normalize the search term; an empty/whitespace term is a no-op.
        search_term = search.strip() if search else ""

        where, params = self._build_item_filters(
            user_id=user_id if user_id is not None else get_default_user_id(),
            content_type=content_type,
            status=status,
            min_rating=min_rating,
            unrated_only=unrated_only,
            include_ignored=include_ignored,
            ignored_only=ignored_only,
            enrichment=enrichment,
        )
        order_by = _SORT_ORDER_BY[sort_by]

        with self.connection() as conn:
            cursor = conn.cursor()
            if search_term:
                return self._search_page(
                    cursor, where, params, order_by, limit, offset, search_term
                )
            return self._fetch_page(cursor, where, params, order_by, limit, offset)

    @staticmethod
    def _build_item_filters(
        user_id: int,
        content_type: ContentType | None,
        status: ConsumptionStatus | list[ConsumptionStatus] | None,
        min_rating: int | None,
        unrated_only: bool,
        include_ignored: bool,
        ignored_only: bool,
        enrichment: EnrichmentFilter | None,
    ) -> tuple[str, list[Any]]:
        where = " WHERE ci.user_id = ? AND ci.merged_into IS NULL"
        params: list[Any] = [user_id]

        if content_type is not None:
            where += " AND ci.content_type = ?"
            params.append(get_enum_value(content_type))

        if enrichment == "enriched":
            where += f" AND ({_ENRICHED_PREDICATE})"
        elif enrichment == "not_enriched":
            where += f" AND NOT ({_ENRICHED_PREDICATE})"

        if status is not None:
            if isinstance(status, list):
                placeholders = ", ".join("?" for _ in status)
                where += f" AND ci.status IN ({placeholders})"
                params.extend(get_enum_value(s) for s in status)
            else:
                where += " AND ci.status = ?"
                params.append(get_enum_value(status))

        if min_rating is not None:
            where += " AND ci.rating >= ?"
            params.append(min_rating)

        if unrated_only:
            where += " AND ci.rating IS NULL"

        if not include_ignored:
            where += " AND (ci.ignored = 0 OR ci.ignored IS NULL)"

        if ignored_only:
            where += " AND ci.ignored = 1"

        return where, params

    @staticmethod
    def _page_clause(limit: int | None, offset: int) -> tuple[str, list[Any]]:
        """SQLite accepts OFFSET only as a suffix of LIMIT, so an offset with no
        limit uses -1, SQLite's "unbounded" limit.
        """
        if not limit and offset <= 0:
            return "", []
        clause = " LIMIT ?"
        params: list[Any] = [limit or -1]
        if offset > 0:
            clause += " OFFSET ?"
            params.append(offset)
        return clause, params

    def _fetch_page(
        self,
        cursor: sqlite3.Cursor,
        where: str,
        params: list[Any],
        order_by: str,
        limit: int | None,
        offset: int,
    ) -> list[ContentItem]:
        page_clause, page_params = self._page_clause(limit, offset)
        cursor.execute(
            f"{_CONTENT_ITEM_SELECT}{where} ORDER BY {order_by}{page_clause}",
            [*params, *page_params],
        )
        return [self._row_to_content_item(row) for row in cursor.fetchall()]

    def _search_page(
        self,
        cursor: sqlite3.Cursor,
        where: str,
        params: list[Any],
        order_by: str,
        limit: int | None,
        offset: int,
        search_term: str,
    ) -> list[ContentItem]:
        """One matched set, ranked by the tier each item answered on. The rank is
        a property of the whole set, not of a page, so the pages of a search
        concatenate into the unpaged answer.
        """
        needle = normalize_for_search(search_term)
        # A term of pure punctuation normalizes away, and an empty needle
        # would otherwise be a substring of every stored search text.
        if not needle:
            return []

        # Read the way _page_clause reads them: only a positive limit bounds
        # the page — it hands a negative one to SQLite, where it means
        # unbounded — and a non-positive offset skips nothing.
        start = max(offset, 0)
        page_end = start + limit if limit and limit > 0 else None

        cursor.execute(f"{_SEARCH_CANDIDATE_SELECT}{where} ORDER BY {order_by}", params)
        matched: list[tuple[SearchMatchTier, int]] = []
        for row in cursor:
            tier = search_text_match_tier(row["search_text"], needle)
            if tier is not None:
                matched.append((tier, row["id"]))

        # Keyed on the tier alone, and stable, so the caller's sort still orders
        # the items within one.
        matched.sort(key=lambda match: match[0])
        page = [db_id for _, db_id in matched[start:page_end]]
        return self._items_by_db_ids(cursor, page) if page else []

    def get_unconsumed_items(
        self,
        user_id: int | None = None,
        content_type: ContentType | None = None,
        limit: int | None = None,
        include_ignored: bool = True,
    ) -> list[ContentItem]:
        return self.get_content_items(
            user_id=user_id,
            content_type=content_type,
            status=[ConsumptionStatus.UNREAD, ConsumptionStatus.CURRENTLY_CONSUMING],
            limit=limit,
            include_ignored=include_ignored,
        )

    def get_completed_items(
        self,
        user_id: int | None = None,
        content_type: ContentType | None = None,
        min_rating: int | None = None,
        limit: int | None = None,
        include_ignored: bool = True,
        sort_by: str = "title",
    ) -> list[ContentItem]:
        return self.get_content_items(
            user_id=user_id,
            content_type=content_type,
            status=[ConsumptionStatus.COMPLETED, ConsumptionStatus.CURRENTLY_CONSUMING],
            min_rating=min_rating,
            limit=limit,
            include_ignored=include_ignored,
            sort_by=sort_by,
        )

    def _row_to_content_item(self, row: sqlite3.Row) -> ContentItem:
        """A column it does not carry raises rather than reading as absent
        data: the aliases are generated from DETAIL_FIELDS, so a name that
        misses is a bug in the declaration and not a value the row lacks.
        """
        content_type = ContentType(row["content_type"])
        metadata: dict[str, Any] = {}
        author: str | None = None

        spec = DETAIL_FIELDS[content_type.value]
        for detail_field in spec.fields:
            column = detail_field.column
            if column is None:
                continue
            raw = row[detail_field.select_alias or column]
            if detail_field.kind is FieldKind.CREATOR:
                author = raw
                continue
            value = detail_field.codec.load(raw)
            if value:
                metadata[detail_field.metadata_key] = value

        # The blob last, so a key it repeats wins over the column that claims it.
        if blob := row[spec.metadata_alias]:
            try:
                leftover = json.loads(blob)
            except (json.JSONDecodeError, TypeError):
                leftover = None
            if isinstance(leftover, dict):
                metadata.update(leftover)

        date_completed = None
        if date_completed_str := row["date_completed"]:
            try:
                date_completed = datetime.fromisoformat(date_completed_str).date()
            except (ValueError, AttributeError):
                pass

        external_ids = sorted(
            (
                ExternalId(
                    source=pair["source"],
                    external_id=pair["external_id"],
                    display_name=source_label(
                        pair["source"], pair["named"], pair["plugin"]
                    ),
                )
                for pair in json.loads(row["external_ids"])
            ),
            key=lambda pair: (pair.source, pair.external_id),
        )

        # Re-saving what was read must not record a pair no row holds, so the
        # save key carries this item's own source's id and no other.
        own_id = next(
            (pair.external_id for pair in external_ids if pair.source == row["source"]),
            None,
        )

        return ContentItem(
            user_id=row["user_id"],
            id=own_id,
            external_ids=external_ids,
            db_id=row["id"],
            title=row["title"],
            author=author,
            content_type=content_type,
            rating=row["rating"],
            review=row["review"],
            status=ConsumptionStatus(row["status"]),
            date_completed=date_completed,
            cover_url=row["cover_url"],
            source=row["source"],
            ignored=bool(row["ignored"]),
            enriched=self._row_is_enriched(row),
            manual_fields=parse_manual_fields(row["manual_fields"], content_type.value),
            metadata=metadata,
        )

    @staticmethod
    def _row_is_enriched(row: sqlite3.Row) -> bool:
        """Mirrors ``_ENRICHED_PREDICATE`` so the per-row flag and the list
        filter agree: a clean row (real provider, no error, not pending).
        """
        if row["enrichment_item_id"] is None:
            return False
        if row["needs_enrichment"]:
            return False
        if row["enrichment_error"] is not None:
            return False
        if row["enrichment_quality"] == "not_found":
            return False
        provider = row["enrichment_provider"]
        return provider is not None and provider != "none"

    def update_item_from_ui(
        self,
        db_id: int,
        title: str | Unset = UNSET,
        status: str | Unset = UNSET,
        rating: int | None | Unset = UNSET,
        review: str | None | Unset = UNSET,
        seasons_watched: list[int] | None = None,
        genres: list[str] | None = None,
        tags: list[str] | None = None,
        description: str | None = None,
        release_year: int | None = None,
        creator: str | None = None,
        user_id: int | None = None,
    ) -> bool:
        """No status empties the list: the dialog hides the checklist for a show
        whose total never synced. Every field supplied is held from here on.
        """
        if description is not None:
            # Whitespace is not a description: a blank normalises to the clear,
            # in the column and in the row holding it alike.
            description = description.strip()

        with self.connection() as conn:
            cursor = conn.cursor()

            before = self._item_on_cursor(cursor, db_id, user_id)
            if before is None:
                return False

            content_type = get_enum_value(before.content_type)
            existing_status = get_enum_value(before.status)
            resolved_status = existing_status if status is UNSET else status
            corrected = _corrected_fields(
                title=title,
                status=status,
                rating=rating,
                review=review,
                genres=genres,
                tags=tags,
                description=description,
                release_year=release_year,
                creator=creator,
            )

            _assert_correctable(content_type, release_year, creator)

            if content_type == "tv_show":
                cursor.execute(
                    "SELECT seasons, metadata FROM tv_show_details"
                    " WHERE content_item_id = ?",
                    (db_id,),
                )
                tv_row = cursor.fetchone()
                total_seasons = tv_row["seasons"] if tv_row else None
                existing_metadata = _metadata_blob(
                    tv_row["metadata"] if tv_row else None
                )

                if tv_row:
                    if seasons_watched is None:
                        if status == "completed":
                            seasons_watched = seasons_watched_for_completed(
                                total_seasons
                            )
                    elif status is UNSET:
                        resolved_status = status_for_seasons_watched(
                            seasons_watched, total_seasons
                        ).value
                        # Held like a stated one: unheld, the next sync
                        # forward-resolves it back to what it was.
                        corrected["status"] = resolved_status

                if tv_row and seasons_watched is not None:
                    now_iso = utc_now().isoformat()
                    existing_dates = existing_metadata.get("seasons_watched_dates")
                    if not isinstance(existing_dates, dict):
                        existing_dates = {}
                    previously_watched = existing_metadata.get("seasons_watched")
                    if not isinstance(previously_watched, list):
                        previously_watched = []
                    # A season watched before this edit but undated stays
                    # undated rather than being dated now.
                    new_dates: dict[str, str] = {}
                    for season in seasons_watched:
                        key = str(season)
                        if key in existing_dates:
                            new_dates[key] = existing_dates[key]
                        elif season not in previously_watched:
                            new_dates[key] = now_iso
                    existing_metadata["seasons_watched"] = seasons_watched
                    existing_metadata["seasons_watched_dates"] = new_dates
                    cursor.execute(
                        "UPDATE tv_show_details SET metadata = ?"
                        " WHERE content_item_id = ?",
                        (json.dumps(existing_metadata), db_id),
                    )

            set_parts = ["status = ?", "updated_at = CURRENT_TIMESTAMP"]
            params: list[Any] = [resolved_status]
            if rating is not UNSET:
                set_parts.append("rating = ?")
                params.append(rating)
            if review is not UNSET:
                set_parts.append("review = ?")
                params.append(corrected["review"])
            if resolved_status == "completed" and existing_status != "completed":
                set_parts.append("date_completed = COALESCE(date_completed, ?)")
                params.append(local_today().isoformat())
            params.append(db_id)
            cursor.execute(
                f"UPDATE content_items SET {', '.join(set_parts)} WHERE id = ?",
                params,
            )

            record_field_writes(
                cursor,
                db_id,
                MANUAL_WRITER,
                [
                    FieldWrite(ledger_field(content_type, field), value)
                    for field, value in corrected.items()
                ],
            )
            self._write_ledger_fields(cursor, db_id, content_type, {})
            write_derived_columns(cursor, db_id)

            conn.commit()
            return True

    def clear_manual_field(
        self, db_id: int, field: str, user_id: int | None = None
    ) -> bool:
        """The field keeps the value it has and stops being protected: what the
        operator stated decides the column only once the rebuild writes it.
        """
        with self.connection() as conn:
            cursor = conn.cursor()
            item = self._item_on_cursor(cursor, db_id, user_id)
            if item is None:
                return False
            recorded = ledger_field(get_enum_value(item.content_type), field)
            if not drop_manual_field(cursor, db_id, recorded):
                return False
            conn.commit()
            return True

    def _write_detail_columns(
        self,
        cursor: sqlite3.Cursor,
        db_id: int,
        content_type: str,
        updates: dict[str, Any],
    ) -> None:
        spec = DETAIL_FIELDS.get(content_type)
        if spec is None:
            raise ValueError(f"Unknown content_type: {content_type!r}")
        table = spec.table
        if table not in ALLOWED_DETAIL_TABLES:
            raise ValueError(f"Unknown detail table: {table!r}")

        columns = list(updates)
        cursor.execute(f"SELECT 1 FROM {table} WHERE content_item_id = ?", (db_id,))
        if cursor.fetchone() is not None:
            set_clause = ", ".join(f"{name} = ?" for name in columns)
            cursor.execute(
                f"UPDATE {table} SET {set_clause} WHERE content_item_id = ?",
                [*updates.values(), db_id],
            )
        else:
            col_list = ", ".join(["content_item_id", *columns])
            placeholders = ", ".join("?" for _ in range(len(columns) + 1))
            cursor.execute(
                f"INSERT INTO {table} ({col_list}) VALUES ({placeholders})",
                [db_id, *updates.values()],
            )

    def merge_content_items(
        self,
        survivor_id: int,
        absorbed_id: int,
        evidence: MergeEvidence,
        evidence_detail: str | None = None,
        user_id: int | None = None,
    ) -> MergeRecord:
        with self.connection() as conn:
            cursor = conn.cursor()
            record = absorb_item(
                cursor,
                survivor_id,
                absorbed_id,
                evidence,
                evidence_detail=evidence_detail,
                user_id=user_id,
            )
            self._rebuild_merge_group(cursor, survivor_id)
            conn.commit()
            return record

    def unmerge_content_items(
        self, merge_id: int, user_id: int | None = None
    ) -> MergeRecord | None:
        with self.connection() as conn:
            cursor = conn.cursor()
            record = unmerge_item(cursor, merge_id, user_id=user_id)
            if record is not None:
                self._rebuild_merge_group(
                    cursor,
                    record.survivor_id,
                    clear_fields=fields_leaving_the_group(
                        cursor, record.survivor_id, record.absorbed_id
                    ),
                )
            conn.commit()
            return record

    def _rebuild_merge_group(
        self,
        cursor: sqlite3.Cursor,
        survivor_id: int,
        clear_fields: frozenset[str] = frozenset(),
    ) -> None:
        """The group the ledger resolves over just changed, so the survivor is
        rebuilt here: a one-off import may never save this item again. The undo
        leg runs it too, which is what takes these writes back.
        """
        cursor.execute(
            "SELECT content_type FROM content_items WHERE id = ?", (survivor_id,)
        )
        content_type = cursor.fetchone()["content_type"]
        self._write_ledger_fields(
            cursor, survivor_id, content_type, {}, clear_fields=clear_fields
        )
        write_derived_columns(cursor, survivor_id)
        if content_type == "tv_show":
            # The group's highest season count is the survivor's now, so a show
            # completed at the old total has an unwatched season again.
            self._handle_tv_season_change(cursor, survivor_id)

    def list_content_item_merges(self, user_id: int | None = None) -> list[MergeRecord]:
        effective_user_id = user_id if user_id is not None else get_default_user_id()
        with self.connection() as conn:
            return list_merges(conn.cursor(), effective_user_id)

    def list_duplicate_suggestions(
        self,
        user_id: int | None = None,
        content_type: ContentType | None = None,
        limit: int | None = None,
    ) -> SuggestionPage:
        effective_user_id = user_id if user_id is not None else get_default_user_id()
        with self.connection() as conn:
            return find_duplicate_suggestions(
                conn.cursor(),
                effective_user_id,
                content_type=(
                    None if content_type is None else get_enum_value(content_type)
                ),
                limit=limit,
            )

    def decline_duplicate_suggestion(
        self, one_id: int, other_ids: Sequence[int], user_id: int | None = None
    ) -> list[DeclinedPair]:
        effective_user_id = user_id if user_id is not None else get_default_user_id()
        with self.connection() as conn:
            declined = decline_duplicate(
                conn.cursor(), effective_user_id, one_id, other_ids
            )
            conn.commit()
            return declined

    def list_declined_duplicates(
        self, user_id: int | None = None
    ) -> list[DeclinedPair]:
        effective_user_id = user_id if user_id is not None else get_default_user_id()
        with self.connection() as conn:
            return list_declines(conn.cursor(), effective_user_id)

    def undecline_duplicate_suggestion(
        self, one_id: int, other_id: int, user_id: int | None = None
    ) -> DeclinedPair | None:
        effective_user_id = user_id if user_id is not None else get_default_user_id()
        with self.connection() as conn:
            lifted = undecline_duplicate(
                conn.cursor(), effective_user_id, one_id, other_id
            )
            conn.commit()
            return lifted

    def set_item_ignored(
        self, db_id: int, ignored: bool, user_id: int | None = None
    ) -> bool:
        with self.connection() as conn:
            cursor = conn.cursor()
            if user_id is not None:
                cursor.execute(
                    "UPDATE content_items "
                    "SET ignored = ?, updated_at = CURRENT_TIMESTAMP "
                    "WHERE id = ? AND user_id = ? AND merged_into IS NULL",
                    (1 if ignored else 0, db_id, user_id),
                )
            else:
                cursor.execute(
                    "UPDATE content_items "
                    "SET ignored = ?, updated_at = CURRENT_TIMESTAMP "
                    "WHERE id = ? AND merged_into IS NULL",
                    (1 if ignored else 0, db_id),
                )
            conn.commit()
            return cursor.rowcount > 0

    def count_items(
        self,
        user_id: int | None = None,
        content_type: ContentType | None = None,
        status: ConsumptionStatus | None = None,
    ) -> int:
        effective_user_id = user_id if user_id is not None else get_default_user_id()

        with self.connection() as conn:
            cursor = conn.cursor()
            query = (
                "SELECT COUNT(*) FROM content_items"
                " WHERE user_id = ? AND merged_into IS NULL"
            )
            params: list[Any] = [effective_user_id]

            if content_type:
                query += " AND content_type = ?"
                content_type_value = get_enum_value(content_type)
                params.append(content_type_value)

            if status:
                query += " AND status = ?"
                status_value = get_enum_value(status)
                params.append(status_value)

            cursor.execute(query, params)
            result = cursor.fetchone()
            return result[0] if result else 0

    def get_items_needing_enrichment(
        self,
        content_type: ContentType | None = None,
        user_id: int | None = None,
        limit: int = 100,
        after_db_id: int | None = None,
        content_item_id: int | None = None,
    ) -> list[tuple[int, ContentItem]]:
        """Results are ordered by ID, so a caller walking the queue passes the
        last ID it saw as *after_db_id* to page past the items it already
        handled — including any it left queued on purpose.
        """
        effective_user_id = user_id if user_id is not None else get_default_user_id()

        with self.connection() as conn:
            cursor = conn.cursor()
            query, params = self._build_enrichment_query(
                effective_user_id,
                content_type,
                "exclude",
                count_only=False,
                after_db_id=after_db_id,
                content_item_id=content_item_id,
            )
            query += " ORDER BY ci.id LIMIT ?"
            params.append(limit)

            cursor.execute(query, params)
            rows = cursor.fetchall()
            db_ids = [row["id"] for row in rows]

        # Batch-fetch all items in a single query (outside the first connection)
        items = self.get_content_items_by_db_ids(db_ids)
        return [(item.db_id, item) for item in items if item.db_id is not None]

    def count_settled_without_cover(self, user_id: int) -> int:
        """``not_found`` is excluded: no provider held those items at all, and
        retrying them is what ``--retry-not-found`` is for.
        """
        with self.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT COUNT(*) FROM content_items ci"
                " JOIN enrichment_status es ON es.content_item_id = ci.id"
                " WHERE ci.user_id = ? AND ci.merged_into IS NULL"
                " AND ci.cover_url IS NULL AND es.needs_enrichment = 0"
                " AND (es.enrichment_quality IS NULL"
                "      OR es.enrichment_quality != 'not_found')"
                " AND NOT EXISTS (SELECT 1 FROM content_item_dead_covers d"
                "                  WHERE d.content_item_id = ci.id)",
                (user_id,),
            )
            row = cursor.fetchone()
            return int(row[0]) if row else 0

    def count_items_needing_enrichment(
        self,
        content_type: ContentType | None = None,
        user_id: int | None = None,
        content_item_id: int | None = None,
    ) -> int:
        """Items settled as ``not_found`` are counted separately, not here."""
        effective_user_id = user_id if user_id is not None else get_default_user_id()

        with self.connection() as conn:
            cursor = conn.cursor()
            query, params = self._build_enrichment_query(
                effective_user_id,
                content_type,
                "exclude",
                count_only=True,
                content_item_id=content_item_id,
            )
            cursor.execute(query, params)
            row = cursor.fetchone()
            return int(row[0]) if row else 0

    def get_not_found_ids(
        self,
        content_type: ContentType | None = None,
        user_id: int | None = None,
    ) -> list[int]:
        """An item with no ``enrichment_status`` row has never been attempted, so
        it is not a retry candidate and this query does not return it.
        """
        effective_user_id = user_id if user_id is not None else get_default_user_id()

        with self.connection() as conn:
            cursor = conn.cursor()
            query, params = self._build_enrichment_query(
                effective_user_id,
                content_type,
                "only",
                count_only=False,
            )
            cursor.execute(query, params)
            return [int(row["id"]) for row in cursor.fetchall()]

    @staticmethod
    def _build_enrichment_query(
        user_id: int,
        content_type: ContentType | None,
        not_found: NotFoundMode,
        count_only: bool,
        after_db_id: int | None = None,
        content_item_id: int | None = None,
    ) -> tuple[str, list[Any]]:
        """The SELECT clause is hardcoded based on ``count_only`` rather than
        accepting an open string, so this helper cannot be misused to inject SQL.
        """
        select_clause = "SELECT COUNT(*)" if count_only else "SELECT ci.id"

        if not_found == "only":
            status_filter = "es.enrichment_quality = ?"
        else:
            status_filter = "(es.content_item_id IS NULL OR es.needs_enrichment = 1)"

        query = f"""
            {select_clause}
            FROM content_items ci
            LEFT JOIN enrichment_status es ON ci.id = es.content_item_id
            WHERE ci.user_id = ?
              AND ci.merged_into IS NULL
              AND {status_filter}
        """
        params: list[Any] = [user_id]
        if not_found != "exclude":
            params.append("not_found")
        if content_type:
            query += " AND ci.content_type = ?"
            params.append(get_enum_value(content_type))
        if after_db_id is not None:
            query += " AND ci.id > ?"
            params.append(after_db_id)
        if content_item_id is not None:
            query += " AND ci.id = ?"
            params.append(content_item_id)
        return query, params
