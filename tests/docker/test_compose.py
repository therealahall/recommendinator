from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest
import yaml

from src.web import healthcheck

# parents[2] resolves /tests/docker/test_compose.py -> repo root.
_REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE = _REPO_ROOT / "docker-compose.yml"
DOCKERFILE = _REPO_ROOT / "Dockerfile"
DOCKERIGNORE = _REPO_ROOT / ".dockerignore"

RUNTIME_STAGE = "runtime"

APP_SERVICE = "app"

STAGE_HEADER = re.compile(
    r"^FROM\s+\S+(?:\s+AS\s+(?P<name>\S+))?\s*$",
    re.IGNORECASE | re.MULTILINE,
)

# The arguments of an `apt-get install`, following backslash continuations onto
# the following lines. Anything after the shell's `&&` belongs to the next
# command, not to the install, so the caller cuts there.
_APT_INSTALL = re.compile(r"apt-get install\b(?P<arguments>(?:[^\n\\]|\\\n)*)")

# A `HEALTHCHECK`/`CMD` instruction's arguments, following backslash
# continuations onto the following lines.
_INSTRUCTION = r"^{name}\b(?P<arguments>(?:[^\n\\]|\\\n)*)"

# Built from the module's own name so a rename cannot leave the image invoking
# a path that no longer imports.
HEALTHCHECK_COMMAND = f'CMD ["python", "-m", "{healthcheck.__name__}"]'

# The mapping compose renders when nothing is set. Container port 8000 is fixed
# by the image's CMD; the host side is the part operators change.
DEFAULT_MAPPING = "127.0.0.1:18473:8000"

# The host part of that mapping: what an operator overrides to publish any
# further than this machine.
LOOPBACK_PREFIX = "127.0.0.1:"

# Read-write, and the container path the app's own `logs/` resolves to under
# the image's /app workdir.
LOG_MOUNT = "./logs:/app/logs"

# The two forms this file uses, which differ on an empty value —
# APP_BIND_PREFIX relies on that. `${NAME}` and `${NAME:?err}` would be left
# untouched here and fail the assertions loudly rather than render something
# nobody gets.
_INTERPOLATION = re.compile(r"\$\{([A-Z][A-Z0-9_]*):?-([^}]*)\}")

# Paths that must never reach a builder. Some are gitignored secrets, the rest
# are simply nobody's business on the far side of a `docker build`.
UNSHIPPABLE = [
    "private",
    "config/config.yaml",
    "data/.credential_key",
]


def _stages() -> dict[str, str]:
    source = DOCKERFILE.read_text()
    headers = list(STAGE_HEADER.finditer(source))
    stages: dict[str, str] = {}
    for index, header in enumerate(headers):
        name = header.group("name")
        if name is None:
            continue
        end = headers[index + 1].start() if index + 1 < len(headers) else len(source)
        stages[name] = source[header.end() : end]
    return stages


def _instructions(stage: str) -> str:
    return "\n".join(
        line for line in stage.splitlines() if not line.lstrip().startswith("#")
    )


def _instruction(stage: str, name: str) -> str:
    """Return *name*'s arguments in *stage*, collapsed onto one line."""
    match = re.search(
        _INSTRUCTION.format(name=name), _instructions(stage), re.MULTILINE
    )
    assert match is not None, f"no {name} instruction in this stage"
    return " ".join(match.group("arguments").split())


def _apt_packages(stage: str) -> set[str]:
    instructions = _instructions(stage)
    packages: set[str] = set()
    for match in _APT_INSTALL.finditer(instructions):
        arguments = match.group("arguments").replace("\\\n", " ").split("&&")[0]
        packages.update(word for word in arguments.split() if not word.startswith("-"))
    return packages


def _services() -> dict[str, dict]:
    return yaml.safe_load(COMPOSE.read_text())["services"]


def _port_specs(service: str) -> list[str]:
    """Return the raw ``ports`` entries of ``service``, un-interpolated."""
    return _services()[service]["ports"]


def _environment(service: str) -> list[str]:
    """Return the raw ``environment`` entries of ``service``, un-interpolated."""
    return list(_services()[service].get("environment") or [])


def _render(spec: str, **env: str) -> str:
    """Substitute ``${NAME:-default}`` from ``env`` alone, as compose does.

    With no keyword arguments it renders the file exactly as a clone with no
    `.env` and nothing exported gets it.
    """
    return _INTERPOLATION.sub(
        lambda match: env.get(match.group(1), match.group(2)), spec
    )


def _ignore_rules() -> list[str]:
    """Return a .dockerignore's patterns in file order, comments dropped."""
    return [
        line.strip()
        for line in DOCKERIGNORE.read_text().splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def _allowlist() -> set[str]:
    """Return the paths a .dockerignore puts back into its context."""
    return {rule[1:] for rule in _ignore_rules() if rule.startswith("!")}


def _reaches_the_builder(path: str) -> bool:
    """Whether *path* survives .dockerignore's allowlist.

    Over-approximate in the safe direction: the narrowing rules below the
    allowlist are not modelled, so nothing this calls unreachable can arrive.
    """
    return any(
        path == allowed or path.startswith(f"{allowed}/") for allowed in _allowlist()
    )


class TestComposeDefaultPortMapping:
    """What a clone with no `.env` and no exported variables publishes."""

    def test_renders_the_short_form_bound_to_loopback(self) -> None:
        """Regression: `docker compose up -d` published the app to the whole LAN.

        The short form defaulted to no host part, which publishes on every
        interface. Rendered rather than read: the expression says nothing about
        what compose makes of it.
        """
        (spec,) = _port_specs(APP_SERVICE)
        rendered = _render(spec)

        assert isinstance(spec, str), "must be the short form, not a long-form mapping"
        assert rendered == DEFAULT_MAPPING
        # Spelled out separately from the equality above so the failure names
        # the defect class: a mapping reachable from another machine.
        assert rendered.startswith(
            LOOPBACK_PREFIX
        ), f"{rendered} is published beyond this host by default"


class TestTheApplicationLogOutlivesTheContainer:
    """Regression: nothing mounted ``./logs``, so the log went to the
    container's writable layer and `compose pull && up -d` destroyed the only
    copy of the one surface that keeps a sync failure's real cause.
    """

    def test_the_log_directory_is_bind_mounted(self) -> None:
        assert LOG_MOUNT in _services()[APP_SERVICE]["volumes"]

    def test_a_fresh_clone_gets_the_bind_source(self) -> None:
        """Docker root-owns a missing one, and a ``/app/logs`` the container
        user cannot write raises inside ``create_app``. Presence here is half
        of it: an ignored keepfile is checked out by nobody."""
        assert (_REPO_ROOT / "logs" / ".gitkeep").is_file()
        ignored = subprocess.run(
            ["git", "check-ignore", "--quiet", "logs/.gitkeep"],
            cwd=_REPO_ROOT,
            check=False,
        )

        # 1 is "no pattern matches"; 0 is ignored and 128 is a git fault.
        assert ignored.returncode == 1


class TestTheTimezoneOverrideReachesTheContainer:
    def test_tz_reaches_the_container(self) -> None:
        assert any(
            entry.startswith("TZ=") for entry in _environment(APP_SERVICE)
        ), f"{APP_SERVICE} does not pass TZ into the container"

    def test_the_runtime_stage_installs_the_zone_database_regression(self) -> None:
        assert "tzdata" in _apt_packages(_stages()[RUNTIME_STAGE]), (
            f"the {RUNTIME_STAGE} stage does not install tzdata, so a zone "
            "passed via TZ may not resolve inside the container"
        )


class TestTheShippedImageRunsTheLivenessProbe:
    """A probe is only worth writing if the image actually invokes it.

    ``test_healthcheck.py`` holds what it answers; this holds that the image
    asks it.
    """

    def test_the_healthcheck_runs_the_probe(self) -> None:
        """Regression: the healthcheck called ``urlopen`` on ``/api/status``.

        Bearer auth made 401 the only answer and ``urlopen`` raises on it, so
        the image went permanently unhealthy.
        """
        assert _instruction(_stages()[RUNTIME_STAGE], "HEALTHCHECK").endswith(
            HEALTHCHECK_COMMAND
        )


class TestTheBundleIsBuiltAgainstTheVersionItStamps:
    def test_the_frontend_builder_is_handed_the_file_the_version_lives_in(self) -> None:
        assert "pyproject.toml" in _instructions(_stages()["frontend-builder"])


class TestTheBuildContextIsAnAllowlist:
    """Denying by default makes an unreviewed path a build failure rather than a
    disclosure: the narrow COPYs keep secrets out of the image, everything else
    still crosses to the builder."""

    def test_nothing_is_in_the_context_until_a_rule_names_it(self) -> None:
        """Regression: the file listed what to leave out, so ``private/`` and
        ``.env`` — which nobody had thought of — were in by default."""
        assert _ignore_rules()[0] == "*"

    @pytest.mark.parametrize("path", UNSHIPPABLE)
    def test_sensitive_paths_cannot_reach_the_builder(self, path: str) -> None:
        assert not _reaches_the_builder(path)

    def test_the_application_source_does_reach_the_builder(self) -> None:
        """The model has to be able to say yes, or the exclusions above pass
        against a parse that found no exceptions at all."""
        assert _reaches_the_builder("src/web/app.py")
