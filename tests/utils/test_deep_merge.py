"""Tests for the recursive dictionary deep-merge helper."""

from src.utils.deep_merge import deep_merge


class TestDeepMerge:
    """Tests for deep_merge."""

    def test_nested_dicts_merge_recursively(self) -> None:
        """Nested dicts merge per-key rather than being replaced wholesale."""
        base = {"web": {"port": 1, "host": "local"}}
        override = {"web": {"port": 2}}

        assert deep_merge(base, override) == {"web": {"port": 2, "host": "local"}}

    def test_list_is_replaced_not_concatenated(self) -> None:
        """Lists are replaced wholesale, never merged or concatenated."""
        assert deep_merge({"a": [1, 2]}, {"a": [3]}) == {"a": [3]}

    def test_inputs_not_mutated(self) -> None:
        """Neither argument is mutated and nested values are deep-copied."""
        base = {"web": {"port": 1}}
        override = {"web": {"host": "x"}}

        result = deep_merge(base, override)
        result["web"]["port"] = 999

        assert base == {"web": {"port": 1}}
        assert override == {"web": {"host": "x"}}
