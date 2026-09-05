from __future__ import annotations

import re
from collections.abc import Sequence
from difflib import SequenceMatcher

# A provider's search ranks by popularity, so whenever the searched work is
# absent some other work sits at result 0 — 'Fully Loaded' returning 'Herbie:
# Fully Loaded'. Only a near-identical title may be taken for the same work.
MINIMUM_TITLE_SIMILARITY = 0.85

# A festival cut, a regional release and a show's first air date all drift from
# the year an importer recorded; further apart than this is a different work.
MAXIMUM_YEAR_DRIFT = 3

_LEADING_ARTICLES = frozenset({"the", "a", "an"})
_NON_WORD = re.compile(r"[\W_]+")


def normalize_title(title: str) -> str:
    words = _NON_WORD.sub(" ", title.casefold()).split()
    if len(words) > 1 and words[0] in _LEADING_ARTICLES:
        words = words[1:]
    return " ".join(words)


def title_similarity(left: str, right: str) -> float:
    left_normalized = normalize_title(left)
    right_normalized = normalize_title(right)
    # SequenceMatcher scores two empty strings 1.0, which would make a title
    # that normalizes away match every candidate.
    if not left_normalized or not right_normalized:
        return 0.0
    return SequenceMatcher(None, left_normalized, right_normalized).ratio()


def best_match_index(
    searched_title: str,
    item_year: int | None,
    candidates: Sequence[tuple[str, int | None]],
) -> int | None:
    """Index of the closest (title, year) candidate worth trusting, if any."""
    best: tuple[float, int] | None = None

    for index, (title, year) in enumerate(candidates):
        if (
            item_year is not None
            and year is not None
            and abs(year - item_year) > MAXIMUM_YEAR_DRIFT
        ):
            continue
        score = title_similarity(searched_title, title)
        if score < MINIMUM_TITLE_SIMILARITY:
            continue
        if best is None or score > best[0]:
            best = (score, index)

    return None if best is None else best[1]
