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


# Collapse runs of non-alphanumeric characters to single spaces.
_NON_ALNUM_PATTERN = re.compile(r"[^0-9a-z]+")


def normalize_for_search(text: str) -> str:
    """Normalize a string for search matching.

    Strips leading articles (via get_sort_title), lowercases, replaces
    punctuation with spaces, and collapses whitespace.  This lets
    "Die Hard (1988)" and "die hard" compare on equal footing.

    Args:
        text: The string to normalize.

    Returns:
        A normalized, article-stripped, punctuation-free string.
    """
    if not text:
        return ""

    normalized = get_sort_title(text)
    normalized = _NON_ALNUM_PATTERN.sub(" ", normalized)
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


def matches_search(haystack: str, needle: str) -> bool:
    """Check whether *haystack* matches the search term *needle*.

    Matching is case-insensitive and article/punctuation-normalized across
    three tiers: exact equality, substring containment, and fuzzy
    (typo-tolerant) matching via FUZZY_MATCH_THRESHOLD.

    Args:
        haystack: The candidate string (e.g. a title or creator name).
        needle: The search term.

    Returns:
        True if the haystack matches the search term at any tier.
    """
    if not haystack or not needle:
        return False

    haystack_norm = normalize_for_search(haystack)
    needle_norm = normalize_for_search(needle)
    if not haystack_norm or not needle_norm:
        return False

    if haystack_norm == needle_norm:
        return True
    if needle_norm in haystack_norm:
        return True
    return _best_window_ratio(needle_norm, haystack_norm) >= FUZZY_MATCH_THRESHOLD
