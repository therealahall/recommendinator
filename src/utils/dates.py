"""Shared ISO 8601 timestamp parsing."""

from __future__ import annotations

from datetime import datetime


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
