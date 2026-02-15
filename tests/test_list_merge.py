"""Tests for the merge_string_lists utility."""

from src.utils.list_merge import merge_string_lists


class TestMergeStringLists:
    """Tests for case-insensitive string list merging."""

    def test_disjoint_lists(self) -> None:
        """Non-overlapping lists should be concatenated."""
        result = merge_string_lists(["Drama"], ["Comedy"])
        assert result == ["Drama", "Comedy"]

    def test_duplicate_removed_case_insensitive(self) -> None:
        """Duplicates (case-insensitive) should be removed."""
        result = merge_string_lists(["Drama"], ["drama", "Action"])
        assert result == ["Drama", "Action"]

    def test_existing_casing_preserved(self) -> None:
        """First occurrence's casing should be preserved."""
        result = merge_string_lists(["DRAMA"], ["drama"])
        assert result == ["DRAMA"]

    def test_empty_existing(self) -> None:
        """Empty existing list should return new list."""
        result = merge_string_lists([], ["Drama", "Action"])
        assert result == ["Drama", "Action"]

    def test_empty_new(self) -> None:
        """Empty new list should return existing list."""
        result = merge_string_lists(["Drama", "Action"], [])
        assert result == ["Drama", "Action"]

    def test_both_empty(self) -> None:
        """Both empty should return empty."""
        result = merge_string_lists([], [])
        assert result == []

    def test_multiple_overlaps(self) -> None:
        """Multiple overlapping items should all be deduplicated."""
        result = merge_string_lists(
            ["Drama", "Action", "Comedy"],
            ["drama", "action", "Thriller"],
        )
        assert result == ["Drama", "Action", "Comedy", "Thriller"]

    def test_order_preserved(self) -> None:
        """Existing items come first, then new items in order."""
        result = merge_string_lists(["B", "A"], ["C", "D"])
        assert result == ["B", "A", "C", "D"]
