from __future__ import annotations

import re

import requests

from src.utils.request_errors import scrub_request_error

#: Every character ``str.splitlines`` breaks on. A rule line and a log entry
#: both end at any of them, so both sanitizers below are built from this rather
#: than from a list each.
LINE_BREAKS = "\n\r\v\f\x1c\x1d\x1e\x85\N{LINE SEPARATOR}\N{PARAGRAPH SEPARATOR}"

_WHITESPACE_RUN_RE = re.compile(rf"[\s{re.escape(LINE_BREAKS)}]+")

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

# Edition suffixes: "Game - Deluxe Edition", "Game: GOTY Edition"
EDITION_PATTERN = re.compile(
    r"\s*[-:]\s*("
    r"Deluxe Edition|"
    r"GOTY Edition|"
    r"Game of the Year Edition|"
    r"Definitive Edition|"
    r"Complete Edition|"
    r"Enhanced Edition|"
    r"Ultimate Edition|"
    r"Special Edition|"
    r"Collector's Edition|"
    r"Anniversary Edition|"
    r"Remastered|"
    r"Remake"
    r")\s*$",
    re.IGNORECASE,
)
# Edition in parentheses: "(Deluxe Edition)", "(GOTY)", "(Legendary)"
EDITION_PAREN_PATTERN = re.compile(
    r"\s*\(("
    r"Deluxe|"
    r"GOTY|"
    r"Game of the Year|"
    r"Definitive|"
    r"Complete|"
    r"Enhanced|"
    r"Ultimate|"
    r"Special|"
    r"Collector's|"
    r"Anniversary|"
    r"Legendary|"
    r"Remastered|"
    r"Remake"
    r")(?:\s+Edition)?\)\s*$",
    re.IGNORECASE,
)
# DLC suffixes: "Game + DLC Name (DLC)"
DLC_SUFFIX_PATTERN = re.compile(r"\s*\+\s*.+?\s*\(DLC\)\s*$", re.IGNORECASE)
TRADEMARK_PATTERN = re.compile(r"[™®©]")

_UPPERCASE_WORDS: dict[str, str] = {
    "tv": "TV",
    "gog": "GOG",
    "api": "API",
    "id": "ID",
    "csv": "CSV",
    "json": "JSON",
    "rss": "RSS",
    "roms": "ROMs",
}


def humanize_source_id(source_id: str) -> str:
    words = re.split(r"[_-]", source_id)
    return " ".join(_UPPERCASE_WORDS.get(word, word.capitalize()) for word in words)


def clean_game_title_for_search(title: str) -> str:
    """A store's own name for a game, reduced to the one a catalogue lists it
    under. Shared, so every provider searching for a game sends the same string.
    """
    cleaned = title
    cleaned = TRADEMARK_PATTERN.sub("", cleaned).strip()
    # Remove DLC suffix (must run before edition patterns to avoid partial matches)
    cleaned = DLC_SUFFIX_PATTERN.sub("", cleaned).strip()
    cleaned = EDITION_PATTERN.sub("", cleaned).strip()
    cleaned = EDITION_PAREN_PATTERN.sub("", cleaned).strip()
    return cleaned if cleaned else title


def is_blank(value: str) -> bool:
    """The one emptiness rule both doors reach: ``min_length`` cannot say it."""
    return not value.strip()


def _log_escape(match: re.Match[str]) -> str:
    character = match.group()
    return _LOG_ESCAPES.get(character, f"\\u{ord(character):04x}")


def sanitize_for_log(value: str) -> str:
    """Never on a JSON body."""
    return _LOG_UNSAFE_RE.sub(_log_escape, value)


def exception_for_log(error: BaseException) -> str:
    """Never on client-facing text."""
    if isinstance(error, requests.RequestException):
        return sanitize_for_log(scrub_request_error(error))
    return sanitize_for_log(f"{type(error).__name__}: {error}")


def sanitize_rule_text(raw: str) -> str:
    """Strips rather than allowlists: the ``+`` in ``prefer 4+ star ratings`` is
    the operator's own word.
    """
    return _RULE_UNSAFE_RE.sub("", _WHITESPACE_RUN_RE.sub(" ", raw)).strip()


def strip_lone_surrogates(raw: str) -> str:
    """Drop what argv carries and no strict UTF-8 encoder takes."""
    return _LONE_SURROGATE_RE.sub("", raw)


def escape_lone_surrogates(raw: str) -> str:
    """Spell out the same bytes instead of dropping them."""
    return _LONE_SURROGATE_RE.sub(_log_escape, raw)
