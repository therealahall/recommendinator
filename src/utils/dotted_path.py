from __future__ import annotations

from collections.abc import Sequence
from typing import Any


def get_leaf(root: dict[str, Any], path: tuple[str, ...], default: Any = None) -> Any:
    node: Any = root
    for key in path:
        if not isinstance(node, dict) or key not in node:
            return default
        node = node[key]
    return node


def set_leaf(root: dict[str, Any], path: tuple[str, ...], value: Any) -> None:
    node = root
    for key in path[:-1]:
        existing = node.get(key)
        if not isinstance(existing, dict):
            existing = {}
            node[key] = existing
        node = existing
    node[path[-1]] = value


def set_leaf_atomically(
    root: dict[str, Any], path: tuple[str, ...], value: Any
) -> None:
    """Same result as :func:`set_leaf`, except that every intermediate dict is
    swapped for an updated copy instead of being mutated.
    """
    head, *rest = path
    if not rest:
        root[head] = value
        return
    existing = root.get(head)
    branch = dict(existing) if isinstance(existing, dict) else {}
    set_leaf_atomically(branch, tuple(rest), value)
    root[head] = branch


def set_leaves_atomically(
    root: dict[str, Any], updates: Sequence[tuple[tuple[str, ...], Any]]
) -> None:
    """Readers are what this protects. It serialises nothing, so two writers
    running at once each copy the same branch and the last store wins,
    silently dropping the other write.
    """
    published: dict[str, Any] = {}
    for path, value in updates:
        head, *rest = path
        if not rest:
            published[head] = value
            continue
        existing = published[head] if head in published else root.get(head)
        branch = dict(existing) if isinstance(existing, dict) else {}
        set_leaf_atomically(branch, tuple(rest), value)
        published[head] = branch
    root.update(published)


def pop_leaf(root: dict[str, Any], path: tuple[str, ...]) -> None:
    node: Any = root
    for key in path[:-1]:
        if not isinstance(node, dict):
            return
        node = node.get(key)
    if isinstance(node, dict):
        node.pop(path[-1], None)
