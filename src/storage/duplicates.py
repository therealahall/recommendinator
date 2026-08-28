"""Suggestions are computed on demand; only a decline is stored, pair by pair
however many copies a block holds."""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
from itertools import combinations

from src.storage.derived import MatchRow, read_live_match_rows, signals_conflict
from src.storage.item_merges import MergeError, absorbing_merge_id
from src.storage.merge import bare_title_key

_DECLINE_SELECT = (
    "SELECT d.lower_item_id, d.higher_item_id, "
    "l.title AS lower_title, h.title AS higher_title "
    "FROM content_item_duplicate_declines d "
    "JOIN content_items l ON l.id = d.lower_item_id "
    "JOIN content_items h ON h.id = d.higher_item_id"
)

#: A library that has never been reviewed offers hundreds of works at once.
SUGGESTION_PAGE_DEFAULT = 25
SUGGESTION_PAGE_MAX = 200

#: The blocks a group can hold grow as 3^(n/3) however the search is written,
#: so a group past this is skipped rather than left to exhaust the API.
GROUP_MEMBER_MAX = 40
MAX_DECLINE_OTHERS = GROUP_MEMBER_MAX - 1

#: d disjoint refusals inside a linked group make 2^d blocks, at any size.
GROUP_BLOCK_MAX = SUGGESTION_PAGE_MAX

logger = logging.getLogger(__name__)


class _TooManyBlocks(Exception):
    """Abandons the search rather than the recursion unwinding a level at a time."""


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
    """*survivor_id* is a proposal — the oldest copy, as the save door resolves a
    title collision — and any copy may be kept instead."""

    content_type: str
    evidence: SuggestionEvidence
    evidence_detail: str
    survivor_id: int
    copies: tuple[DuplicateSide, ...]


@dataclass(frozen=True)
class SuggestionPage:
    """*total* counts the whole filtered set, not the slice this carries."""

    total: int
    suggestions: list[DuplicateSuggestion]
    also_offered: frozenset[int] = frozenset()
    skipped_works: int = 0


def find_duplicate_suggestions(
    cursor: sqlite3.Cursor,
    user_id: int,
    content_type: str | None = None,
    limit: int | None = None,
) -> SuggestionPage:
    declined = _declined_pairs(cursor, user_id)
    groups: dict[tuple[str, str], list[MatchRow]] = {}
    for row in read_live_match_rows(cursor, user_id):
        if content_type is not None and row.content_type != content_type:
            continue
        key = bare_title_key(row.title)
        if key:
            groups.setdefault((row.content_type, key), []).append(row)

    suggestions: list[DuplicateSuggestion] = []
    skipped = 0
    for (member_type, key), members in groups.items():
        blocks = _blocks(members, declined, key)
        if blocks is None:
            skipped += 1
            continue
        suggestions += [_suggestion(member_type, key, block) for block in blocks]

    return SuggestionPage(
        total=len(suggestions),
        suggestions=suggestions if limit is None else suggestions[:limit],
        also_offered=_offered_twice(suggestions),
        skipped_works=skipped,
    )


def decline_duplicate(
    cursor: sqlite3.Cursor, user_id: int, one_id: int, other_ids: Sequence[int]
) -> list[DeclinedPair]:
    """One pair per refusal, so the copies it did not name still pair with each
    other, and a re-sync lands on the rows the refusal already named."""
    pairs: list[DeclinedPair] = []
    for other_id in dict.fromkeys(other_ids):
        pair = _live_pair(cursor, user_id, one_id, other_id)
        if pair is None:
            return []
        pairs.append(pair)
    cursor.executemany(
        "INSERT OR IGNORE INTO content_item_duplicate_declines"
        " (user_id, lower_item_id, higher_item_id) VALUES (?, ?, ?)",
        [(user_id, pair.one_id, pair.other_id) for pair in pairs],
    )
    return pairs


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
    """Lift a refusal, unless a merge holds a side: the pass offers live rows
    only, so lifting there would drop the decision and offer nothing back.
    """
    stored = _ordered(one_id, other_id)
    cursor.execute(
        f"{_DECLINE_SELECT} WHERE d.user_id = ?"
        " AND d.lower_item_id = ? AND d.higher_item_id = ?",
        (user_id, *stored),
    )
    row = cursor.fetchone()
    if row is None:
        return None
    for item_id in stored:
        hiding = absorbing_merge_id(cursor, item_id)
        if hiding is not None:
            raise MergeError(
                f"The refusal on items {stored[0]} and {stored[1]} cannot be"
                f" lifted before merge {hiding}, which absorbed item {item_id}."
            )
    cursor.execute(
        "DELETE FROM content_item_duplicate_declines"
        " WHERE user_id = ? AND lower_item_id = ? AND higher_item_id = ?",
        (user_id, *stored),
    )
    return _pair_from_row(row)


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


def _offered_twice(suggestions: list[DuplicateSuggestion]) -> frozenset[int]:
    """Read before the limit slices, so a copy still says so where it is cut."""
    seen: set[int] = set()
    twice: set[int] = set()
    for suggestion in suggestions:
        for side in suggestion.copies:
            if side.db_id in seen:
                twice.add(side.db_id)
            seen.add(side.db_id)
    return frozenset(twice)


def _declined_pairs(cursor: sqlite3.Cursor, user_id: int) -> set[tuple[int, int]]:
    cursor.execute(
        "SELECT lower_item_id, higher_item_id FROM content_item_duplicate_declines"
        " WHERE user_id = ?",
        (user_id,),
    )
    return {(row["lower_item_id"], row["higher_item_id"]) for row in cursor.fetchall()}


def _blocks(
    members: list[MatchRow], declined: set[tuple[int, int]], key: str
) -> list[list[MatchRow]] | None:
    """A pass seeded per copy would lose a set that copy is not in, so Bron-Kerbosch."""
    if len(members) > GROUP_MEMBER_MAX:
        logger.warning(
            "Skipping the %d copies matching %r: a group over %d copies is not"
            " offered for review. Merge some of them to see the rest.",
            len(members),
            key,
            GROUP_MEMBER_MAX,
        )
        return None

    linked: dict[int, set[int]] = {index: set() for index in range(len(members))}
    for (index, one), (other_index, other) in combinations(enumerate(members), 2):
        if _ordered(one.db_id, other.db_id) in declined:
            continue
        if signals_conflict(one.signals, other.signals):
            continue
        linked[index].add(other_index)
        linked[other_index].add(index)

    found: list[list[int]] = []

    def extend(block: list[int], candidates: set[int], excluded: set[int]) -> None:
        if not candidates and not excluded:
            if len(block) > 1:
                found.append(block)
                if len(found) > GROUP_BLOCK_MAX:
                    raise _TooManyBlocks
            return
        # Pivoting: a shelf of one work in five translations is a clique, and
        # without it a clique of m copies costs 2^m calls to yield one block.
        pivot = max(
            candidates | excluded, key=lambda one: len(candidates & linked[one])
        )
        for index in sorted(candidates - linked[pivot]):
            extend(
                [*block, index],
                candidates & linked[index],
                excluded & linked[index],
            )
            candidates = candidates - {index}
            excluded = excluded | {index}

    try:
        extend([], set(linked), set())
    except _TooManyBlocks:
        logger.warning(
            "Skipping the %d copies matching %r: they split into more than %d"
            " blocks. Merge some of them to see the rest.",
            len(members),
            key,
            GROUP_BLOCK_MAX,
        )
        return None

    return [
        [members[index] for index in block]
        for block in sorted(sorted(block) for block in found)
    ]


def _suggestion(
    content_type: str, key: str, copies: list[MatchRow]
) -> DuplicateSuggestion:
    titles = {row.normalized_title for row in copies}
    exact = len(titles) == 1 and bool(copies[0].normalized_title)
    return DuplicateSuggestion(
        content_type=content_type,
        evidence=(
            SuggestionEvidence.NORMALIZED_TITLE
            if exact
            else SuggestionEvidence.TITLE_QUALIFIER
        ),
        evidence_detail=copies[0].normalized_title if exact else key,
        survivor_id=copies[0].db_id,
        copies=tuple(_side(row) for row in copies),
    )
