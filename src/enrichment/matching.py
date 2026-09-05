from __future__ import annotations

import re
from collections.abc import Sequence
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


def _year_rank(item_year: int | None, year: int | None) -> tuple[int, int]:
    """Sorts a candidate nearest the item's year first, and a dateless one last."""
    if item_year is None or year is None:
        return (1, 0)
    return (0, abs(year - item_year))


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
    best_rank: tuple[float, tuple[int, int]] | None = None
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
        if score < MINIMUM_TITLE_SIMILARITY:
            continue
        # Lower is better, and only a strict improvement displaces the incumbent,
        # so candidates alike on both keep the provider's ranking.
        rank = (-score, _year_rank(item_year, year))
        if best_rank is None or rank < best_rank:
            best_rank, best_index = rank, index

    return best_index
