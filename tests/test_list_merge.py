from src.utils.list_merge import merge_string_lists


class TestMergeStringLists:
    def test_existing_casing_preserved(self) -> None:
        result = merge_string_lists(["DRAMA"], ["drama"])
        assert result == ["DRAMA"]

    def test_multiple_overlaps(self) -> None:
        result = merge_string_lists(
            ["Drama", "Action", "Comedy"],
            ["drama", "action", "Thriller"],
        )
        assert result == ["Drama", "Action", "Comedy", "Thriller"]
