from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

# parents[1] resolves /tests/workflow_layout.py -> repo root.
_REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = _REPO_ROOT / ".github" / "workflows"


def parsed_workflow(path: Path) -> dict[str, Any]:
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    return loaded if isinstance(loaded, dict) else {}


def workflow_jobs(path: Path) -> dict[str, Any]:
    """None for a file declaring none, so anything else living in the directory
    is swept over rather than crashing the sweep on a KeyError."""
    declared: dict[str, Any] = parsed_workflow(path).get("jobs") or {}
    return declared
