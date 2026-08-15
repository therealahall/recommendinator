from __future__ import annotations

import re
from pathlib import Path
from typing import NamedTuple

import yaml

# parents[2] resolves /tests/docker/test_compose.py -> repo root.
_REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE = _REPO_ROOT / "docker-compose.yml"
DOCKERFILE = _REPO_ROOT / "Dockerfile"

RUNTIME_STAGE = "runtime"

APP_SERVICE = "app"

STAGE_HEADER = re.compile(
    r"^FROM\s+(?P<parent>\S+)(?:\s+AS\s+(?P<name>\S+))?\s*$",
    re.IGNORECASE | re.MULTILINE,
)

_APT_INSTALL = re.compile(r"apt-get install\b(?P<arguments>(?:[^\n\\]|\\\n)*)")


class _Stage(NamedTuple):
    parent: str
    body: str


def _stages() -> dict[str, _Stage]:
    source = DOCKERFILE.read_text()
    headers = list(STAGE_HEADER.finditer(source))
    stages: dict[str, _Stage] = {}
    for index, header in enumerate(headers):
        name = header.group("name")
        if name is None:
            continue
        end = headers[index + 1].start() if index + 1 < len(headers) else len(source)
        stages[name] = _Stage(header.group("parent"), source[header.end() : end])
    return stages


def _instructions(stage: _Stage) -> str:
    """Prose explaining an install names the same words it does, so drop it."""
    return "\n".join(
        line for line in stage.body.splitlines() if not line.lstrip().startswith("#")
    )


def _apt_packages(stage: _Stage) -> set[str]:
    instructions = _instructions(stage)
    packages: set[str] = set()
    for match in _APT_INSTALL.finditer(instructions):
        # Anything past the shell's `&&` is the next command, not a package.
        arguments = match.group("arguments").replace("\\\n", " ").split("&&")[0]
        packages.update(word for word in arguments.split() if not word.startswith("-"))
    return packages


def _environment(service: str) -> list[str]:
    services = yaml.safe_load(COMPOSE.read_text())["services"]
    return list(services[service].get("environment") or [])


class TestTheTimezoneOverrideReachesTheContainer:
    """Two halves of one guarantee, each inert alone: compose has to name ``TZ``
    to pass it in, and glibc falls back to UTC silently without the zone
    database — either way every evening completion lands a day forward."""

    def test_tz_reaches_the_container(self) -> None:
        assert any(
            entry.startswith("TZ=") for entry in _environment(APP_SERVICE)
        ), f"{APP_SERVICE} does not pass TZ into the container"

    def test_the_runtime_stage_installs_the_zone_database_regression(self) -> None:
        assert "tzdata" in _apt_packages(_stages()[RUNTIME_STAGE]), (
            f"the {RUNTIME_STAGE} stage does not install tzdata, so a zone "
            "passed via TZ may not resolve inside the container"
        )
