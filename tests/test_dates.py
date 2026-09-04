from datetime import UTC, date, datetime
from unittest.mock import patch

import pytest

from src.utils.dates import (
    later_iso_timestamp,
    local_date_from_iso_timestamp,
    local_today,
    merge_seasons_watched_dates,
    parse_iso_timestamp,
)


def test_parse_iso_timestamp_normalizes_trailing_z():
    assert parse_iso_timestamp("2026-03-10T12:00:00Z") == datetime(
        2026, 3, 10, 12, 0, 0, tzinfo=UTC
    )


def test_parse_iso_timestamp_returns_none_for_malformed_string():
    assert parse_iso_timestamp("not-a-timestamp") is None


def test_later_iso_timestamp_returns_the_later_of_two_aware_timestamps():
    earlier = "2026-01-01T00:00:00+00:00"
    later = "2026-06-01T00:00:00+00:00"
    assert later_iso_timestamp(earlier, later) == later
    assert later_iso_timestamp(later, earlier) == later


def test_later_iso_timestamp_compares_naive_and_aware_without_raising():
    naive_earlier = "2026-01-01T00:00:00"
    aware_later = "2026-06-01T00:00:00+00:00"
    assert later_iso_timestamp(naive_earlier, aware_later) == aware_later
    assert later_iso_timestamp(aware_later, naive_earlier) == aware_later

    naive_later = "2026-06-01T00:00:00"
    aware_earlier = "2026-01-01T00:00:00+00:00"
    assert later_iso_timestamp(naive_later, aware_earlier) == naive_later


def test_merge_seasons_watched_dates_keeps_later_date_per_season():
    a = {"1": "2026-01-01T00:00:00+00:00", "2": "2026-06-01T00:00:00+00:00"}
    b = {"1": "2026-03-01T00:00:00+00:00", "2": "2026-02-01T00:00:00+00:00"}
    assert merge_seasons_watched_dates(a, b) == {
        "1": "2026-03-01T00:00:00+00:00",
        "2": "2026-06-01T00:00:00+00:00",
    }


def test_merge_seasons_watched_dates_gap_fills_season_present_on_one_side():
    a = {"1": "2026-01-01T00:00:00+00:00"}
    b = {"2": "2026-02-01T00:00:00+00:00"}
    assert merge_seasons_watched_dates(a, b) == {
        "1": "2026-01-01T00:00:00+00:00",
        "2": "2026-02-01T00:00:00+00:00",
    }


class TestLocalDateFromIsoTimestamp:
    @pytest.mark.parametrize(
        ("zone", "raw", "expected"),
        [
            ("America/Los_Angeles", "2026-01-16T07:59:00Z", date(2026, 1, 15)),
            ("Asia/Tokyo", "2026-01-14T15:01:00Z", date(2026, 1, 15)),
            ("America/Los_Angeles", "2026-07-16T06:59:00Z", date(2026, 7, 15)),
        ],
    )
    def test_boundary_instant_keeps_the_local_calendar_day(
        self, host_timezone, zone, raw, expected
    ):
        """The host zone is set explicitly so the assertions hold wherever the suite
        runs."""
        host_timezone(zone)
        assert local_date_from_iso_timestamp(raw) == expected

    @pytest.mark.parametrize("raw", [None, "not-a-timestamp"])
    def test_returns_none_for_unparseable_input(self, raw):
        assert local_date_from_iso_timestamp(raw) is None

    @pytest.mark.parametrize(
        ("zone", "raw", "expected"),
        [
            ("Asia/Kolkata", "2026-01-14T18:30:00Z", date(2026, 1, 15)),
            ("Asia/Kolkata", "2026-01-14T18:29:59.999999Z", date(2026, 1, 14)),
        ],
    )
    def test_local_midnight_itself_belongs_to_the_day_it_starts(
        self, host_timezone, zone, raw, expected
    ):
        """Tighter than the 23:59/00:01 sweep: the instant that *is* local midnight
        must be the new day, and the microsecond before it the old one."""
        host_timezone(zone)
        assert local_date_from_iso_timestamp(raw) == expected


class TestLocalToday:
    """Bug reported: marking something complete in the evening dated it tomorrow."""

    @pytest.mark.parametrize(
        ("zone", "instant", "expected"),
        [
            (
                "America/Los_Angeles",
                datetime(2026, 3, 15, 4, 0, tzinfo=UTC),
                date(2026, 3, 14),
            ),
            ("Asia/Tokyo", datetime(2026, 3, 14, 23, 0, tzinfo=UTC), date(2026, 3, 15)),
        ],
    )
    def test_today_is_the_host_calendar_day_regression(
        self, host_timezone, zone, instant, expected
    ):
        host_timezone(zone)
        with patch("src.utils.dates.utc_now", return_value=instant):
            assert local_today() == expected
