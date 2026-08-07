"""Read, write, and delete nested dict leaves addressed by a key path.

A *path* is a tuple of keys describing a nested location: ``("web", "port")``
addresses ``root["web"]["port"]``. These helpers back the dotted-key config
layering used across settings assembly, secret migration, and live-apply so
every site traverses nested config the same way instead of re-implementing the
walk.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any


def get_leaf(root: dict[str, Any], path: tuple[str, ...], default: Any = None) -> Any:
    """Return the value at *path* in *root*, or *default* if any segment is absent.

    Args:
        root: The mapping to read from.
        path: Keys describing the nested location, from the root down.
        default: Value returned when the path (or an intermediate dict) is missing.

    Returns:
        The leaf value, or *default* when the path does not fully resolve.
    """
    node: Any = root
    for key in path:
        if not isinstance(node, dict) or key not in node:
            return default
        node = node[key]
    return node


def set_leaf(root: dict[str, Any], path: tuple[str, ...], value: Any) -> None:
    """Write *value* at *path* in *root*, creating intermediate dicts as needed.

    Any intermediate segment that is missing or not a dict is replaced with a
    fresh dict. Mutates *root* in place.

    Args:
        root: The mapping to mutate.
        path: Keys describing the nested location (must be non-empty).
        value: The leaf value to set.
    """
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
    """Write *value* at *path* in *root*, replacing the dicts along the way.

    Same result as :func:`set_leaf`, except that every intermediate dict is
    swapped for an updated copy instead of being mutated. A reader that already
    holds one of them keeps the mapping it fetched, whole, so it can iterate it
    while this writes — which :func:`set_leaf` cannot promise, since adding a
    key to a dict under an iterator raises.

    Args:
        root: The mapping to write into. Only its own key at ``path[0]`` is
            reassigned, which is a single store no reader can land inside.
        path: Keys describing the nested location (must be non-empty).
        value: The leaf value to set.
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
    """Write every leaf in *updates*, publishing each section in one store.

    :func:`set_leaf_atomically` per update would publish each one separately,
    letting a reader between two of them see a mixture of the writes. Building
    every branch first and assigning the top-level keys at the end means a
    reader holding, or about to read, one section sees either all of the
    updates landing in it or none.

    Args:
        root: The mapping to write into. Only the top-level keys the updates
            address are reassigned, one store each.
        updates: ``(path, value)`` pairs, each path non-empty.
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
    """Delete the leaf at *path* in *root*, leaving parent dicts intact.

    A no-op when the path (or an intermediate dict) is absent. Mutates *root*
    in place.

    Args:
        root: The mapping to mutate.
        path: Keys describing the nested location (must be non-empty).
    """
    node: Any = root
    for key in path[:-1]:
        if not isinstance(node, dict):
            return
        node = node.get(key)
    if isinstance(node, dict):
        node.pop(path[-1], None)
