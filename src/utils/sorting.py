import re
from difflib import SequenceMatcher

FUZZY_MATCH_THRESHOLD = 0.8

# A candidate matching neither the exact nor the substring tier costs a SequenceMatcher
# window slid across it, so the term's length multiplies that scan's cost.
MAX_SEARCH_LENGTH = 200

# Intentionally English-only: a multilingual set collides with English words (German
# "die" in "Die Hard", Spanish "el" in "El Camino"), sorting them under the wrong
# letter.
ARTICLES = frozenset({"a", "an", "the"})

_ARTICLE_PATTERN = re.compile(
    r"^(" + "|".join(re.escape(article) for article in ARTICLES) + r")\s+",
    re.IGNORECASE,
)


def get_sort_title(title: str) -> str:
    if not title:
        return ""

    normalized = title.lower().strip()

    match = _ARTICLE_PATTERN.match(normalized)
    if match:
        normalized = normalized[match.end() :]

    return normalized


def _contains_on_word_boundary(shorter: str, longer: str) -> bool:
    """Returns False for an empty `shorter`: it has no boundaries to align and
    str.find("") would otherwise loop forever.
    """
    if not shorter:
        return False

    shorter_length = len(shorter)
    start = longer.find(shorter)
    while start != -1:
        before_ok = start == 0 or not longer[start - 1].isalnum()
        end = start + shorter_length
        after_ok = end == len(longer) or not longer[end].isalnum()
        if before_ok and after_ok:
            return True
        start = longer.find(shorter, start + 1)
    return False


def titles_similar(title1: str, title2: str) -> bool:
    if not title1 or not title2:
        return False

    t1_norm = get_sort_title(title1)
    t2_norm = get_sort_title(title2)

    if t1_norm == t2_norm:
        return True

    # Compare the shorter against the longer. When lengths are equal the two
    # strings differ (equality returned above), so neither can contain the
    # other and the helper returns False regardless of ordering.
    shorter, longer = sorted((t1_norm, t2_norm), key=len)
    return _contains_on_word_boundary(shorter, longer)


# Collapse runs of non-word characters (and underscores) to single spaces.
# Python's \w spans every script, so letters outside ASCII survive: an
# ASCII-only class would normalize a Cyrillic or Japanese title to the empty
# string, making it unreachable through search.
_NON_WORD_PATTERN = re.compile(r"[\W_]+")


def normalize_for_search(text: str) -> str:
    if not text:
        return ""

    normalized = get_sort_title(text)
    normalized = _NON_WORD_PATTERN.sub(" ", normalized)
    return normalized.strip()


def _best_window_ratio(needle: str, haystack: str) -> float:
    if len(needle) >= len(haystack):
        return SequenceMatcher(None, needle, haystack).ratio()

    best = 0.0
    window = len(needle)
    for start in range(len(haystack) - window + 1):
        ratio = SequenceMatcher(None, needle, haystack[start : start + window]).ratio()
        if ratio >= FUZZY_MATCH_THRESHOLD:
            # The caller only needs to know the threshold is met; no window can
            # raise the verdict beyond "matches", so stop scanning early.
            return ratio
        if ratio > best:
            best = ratio
    return best


def _matches_normalized(haystack_norm: str, needle_norm: str) -> bool:
    """Matching is case-insensitive and article/punctuation-normalized, because
    both sides came through :func:`normalize_for_search`.
    """
    if not haystack_norm:
        return False
    if haystack_norm == needle_norm:
        return True
    if needle_norm in haystack_norm:
        return True
    return _best_window_ratio(needle_norm, haystack_norm) >= FUZZY_MATCH_THRESHOLD


# Separates the parts of a search text. Search normalization collapses every
# non-word character to a space, so neither a part nor a search term can hold
# a newline: a substring found in the joined string lies inside one part.
_SEARCH_TEXT_SEPARATOR = "\n"


def build_search_text(
    title: str | None, creator: str | None, series: str | None = None
) -> str:
    """The haystack a search matches an item against, a part per stated field."""
    return _SEARCH_TEXT_SEPARATOR.join(
        (
            normalize_for_search(title or ""),
            normalize_for_search(creator or ""),
            normalize_for_search(series or ""),
        )
    )


def search_text_matches(search_text: str | None, needle_norm: str) -> bool:
    """Runs all three tiers against each part the text holds, so an item matches
    on its title, its creator or its series, never on them read as one string.
    """
    if not search_text or not needle_norm:
        return False
    return any(
        _matches_normalized(part, needle_norm)
        for part in search_text.split(_SEARCH_TEXT_SEPARATOR)
    )
