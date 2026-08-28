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

# Caps on user-supplied regex: length here, count below. Execution time stays
# unbounded — Python's re takes no timeout — so these make catastrophic
# backtracking harder to reach, not impossible.
_MAX_PATTERN_LENGTH = 200

# A stash spanning every console still needs only a handful of extra patterns.
_MAX_PATTERN_COUNT = 32

# Real-world chains rarely exceed 6 tail groups; the cap also bounds work
# on adversarial input.
_MAX_TRAILING_PASSES = 8


def clean_display_title(
    raw: str, extra_patterns: Iterable[re.Pattern[str]] | None = None
) -> str:
    """User-supplied ``extra_patterns`` are applied last, after the built-ins."""
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
    for _ in range(_MAX_TRAILING_PASSES):
        new = _TRAILING_BRACKET.sub("", title)
        new = _TRAILING_PAREN.sub("", new)
        if new == title:
            break
        title = new.rstrip()
    return title


def compile_extra_patterns(patterns: Iterable[str]) -> list[re.Pattern[str]]:
    raw_patterns = list(patterns)
    if len(raw_patterns) > _MAX_PATTERN_COUNT:
        raise ValueError(
            f"Pattern list exceeds {_MAX_PATTERN_COUNT} entries "
            f"({len(raw_patterns)})"
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
    return _WHITESPACE_RUN.sub(" ", text)


def normalize_title_key(title: str) -> str:
    """Two titles whose normalized keys match are considered the same game by
    the plugin's title-level deduplication.
    """
    return _WHITESPACE_RUN.sub(" ", title.strip().lower())
