"""Tests for the nested-dict leaf helpers used by the dotted-key config layer."""

import copy

import pytest

from src.utils.dotted_path import (
    get_leaf,
    pop_leaf,
    set_leaf,
    set_leaf_atomically,
    set_leaves_atomically,
)


class TestGetLeaf:
    """Tests for get_leaf."""

    def test_reads_nested_value(self) -> None:
        """A present nested path returns its leaf value."""
        assert get_leaf({"web": {"port": 18473}}, ("web", "port")) == 18473

    def test_missing_leaf_returns_default(self) -> None:
        """A missing final segment falls back to the supplied default."""
        assert get_leaf({"web": {}}, ("web", "port"), 8080) == 8080

    def test_missing_intermediate_returns_default(self) -> None:
        """A missing intermediate segment falls back to the default."""
        assert get_leaf({}, ("web", "port"), 8080) == 8080

    def test_non_dict_intermediate_returns_default(self) -> None:
        """A non-dict intermediate segment falls back to the default."""
        assert get_leaf({"web": 5}, ("web", "port"), 8080) == 8080

    def test_default_is_none_when_unspecified(self) -> None:
        """The default defaults to None when the caller omits it."""
        assert get_leaf({"web": {}}, ("web", "port")) is None


class TestSetLeaf:
    """Tests for set_leaf."""

    def test_sets_existing_nested_value(self) -> None:
        """Writing at an existing path replaces the leaf in place."""
        config = {"web": {"port": 1}}
        set_leaf(config, ("web", "port"), 2)
        assert config == {"web": {"port": 2}}

    def test_creates_intermediate_dicts(self) -> None:
        """Missing intermediate dicts are created on the way down."""
        config: dict = {}
        set_leaf(config, ("web", "port"), 18473)
        assert config == {"web": {"port": 18473}}

    def test_replaces_non_dict_intermediate(self) -> None:
        """A non-dict intermediate segment is replaced with a fresh dict."""
        config = {"web": 5}
        set_leaf(config, ("web", "port"), 18473)
        assert config == {"web": {"port": 18473}}

    def test_single_segment_path(self) -> None:
        """A single-segment path writes a top-level key."""
        config: dict = {}
        set_leaf(config, ("port",), 1)
        assert config == {"port": 1}


class TestSetLeafAtomically:
    """Tests for set_leaf_atomically."""

    @pytest.mark.parametrize(
        "path",
        [("web", "port"), ("port",), ("web", "tls", "cert"), ("logging", "file")],
    )
    def test_writes_the_same_result_as_set_leaf(self, path: tuple[str, ...]) -> None:
        """Both helpers land the same value in the same place, path for path.

        Run side by side rather than compared against a hardcoded outcome,
        which is the only way this notices the two drifting apart.
        """
        base: dict = {"web": {"port": 1, "host": "x"}, "logging": 5}
        in_place = copy.deepcopy(base)
        swapped = copy.deepcopy(base)

        set_leaf(in_place, path, 2)
        set_leaf_atomically(swapped, path, 2)

        assert swapped == in_place

    def test_leaves_the_replaced_intermediate_untouched(self) -> None:
        """A reader holding the old nested dict keeps it exactly as it was.

        This is the whole point of the helper: the settings service writes a
        scorer weight while the recommendation engine iterates that mapping
        from a threadpool worker, and inserting a key into a dict under an
        iterator raises rather than merely racing.
        """
        held = {"genre_match": 3.0}
        config = {"recommendations": {"scorer_weights": held}}

        set_leaf_atomically(
            config, ("recommendations", "scorer_weights", "adaptation"), 1.5
        )

        assert held == {"genre_match": 3.0}
        assert config["recommendations"]["scorer_weights"] == {
            "genre_match": 3.0,
            "adaptation": 1.5,
        }

    def test_creates_intermediate_dicts(self) -> None:
        """Missing intermediate dicts are created on the way down."""
        config: dict = {}
        set_leaf_atomically(config, ("web", "port"), 18473)
        assert config == {"web": {"port": 18473}}

    def test_replaces_non_dict_intermediate(self) -> None:
        """A non-dict intermediate segment is replaced with a fresh dict."""
        config = {"web": 5}
        set_leaf_atomically(config, ("web", "port"), 18473)
        assert config == {"web": {"port": 18473}}

    def test_single_segment_path(self) -> None:
        """A single-segment path writes a top-level key."""
        config: dict = {}
        set_leaf_atomically(config, ("port",), 1)
        assert config == {"port": 1}


class TestSetLeavesAtomically:
    """Tests for set_leaves_atomically."""

    def test_a_reader_sees_every_update_or_none_of_them(self) -> None:
        """The whole batch replaces the section a reader holds, in one store.

        This is the whole point of the helper: the Settings page saves several
        keys at once, and a request resolving its configuration between two
        separate writes would rank on a mixture nobody ever saved.
        """
        held = {"genre_match": 3.0, "adaptation": 1.0}
        config: dict = {"recommendations": {"scorer_weights": held}}

        set_leaves_atomically(
            config,
            [
                (("recommendations", "scorer_weights", "genre_match"), 0.0),
                (("recommendations", "min_rating_for_preference"), 1),
            ],
        )

        assert held == {"genre_match": 3.0, "adaptation": 1.0}
        assert config["recommendations"] == {
            "scorer_weights": {"genre_match": 0.0, "adaptation": 1.0},
            "min_rating_for_preference": 1,
        }

    def test_writes_across_sections(self) -> None:
        """Updates addressing different top-level keys all land."""
        config: dict = {"web": {"port": 1}}

        set_leaves_atomically(
            config, [(("web", "port"), 2), (("logging", "level"), "DEBUG")]
        )

        assert config == {"web": {"port": 2}, "logging": {"level": "DEBUG"}}

    def test_single_segment_path(self) -> None:
        """A single-segment path writes a top-level key."""
        config: dict = {}
        set_leaves_atomically(config, [(("port",), 1)])
        assert config == {"port": 1}

    def test_no_updates_leaves_the_config_alone(self) -> None:
        """A save whose leaves are all restart-gated publishes nothing."""
        config: dict = {"web": {"port": 1}}
        set_leaves_atomically(config, [])
        assert config == {"web": {"port": 1}}


class TestPopLeaf:
    """Tests for pop_leaf."""

    def test_removes_leaf_keeps_parents(self) -> None:
        """Popping a leaf removes only it, leaving sibling keys intact."""
        config = {"web": {"port": 1, "host": "x"}}
        pop_leaf(config, ("web", "port"))
        assert config == {"web": {"host": "x"}}

    def test_missing_leaf_is_noop(self) -> None:
        """Popping an absent leaf leaves the dict unchanged."""
        config = {"web": {"host": "x"}}
        pop_leaf(config, ("web", "port"))
        assert config == {"web": {"host": "x"}}

    def test_missing_intermediate_is_noop(self) -> None:
        """Popping through an absent intermediate leaves the dict unchanged."""
        config: dict = {}
        pop_leaf(config, ("web", "port"))
        assert config == {}
