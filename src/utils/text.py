"""Text formatting utilities."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.models.content import ContentItem

# Allowlist: Unicode word characters (letters, digits, _), whitespace, and
# punctuation common in genre names (hyphen, ampersand, apostrophe, comma,
# period, slash). Everything else is stripped, including brackets, quotes,
# parentheses, and control characters that could escape prompt structure or
# enable LLM prompt injection.
_GENRE_UNSAFE_RE = re.compile(r"[^\w\s\-&',./]")
_MAX_GENRE_LENGTH = 50

# Broader allowlist for free-text metadata (series names, etc.) that gets
# interpolated into LLM prompts.  Allows colons and parentheses beyond
# what the genre allowlist permits, but still strips control characters
# and prompt-injection vectors like newlines.
_PROMPT_TEXT_UNSAFE_RE = re.compile(r"[^\w\s\-&',./:()!?]")
_MAX_PROMPT_TEXT_LENGTH = 100

_UPPERCASE_WORDS: dict[str, str] = {
    "tv": "TV",
    "gog": "GOG",
    "api": "API",
    "id": "ID",
    "csv": "CSV",
    "json": "JSON",
}


def humanize_source_id(source_id: str) -> str:
    """Convert a snake_case source ID to a human-readable title.

    Applies title-casing with special handling for known acronyms.

    Examples:
        ``finished_tv_shows`` → ``Finished TV Shows``
        ``gog`` → ``GOG``
        ``my_books`` → ``My Books``
        ``personal_site_games`` → ``Personal Site Games``
    """
    words = source_id.split("_")
    return " ".join(_UPPERCASE_WORDS.get(word, word.capitalize()) for word in words)


def _sanitize_genre(raw: str) -> str:
    """Strip characters that could escape prompt structure from a genre string.

    Removes newlines and control characters, applies an allowlist of characters
    expected in real genre names, and enforces a length cap.

    Args:
        raw: Raw genre string from metadata.

    Returns:
        Sanitized genre string, possibly empty.
    """
    cleaned = raw.replace("\n", " ").replace("\r", " ").strip()
    cleaned = _GENRE_UNSAFE_RE.sub("", cleaned)
    return cleaned[:_MAX_GENRE_LENGTH]


def sanitize_prompt_text(raw: str) -> str:
    """Sanitize free-text metadata before interpolating it into an LLM prompt.

    Uses a broader character allowlist than ``_sanitize_genre`` (permits
    colons, parentheses, etc.) while still stripping newlines, control
    characters, and other prompt-injection vectors.

    Args:
        raw: Raw text string from metadata (e.g., series name).

    Returns:
        Sanitized string, possibly empty.
    """
    cleaned = raw.replace("\n", " ").replace("\r", " ").strip()
    cleaned = _PROMPT_TEXT_UNSAFE_RE.sub("", cleaned)
    return cleaned[:_MAX_PROMPT_TEXT_LENGTH]


def extract_raw_genres(item: ContentItem, limit: int = 4) -> list[str]:
    """Extract genre tags from an item's metadata for prompt inclusion.

    Checks ``"genres"`` (canonical list format from enrichment) first,
    then falls back to ``"genre"`` (legacy CSV-import string). Each genre
    value is sanitized to prevent prompt injection.

    Unlike ``recommendations.scorers.extract_genres``, this returns the
    original genre strings with only injection sanitization applied.
    Use this for prompt formatting; use the scorer version for
    cross-content-type matching and normalization.

    Args:
        item: ContentItem to extract genres from.
        limit: Maximum number of genres to return.

    Returns:
        List of sanitized genre strings, possibly empty.
    """
    if not item.metadata:
        return []

    genres = item.metadata.get("genres")
    if isinstance(genres, list) and genres:
        string_genres = [genre for genre in genres[:limit] if isinstance(genre, str)]
        sanitized = [_sanitize_genre(genre) for genre in string_genres]
        return [genre for genre in sanitized if genre]

    genre_string = item.metadata.get("genre")
    if isinstance(genre_string, str) and genre_string:
        sanitized = [_sanitize_genre(part) for part in genre_string.split(",")]
        return [genre for genre in sanitized[:limit] if genre]

    return []


def format_genre_tag(item: ContentItem, limit: int = 4) -> str:
    """Format genre metadata as a bracketed tag for prompt inclusion.

    Returns ``" [Drama, War]"`` when genres exist, or ``""`` otherwise.

    Args:
        item: ContentItem to format genres for.
        limit: Maximum number of genres to return.

    Returns:
        Genre tag string with leading space, or empty string.
    """
    genres = extract_raw_genres(item, limit=limit)
    if not genres:
        return ""
    return f" [{', '.join(genres)}]"
