"""Shared ISO 8601 timestamp parsing."""

from __future__ import annotations

from datetime import UTC, datetime


def parse_iso_timestamp(raw: object) -> datetime | None:
    """Parse an ISO 8601 timestamp, tolerating a trailing ``Z`` offset.

    Normalizes a ``Z`` UTC suffix (not accepted by ``datetime.fromisoformat``
    on all supported Python versions) to ``+00:00`` before parsing. Accepts
    ``object`` rather than ``str`` because callers typically pull *raw* from
    untyped metadata dicts (Trakt API responses, stored item metadata) where
    the value's real type is not guaranteed until this function checks it.

    Args:
        raw: Timestamp value, e.g. from Trakt's API or stored metadata.

    Returns:
        Parsed ``datetime``, or ``None`` if *raw* is not a string or is not a
        valid ISO 8601 timestamp.
    """
    if not isinstance(raw, str):
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def later_iso_timestamp(a: str | None, b: str | None) -> str | None:
    """Return whichever of two ISO 8601 timestamp strings is later.

    Returns the input string unchanged (not a re-formatted datetime) so
    callers can store the original representation. If one side is
    ``None`` or unparseable, the other is returned as-is; if both are
    ``None``/unparseable, returns ``None``.

    Both parsed values are normalized to timezone-aware UTC before
    comparing (a naive datetime is assumed to already be UTC) so that a
    naive-vs-aware comparison never raises ``TypeError``.

    Args:
        a: First ISO 8601 timestamp string, or None.
        b: Second ISO 8601 timestamp string, or None.

    Returns:
        The later of *a* and *b*, or the only parseable one, or None.
    """
    parsed_a = parse_iso_timestamp(a)
    parsed_b = parse_iso_timestamp(b)
    if parsed_a is None:
        return b if parsed_b is not None else None
    if parsed_b is None:
        return a

    aware_a = parsed_a if parsed_a.tzinfo is not None else parsed_a.replace(tzinfo=UTC)
    aware_b = parsed_b if parsed_b.tzinfo is not None else parsed_b.replace(tzinfo=UTC)
    return a if aware_a >= aware_b else b


def merge_seasons_watched_dates(a: object, b: object) -> dict[str, str] | None:
    """Merge two ``seasons_watched_dates`` maps, keeping the later date per season.

    Each argument is a season-number-string -> ISO 8601 timestamp mapping (or
    should be — callers pull these from untyped metadata dicts, so a
    non-``dict`` value, e.g. from corrupt/legacy data, is treated as empty
    rather than raising). For the union of season keys across both sides,
    the later of the two dates wins (see ``later_iso_timestamp``); a season
    present on only one side is gap-filled with that side's date.

    Args:
        a: First seasons_watched_dates mapping, or any non-dict value.
        b: Second seasons_watched_dates mapping, or any non-dict value.

    Returns:
        The combined mapping, or ``None`` if both sides are empty/non-dict
        or every season's dates are unparseable.
    """
    dates_a = a if isinstance(a, dict) else {}
    dates_b = b if isinstance(b, dict) else {}
    combined = {
        season: later
        for season in {*dates_a, *dates_b}
        if (later := later_iso_timestamp(dates_a.get(season), dates_b.get(season)))
        is not None
    }
    return combined or None
