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

_UPPERCASE_WORDS: dict[str, str] = {
    "tv": "TV",
    "gog": "GOG",
    "api": "API",
    "id": "ID",
    "csv": "CSV",
    "json": "JSON",
}


def humanize_source_id(source_id: str) -> str:
    words = re.split(r"[_-]", source_id)
    return " ".join(_UPPERCASE_WORDS.get(word, word.capitalize()) for word in words)


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
