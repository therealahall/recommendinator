from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any

# A provider's search ranks by popularity, so whenever the searched work is
# absent some other work sits at result 0 — 'Fully Loaded' returning 'Herbie:
# Fully Loaded'. Only a near-identical title may be taken for the same work.
MINIMUM_TITLE_SIMILARITY = 0.85

# A festival cut, a regional release and a show's first air date all drift from
# the year an importer recorded; further apart than this is a different work.
MAXIMUM_YEAR_DRIFT = 3

_LEADING_ARTICLES = frozenset({"the", "a", "an"})
_NON_WORD = re.compile(r"[\W_]+")

# Plex and Tautulli name a file "Star Wars: Episode V - The Empire Strikes
# Back" where a provider holds only "The Empire Strikes Back".
_FRANCHISE_PREFIX = re.compile(r"^.*(?::|\s-\s)")

_SUBTITLE_BOUNDARY = re.compile(r":|\s-\s")


def normalize_title(title: str) -> str:
    words = _NON_WORD.sub(" ", title.casefold()).split()
    if len(words) > 1 and words[0] in _LEADING_ARTICLES:
        words = words[1:]
    return " ".join(words)


def year_of(value: Any) -> int | None:
    try:
        return int(str(value)[:4])
    except ValueError:
        return None


def title_similarity(left: str, right: str) -> float:
    left_normalized = normalize_title(left)
    right_normalized = normalize_title(right)
    # SequenceMatcher scores two empty strings 1.0, which would make a title
    # that normalizes away match every candidate.
    if not left_normalized or not right_normalized:
        return 0.0
    return SequenceMatcher(None, left_normalized, right_normalized).ratio()


def _is_subtitled_form_of(title: str, searched: str) -> bool:
    parts = _SUBTITLE_BOUNDARY.split(title, maxsplit=1)
    # Whole, never close: 'Ultima I' and 'Ultima II' score 0.94 against each other.
    return len(parts) == 2 and normalize_title(parts[0]) == normalize_title(searched)


def _year_rank(item_year: int | None, year: int | None) -> tuple[int, int]:
    """Sorts a candidate nearest the item's year first, and a dateless one last."""
    if item_year is None or year is None:
        return (1, 0)
    return (0, abs(year - item_year))


@dataclass(frozen=True)
class Candidate:
    """One record a provider offers, carrying what tells two of them apart."""

    record_id: str
    title: str
    year: int | None = None
    creator: str | None = None
    cover_url: str | None = None
    #: Alternates the ranker also compares, an original-language title above all.
    also_titled: tuple[str, ...] = field(default_factory=tuple)


def best_match(
    searched_title: str,
    item_year: int | None,
    candidates: Sequence[Candidate],
) -> Candidate | None:
    index = best_match_index(
        searched_title,
        item_year,
        [
            ([candidate.title, *candidate.also_titled], candidate.year)
            for candidate in candidates
        ],
    )
    return None if index is None else candidates[index]


def best_match_index(
    searched_title: str,
    item_year: int | None,
    candidates: Sequence[tuple[list[str], int | None]],
) -> int | None:
    """Index of the closest (titles, year) candidate worth trusting, if any."""
    # Only the item's title is stripped. Stripping a candidate's would let
    # 'Herbie: Fully Loaded' stand in for 'Fully Loaded', the substitution the
    # similarity bar exists to refuse.
    searched_variants = {searched_title, _FRANCHISE_PREFIX.sub("", searched_title)}
    best_rank: tuple[bool, float, tuple[int, int]] | None = None
    best_index: int | None = None

    for index, (titles, year) in enumerate(candidates):
        if (
            item_year is not None
            and year is not None
            and abs(year - item_year) > MAXIMUM_YEAR_DRIFT
        ):
            continue
        score = max(
            (
                title_similarity(searched, title)
                for searched in searched_variants
                for title in titles
            ),
            default=0.0,
        )
        outright = score >= MINIMUM_TITLE_SIMILARITY
        if not outright and not any(
            _is_subtitled_form_of(title, searched_title) for title in titles
        ):
            continue
        # Lower is better, and only a strict improvement displaces the incumbent,
        # so candidates alike on both keep the provider's ranking.
        rank = (not outright, -score, _year_rank(item_year, year))
        if best_rank is None or rank < best_rank:
            best_rank, best_index = rank, index

    return best_index
