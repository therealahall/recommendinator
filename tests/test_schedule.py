"""Tests for sync-interval presets, failure backoff and due computation."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from src.ingestion.registry import get_registry
from src.ingestion.schedule import (
    SYNC_INTERVAL_KEYS,
    effective_interval,
    is_due,
    next_due,
    resolve_interval,
)
from tests.fakes.source_plugins import FakeFilePlugin

NOW = datetime(2026, 8, 16, 12, 0, tzinfo=UTC)


class WeeklyPlugin(FakeFilePlugin):
    default_sync_interval = "weekly"


class TestEffectiveInterval:
    @pytest.mark.parametrize(
        ("failures", "expected"),
        [
            (0, timedelta(hours=1)),
            (1, timedelta(hours=2)),
            (10, timedelta(hours=24)),
        ],
    )
    def test_hourly_backs_off_to_the_24h_ceiling(
        self, failures: int, expected: timedelta
    ) -> None:
        assert effective_interval("hourly", failures) == expected

    @pytest.mark.parametrize("failures", [0, 100])
    def test_weekly_never_backs_off(self, failures: int) -> None:
        assert effective_interval("weekly", failures) == timedelta(days=7)

    def test_off_has_no_interval(self) -> None:
        assert effective_interval("off", 0) is None


class TestDue:
    def test_never_run_source_is_due_now_not_undated(self) -> None:
        assert next_due(NOW, None, "daily", 0) == NOW
        assert is_due(NOW, None, "daily", 0) is True

    def test_off_is_never_due(self) -> None:
        assert is_due(NOW, None, "off", 0) is False
        assert is_due(NOW, NOW - timedelta(days=365), "off", 0) is False
        assert next_due(NOW, None, "off", 0) is None
        assert next_due(NOW, NOW - timedelta(days=365), "off", 0) is None

    def test_due_only_once_the_interval_has_elapsed(self) -> None:
        last = NOW - timedelta(hours=23)
        assert is_due(NOW, last, "daily", 0) is False
        assert is_due(NOW + timedelta(hours=1), last, "daily", 0) is True

    def test_next_due_widens_with_the_failure_streak(self) -> None:
        last = NOW - timedelta(hours=3)
        assert next_due(NOW, last, "hourly", 3) == last + timedelta(hours=8)
        assert is_due(NOW, last, "hourly", 3) is False


class TestResolveInterval:
    def test_unset_falls_through_to_the_plugin_default(self) -> None:
        assert resolve_interval(None, FakeFilePlugin()) == "daily"
        assert resolve_interval(None, WeeklyPlugin()) == "weekly"

    def test_stored_value_wins_over_the_plugin_default(self) -> None:
        assert resolve_interval("6h", WeeklyPlugin()) == "6h"

    def test_unknown_stored_value_falls_back_to_the_plugin_default(self) -> None:
        assert resolve_interval("fortnightly", WeeklyPlugin()) == "weekly"

    def test_shipped_plugins_declare_a_known_preset(self) -> None:
        plugins = get_registry().get_all_plugins()
        assert plugins
        for plugin in plugins.values():
            assert plugin.default_sync_interval in SYNC_INTERVAL_KEYS
