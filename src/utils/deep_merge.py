"""Recursive dictionary merge used to layer config sources by precedence."""

from __future__ import annotations

import copy
from typing import Any


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Return a new dict with *override* deep-merged on top of *base*.

    Nested dicts are merged recursively; any non-dict value in *override*
    (scalar, list, ``None``) replaces the corresponding value in *base*. Lists
    are replaced wholesale, never concatenated. Neither argument is mutated —
    values carried into the result are deep-copied.

    Args:
        base: The lower-precedence mapping.
        override: The higher-precedence mapping; its values win on conflict.

    Returns:
        A new merged dict.
    """
    result = copy.deepcopy(base)
    for key, value in override.items():
        existing = result.get(key)
        if isinstance(existing, dict) and isinstance(value, dict):
            result[key] = deep_merge(existing, value)
        else:
            result[key] = copy.deepcopy(value)
    return result
