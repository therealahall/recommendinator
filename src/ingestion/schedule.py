"""Sync cadence presets and due computation for automated per-source syncing.

Pure logic: callers pass the instants in, aware UTC as
:func:`src.utils.dates.utc_now` returns them.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from src.ingestion.plugin_base import SourcePlugin


@dataclass(frozen=True)
class SyncIntervalPreset:
    """One selectable sync cadence. ``duration`` is None for "off"."""

    key: str
    label: str
    duration: timedelta | None


#: The single source of truth for sync cadences: the CLI choice, the web
#: validation and the frontend option list are all built from this, in this
#: order, never from a copy.
SYNC_INTERVAL_PRESETS: tuple[SyncIntervalPreset, ...] = (
    SyncIntervalPreset("off", "Off", None),
    SyncIntervalPreset("hourly", "Every hour", timedelta(hours=1)),
    SyncIntervalPreset("6h", "Every 6 hours", timedelta(hours=6)),
    SyncIntervalPreset("daily", "Daily", timedelta(days=1)),
    SyncIntervalPreset("weekly", "Weekly", timedelta(days=7)),
)

SYNC_INTERVAL_KEYS: tuple[str, ...] = tuple(
    preset.key for preset in SYNC_INTERVAL_PRESETS
)

_PRESETS_BY_KEY: dict[str, SyncIntervalPreset] = {
    preset.key: preset for preset in SYNC_INTERVAL_PRESETS
}

#: Backoff never widens a cadence past a day, so a source broken since last
#: week is still retried today and the operator's fix takes effect without them
#: hunting for a "sync now" button.
MAX_BACKOFF_INTERVAL = timedelta(hours=24)

# Five doublings put even the shortest preset past the ceiling. Capping the
# exponent keeps a source that has failed thousands of times from multiplying a
# timedelta by 2**thousands, which raises OverflowError.
_MAX_BACKOFF_DOUBLINGS = 5


def resolve_interval(stored: str | None, plugin: SourcePlugin) -> str:
    """Resolve a source's stored cadence to a preset key.

    Unset, or naming a preset this release no longer ships, falls back to the
    plugin's declared default — repaired here so no caller downstream meets
    either.
    """
    if stored is not None and stored in _PRESETS_BY_KEY:
        return stored
    return plugin.default_sync_interval


def effective_interval(base_key: str, consecutive_failures: int) -> timedelta | None:
    """The wait a source has earned: its cadence doubled per consecutive failure.

    Capped at :data:`MAX_BACKOFF_INTERVAL`, which never shortens a base already
    longer than it. None for "off".
    """
    base = _PRESETS_BY_KEY[base_key].duration
    if base is None:
        return None
    doublings = min(max(consecutive_failures, 0), _MAX_BACKOFF_DOUBLINGS)
    multiplier: int = 2**doublings
    return max(base, min(base * multiplier, MAX_BACKOFF_INTERVAL))


def next_due(
    last_finished_at: datetime | None,
    base_key: str,
    consecutive_failures: int,
) -> datetime | None:
    """The instant the source is next eligible to sync.

    None when there is no such instant: the source is "off", or it has never
    run and is due now (see :func:`is_due`).
    """
    interval = effective_interval(base_key, consecutive_failures)
    if interval is None or last_finished_at is None:
        return None
    return last_finished_at + interval


def is_due(
    now: datetime,
    last_finished_at: datetime | None,
    base_key: str,
    consecutive_failures: int,
) -> bool:
    """Whether the source should sync at *now*.

    A source that has never run is due immediately; one set to "off" never is.
    """
    interval = effective_interval(base_key, consecutive_failures)
    if interval is None:
        return False
    if last_finished_at is None:
        return True
    return now >= last_finished_at + interval
