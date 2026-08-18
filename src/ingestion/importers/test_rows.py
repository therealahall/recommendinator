"""Tests for the row helpers every importer shares."""

from src.ingestion.importers.rows import parse_boolean_field, parse_seasons_watched
from src.utils.series import MAX_SEASONS


class TestParseBooleanField:
    def test_the_spellings_a_file_may_use_for_true(self) -> None:
        assert parse_boolean_field("true") is True
        assert parse_boolean_field("TRUE") is True
        assert parse_boolean_field("yes") is True
        assert parse_boolean_field("1") is True
        assert parse_boolean_field(True) is True
        assert parse_boolean_field(1) is True

    def test_everything_else_reads_false(self) -> None:
        assert parse_boolean_field("false") is False
        assert parse_boolean_field("no") is False
        assert parse_boolean_field("0") is False
        assert parse_boolean_field("") is False
        assert parse_boolean_field(None) is False
        assert parse_boolean_field(0) is False


class TestParseSeasonsWatched:
    def test_a_comma_separated_cell_lists_the_seasons_named(self) -> None:
        assert parse_seasons_watched("1,2,5,6") == [1, 2, 5, 6]

    def test_a_bare_count_still_expands(self) -> None:
        assert parse_seasons_watched(5) == [1, 2, 3, 4, 5]

    def test_an_array_comes_back_sorted(self) -> None:
        assert parse_seasons_watched([6, 1, 5, 2]) == [1, 2, 5, 6]

    def test_a_huge_count_is_capped(self) -> None:
        """A malformed count must not expand into an unbounded list."""
        assert parse_seasons_watched(2_000_000_000) == list(range(1, MAX_SEASONS + 1))

    def test_out_of_range_entries_are_dropped_and_the_cap_kept(self) -> None:
        assert parse_seasons_watched(
            [1, 5, 0, -3, MAX_SEASONS, MAX_SEASONS + 1, 2_000_000]
        ) == [1, 5, MAX_SEASONS]
