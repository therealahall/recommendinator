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


def find_duplicate_suggestions(
    cursor: sqlite3.Cursor, user_id: int
) -> list[DuplicateSuggestion]:
    """Every undecided pair of live rows that looks like one work."""
    declined = _declined_pairs(cursor, user_id)
    groups: dict[tuple[str, str], list[MatchRow]] = {}
    for row in read_live_match_rows(cursor, user_id):
        key = bare_title_key(row.title)
        if key:
            groups.setdefault((row.content_type, key), []).append(row)

    suggestions: list[DuplicateSuggestion] = []
    for (content_type, key), members in groups.items():
        for survivor, absorbed in combinations(members, 2):
            if _ordered(survivor.db_id, absorbed.db_id) in declined:
                continue
            if signals_conflict(survivor.signals, absorbed.signals):
                continue
            suggestions.append(_suggestion(content_type, key, survivor, absorbed))
    return suggestions


def decline_duplicate(
    cursor: sqlite3.Cursor, user_id: int, one_id: int, other_id: int
) -> bool:
    """Refuse a pair for good, reporting whether there was one to refuse.

    Naming two rows is what survives a re-sync: a source reporting either item
    again lands on the row it already has.
    """
    pair = _live_pair(cursor, user_id, one_id, other_id)
    if pair is None:
        return False
    cursor.execute(
        "INSERT OR IGNORE INTO content_item_duplicate_declines"
        " (user_id, lower_item_id, higher_item_id) VALUES (?, ?, ?)",
        (user_id, *pair),
    )
    return True


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
) -> tuple[int, int] | None:
    if one_id == other_id:
        return None
    cursor.execute(
        "SELECT id FROM content_items"
        " WHERE id IN (?, ?) AND user_id = ? AND merged_into IS NULL",
        (one_id, other_id, user_id),
    )
    if len(cursor.fetchall()) != 2:
        return None
    return _ordered(one_id, other_id)


def _declined_pairs(cursor: sqlite3.Cursor, user_id: int) -> set[tuple[int, int]]:
    cursor.execute(
        "SELECT lower_item_id, higher_item_id FROM content_item_duplicate_declines"
        " WHERE user_id = ?",
        (user_id,),
    )
    return {(row["lower_item_id"], row["higher_item_id"]) for row in cursor.fetchall()}
