"""Where the image puts files more than one suite names.

The smoke test reads the first-run seed out of the built image, the entrypoint
defaults to the same path, and one Dockerfile restructure has to move both.
"""

from __future__ import annotations

import posixpath
import re
from pathlib import Path

# parents[1] resolves /tests/image_layout.py -> repo root.
_REPO_ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = _REPO_ROOT / "Dockerfile"

# The build-context path of the first-run seed. Where the image puts it is the
# Dockerfile's decision; this is the only end of it that never moves.
SEED_SOURCE = "config/example.yaml"

# `COPY [flags] <SEED_SOURCE> <destination>`.
_SEED_COPY = re.compile(
    rf"^COPY\s+(?:--\S+\s+)*{re.escape(SEED_SOURCE)}\s+(?P<destination>\S+)\s*$",
    re.MULTILINE,
)
_WORKDIR = re.compile(r"^WORKDIR\s+(?P<path>\S+)\s*$", re.MULTILINE)


def shipped_seed_path() -> str:
    """The container path ``Dockerfile`` copies the first-run seed to.

    Resolved against the WORKDIR in force at that COPY, since the destination
    is written relative.
    """
    source = DOCKERFILE.read_text(encoding="utf-8")
    copied = _SEED_COPY.search(source)
    assert copied is not None, f"the Dockerfile copies no {SEED_SOURCE}"
    preceding = [
        match for match in _WORKDIR.finditer(source) if match.start() < copied.start()
    ]
    assert preceding, "no WORKDIR precedes the seed COPY"
    return posixpath.normpath(
        posixpath.join(preceding[-1].group("path"), copied.group("destination"))
    )
