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

Users may still add their own patterns. Those are capped by count and by
length (see ``compile_extra_patterns``), which bounds how much regex runs
against each title. Nothing here bounds how long a single pattern takes:
``re`` has no execution timeout, and no cheap static check tells a safe
pattern from a catastrophically backtracking one. A pattern that backtracks
exponentially does not end when the scan ends — a Python thread cannot be
cancelled, so the sync worker running it is lost until the process restarts,
and every later sync runs with one fewer worker.
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

# Filesystem-safe naming substitutes underscores for spaces. Applied before
# the trailing-strip loop so chains like "Some_Game_(USA)_(Beta)" become
# "Some Game (USA) (Beta)" and the trailing-paren regex can match.
_UNDERSCORE_RUN = re.compile(r"_+")

# Cap on user-supplied regex string length. Bounds how much pattern text runs
# against each title; it does not bound run time, because a five-character
# pattern can still backtrack exponentially.
_MAX_PATTERN_LENGTH = 200

# Cap on how many patterns one source may carry. Each is applied to every
# title, so the per-title cost is (patterns x titles); a real stash needs a
# handful.
_MAX_PATTERNS = 10

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

    title = _UNDERSCORE_RUN.sub(" ", title)

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
    """Compile user-supplied regex strings under a count cap and a length cap.

    At most ``_MAX_PATTERNS`` patterns, each at most ``_MAX_PATTERN_LENGTH``
    characters. That bounds how much regex text runs against every title.

    It does **not** bound how long a pattern runs, and that is the residual
    risk. ``extra_strip_patterns`` is settable over the network
    (``POST /api/sync/sources`` stores whatever the schema names) and Python's
    ``re`` has no execution timeout, so a caller who can write source config
    can hand a worker thread a match that never returns — ``(a+)+`` against a
    long enough title is five characters of input. That thread cannot be
    cancelled, so it is lost for the lifetime of the process, and every
    subsequent sync runs with one fewer worker; enough of them and syncing
    stops entirely. Bounding it for real needs the match run somewhere
    killable (a subprocess with a timeout) or a backtracking-free engine such
    as RE2. Deciding whether an arbitrary regex backtracks catastrophically is
    not something a cheap static check can do, so this does not pretend to.

    Raises ``ValueError`` on the first pattern that breaks a cap or that does
    not compile.
    """
    raw_patterns = list(patterns)
    if len(raw_patterns) > _MAX_PATTERNS:
        raise ValueError(
            f"At most {_MAX_PATTERNS} patterns are allowed, got {len(raw_patterns)}"
        )

    compiled: list[re.Pattern[str]] = []
    for raw_pattern in raw_patterns:
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
