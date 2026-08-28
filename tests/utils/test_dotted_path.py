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
    def test_reads_nested_value(self) -> None:
        assert get_leaf({"web": {"port": 18473}}, ("web", "port")) == 18473

    def test_non_dict_intermediate_returns_default(self) -> None:
        assert get_leaf({"web": 5}, ("web", "port"), 8080) == 8080


class TestSetLeaf:
    def test_creates_intermediate_dicts(self) -> None:
        config: dict = {}
        set_leaf(config, ("web", "port"), 18473)
        assert config == {"web": {"port": 18473}}


class TestSetLeafAtomically:
    @pytest.mark.parametrize(
        "path",
        [("port",), ("web", "tls", "cert")],
    )
    def test_writes_the_same_result_as_set_leaf(self, path: tuple[str, ...]) -> None:
        base: dict = {"web": {"port": 1, "host": "x"}, "logging": 5}
        in_place = copy.deepcopy(base)
        swapped = copy.deepcopy(base)

        set_leaf(in_place, path, 2)
        set_leaf_atomically(swapped, path, 2)

        assert get_leaf(swapped, path) == 2
        assert swapped == in_place

    def test_leaves_the_replaced_intermediate_untouched(self) -> None:
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


class _RecordingRoot(dict):
    def __init__(self, initial: dict) -> None:
        super().__init__(initial)
        self.snapshots: list[dict] = []

    def _record(self) -> None:
        self.snapshots.append(copy.deepcopy(dict(self)))

    def __setitem__(self, key: str, value: object) -> None:
        super().__setitem__(key, value)
        self._record()

    def update(self, *args: object, **kwargs: object) -> None:
        super().update(*args, **kwargs)
        self._record()


_WEIGHT_PATH = ("recommendations", "scorer_weights", "genre_match")
_PORT_PATH = ("web", "port")
_TWO_SECTION_BATCH = [(_WEIGHT_PATH, 0.0), (_PORT_PATH, 18473)]


def _recording_root() -> _RecordingRoot:
    return _RecordingRoot(
        {"recommendations": {"scorer_weights": {"genre_match": 3.0}}, "web": {}}
    )


class TestSetLeavesAtomically:
    def test_no_state_the_config_passes_through_holds_half_the_batch(self) -> None:
        config = _recording_root()

        set_leaves_atomically(config, _TWO_SECTION_BATCH)

        assert config.snapshots, "the updates never reached the root"
        for snapshot in config.snapshots:
            weight_landed = get_leaf(snapshot, _WEIGHT_PATH) == 0.0
            port_landed = get_leaf(snapshot, _PORT_PATH) == 18473
            assert (
                weight_landed == port_landed
            ), f"half a batch was readable: {snapshot}"
        assert get_leaf(config, _WEIGHT_PATH) == 0.0
        assert get_leaf(config, _PORT_PATH) == 18473

    def test_a_reader_sees_every_update_or_none_of_them(self) -> None:
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

    def test_no_updates_leaves_the_config_alone(self) -> None:
        config: dict = {"web": {"port": 1}}
        set_leaves_atomically(config, [])
        assert config == {"web": {"port": 1}}


class TestPopLeaf:
    def test_removes_leaf_keeps_parents(self) -> None:
        config = {"web": {"port": 1, "host": "x"}}
        pop_leaf(config, ("web", "port"))
        assert config == {"web": {"host": "x"}}

    def test_missing_intermediate_is_noop(self) -> None:
        config: dict = {}
        pop_leaf(config, ("web", "port"))
        assert config == {}
