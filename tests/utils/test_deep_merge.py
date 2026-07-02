"""Tests for the recursive dictionary deep-merge helper."""

from src.utils.deep_merge import deep_merge


class TestDeepMerge:
    """Tests for deep_merge."""

    def test_override_scalar_wins(self) -> None:
        """A scalar in override replaces the base value."""
        assert deep_merge({"a": 1}, {"a": 2}) == {"a": 2}

    def test_missing_key_from_base_is_kept(self) -> None:
        """Keys only present in base survive the merge."""
        assert deep_merge({"a": 1, "b": 2}, {"a": 9}) == {"a": 9, "b": 2}

    def test_new_key_from_override_is_added(self) -> None:
        """Keys only present in override are added."""
        assert deep_merge({"a": 1}, {"b": 2}) == {"a": 1, "b": 2}

    def test_nested_dicts_merge_recursively(self) -> None:
        """Nested dicts merge per-key rather than being replaced wholesale."""
        base = {"web": {"port": 1, "host": "local"}}
        override = {"web": {"port": 2}}

        assert deep_merge(base, override) == {"web": {"port": 2, "host": "local"}}

    def test_list_is_replaced_not_concatenated(self) -> None:
        """Lists are replaced wholesale, never merged or concatenated."""
        assert deep_merge({"a": [1, 2]}, {"a": [3]}) == {"a": [3]}

    def test_none_override_replaces_dict(self) -> None:
        """A ``None`` in override replaces a base dict (non-dict wins)."""
        assert deep_merge({"a": {"b": 1}}, {"a": None}) == {"a": None}

    def test_dict_override_replaces_scalar_base(self) -> None:
        """A dict in override replaces a scalar base value at that key."""
        assert deep_merge({"a": 1}, {"a": {"b": 2}}) == {"a": {"b": 2}}

    def test_inputs_not_mutated(self) -> None:
        """Neither argument is mutated and nested values are deep-copied."""
        base = {"web": {"port": 1}}
        override = {"web": {"host": "x"}}

        result = deep_merge(base, override)
        result["web"]["port"] = 999

        assert base == {"web": {"port": 1}}
        assert override == {"web": {"host": "x"}}

    def test_empty_override_returns_copy_of_base(self) -> None:
        """An empty override yields a base copy that is independent."""
        base = {"a": {"b": 1}}

        result = deep_merge(base, {})
        result["a"]["b"] = 2

        assert base == {"a": {"b": 1}}
