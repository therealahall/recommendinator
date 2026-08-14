"""Text formatting utilities."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

import requests

from src.utils.request_errors import scrub_request_error

if TYPE_CHECKING:
    from src.models.content import ContentItem

#: Every character ``str.splitlines`` breaks on. A prompt line and a log entry
#: both end at any of them, so both sanitizers below are built from this rather
#: than from a list each.
LINE_BREAKS = "\n\r\v\f\x1c\x1d\x1e\x85\N{LINE SEPARATOR}\N{PARAGRAPH SEPARATOR}"

_WHITESPACE_RUN_RE = re.compile(rf"[\s{re.escape(LINE_BREAKS)}]+")

# Both allowlists admit U+0020, never \s: \s matches every LINE_BREAKS member
# too, so an allowlisted \s is a forged prompt line.
_GENRE_UNSAFE_RE = re.compile(r"[^\w \-&',./]")
_MAX_GENRE_LENGTH = 50

# Colons and parentheses beyond the genre allowlist, for free-text metadata
# such as series names.
_PROMPT_TEXT_UNSAFE_RE = re.compile(r"[^\w \-&',./:()!?]")
_MAX_PROMPT_TEXT_LENGTH = 100

# NUL joins them because it ends the entry for any reader that stops at one.
# The first three keep the spelling every log reader knows; the rest have no
# conventional one, so they get the codepoint that identifies them.
_LOG_ESCAPES = {"\n": "\\n", "\r": "\\r", "\0": "\\0"} | {
    character: f"\\u{ord(character):04x}"
    for character in LINE_BREAKS
    if character not in "\n\r"
}
# A lone surrogate costs the whole entry, not a character: the handler's
# encoder raises inside emit and handleError swallows it. os.listdir hands one
# over for any filename that is not valid UTF-8.
_SURROGATE_RANGE = "\ud800-\udfff"
# ESC[2K\r erases the line an operator just read — CWE-117 without a break in
# it. C1 is in because the log file is UTF-8, and a terminal decoding U+009B
# out of it obeys CSI.
_CONTROL_RANGE = r"\x00-\x1f\x7f\x80-\x9f"
_LOG_UNSAFE_RE = re.compile(
    f"[{_CONTROL_RANGE}{re.escape(''.join(_LOG_ESCAPES))}{_SURROGATE_RANGE}]"
)

# Quotes and braces forge structure in the quoted slot. Only a lone surrogate
# fails the encode of the request body; controls encode fine and go because a
# rule is prose.
_RULE_UNSAFE_RE = re.compile(rf'["{{}}{_CONTROL_RANGE}{_SURROGATE_RANGE}]')

_LONE_SURROGATE_RE = re.compile(f"[{_SURROGATE_RANGE}]")

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
        ``calibre-web`` → ``Calibre Web``
    """
    words = re.split(r"[_-]", source_id)
    return " ".join(_UPPERCASE_WORDS.get(word, word.capitalize()) for word in words)


def _log_escape(match: re.Match[str]) -> str:
    """The conventional spelling where one exists, the codepoint otherwise."""
    character = match.group()
    return _LOG_ESCAPES.get(character, f"\\u{ord(character):04x}")


def sanitize_for_log(value: str) -> str:
    """Escape every line break, control character and lone surrogate.

    The single-line file format means an unescaped break forges an entry
    (CWE-117), a terminal control rewrites one and a surrogate deletes one.
    Never on a JSON body.
    """
    return _LOG_UNSAFE_RE.sub(_log_escape, value)


def exception_for_log(error: BaseException) -> str:
    """Render an exception as one log-safe line, class name included.

    A ``requests`` fault is scrubbed instead: its words quote the request URL
    and the ``?api_key=`` in it. Dispatching here spares every caller's
    handler ordering. Never on client-facing text.
    """
    if isinstance(error, requests.RequestException):
        return sanitize_for_log(scrub_request_error(error))
    return sanitize_for_log(f"{type(error).__name__}: {error}")


def _clean_for_prompt(raw: str, unsafe: re.Pattern[str]) -> str:
    """Collapse whitespace to single spaces and strip what *unsafe* matches.

    Args:
        raw: Raw text from metadata or user input.
        unsafe: Pattern matching every character to strip.

    Returns:
        Single-line cleaned text, uncapped and possibly empty.
    """
    return unsafe.sub("", _WHITESPACE_RUN_RE.sub(" ", raw)).strip()


def _sanitize_genre(raw: str) -> str:
    """Reduce a genre string to the characters real genre names use.

    Args:
        raw: Raw genre string from metadata.

    Returns:
        Sanitized genre string, possibly empty.
    """
    return _clean_for_prompt(raw, _GENRE_UNSAFE_RE)[:_MAX_GENRE_LENGTH]


def sanitize_prompt_text_long(raw: str, max_length: int = 200) -> str:
    """Sanitize free-text with a custom length cap.

    Same structural sanitization as :func:`sanitize_prompt_text` (strips
    line breaks, control characters, injection vectors) but allows a longer
    cap — suitable for conversation history messages, where 100 chars is too
    restrictive. Preference rules use :func:`sanitize_rule_text`; this
    allowlist eats the ``+`` in ``prefer 4+ star ratings``.

    Args:
        raw: Raw text string.
        max_length: Maximum output length (default 200).

    Returns:
        Sanitized single-line string, capped to *max_length*.
    """
    return _clean_for_prompt(raw, _PROMPT_TEXT_UNSAFE_RE)[:max_length]


def sanitize_rule_text(raw: str) -> str:
    """Reduce a preference rule to one safe line of prompt text.

    Strips rather than allowlists: the ``+`` in ``prefer 4+ star ratings`` is
    the operator's own word. Uncapped; the caller applies its storage cap.
    """
    return _clean_for_prompt(raw, _RULE_UNSAFE_RE)


def strip_lone_surrogates(raw: str) -> str:
    """Drop what argv carries and no strict UTF-8 encoder takes.

    ``surrogateescape`` hands an undecodable byte over as a lone surrogate, and
    SQLite and ``click.echo`` both raise on one. Line breaks stay: they encode.
    """
    return _LONE_SURROGATE_RE.sub("", raw)


def sanitize_prompt_text(raw: str) -> str:
    """Sanitize free-text metadata before interpolating it into an LLM prompt.

    Uses a broader character allowlist than ``_sanitize_genre`` (permits
    colons, parentheses, etc.) while still stripping line breaks, control
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
    cleaned = _clean_for_prompt(raw, _PROMPT_TEXT_UNSAFE_RE)
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
