"""Duplicate pairs already in the library, offered rather than merged.

The save door decides a pair on first contact, so two rows that already exist
stay two. Suggestions are computed on demand; only a decline is stored.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from enum import Enum
from itertools import combinations

from src.storage.derived import MatchRow, read_live_match_rows, signals_conflict
from src.storage.merge import bare_title_key

_DECLINE_SELECT = """
    SELECT d.lower_item_id, d.higher_item_id,
           l.title AS lower_title, h.title AS higher_title
      FROM content_item_duplicate_declines d
      JOIN content_items l ON l.id = d.lower_item_id
      JOIN content_items h ON h.id = d.higher_item_id
"""

#: k copies of one work make k(k-1)/2 pairs, so a first pass over a real
#: library runs to hundreds and no surface may offer them all at once.
SUGGESTION_PAGE_DEFAULT = 25
SUGGESTION_PAGE_MAX = 200


class SuggestionEvidence(Enum):
    NORMALIZED_TITLE = "normalized_title"
    TITLE_QUALIFIER = "title_qualifier"


@dataclass(frozen=True)
class DuplicateSide:
    db_id: int
    title: str
    source: str | None
    creator: str | None
    release_year: int | None


@dataclass(frozen=True)
class DeclinedPair:
    """A refusal in force, carrying both titles so a mistyped id shows itself."""

    one_id: int
    one_title: str
    other_id: int
    other_title: str


@dataclass(frozen=True)
class DuplicateSuggestion:
    """Two live rows that look like one work, and what says so.

    The roles are a proposal: the older row is offered as the survivor, as the
    save door resolves a title collision, and either way round is a merge.
    """

    content_type: str
    evidence: SuggestionEvidence
    evidence_detail: str
    survivor: DuplicateSide
    absorbed: DuplicateSide


@dataclass(frozen=True)
class SuggestionPage:
    """*total* counts the whole filtered set, not the slice this carries."""

    total: int
    suggestions: list[DuplicateSuggestion]


def find_duplicate_suggestions(
    cursor: sqlite3.Cursor,
    user_id: int,
    content_type: str | None = None,
    limit: int | None = None,
) -> SuggestionPage:
    """Undecided pairs of live rows that look like one work, and how many."""
    declined = _declined_pairs(cursor, user_id)
    groups: dict[tuple[str, str], list[MatchRow]] = {}
    for row in read_live_match_rows(cursor, user_id):
        if content_type is not None and row.content_type != content_type:
            continue
        key = bare_title_key(row.title)
        if key:
            groups.setdefault((row.content_type, key), []).append(row)

    suggestions: list[DuplicateSuggestion] = []
    for (member_type, key), members in groups.items():
        for survivor, absorbed in combinations(members, 2):
            if _ordered(survivor.db_id, absorbed.db_id) in declined:
                continue
            if signals_conflict(survivor.signals, absorbed.signals):
                continue
            suggestions.append(_suggestion(member_type, key, survivor, absorbed))
    return SuggestionPage(
        total=len(suggestions),
        suggestions=suggestions if limit is None else suggestions[:limit],
    )


def decline_duplicate(
    cursor: sqlite3.Cursor, user_id: int, one_id: int, other_id: int
) -> DeclinedPair | None:
    """Refuse a pair for good, reporting the pair refused, or ``None`` if none.

    Naming two rows is what survives a re-sync: a source reporting either item
    again lands on the row it already has.
    """
    pair = _live_pair(cursor, user_id, one_id, other_id)
    if pair is None:
        return None
    cursor.execute(
        "INSERT OR IGNORE INTO content_item_duplicate_declines"
        " (user_id, lower_item_id, higher_item_id) VALUES (?, ?, ?)",
        (user_id, pair.one_id, pair.other_id),
    )
    return pair


def list_declines(cursor: sqlite3.Cursor, user_id: int) -> list[DeclinedPair]:
    """Refusals in force, lowest id first; a side merged away still lists."""
    cursor.execute(
        f"{_DECLINE_SELECT} WHERE d.user_id = ?"
        " ORDER BY d.lower_item_id, d.higher_item_id",
        (user_id,),
    )
    return [_pair_from_row(row) for row in cursor.fetchall()]


def undecline_duplicate(
    cursor: sqlite3.Cursor, user_id: int, one_id: int, other_id: int
) -> DeclinedPair | None:
    """Lift a refusal; the pass recomputes per call, so the pair returns now."""
    stored = _ordered(one_id, other_id)
    cursor.execute(
        f"{_DECLINE_SELECT} WHERE d.user_id = ?"
        " AND d.lower_item_id = ? AND d.higher_item_id = ?",
        (user_id, *stored),
    )
    row = cursor.fetchone()
    if row is None:
        return None
    cursor.execute(
        "DELETE FROM content_item_duplicate_declines"
        " WHERE user_id = ? AND lower_item_id = ? AND higher_item_id = ?",
        (user_id, *stored),
    )
    return _pair_from_row(row)


def _suggestion(
    content_type: str, key: str, survivor: MatchRow, absorbed: MatchRow
) -> DuplicateSuggestion:
    exact = bool(survivor.normalized_title) and (
        survivor.normalized_title == absorbed.normalized_title
    )
    return DuplicateSuggestion(
        content_type=content_type,
        evidence=(
            SuggestionEvidence.NORMALIZED_TITLE
            if exact
            else SuggestionEvidence.TITLE_QUALIFIER
        ),
        evidence_detail=survivor.normalized_title if exact else key,
        survivor=_side(survivor),
        absorbed=_side(absorbed),
    )


def _side(row: MatchRow) -> DuplicateSide:
    return DuplicateSide(
        db_id=row.db_id,
        title=row.title,
        source=row.source,
        creator=row.signals.creator,
        release_year=row.signals.release_year.value,
    )


def _ordered(one_id: int, other_id: int) -> tuple[int, int]:
    """The pair as the table stores it, so a decline is found either way round."""
    return min(one_id, other_id), max(one_id, other_id)


def _live_pair(
    cursor: sqlite3.Cursor, user_id: int, one_id: int, other_id: int
) -> DeclinedPair | None:
    if one_id == other_id:
        return None
    cursor.execute(
        "SELECT id, title FROM content_items"
        " WHERE id IN (?, ?) AND user_id = ? AND merged_into IS NULL ORDER BY id",
        (one_id, other_id, user_id),
    )
    rows = cursor.fetchall()
    if len(rows) != 2:
        return None
    lower, higher = rows
    return DeclinedPair(
        one_id=lower["id"],
        one_title=lower["title"],
        other_id=higher["id"],
        other_title=higher["title"],
    )


def _pair_from_row(row: sqlite3.Row) -> DeclinedPair:
    return DeclinedPair(
        one_id=row["lower_item_id"],
        one_title=row["lower_title"],
        other_id=row["higher_item_id"],
        other_title=row["higher_title"],
    )


def _declined_pairs(cursor: sqlite3.Cursor, user_id: int) -> set[tuple[int, int]]:
    cursor.execute(
        "SELECT lower_item_id, higher_item_id FROM content_item_duplicate_declines"
        " WHERE user_id = ?",
        (user_id,),
    )
    return {(row["lower_item_id"], row["higher_item_id"]) for row in cursor.fetchall()}
