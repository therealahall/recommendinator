"""Tests for shared ISO 8601 timestamp parsing."""

from datetime import UTC, datetime

from src.utils.dates import (
    later_iso_timestamp,
    merge_seasons_watched_dates,
    parse_iso_timestamp,
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
