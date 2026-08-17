"""Tests for sync-interval presets, failure backoff and due computation."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from src.ingestion.registry import get_registry
from src.ingestion.schedule import (
    MAX_BACKOFF_INTERVAL,
    SYNC_INTERVAL_KEYS,
    effective_interval,
    is_due,
    next_due,
    resolve_interval,
)
from tests.fakes.source_plugins import FakeFilePlugin

NOW = datetime(2026, 8, 16, 12, 0, tzinfo=UTC)


class WeeklyPlugin(FakeFilePlugin):
    """A plugin that declares a cadence of its own instead of inheriting."""

    default_sync_interval = "weekly"


class TestEffectiveInterval:
    """The interval a source is actually synced at, given its failure streak."""

    @pytest.mark.parametrize(
        ("failures", "expected"),
        [
            (0, timedelta(hours=1)),
            (1, timedelta(hours=2)),
            (2, timedelta(hours=4)),
            (3, timedelta(hours=8)),
            (4, timedelta(hours=16)),
            (5, timedelta(hours=24)),
            (10, timedelta(hours=24)),
        ],
    )
    def test_hourly_backs_off_to_the_24h_ceiling(
        self, failures: int, expected: timedelta
    ) -> None:
        """Doubling stops at 24h — an unbounded one would park a source forever."""
        assert effective_interval("hourly", failures) == expected

    @pytest.mark.parametrize("failures", [0, 1, 5, 100])
    def test_weekly_never_backs_off(self, failures: int) -> None:
        """Clamping to the 24h ceiling must not *shorten* a longer base."""
        assert effective_interval("weekly", failures) == timedelta(days=7)

    def test_off_has_no_interval(self) -> None:
        assert effective_interval("off", 0) is None

    def test_every_preset_key_resolves_to_an_interval(self) -> None:
        """A preset added without a duration would KeyError in the sync loop."""
        for key in SYNC_INTERVAL_KEYS:
            interval = effective_interval(key, 0)
            assert interval is None if key == "off" else interval > timedelta(0)

    def test_every_short_preset_backs_off_all_the_way_to_the_ceiling(self) -> None:
        """The doubling count is capped at a hardcoded five, which only clears
        the ceiling while the shortest preset is at least 45 minutes. A cadence
        added below that would stop backing off early, and silently."""
        for key in SYNC_INTERVAL_KEYS:
            base = effective_interval(key, 0)
            if base is None or base >= MAX_BACKOFF_INTERVAL:
                continue
            assert effective_interval(key, 100) == MAX_BACKOFF_INTERVAL


class TestDue:
    """When a configured source is next eligible to sync."""

    def test_never_run_source_is_due_immediately(self) -> None:
        """No run history means the first sync happens on the next tick."""
        assert is_due(NOW, None, "daily", 0) is True
        assert next_due(None, "daily", 0) is None

    def test_off_is_never_due(self) -> None:
        assert is_due(NOW, None, "off", 0) is False
        assert is_due(NOW, NOW - timedelta(days=365), "off", 0) is False
        assert next_due(NOW - timedelta(days=365), "off", 0) is None

    def test_due_only_once_the_interval_has_elapsed(self) -> None:
        last = NOW - timedelta(hours=23)
        assert is_due(NOW, last, "daily", 0) is False
        assert is_due(NOW + timedelta(hours=1), last, "daily", 0) is True

    def test_next_due_widens_with_the_failure_streak(self) -> None:
        """A failing source must not be retried on its base cadence."""
        last = NOW - timedelta(hours=3)
        assert next_due(last, "hourly", 3) == last + timedelta(hours=8)
        assert is_due(NOW, last, "hourly", 3) is False


class TestResolveInterval:
    """Where a source's stored cadence meets its plugin's declared default."""

    def test_unset_falls_through_to_the_plugin_default(self) -> None:
        """NULL resolves here and nowhere else, so callers never see None."""
        assert resolve_interval(None, FakeFilePlugin()) == "daily"
        assert resolve_interval(None, WeeklyPlugin()) == "weekly"

    def test_stored_value_wins_over_the_plugin_default(self) -> None:
        assert resolve_interval("6h", WeeklyPlugin()) == "6h"

    def test_unknown_stored_value_falls_back_to_the_plugin_default(self) -> None:
        """A cadence dropped from a later release must not crash the sync loop."""
        assert resolve_interval("fortnightly", WeeklyPlugin()) == "weekly"

    def test_shipped_plugins_declare_a_known_preset(self) -> None:
        """A typo in a plugin's override would only surface at sync time."""
        plugins = get_registry().get_all_plugins()
        assert plugins
        for plugin in plugins.values():
            assert plugin.default_sync_interval in SYNC_INTERVAL_KEYS
