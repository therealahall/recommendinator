"""Dockerfile reads more than one suite needs.

The seed's path is the smoke test's and the entrypoint's, and the images it
pulls are the compose suite's and the toolchain suite's. One restructure moves
all four.
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

# `FROM <parent> [AS <stage>]` — the only form these Dockerfiles use.
STAGE_HEADER = re.compile(
    r"^FROM\s+(?P<parent>\S+)(?:\s+AS\s+(?P<name>\S+))?\s*$",
    re.IGNORECASE | re.MULTILINE,
)

# `COPY --from=<image or stage>`.
_COPY_FROM = re.compile(r"^COPY\s+(?:--\S+\s+)*--from=(?P<source>\S+)", re.MULTILINE)

# The `<name>:<version>` head of a reference. What follows it — a `-slim`
# variant, the digest — says how the image is built, not which version it is.
_TAGGED = re.compile(r"^(?P<name>[^\s:@]+):(?P<version>\d+(?:\.\d+)*)")


def pulled_images(dockerfile: Path = DOCKERFILE) -> list[str]:
    """Every registry image *dockerfile* pulls, in file order, stages excluded."""
    source = dockerfile.read_text(encoding="utf-8")
    headers = list(STAGE_HEADER.finditer(source))
    stages = {header.group("name") for header in headers}
    referenced = [header.group("parent") for header in headers]
    referenced += _COPY_FROM.findall(source)
    return [reference for reference in referenced if reference not in stages]


def pulled_versions(dockerfile: Path = DOCKERFILE) -> dict[str, list[str]]:
    """The version each pulled image is tagged with, keyed by image name."""
    versions: dict[str, list[str]] = {}
    for reference in pulled_images(dockerfile):
        tagged = _TAGGED.match(reference)
        assert tagged is not None, f"{reference} names no version"
        versions.setdefault(tagged["name"], []).append(tagged["version"])
    return versions


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
