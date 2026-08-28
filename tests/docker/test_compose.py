from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from src.utils.logging import _LOG_BASE_DIR
from src.web import healthcheck

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

_APT_INSTALL = re.compile(r"apt-get install\b(?P<arguments>(?:[^\n\\]|\\\n)*)")

_INSTRUCTION = r"^{name}\b(?P<arguments>(?:[^\n\\]|\\\n)*)"

HEALTHCHECK_COMMAND = f'CMD ["python", "-m", "{healthcheck.__name__}"]'

DEFAULT_MAPPING = "127.0.0.1:18473:8000"

LOOPBACK_PREFIX = "127.0.0.1:"

_INTERPOLATION = re.compile(r"\$\{([A-Z][A-Z0-9_]*):?-([^}]*)\}")

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
    return _services()[service]["ports"]


def _environment(service: str) -> list[str]:
    return list(_services()[service].get("environment") or [])


def _render(spec: str, **env: str) -> str:
    return _INTERPOLATION.sub(
        lambda match: env.get(match.group(1), match.group(2)), spec
    )


def _ignore_rules() -> list[str]:
    return [
        line.strip()
        for line in DOCKERIGNORE.read_text().splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def _allowlist() -> set[str]:
    return {rule[1:] for rule in _ignore_rules() if rule.startswith("!")}


def _reaches_the_builder(path: str) -> bool:
    return any(
        path == allowed or path.startswith(f"{allowed}/") for allowed in _allowlist()
    )


class TestComposeDefaultPortMapping:
    def test_renders_the_short_form_bound_to_loopback(self) -> None:
        (spec,) = _port_specs(APP_SERVICE)
        rendered = _render(spec)

        assert isinstance(spec, str), "must be the short form, not a long-form mapping"
        assert rendered == DEFAULT_MAPPING
        assert rendered.startswith(
            LOOPBACK_PREFIX
        ), f"{rendered} is published beyond this host by default"


class TestTheApplicationLogOutlivesTheContainer:
    def test_the_log_directory_rides_a_mount_the_deployment_already_has(self) -> None:
        root = _LOG_BASE_DIR.parts[0]

        assert f"./{root}:/app/{root}" in _services()[APP_SERVICE]["volumes"]


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
    def test_the_healthcheck_runs_the_probe(self) -> None:
        assert _instruction(_stages()[RUNTIME_STAGE], "HEALTHCHECK").endswith(
            HEALTHCHECK_COMMAND
        )


class TestTheBuildContextIsAnAllowlist:
    def test_nothing_is_in_the_context_until_a_rule_names_it(self) -> None:
        assert _ignore_rules()[0] == "*"

    @pytest.mark.parametrize("path", UNSHIPPABLE)
    def test_sensitive_paths_cannot_reach_the_builder(self, path: str) -> None:
        assert not _reaches_the_builder(path)

    def test_the_application_source_does_reach_the_builder(self) -> None:
        assert _reaches_the_builder("src/web/app.py")
