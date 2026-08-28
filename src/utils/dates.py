from __future__ import annotations

from datetime import UTC, date, datetime


def parse_iso_timestamp(raw: object) -> datetime | None:
    """Normalizes a ``Z`` UTC suffix (not accepted by ``datetime.fromisoformat``
    on all supported Python versions) to ``+00:00`` before parsing.
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
    """The only clock read behind the storage and date layers, so that every
    stamp they take — a completion date, a season watch timestamp — moves
    together when a test freezes it.
    """
    return datetime.now(UTC)


def local_today() -> date:
    """The counterpart of :func:`local_date_from_iso_timestamp` for the current
    instant: a completion recorded in the app means the day the user is
    living, so an evening completion west of UTC must not be dated tomorrow.
    """
    return utc_now().astimezone().date()


def local_date_from_iso_timestamp(raw: object) -> date | None:
    """``astimezone()`` is called without an argument so the offset is the one in
    force *at that instant*, which keeps a winter timestamp correct when it is
    read in summer.
    """
    parsed = parse_iso_timestamp(raw)
    if parsed is None:
        return None
    try:
        return _as_utc(parsed).astimezone().date()
    except OverflowError:
        # The extremes of the datetime range have no local equivalent in every
        # zone: 9999-12-31T23:59Z shifted east, or 0001-01-01T00:00Z shifted
        # west, falls off the end.
        return None


def later_iso_timestamp(a: str | None, b: str | None) -> str | None:
    parsed_a = parse_iso_timestamp(a)
    parsed_b = parse_iso_timestamp(b)
    if parsed_a is None:
        return b if parsed_b is not None else None
    if parsed_b is None:
        return a

    return a if _as_utc(parsed_a) >= _as_utc(parsed_b) else b


def merge_seasons_watched_dates(a: object, b: object) -> dict[str, str] | None:
    dates_a = a if isinstance(a, dict) else {}
    dates_b = b if isinstance(b, dict) else {}
    combined = {
        season: later
        for season in {*dates_a, *dates_b}
        if (later := later_iso_timestamp(dates_a.get(season), dates_b.get(season)))
        is not None
    }
    return combined or None
