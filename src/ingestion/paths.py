"""``security.allowed_source_roots`` is config.yaml only, deliberately absent from
the settings registry: source config is writable over HTTP, so a caller able to
widen the allowlist could read any file.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# The Docker image mounts ./inputs read-only as the intended data boundary.
DEFAULT_ALLOWED_SOURCE_ROOTS: tuple[str, ...] = ("inputs",)

_allowed_roots: tuple[str, ...] = DEFAULT_ALLOWED_SOURCE_ROOTS


class PathNotAllowed(ValueError):
    """A configured source path is not one an allowed root contains."""


def set_allowed_source_roots(roots: Sequence[str]) -> None:
    """Replace the process-wide allowlist with *roots*, as written."""
    global _allowed_roots
    _allowed_roots = tuple(roots)


def get_allowed_source_roots() -> tuple[str, ...]:
    return _allowed_roots


def configure_allowed_source_roots(config: dict[str, Any]) -> None:
    """Called from ``load_config`` so every entry point shares one answer."""
    section = config.get("security")
    raw = section.get("allowed_source_roots") if isinstance(section, dict) else None

    if raw is None:
        set_allowed_source_roots(DEFAULT_ALLOWED_SOURCE_ROOTS)
        return

    if not isinstance(raw, list) or not all(
        isinstance(entry, str) and entry.strip() for entry in raw
    ):
        logger.warning(
            "Ignoring unusable security.allowed_source_roots %r in config.yaml; "
            "using %s instead. It must be a list of non-empty paths.",
            raw,
            list(DEFAULT_ALLOWED_SOURCE_ROOTS),
        )
        set_allowed_source_roots(DEFAULT_ALLOWED_SOURCE_ROOTS)
        return

    set_allowed_source_roots([entry.strip() for entry in raw])


def resolve_source_path(value: str) -> Path:
    """Both sides resolve before comparison, so a symlink planted inside a root
    cannot reach out of it and a string prefix cannot fake containment.
    """
    try:
        resolved = Path(value).expanduser().resolve()
    except (OSError, RuntimeError, ValueError) as error:
        # A NUL byte raises ValueError out of resolve(), and ``~nosuchuser``
        # raises RuntimeError out of expanduser(). Callers catch PathNotAllowed
        # alone, so anything else leaves validate_config raising and the API
        # answering 500 on caller-controlled input.
        raise PathNotAllowed(f"Path cannot be resolved: {value!r}.") from error
    for root in _allowed_roots:
        base = Path(root).expanduser().resolve()
        if resolved == base or base in resolved.parents:
            return resolved
    raise PathNotAllowed(
        f"Path is outside the allowed source roots: {value}. "
        "Add its directory to security.allowed_source_roots in config.yaml."
    )
