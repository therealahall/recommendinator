"""Sorting utilities for content items."""

import re

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
