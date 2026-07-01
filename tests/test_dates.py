"""Tests for shared ISO 8601 timestamp parsing."""

from datetime import UTC, datetime

from src.utils.dates import parse_iso_timestamp


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
