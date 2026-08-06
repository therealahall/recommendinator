"""Sorting utilities for content items."""

import re
from difflib import SequenceMatcher

# Minimum SequenceMatcher ratio for a fuzzy (typo-tolerant) match.
# The hard requirement is that "Die Heard" matches "Die Hard (1988)": after
# punctuation normalization those are "die heard" vs "die hard 1988", whose best
# window ("die heard" vs "die hard ") scores ~0.89, so the threshold must sit at
# or below that. A near-miss like "Inception" vs "Insepton" scores 0.75 and must
# be rejected, so the threshold must sit above that. 0.8 falls in that band and
# separates real typos from unrelated terms.
FUZZY_MATCH_THRESHOLD = 0.8

# Longest search term either interface accepts. Every search slides a
# SequenceMatcher window over every candidate, so the term's length multiplies
# that pass's cost. 200 characters is far longer than any title worth searching
# for and short enough that the pass stays cheap.
MAX_SEARCH_LENGTH = 200

# Articles to strip when sorting titles. Intentionally English-only: a
# multilingual set collides with English words (German "die" in "Die Hard",
# Spanish "el" in "El Camino"), sorting them under the wrong letter. Locale-aware
# multilingual stripping is deferred to a future per-locale config (see #77).
ARTICLES = frozenset({"a", "an", "the"})

# Regex to match a leading article followed by whitespace
_ARTICLE_PATTERN = re.compile(
    r"^(" + "|".join(re.escape(article) for article in ARTICLES) + r")\s+",
    re.IGNORECASE,
)


def get_sort_title(title: str) -> str:
    """Get a sort key for a title by stripping leading articles.

    This allows titles like "The Lord of the Rings" to be sorted under "L"
    instead of "T".

    Args:
        title: The original title.

    Returns:
        A normalized string suitable for sorting (lowercase, article stripped).

    Examples:
        >>> get_sort_title("The Lord of the Rings")
        'lord of the rings'
        >>> get_sort_title("A Tale of Two Cities")
        'tale of two cities'
        >>> get_sort_title("An American in Paris")
        'american in paris'
        >>> get_sort_title("Die Hard")
        'die hard'
        >>> get_sort_title("1984")
        '1984'
    """
    if not title:
        return ""

    # Normalize to lowercase for consistent sorting
    normalized = title.lower().strip()

    # Strip leading article if present
    match = _ARTICLE_PATTERN.match(normalized)
    if match:
        normalized = normalized[match.end() :]

    return normalized


def _contains_on_word_boundary(shorter: str, longer: str) -> bool:
    """Check if `shorter` occurs in `longer` aligned on word boundaries.

    An occurrence counts only when it is bounded on each side by the string
    start/end or a non-alphanumeric character, so a short title cannot match
    mid-word (e.g. "an" must not match inside "antique").

    Returns False for an empty `shorter`: it has no boundaries to align and
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
    """Check if two titles are similar (fuzzy matching).

    Uses get_sort_title to strip leading English articles and normalize
    case, then checks substring containment.

    Substring containment must align on word boundaries: the shorter
    normalized title only matches when it is bounded by the string start/end
    or a non-alphanumeric character, so it never matches mid-word.

    Args:
        title1: First title.
        title2: Second title.

    Returns:
        True if titles are similar.
    """
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
    """Normalize a string for search matching.

    Strips leading articles (via get_sort_title), lowercases, replaces
    punctuation with spaces, and collapses whitespace.  This lets
    "Die Hard (1988)" and "die hard" compare on equal footing.  Letters in
    any script are preserved, so non-Latin titles normalize to themselves
    rather than to an empty string.

    Args:
        text: The string to normalize.

    Returns:
        A normalized, article-stripped, punctuation-free string.
    """
    if not text:
        return ""

    normalized = get_sort_title(text)
    normalized = _NON_WORD_PATTERN.sub(" ", normalized)
    return normalized.strip()


def _best_window_ratio(needle: str, haystack: str) -> float:
    """Best SequenceMatcher ratio of *needle* against any window of *haystack*.

    Slides a window the length of *needle* across *haystack* so that a typo'd
    term still matches a longer title (e.g. "die heard" vs "die hard 1988")
    without the trailing tokens diluting the score.

    Args:
        needle: The (normalized) search term.
        haystack: The (normalized) candidate string.

    Returns:
        A ratio in the range 0.0 to 1.0.  If any window meets
        FUZZY_MATCH_THRESHOLD the scan stops early and returns that window's
        ratio; otherwise the highest ratio found across all windows is returned.
    """
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
    """Run the three matching tiers over two already-normalized strings.

    Matching is case-insensitive and article/punctuation-normalized, because
    both sides came through :func:`normalize_for_search`. Tiers one and two
    are exact equality and substring containment; tier three is the fuzzy,
    typo-tolerant window scan at :data:`FUZZY_MATCH_THRESHOLD`.
    """
    if not haystack_norm:
        return False
    if haystack_norm == needle_norm:
        return True
    if needle_norm in haystack_norm:
        return True
    return _best_window_ratio(needle_norm, haystack_norm) >= FUZZY_MATCH_THRESHOLD


# Separates the title from the creator in a stored search text. Search
# normalization collapses every non-word character to a space, so neither half
# nor a normalized term can contain a newline: a substring found in the joined
# string therefore always lies inside one half, never across the two.
_SEARCH_TEXT_SEPARATOR = "\n"


def build_search_text(title: str | None, creator: str | None) -> str:
    """Build the stored haystack a library search matches an item against.

    Holding the normalized title and creator in one column lets a search run
    every tier over both halves without loading the item.
    """
    return _SEARCH_TEXT_SEPARATOR.join(
        (normalize_for_search(title or ""), normalize_for_search(creator or ""))
    )


def search_text_matches(search_text: str | None, needle_norm: str) -> bool:
    """Check a stored search text against an already-normalized search term.

    Runs all three tiers against each half the text holds, so an item matches
    on its title or on its creator and never on the two read as one string.

    Args:
        search_text: A stored :func:`build_search_text` value.
        needle_norm: The search term, already through
            :func:`normalize_for_search`.

    Returns:
        True if the title or the creator matches at any tier.
    """
    if not search_text or not needle_norm:
        return False
    return any(
        _matches_normalized(half, needle_norm)
        for half in search_text.split(_SEARCH_TEXT_SEPARATOR)
    )
