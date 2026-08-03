"""Shared ISO 8601 timestamp parsing and local-calendar-date narrowing."""

from __future__ import annotations

from datetime import UTC, date, datetime


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


def _as_utc(value: datetime) -> datetime:
    """Return *value* as an aware UTC datetime, assuming a naive value is UTC."""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def utc_now() -> datetime:
    """Return the current instant as an aware UTC datetime.

    The only clock read behind the storage and date layers, so that every
    stamp they take — a completion date, a season watch timestamp — moves
    together when a test freezes it. Other layers (chat memory, sync job
    bookkeeping) call ``datetime.now`` for themselves, and freezing this
    does not move them.

    Returns:
        The current instant, in UTC.
    """
    return datetime.now(UTC)


def local_today() -> date:
    """Return today's calendar date in the host's zone.

    The counterpart of :func:`local_date_from_iso_timestamp` for the current
    instant: a completion recorded in the app means the day the user is
    living, so an evening completion west of UTC must not be dated tomorrow.
    Both narrowings read the same host zone, which a container inherits
    through the ``TZ`` the operator sets on the service, so an imported date
    and an in-app one are on the same calendar.

    Returns:
        Today's date in the host's zone.
    """
    return utc_now().astimezone().date()


def local_date_from_iso_timestamp(raw: object) -> date | None:
    """Narrow an ISO 8601 timestamp to its calendar date in the host's zone.

    Watch and read timestamps are UTC *instants* — Trakt's ``last_watched_at``,
    this app's own ``seasons_watched_dates`` stamps — so taking ``.date()``
    straight off them reports the UTC calendar day: west of UTC an evening
    watch lands on the following day, east of UTC an early-morning one lands on
    the previous day. Converting to the host's zone first yields the day the
    user actually lived, which is what a completion date means everywhere it is
    shown, exported, merged or ranked.

    There is no timezone setting: the zone comes from the host, which a
    container inherits through the ``TZ`` the operator sets on the service.
    ``astimezone()`` is called without an argument so the offset is the one in
    force *at that instant*, which keeps a winter timestamp correct when it is
    read in summer. A naive timestamp is assumed to already be UTC, as in
    ``later_iso_timestamp``.

    Args:
        raw: Timestamp value, e.g. from Trakt's API or stored metadata.

    Returns:
        The local calendar date, or ``None`` if *raw* is not a parseable
        ISO 8601 timestamp, or if shifting it into the host's zone lands
        outside the range ``datetime`` can represent.
    """
    parsed = parse_iso_timestamp(raw)
    if parsed is None:
        return None
    try:
        return _as_utc(parsed).astimezone().date()
    except OverflowError:
        # The extremes of the datetime range have no local equivalent in every
        # zone: 9999-12-31T23:59Z shifted east, or 0001-01-01T00:00Z shifted
        # west, falls off the end. Callers get None here as they do for any
        # other value this cannot narrow, rather than an exception from a
        # helper that never raises for bad input.
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

    return a if _as_utc(parsed_a) >= _as_utc(parsed_b) else b


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
