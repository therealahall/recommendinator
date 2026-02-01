"""Sorting utilities for content items."""

import re

# Common articles to strip when sorting titles.
# Includes English and some common non-English articles.
ARTICLES = frozenset(
    {
        "a",
        "an",
        "the",
        # French
        "le",
        "la",
        "les",
        "l'",
        "un",
        "une",
        # Spanish
        "el",
        "los",
        "las",
        # German
        "der",
        "die",
        "das",
        "ein",
        "eine",
        # Italian
        "il",
        "lo",
        "i",
        "gli",
    }
)

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
        >>> get_sort_title("Les Misérables")
        'misérables'
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
