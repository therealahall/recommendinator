"""Internal ROM title cleaner for the roms plugin.

Module-private (underscore prefix); not part of the plugin discovery surface.
Used by ``RomScannerPlugin`` to normalize ROM filenames into user-facing
titles.

The cleaner removes the No-Intro / Redump / TOSEC release-tag conventions
that plague ROM filenames — region codes, language sets, year, revision,
disc markers, status flags, dump-quality brackets, hex IDs — leaving the
plain game name. Case is preserved.

A consolidated approach beats user-supplied regex-soup in two ways:
- Defaults are curated and tested against real-world ROM datasets.
- Users only need to add patterns for the corner cases their stash has.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

# Strip exactly one trailing "(...)" or "[...]" group with optional leading
# whitespace. Re-applied in a loop so chains like
# "Game (US) (1994) (Action) (Genesis)" flatten in one call.
_TRAILING_PAREN = re.compile(r"\s*\([^()]*\)\s*$")
_TRAILING_BRACKET = re.compile(r"\s*\[[^\[\]]*\]\s*$")

# Patterns that target noise appearing mid-title (e.g. scene-release domain
# stamps) where the trailing-group strip loop would never see them.
# Applied unconditionally before the trailing strip.
_INLINE_NOISE = [
    re.compile(r"\s*\(nsw2u\.com\)\s*", re.IGNORECASE),
]

# Cap on user-supplied regex string length. A bound on the input regex is the
# pragmatic mitigation for ReDoS in a single-user tool: Python's re engine
# has no timeout, but pathological backtracking patterns are typically much
# longer than legitimate ones. Tested filenames are also NAME_MAX-bounded.
_MAX_PATTERN_LENGTH = 200

# Real-world chains rarely exceed 6 tail groups; the cap also bounds work
# on adversarial input.
_MAX_TRAILING_PASSES = 8


def clean_display_title(
    raw: str, extra_patterns: Iterable[re.Pattern[str]] | None = None
) -> str:
    """Return a user-facing title with ROM release artifacts stripped.

    Built-in cleanup handles:

    - No-Intro / Redump / TOSEC trailing tags: ``(USA)``, ``(Europe)``,
      ``(1994)``, ``(Rev A)``, ``(En,Fr,Es)``, ``(Disc 1)``, ``(Beta)``,
      ``(Proto)``, ``(Sample)``, ``(Demo)``, ``(Unl)``, ``(Alpha)``,
      ``(v1.0)``, and any other trailing parenthesized group
    - Bracket tags: ``[NTSC-U]``, ``[SLUS-00067]``, ``[!]``, ``[b]``, ``[h]``,
      ``[v0]``, ``[T+En]``, ``[0100F2C0115B6000]``
    - Known noise suffixes: ``(nsw2u.com)``

    User-supplied ``extra_patterns`` are applied last, after the built-ins.

    Args:
        raw: The raw filename stem (without file extension).
        extra_patterns: Optional compiled regex objects appended to the
            built-in cleanup pipeline.

    Returns:
        Cleaned title with surrounding whitespace trimmed. Returns the
        empty string if the input is empty after stripping.
    """
    title = raw.strip()
    if not title:
        return ""

    for pattern in _INLINE_NOISE:
        title = pattern.sub(" ", title)

    title = _strip_trailing_groups(title)

    if extra_patterns:
        for pattern in extra_patterns:
            title = pattern.sub("", title)
        # Re-sweep trailing groups so an extra pattern that strips a
        # mid-title segment (exposing a previously-internal paren as the
        # new tail) is also handled.
        title = _strip_trailing_groups(title)

    return _collapse_whitespace(title.strip())


def _strip_trailing_groups(title: str) -> str:
    """Repeatedly remove trailing ``(...)`` and ``[...]`` groups."""
    for _ in range(_MAX_TRAILING_PASSES):
        new = _TRAILING_BRACKET.sub("", title)
        new = _TRAILING_PAREN.sub("", new)
        if new == title:
            break
        title = new.rstrip()
    return title


def compile_extra_patterns(patterns: Iterable[str]) -> list[re.Pattern[str]]:
    """Compile user-supplied regex strings.

    Raises ``ValueError`` on the first invalid pattern (syntax error or
    excessive length).
    """
    compiled: list[re.Pattern[str]] = []
    for raw_pattern in patterns:
        if len(raw_pattern) > _MAX_PATTERN_LENGTH:
            raise ValueError(
                f"Pattern exceeds {_MAX_PATTERN_LENGTH} chars "
                f"({len(raw_pattern)}): {raw_pattern!r}"
            )
        try:
            compiled.append(re.compile(raw_pattern))
        except re.error as error:
            raise ValueError(f"Invalid regex {raw_pattern!r}: {error}") from error
    return compiled


_WHITESPACE_RUN = re.compile(r"\s+")


def _collapse_whitespace(text: str) -> str:
    """Collapse internal whitespace runs to single spaces."""
    return _WHITESPACE_RUN.sub(" ", text)


def normalize_title_key(title: str) -> str:
    """Return a key for case-insensitive whitespace-collapsed dedup.

    Two titles whose normalized keys match are considered the same game by
    the plugin's title-level deduplication.
    """
    return _WHITESPACE_RUN.sub(" ", title.strip().lower())
