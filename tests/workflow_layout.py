"""The workflow directory, read the way GitHub reads it.

GitHub honours `.yaml` as readily as `.yml`, and two suites sweep these files.
A second enumerator is a second place to forget one extension.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

# parents[1] resolves /tests/workflow_layout.py -> repo root.
_REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = _REPO_ROOT / ".github" / "workflows"

_EXTENSIONS = {".yml", ".yaml"}


def workflow_files(directory: Path = WORKFLOWS) -> list[Path]:
    """Every file in *directory* GitHub would run as a workflow."""
    paths = sorted(path for path in directory.iterdir() if path.suffix in _EXTENSIONS)
    assert paths, f"no workflow files under {directory}; a sweep would be empty"
    return paths


def parsed_workflow(path: Path) -> dict[str, Any]:
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    return loaded if isinstance(loaded, dict) else {}


def workflow_jobs(path: Path) -> dict[str, Any]:
    """The `jobs:` block, or none for a file declaring none.

    Anything else living in the directory is then swept over rather than
    crashing the sweep on a KeyError.
    """
    declared: dict[str, Any] = parsed_workflow(path).get("jobs") or {}
    return declared
