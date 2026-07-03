"""Read, write, and delete nested dict leaves addressed by a key path.

A *path* is a tuple of keys describing a nested location: ``("web", "port")``
addresses ``root["web"]["port"]``. These helpers back the dotted-key config
layering used across settings assembly, secret migration, and live-apply so
every site traverses nested config the same way instead of re-implementing the
walk.
"""

from __future__ import annotations

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
