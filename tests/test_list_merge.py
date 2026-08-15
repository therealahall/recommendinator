"""Tests for the merge_string_lists utility."""

from src.utils.list_merge import merge_string_lists


class TestMergeStringLists:
    """Tests for case-insensitive string list merging."""

    def test_existing_casing_preserved(self) -> None:
        """First occurrence's casing should be preserved."""
        result = merge_string_lists(["DRAMA"], ["drama"])
        assert result == ["DRAMA"]

    def test_multiple_overlaps(self) -> None:
        """Multiple overlapping items should all be deduplicated."""
        result = merge_string_lists(
            ["Drama", "Action", "Comedy"],
            ["drama", "action", "Thriller"],
        )
        assert result == ["Drama", "Action", "Comedy", "Thriller"]
