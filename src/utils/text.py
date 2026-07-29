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

# Every C0 control, DEL, every C1 control, and the two Unicode line separators.
# CR/LF forge a log line under the "%(asctime)s | ... | %(message)s" format,
# ESC drives the terminal of whoever cats or tails the file, and the
# separators are line breaks to plenty of log viewers. Spelled with ``\N``
# escapes because the separators themselves are invisible in a source file.
_LOG_UNSAFE_RE = re.compile(
    "[\x00-\x1f\x7f-\x9f\N{LINE SEPARATOR}\N{PARAGRAPH SEPARATOR}]"
)

# Familiar spellings for the controls an operator actually meets. Anything
# else renders as its numeric escape.
_LOG_ESCAPES = {"\0": "\\0", "\t": "\\t", "\n": "\\n", "\r": "\\r"}

# Cap on one value rendered into a log record. Long enough to identify the row
# it came from, short enough that a file full of oversized values cannot bury
# the records around them.
MAX_LOGGED_VALUE_LENGTH = 200

_TRUNCATION_MARKER = "...(truncated)"


def _escape_log_control(match: re.Match[str]) -> str:
    """Render one control character as a printable escape."""
    char = match.group()
    escaped = _LOG_ESCAPES.get(char)
    if escaped is not None:
        return escaped
    codepoint = ord(char)
    return f"\\x{codepoint:02x}" if codepoint <= 0xFF else f"\\u{codepoint:04x}"


def sanitize_for_log(value: str) -> str:
    """Escape control characters and cap the length before logging.

    Values that reach a log line are often user-controlled — a path parameter,
    a settings key, a cell of an uploaded import file. A newline in one of them
    forges a structured log line and an ESC sequence hijacks the terminal of
    whoever reads the file back (CWE-117), so every control character is
    rewritten to a printable escape rather than dropped: the record still shows
    what arrived. The cap keeps a record's size independent of its input.

    Args:
        value: The value to render into a log record.

    Returns:
        The escaped value, truncated with a marker when it exceeds
        :data:`MAX_LOGGED_VALUE_LENGTH`.
    """
    escaped = _LOG_UNSAFE_RE.sub(_escape_log_control, value)
    if len(escaped) > MAX_LOGGED_VALUE_LENGTH:
        return escaped[:MAX_LOGGED_VALUE_LENGTH] + _TRUNCATION_MARKER
    return escaped


def humanize_source_id(source_id: str) -> str:
    """Convert a snake_case source ID to a human-readable title.

    Applies title-casing with special handling for known acronyms.

    Examples:
        ``finished_tv_shows`` → ``Finished TV Shows``
        ``gog`` → ``GOG``
        ``my_books`` → ``My Books``
        ``personal_site_games`` → ``Personal Site Games``
        ``calibre-web`` → ``Calibre Web``
    """
    words = re.split(r"[_-]", source_id)
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


def sanitize_prompt_text_long(raw: str, max_length: int = 200) -> str:
    """Sanitize free-text with a custom length cap.

    Same structural sanitization as :func:`sanitize_prompt_text` (strips
    newlines, control characters, injection vectors) but allows a longer
    cap — suitable for conversation history messages where 100 chars is
    too restrictive.

    Args:
        raw: Raw text string.
        max_length: Maximum output length (default 200).

    Returns:
        Sanitized string, capped to *max_length*.
    """
    cleaned = raw.replace("\n", " ").replace("\r", " ").strip()
    cleaned = _PROMPT_TEXT_UNSAFE_RE.sub("", cleaned)
    return cleaned[:max_length]


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
    result, _ = sanitize_prompt_text_with_truncation(raw)
    return result


def sanitize_prompt_text_with_truncation(raw: str) -> tuple[str, bool]:
    """Sanitize free-text and report whether truncation occurred.

    Same sanitization as :func:`sanitize_prompt_text`, but also returns
    a flag indicating whether the cleaned text exceeded the cap and was
    truncated. Useful when callers need to append an ellipsis only on
    actual truncation (not on naturally-at-cap text).

    Args:
        raw: Raw text string from metadata.

    Returns:
        Tuple of (sanitized string, was_truncated).
    """
    cleaned = raw.replace("\n", " ").replace("\r", " ").strip()
    cleaned = _PROMPT_TEXT_UNSAFE_RE.sub("", cleaned)
    was_truncated = len(cleaned) > _MAX_PROMPT_TEXT_LENGTH
    return cleaned[:_MAX_PROMPT_TEXT_LENGTH], was_truncated


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
