"""No sync path merges rows: it deleted ids other sources held."""

import json
import re
import sqlite3
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any

from src.models.detail_fields import (
    RELEASE_YEAR_FIELDS,
    ContentTypeFields,
    text_names,
    to_int,
)
from src.utils.dates import merge_seasons_watched_dates
from src.utils.list_merge import merge_string_lists
from src.utils.series import merge_seasons_watched
from src.utils.sorting import FUZZY_MATCH_THRESHOLD

__all__ = [
    "ALLOWED_DETAIL_TABLES",
    "MERGEABLE_DETAIL_COLUMNS",
    "MONOTONIC_DETAIL_COLUMNS",
    "StatedYear",
    "assert_known_detail_table",
    "bare_title_key",
    "creators_conflict",
    "detail_columns",
    "detail_join",
    "merge_detail_tables",
    "merge_enrichment_status",
    "merge_scalar_columns",
    "normalize_creator_for_matching",
    "normalize_title_for_matching",
    "parse_json_list",
    "regions_conflict",
    "resolve_status_forward",
    "stated_creator",
    "stated_region",
    "stated_release_year",
    "years_conflict",
]


def parse_json_list(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            return text_names(parsed)
    except (json.JSONDecodeError, TypeError):
        pass
    return []


# Hand-written rather than derived from ``models.detail_fields``: it is the
# source of ALLOWED_DETAIL_TABLES, which guards every SQL identifier this
# module and sqlite_db interpolate, so it must not move with the declaration it
# checks. TestDetailTableColumnsConsistency proves they name the same columns.
_DETAIL_TABLE_COLUMNS: dict[str, tuple[str, ...]] = {
    "book_details": (
        "author",
        "pages",
        "isbn",
        "isbn13",
        "publisher",
        "year_published",
        "genres",
        "tags",
        "description",
    ),
    "movie_details": (
        "director",
        "runtime",
        "release_year",
        "genres",
        "studio",
        "tags",
        "description",
    ),
    "tv_show_details": (
        "creators",
        "seasons",
        "episodes",
        "network",
        "release_year",
        "genres",
        "tags",
        "description",
    ),
    "video_game_details": (
        "developer",
        "publisher",
        "platforms",
        "genres",
        "release_year",
        "tags",
        "description",
    ),
}

# Derived from _DETAIL_TABLE_COLUMNS so there is no independent list to keep in
# sync. Guards every table name a caller takes from the field declaration in
# src/models/detail_fields.py, before it reaches SQL identifier interpolation.
ALLOWED_DETAIL_TABLES: frozenset[str] = frozenset(_DETAIL_TABLE_COLUMNS.keys())


def detail_columns(table: str) -> tuple[str, ...]:
    if table not in _DETAIL_TABLE_COLUMNS:
        raise ValueError(f"Unknown detail table: {table!r}")
    return (*_DETAIL_TABLE_COLUMNS[table], "metadata")


def assert_known_detail_table(spec: ContentTypeFields) -> None:
    if spec.table not in ALLOWED_DETAIL_TABLES:
        raise ValueError(f"Unknown detail table: {spec.table!r}")


def detail_join(spec: ContentTypeFields) -> str:
    """Shared by the joined read in ``sqlite_db`` and the derived-column source
    select in ``derived``, which both join every detail table onto the same
    ``ci`` alias and must agree on what that join is.
    """
    assert_known_detail_table(spec)
    return (
        f"LEFT JOIN {spec.table} {spec.table_alias}"
        f" ON ci.id = {spec.table_alias}.content_item_id"
    )


MERGEABLE_DETAIL_COLUMNS: frozenset[str] = frozenset({"genres", "tags"})

MONOTONIC_DETAIL_COLUMNS: frozenset[str] = frozenset({"seasons", "episodes"})


_STATUS_ORDER: dict[str, int] = {
    "unread": 0,
    "currently_consuming": 1,
    "completed": 2,
}


def resolve_status_forward(existing_status: str | None, incoming_status: str) -> str:
    """Status can only advance: unread → currently_consuming → completed."""
    if existing_status is None:
        return incoming_status
    existing_order = _STATUS_ORDER.get(existing_status, 0)
    incoming_order = _STATUS_ORDER.get(incoming_status, 0)
    if incoming_order >= existing_order:
        return incoming_status
    return existing_status


# "The Office (US)" and "DOOM (2016)" collapse onto their namesakes; the year
# veto below and _title_match's refusal to pick between two rows back that.
_REGION_QUALIFIERS = frozenset({"us", "usa", "uk", "gb", "au", "nz", "ca", "jp", "eu"})
_YEAR = re.compile(r"^\d{4}$")
# A translation and an audiobook are printings of one work. Keyed on the word
# "edition" because a list of languages could never be complete.
_EDITION = re.compile(r"(?:^|\s)edition$|^(?:un)?abridged$")
# Books get no year veto, so a stripped "(3rd Edition)" hides a second textbook.
_NUMBERED = re.compile(
    r"\d|\b(?:first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth"
    r"|eleventh|twelfth)\b"
)
_TRAILING_PARENTHETICAL = re.compile(r"\s*\(([^()]*)\)\s*$")
_TITLE_YEAR = re.compile(r"\((\d{4})\)\s*$")


def _is_qualifier(inner: str) -> bool:
    inner = inner.strip()
    if _YEAR.match(inner):
        return True
    if _EDITION.search(inner) and not _NUMBERED.search(inner):
        return True
    return re.sub(r"[\W_]", "", inner) in _REGION_QUALIFIERS


def _strip_qualifying_parentheticals(title: str) -> str:
    while match := _TRAILING_PARENTHETICAL.search(title):
        if not _is_qualifier(match.group(1)):
            break
        title = title[: match.start()]
    return title


def normalize_title_for_matching(title: str) -> str:
    if not title:
        return ""

    normalized = title.lower().strip()

    normalized = re.sub(r"[™®©]", "", normalized)

    suffixes_to_remove = [
        r"\s*[:\-–]\s*remastered\s*$",
        r"\s*remastered\s*$",
        r"\s*[:\-–]\s*definitive edition\s*$",
        r"\s*definitive edition\s*$",
        r"\s*[:\-–]\s*game of the year edition\s*$",
        r"\s*goty edition\s*$",
        r"\s*[:\-–]\s*anniversary edition\s*$",
        r"\s*anniversary edition\s*$",
        r"\s*[:\-–]\s*special edition\s*$",
        r"\s*special edition\s*$",
        r"\s*[:\-–]\s*ultimate edition\s*$",
        r"\s*ultimate edition\s*$",
        r"\s*[:\-–]\s*complete edition\s*$",
        r"\s*complete edition\s*$",
        r"\s*[:\-–]\s*deluxe edition\s*$",
        r"\s*deluxe edition\s*$",
        r"\s*\(remastered\)\s*$",
        r"\s*\(remaster\)\s*$",
    ]

    for suffix in suffixes_to_remove:
        normalized = re.sub(suffix, "", normalized, flags=re.IGNORECASE)

    normalized = _strip_qualifying_parentheticals(normalized)

    normalized = re.sub(r"^(the|a|an)\s+", "", normalized)

    # Hyphens go first so "Year-One" matches "Year One" rather than "YearOne".
    normalized = re.sub(r"-", " ", normalized)

    normalized = re.sub(r"[^\w\s]", "", normalized)

    # Order matters - check longer numerals first
    roman_map = [
        (r"\bviii\b", "8"),
        (r"\bvii\b", "7"),
        (r"\bvi\b", "6"),
        (r"\biv\b", "4"),
        (r"\bv\b", "5"),
        (r"\biii\b", "3"),
        (r"\bii\b", "2"),
        # Trailing only: elsewhere "I" is the pronoun of "I Am Legend".
        (r"\bi\s*$", "1"),
        (r"\bix\b", "9"),
        (r"\bx\b", "10"),
    ]
    for roman, arabic in roman_map:
        normalized = re.sub(roman, arabic, normalized)

    normalized = re.sub(r"\s+", " ", normalized).strip()

    return normalized


def bare_title_key(title: str) -> str:
    """Not the save door's key: "(Malazan Book 2)" may name a different work."""
    return normalize_title_for_matching(_TRAILING_PARENTHETICAL.sub("", title))


# One region spelled two ways, which the veto would take for two shows.
_REGION_ALIASES = {"usa": "us", "gb": "uk"}


def stated_region(title: str | None) -> str | None:
    remaining = (title or "").lower()
    while match := _TRAILING_PARENTHETICAL.search(remaining):
        inner = match.group(1)
        if not _is_qualifier(inner):
            return None
        region = re.sub(r"[\W_]", "", inner.strip())
        if region in _REGION_QUALIFIERS:
            return _REGION_ALIASES.get(region, region)
        remaining = remaining[: match.start()]
    return None


def regions_conflict(one: str | None, other: str | None) -> bool:
    """Not the year rule: a region only one side states is not disagreement, it
    qualifies the Hell's Kitchen nobody else qualifies.
    """
    return one is not None and other is not None and one != other


def _collapse_initials(tokens: list[str]) -> list[str]:
    collapsed: list[str] = []
    following_initial = False
    for token in tokens:
        if following_initial and len(token) == 1:
            collapsed[-1] += token
        else:
            collapsed.append(token)
        following_initial = len(token) == 1
    return collapsed


# A word standing in for a name it is not, so a veto on it hides a real match.
_PLACEHOLDER_CREATORS = frozenset({"unknown", "author unknown"})


def normalize_creator_for_matching(creator: str | None) -> str:
    """The veto's key, order-free: a shelf writes "Rowling, J.K."."""
    if not creator:
        return ""
    tokens = re.sub(r"[\W_]", " ", creator.lower()).split()
    normalized = " ".join(sorted(_collapse_initials(tokens)))
    return "" if normalized in _PLACEHOLDER_CREATORS else normalized


def stated_creator(creator: str | None) -> str | None:
    """The column is fill-only, so a stored "Unknown" is permanent — an import
    writing one undoes what the schema-17 step cleared.
    """
    return creator if normalize_creator_for_matching(creator) else None


# Two Preys shared only "Studios", enough for the veto to read one developer and
# bind GOG's id to Steam's game.
_GENERIC_CREATOR_TOKENS = frozenset(
    {
        "ab",
        "co",
        "company",
        "corp",
        "corporation",
        "entertainment",
        "game",
        "games",
        "gmbh",
        "inc",
        "interactive",
        "limited",
        "llc",
        "ltd",
        "media",
        "production",
        "productions",
        "sa",
        "software",
        "studio",
        "studios",
    }
)


def creators_conflict(one: str | None, other: str | None) -> bool:
    """Unstated or sharing a name is not disagreement: a source omitting the
    author would manufacture duplicates, and "Arkane Lyon" is "Arkane Studios".
    """
    left = normalize_creator_for_matching(one)
    right = normalize_creator_for_matching(other)
    if not left or not right:
        return False
    shared = set(left.split()) & set(right.split())
    if shared - _GENERIC_CREATOR_TOKENS:
        return False
    return SequenceMatcher(None, left, right).ratio() < FUZZY_MATCH_THRESHOLD


@dataclass(frozen=True)
class StatedYear:
    value: int | None = None
    in_title: bool = False


def stated_release_year(
    content_type: str, stated: Any, title: str | None
) -> StatedYear:
    if content_type not in RELEASE_YEAR_FIELDS:
        return StatedYear()
    year = to_int(stated)
    if year is not None:
        return StatedYear(year)
    match = _TITLE_YEAR.search(title or "")
    return StatedYear(int(match.group(1)), in_title=True) if match else StatedYear()


def years_conflict(one: StatedYear, other: StatedYear) -> bool:
    """A year only one source states is not disagreement."""
    if one.value is not None and other.value is not None:
        return one.value != other.value
    return one.in_title or other.in_title


def merge_scalar_columns(cursor: sqlite3.Cursor, keep_id: int, delete_id: int) -> None:
    """Every other user-owned column is carried across, the duplicate row being
    hidden. ``ignored`` is not read here at all: each row keeps its own, because
    carrying it hides the survivor behind the duplicate's ignore. Requires
    ``row_factory = sqlite3.Row``.
    """
    # Skipped entirely when no column moves, so a merge that changes nothing
    # leaves updated_at — a user-facing sort key — alone.
    select_sql = (
        "SELECT status, rating, review, date_completed FROM content_items WHERE id = ?"
    )
    cursor.execute(select_sql, (keep_id,))
    keep_row = cursor.fetchone()
    cursor.execute(select_sql, (delete_id,))
    dup_row = cursor.fetchone()
    if keep_row is None or dup_row is None:
        return

    merged_status = resolve_status_forward(keep_row["status"], dup_row["status"])

    will_change_rating = keep_row["rating"] is None and dup_row["rating"] is not None
    will_change_review = keep_row["review"] is None and dup_row["review"] is not None
    will_change_date = dup_row["date_completed"] is not None and (
        keep_row["date_completed"] is None
        or dup_row["date_completed"] > keep_row["date_completed"]
    )
    if not (
        will_change_rating
        or will_change_review
        or will_change_date
        or merged_status != keep_row["status"]
    ):
        return

    # The CASE expressions repeat the will_change guards on purpose: the Python
    # guard exists to skip the UPDATE, not to decide what it writes.
    cursor.execute(
        """UPDATE content_items
           SET rating = CASE WHEN rating IS NULL THEN ? ELSE rating END,
               review = CASE WHEN review IS NULL THEN ? ELSE review END,
               date_completed = CASE
                   WHEN date_completed IS NULL THEN ?
                   WHEN ? IS NOT NULL AND ? > date_completed THEN ?
                   ELSE date_completed
               END,
               status = ?,
               updated_at = CURRENT_TIMESTAMP
           WHERE id = ?""",
        (
            dup_row["rating"],
            dup_row["review"],
            dup_row["date_completed"],
            dup_row["date_completed"],
            dup_row["date_completed"],
            dup_row["date_completed"],
            merged_status,
            keep_id,
        ),
    )


def merge_enrichment_status(
    cursor: sqlite3.Cursor, keep_id: int, delete_id: int
) -> None:
    """A settled miss counts as settled: one work, one outcome."""
    cursor.execute(
        "SELECT * FROM enrichment_status WHERE content_item_id = ?", (delete_id,)
    )
    absorbed = cursor.fetchone()
    if absorbed is None or absorbed["needs_enrichment"]:
        return
    cursor.execute(
        "SELECT needs_enrichment FROM enrichment_status WHERE content_item_id = ?",
        (keep_id,),
    )
    kept = cursor.fetchone()
    if kept is not None and not kept["needs_enrichment"]:
        return
    cursor.execute(
        "INSERT OR REPLACE INTO enrichment_status "
        "(content_item_id, last_enriched_at, enrichment_provider, "
        "enrichment_quality, needs_enrichment, enrichment_error) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (
            keep_id,
            absorbed["last_enriched_at"],
            absorbed["enrichment_provider"],
            absorbed["enrichment_quality"],
            absorbed["needs_enrichment"],
            absorbed["enrichment_error"],
        ),
    )


def _merge_detail_metadata(
    keep_detail: sqlite3.Row | None, dup_detail: sqlite3.Row | None
) -> str | None:
    # merge_detail_tables already guards both rows, so this only fires if that
    # guard regresses. Skipping matches every other degenerate case here: dedup
    # leaves the kept row alone rather than aborting the whole merge.
    if keep_detail is None or dup_detail is None:
        return None

    dup_meta_raw = dup_detail["metadata"]
    if dup_meta_raw is None:
        return None
    try:
        dup_meta = json.loads(dup_meta_raw)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(dup_meta, dict) or not dup_meta:
        return None

    keep_meta: dict[str, Any] = {}
    keep_meta_raw = keep_detail["metadata"]
    if keep_meta_raw is not None:
        try:
            parsed = json.loads(keep_meta_raw)
        except (json.JSONDecodeError, TypeError):
            return None  # Kept metadata unparseable — skip to avoid data loss
        if not isinstance(parsed, dict):
            return None  # Kept metadata non-dict — skip to preserve it
        keep_meta = parsed

    merged = {**dup_meta, **keep_meta}

    # Exception: seasons_watched is the union of both rows. Each row's list may
    # hold seasons the user ticked by hand, and only the survivor is read.
    combined_seasons = merge_seasons_watched(
        keep_meta.get("seasons_watched"), dup_meta.get("seasons_watched")
    )
    if combined_seasons is not None:
        merged["seasons_watched"] = combined_seasons

    # Exception: seasons_watched_dates keeps the later date per season, so an
    # earlier duplicate never overwrites the kept row's. A None result — both
    # sides unparseable — leaves the blob merge above in place.
    combined_dates = merge_seasons_watched_dates(
        keep_meta.get("seasons_watched_dates"), dup_meta.get("seasons_watched_dates")
    )
    if combined_dates is not None:
        merged["seasons_watched_dates"] = combined_dates

    return json.dumps(merged)


def merge_detail_tables(cursor: sqlite3.Cursor, keep_id: int, delete_id: int) -> None:
    """Leaves ``updated_at`` alone: what moves here is bookkeeping, not an edit
    the user made. Requires ``row_factory = sqlite3.Row``.
    """
    for table, columns in _DETAIL_TABLE_COLUMNS.items():
        cursor.execute(
            f"SELECT * FROM {table} WHERE content_item_id = ?",
            (keep_id,),
        )
        keep_detail = cursor.fetchone()
        cursor.execute(
            f"SELECT * FROM {table} WHERE content_item_id = ?",
            (delete_id,),
        )
        dup_detail = cursor.fetchone()
        if dup_detail is None:
            continue
        if keep_detail is None:
            copied = ", ".join(detail_columns(table))
            cursor.execute(
                f"INSERT INTO {table} (content_item_id, {copied})"
                f" SELECT ?, {copied} FROM {table} WHERE content_item_id = ?",
                (keep_id, delete_id),
            )
            continue

        detail_updates: list[str] = []
        detail_params: list[Any] = []

        for col in columns:
            if col in MERGEABLE_DETAIL_COLUMNS:
                keep_list = parse_json_list(keep_detail[col])
                dup_list = parse_json_list(dup_detail[col])
                if dup_list:
                    merged = merge_string_lists(keep_list, dup_list)
                    detail_updates.append(f"{col} = ?")
                    detail_params.append(json.dumps(merged))
            elif col in MONOTONIC_DETAIL_COLUMNS:
                keep_val = keep_detail[col]
                dup_val = dup_detail[col]
                try:
                    if dup_val is not None and (
                        keep_val is None or int(dup_val) > int(keep_val)
                    ):
                        detail_updates.append(f"{col} = ?")
                        detail_params.append(int(dup_val))
                except (ValueError, TypeError):
                    pass
            elif keep_detail[col] is None and dup_detail[col] is not None:
                detail_updates.append(f"{col} = ?")
                detail_params.append(dup_detail[col])

        merged_meta_json = _merge_detail_metadata(keep_detail, dup_detail)
        if merged_meta_json is not None:
            detail_updates.append("metadata = ?")
            detail_params.append(merged_meta_json)

        if detail_updates:
            detail_clause = ", ".join(detail_updates)
            detail_params.append(keep_id)
            cursor.execute(
                f"UPDATE {table} SET {detail_clause} WHERE content_item_id = ?",
                detail_params,
            )
