"""Pure logic: callers pass aware UTC in."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from src.ingestion.plugin_base import SourcePlugin


@dataclass(frozen=True)
class SyncIntervalPreset:
    key: str
    label: str
    duration: timedelta | None


#: Every interface's option list, in this order, never a copy.
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

#: A source broken since last week is still retried today.
MAX_BACKOFF_INTERVAL = timedelta(hours=24)

# Bounds the exponent below: 2**thousands raises OverflowError.
_MAX_BACKOFF_DOUBLINGS = 5


def resolve_interval(stored: str | None, plugin: SourcePlugin) -> str:
    candidates = (stored or "", plugin.default_sync_interval)
    return next((key for key in candidates if key in _PRESETS_BY_KEY), "off")


def effective_interval(base_key: str, consecutive_failures: int) -> timedelta | None:
    """The cap never shortens a base longer than itself."""
    base = _PRESETS_BY_KEY[base_key].duration
    if base is None:
        return None
    doublings = min(max(consecutive_failures, 0), _MAX_BACKOFF_DOUBLINGS)
    multiplier: int = 2**doublings
    return max(base, min(base * multiplier, MAX_BACKOFF_INTERVAL))


def next_due(
    now: datetime,
    last_finished_at: datetime | None,
    base_key: str,
    consecutive_failures: int,
) -> datetime | None:
    interval = effective_interval(base_key, consecutive_failures)
    if interval is None:
        return None
    if last_finished_at is None:
        return now
    return last_finished_at + interval


def is_due(
    now: datetime,
    last_finished_at: datetime | None,
    base_key: str,
    consecutive_failures: int,
) -> bool:
    due = next_due(now, last_finished_at, base_key, consecutive_failures)
    return due is not None and now >= due
