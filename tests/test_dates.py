"""Tests for shared ISO 8601 timestamp parsing and local-date narrowing."""

from datetime import UTC, date, datetime
from unittest.mock import patch

import pytest

from src.utils.dates import (
    later_iso_timestamp,
    local_date_from_iso_timestamp,
    local_today,
    merge_seasons_watched_dates,
    parse_iso_timestamp,
    utc_now,
)


def test_parse_iso_timestamp_parses_offset_timestamp():
    assert parse_iso_timestamp("2026-03-10T12:00:00+00:00") == datetime(
        2026, 3, 10, 12, 0, 0, tzinfo=UTC
    )


def test_parse_iso_timestamp_normalizes_trailing_z():
    assert parse_iso_timestamp("2026-03-10T12:00:00Z") == datetime(
        2026, 3, 10, 12, 0, 0, tzinfo=UTC
    )


def test_parse_iso_timestamp_returns_none_for_none():
    assert parse_iso_timestamp(None) is None


def test_parse_iso_timestamp_returns_none_for_non_string():
    assert parse_iso_timestamp(12345) is None


def test_parse_iso_timestamp_returns_none_for_malformed_string():
    assert parse_iso_timestamp("not-a-timestamp") is None


def test_parse_iso_timestamp_returns_none_for_empty_string():
    assert parse_iso_timestamp("") is None


def test_later_iso_timestamp_returns_the_later_of_two_aware_timestamps():
    earlier = "2026-01-01T00:00:00+00:00"
    later = "2026-06-01T00:00:00+00:00"
    assert later_iso_timestamp(earlier, later) == later
    assert later_iso_timestamp(later, earlier) == later


def test_later_iso_timestamp_compares_naive_and_aware_without_raising():
    # A naive timestamp is assumed to already be UTC, so it compares
    # correctly against an aware one instead of raising TypeError.
    naive_earlier = "2026-01-01T00:00:00"
    aware_later = "2026-06-01T00:00:00+00:00"
    assert later_iso_timestamp(naive_earlier, aware_later) == aware_later
    assert later_iso_timestamp(aware_later, naive_earlier) == aware_later

    naive_later = "2026-06-01T00:00:00"
    aware_earlier = "2026-01-01T00:00:00+00:00"
    assert later_iso_timestamp(naive_later, aware_earlier) == naive_later


def test_later_iso_timestamp_returns_other_when_one_side_is_none():
    stamp = "2026-01-01T00:00:00+00:00"
    assert later_iso_timestamp(None, stamp) == stamp
    assert later_iso_timestamp(stamp, None) == stamp


def test_later_iso_timestamp_returns_other_when_one_side_is_unparseable():
    stamp = "2026-01-01T00:00:00+00:00"
    assert later_iso_timestamp("not-a-timestamp", stamp) == stamp
    assert later_iso_timestamp(stamp, "not-a-timestamp") == stamp


def test_later_iso_timestamp_returns_none_when_both_sides_missing():
    assert later_iso_timestamp(None, None) is None
    assert later_iso_timestamp(None, "not-a-timestamp") is None
    assert later_iso_timestamp("not-a-timestamp", None) is None
    assert later_iso_timestamp("not-a-timestamp", "also-not-one") is None


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


def test_merge_seasons_watched_dates_treats_non_dict_side_as_empty():
    a = ["not", "a", "dict"]
    b = {"1": "2026-01-01T00:00:00+00:00"}
    assert merge_seasons_watched_dates(a, b) == {"1": "2026-01-01T00:00:00+00:00"}
    assert merge_seasons_watched_dates(b, a) == {"1": "2026-01-01T00:00:00+00:00"}


def test_merge_seasons_watched_dates_returns_none_when_both_sides_non_dict():
    assert merge_seasons_watched_dates(["nope"], None) is None
    assert merge_seasons_watched_dates(None, "also-nope") is None


def test_merge_seasons_watched_dates_returns_none_when_both_sides_empty():
    assert merge_seasons_watched_dates({}, {}) is None


class TestLocalDateFromIsoTimestamp:
    """Tests for narrowing a UTC instant to the host's calendar day."""

    @pytest.mark.parametrize(
        ("zone", "raw", "expected"),
        [
            # Los Angeles (UTC-8 in January): local 23:59 is already the 16th
            # in UTC, local 00:01 is still the 15th.
            ("America/Los_Angeles", "2026-01-16T07:59:00Z", date(2026, 1, 15)),
            ("America/Los_Angeles", "2026-01-15T08:01:00Z", date(2026, 1, 15)),
            # Tokyo (UTC+9): local 00:01 is still the 14th in UTC, local 23:59
            # is the 15th.
            ("Asia/Tokyo", "2026-01-14T15:01:00Z", date(2026, 1, 15)),
            ("Asia/Tokyo", "2026-01-15T14:59:00Z", date(2026, 1, 15)),
            # Los Angeles on daylight time (UTC-7): the offset is the one in
            # force at that instant, not a single number for the whole year.
            ("America/Los_Angeles", "2026-07-16T06:59:00Z", date(2026, 7, 15)),
            ("America/Los_Angeles", "2026-07-15T07:01:00Z", date(2026, 7, 15)),
        ],
    )
    def test_boundary_instant_keeps_the_local_calendar_day(
        self, host_timezone, zone, raw, expected
    ):
        """Instants either side of local midnight narrow to the local day.

        Each case is a local 23:59 or 00:01 expressed as the UTC instant Trakt
        would record, for one negative and one positive UTC offset. The host
        zone is set explicitly so the assertions hold wherever the suite runs.
        """
        host_timezone(zone)
        assert local_date_from_iso_timestamp(raw) == expected

    def test_naive_timestamp_is_treated_as_utc(self, host_timezone):
        """A timestamp with no offset is read as UTC, as in later_iso_timestamp."""
        host_timezone("America/Los_Angeles")
        assert local_date_from_iso_timestamp("2026-01-16T07:59:00") == date(2026, 1, 15)

    def test_numeric_offset_is_converted_not_dropped(self, host_timezone):
        """A non-UTC offset is honoured, then converted to the host's zone."""
        host_timezone("UTC")
        assert local_date_from_iso_timestamp("2026-01-16T00:30:00+02:00") == date(
            2026, 1, 15
        )

    @pytest.mark.parametrize("raw", [None, 12345, "", "not-a-timestamp"])
    def test_returns_none_for_unparseable_input(self, raw):
        """Missing or malformed values yield None rather than raising."""
        assert local_date_from_iso_timestamp(raw) is None

    @pytest.mark.parametrize(
        ("zone", "raw", "expected"),
        [
            # Los Angeles (UTC-8 in January): local midnight exactly, and the
            # last microsecond of the day before it.
            ("America/Los_Angeles", "2026-01-15T08:00:00Z", date(2026, 1, 15)),
            ("America/Los_Angeles", "2026-01-15T07:59:59.999999Z", date(2026, 1, 14)),
            # Kolkata (UTC+5:30): a half-hour offset, which whole-hour
            # arithmetic gets wrong on exactly these two instants.
            ("Asia/Kolkata", "2026-01-14T18:30:00Z", date(2026, 1, 15)),
            ("Asia/Kolkata", "2026-01-14T18:29:59.999999Z", date(2026, 1, 14)),
        ],
    )
    def test_local_midnight_itself_belongs_to_the_day_it_starts(
        self, host_timezone, zone, raw, expected
    ):
        """The day flips at local midnight, not a microsecond either side of it.

        Tighter than the 23:59/00:01 sweep: the instant that *is* local midnight
        must be the new day, and the microsecond before it the old one. Kolkata
        covers a half-hour offset, where an implementation that rounded to whole
        hours would land on the wrong day for both cases.
        """
        host_timezone(zone)
        assert local_date_from_iso_timestamp(raw) == expected

    def test_utc_host_narrows_to_the_utc_day_unchanged(self, host_timezone):
        """On a UTC host the narrowing is a no-op, so CI sees the old behaviour.

        The fix ships without a timezone setting, so a host left on UTC (the
        default for a container with no ``TZ``) must keep dating a watch by its
        UTC day rather than acquiring some other offset.
        """
        host_timezone("UTC")
        assert local_date_from_iso_timestamp("2026-03-15T04:00:00.000Z") == date(
            2026, 3, 15
        )

    @pytest.mark.parametrize(
        ("zone", "raw"),
        [
            # Converting the last representable instant forward, or the first
            # one backward, lands outside what ``date`` can hold.
            ("Asia/Tokyo", "9999-12-31T23:59:59+00:00"),
            ("America/Los_Angeles", "0001-01-01T00:00:00+00:00"),
        ],
    )
    def test_instant_outside_the_local_date_range_yields_none(
        self, host_timezone, zone, raw
    ):
        """An instant whose local day is unrepresentable returns None, not a raise.

        Timestamps reach this helper from a third-party API response and from
        stored metadata, so it is documented to answer None for anything it
        cannot narrow. The extremes of the ``datetime`` range are the one case
        where the conversion itself, rather than the parse, has no answer.
        """
        host_timezone(zone)
        assert local_date_from_iso_timestamp(raw) is None


class TestLocalToday:
    """Tests for dating "now" by the calendar day the user is living.

    Bug reported: marking something complete in the evening dated it tomorrow.
    Every in-app completion stamped ``datetime.now(UTC).date()``, so a user in
    America/Los_Angeles finishing a book at 21:00 got the next day's date —
    while a date arriving from an import was narrowed to the host's zone by
    ``local_date_from_iso_timestamp``. The two dates fed the same variety
    ladder ordering off two different calendars, and the ``TZ`` a Docker
    operator sets was honoured for one and ignored for the other.
    Fix: ``local_today`` narrows the current instant the same way, and every
    stamping site reads it.
    """

    @pytest.mark.parametrize(
        ("zone", "instant", "expected"),
        [
            # 21:00 on the 14th in Los Angeles (UTC-7 in March) is already the
            # 15th in UTC — the reported case.
            (
                "America/Los_Angeles",
                datetime(2026, 3, 15, 4, 0, tzinfo=UTC),
                date(2026, 3, 14),
            ),
            # 08:00 on the 15th in Tokyo (UTC+9) is still the 14th in UTC.
            ("Asia/Tokyo", datetime(2026, 3, 14, 23, 0, tzinfo=UTC), date(2026, 3, 15)),
        ],
    )
    def test_today_is_the_host_calendar_day_regression(
        self, host_timezone, zone, instant, expected
    ):
        """Today is the host's calendar day, not the UTC one.

        The clock is frozen at an instant that falls on a different calendar
        day in the host's zone than in UTC, which is the only way to tell the
        two implementations apart — and, unlike a live clock, it says the same
        thing when the suite runs across UTC midnight.
        """
        host_timezone(zone)
        with patch("src.utils.dates.utc_now", return_value=instant):
            assert local_today() == expected

    def test_utc_host_gets_the_utc_day(self, host_timezone):
        """On a host left on UTC the narrowing changes nothing."""
        host_timezone("UTC")
        with patch(
            "src.utils.dates.utc_now",
            return_value=datetime(2026, 3, 15, 4, 0, tzinfo=UTC),
        ):
            assert local_today() == date(2026, 3, 15)

    def test_utc_now_is_aware_and_current(self):
        """The clock every stamp reads returns an aware UTC instant."""
        before = datetime.now(UTC)
        now = utc_now()
        assert now.tzinfo is not None
        assert before <= now <= datetime.now(UTC)
