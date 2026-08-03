"""Static checks on the deployment's compose file and the image it runs.

The port mapping is parsed out of the YAML and then *rendered* the way compose
interpolates it, so the assertions are about the string compose actually gets
rather than the expression written in the file. The timezone assertions span
both files, because passing ``TZ`` into a container and being able to resolve
it there are separate requirements and either one alone is inert. No Docker CLI
or daemon needed.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import NamedTuple

import pytest
import yaml

# parents[2] resolves /tests/docker/test_compose.py -> repo root.
_REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE = _REPO_ROOT / "docker-compose.yml"
DOCKERFILE = _REPO_ROOT / "Dockerfile"

# The stage both shipped targets build on, and the targets themselves. Anything
# installed in the shared stage reaches both; anything installed in one target
# reaches only that one.
RUNTIME_BASE_STAGE = "runtime-base"
APP_TARGETS = ["default", "ai"]

# `FROM <image> AS <stage>` — the only form this Dockerfile uses.
_STAGE_HEADER = re.compile(
    r"^FROM\s+(?P<parent>\S+)(?:\s+AS\s+(?P<name>\S+))?\s*$",
    re.IGNORECASE | re.MULTILINE,
)

# The arguments of an `apt-get install`, following backslash continuations onto
# the following lines. Anything after the shell's `&&` belongs to the next
# command, not to the install, so the caller cuts there.
_APT_INSTALL = re.compile(r"apt-get install\b(?P<arguments>(?:[^\n\\]|\\\n)*)")

# Services that publish the web UI. Both inherit the mapping from the
# x-app-common anchor, so both must be checked — a mapping moved onto one
# service only would leave the other unreachable.
APP_SERVICES = ["app", "app-ai"]

# The mapping compose renders when nothing is set. Container port 8000 is fixed
# by the image's CMD; the host side is the part operators change.
DEFAULT_MAPPING = "18473:8000"

# `${NAME:-default}`, the only interpolation form this file uses. Compose also
# understands `${NAME}` and `${NAME:?err}`; if either ever appears here the
# substitution below leaves it untouched and the assertions fail loudly, rather
# than quietly rendering something that never occurs in practice.
_INTERPOLATION = re.compile(r"\$\{([A-Z][A-Z0-9_]*):-([^}]*)\}")

# Every variable named in the "Environment overrides" comment block at the top,
# written as `#   NAME — description`.
_DOCUMENTED = re.compile(r"^#\s{3}([A-Z][A-Z0-9_]*)\s+—", re.MULTILINE)


def _compose_text() -> str:
    return COMPOSE.read_text()


class _Stage(NamedTuple):
    """What a stage builds on, and the instructions it runs."""

    parent: str
    body: str


def _stages() -> dict[str, _Stage]:
    """Map each named Dockerfile stage to what it inherits and what it runs.

    A stage's body runs from its ``FROM`` line to the next one, so every
    instruction is attributed to the stage that actually executes it.
    """
    source = DOCKERFILE.read_text()
    headers = list(_STAGE_HEADER.finditer(source))
    stages: dict[str, _Stage] = {}
    for index, header in enumerate(headers):
        name = header.group("name")
        if name is None:
            continue
        end = headers[index + 1].start() if index + 1 < len(headers) else len(source)
        stages[name] = _Stage(header.group("parent"), source[header.end() : end])
    return stages


def _apt_packages(stage: _Stage) -> set[str]:
    """Return the packages ``apt-get install`` asks for in *stage*.

    Comment lines are dropped before matching and only the install's own
    arguments are read, so a package named in the prose explaining why it is
    installed cannot stand in for installing it.
    """
    instructions = "\n".join(
        line for line in stage.body.splitlines() if not line.lstrip().startswith("#")
    )
    packages: set[str] = set()
    for match in _APT_INSTALL.finditer(instructions):
        arguments = match.group("arguments").replace("\\\n", " ").split("&&")[0]
        packages.update(word for word in arguments.split() if not word.startswith("-"))
    return packages


def _port_specs(service: str) -> list[str]:
    """Return the raw ``ports`` entries of ``service``, un-interpolated."""
    compose = yaml.safe_load(_compose_text())
    return compose["services"][service]["ports"]


def _environment(service: str) -> list[str]:
    """Return the raw ``environment`` entries of ``service``, un-interpolated."""
    compose = yaml.safe_load(_compose_text())
    return list(compose["services"][service].get("environment") or [])


def _render(spec: str, **env: str) -> str:
    """Substitute ``${NAME:-default}`` the way compose does, from ``env`` only.

    Anything absent from ``env`` falls back to its inline default, so calling
    this with no keyword arguments renders the file exactly as a contributor
    with no `.env` and no exported variables would get it.
    """
    return _INTERPOLATION.sub(
        lambda match: env.get(match.group(1), match.group(2)), spec
    )


class TestComposeDefaultPortMapping:
    """What a clone with no `.env` and no exported variables publishes."""

    @pytest.mark.parametrize("service", APP_SERVICES)
    def test_renders_the_bare_short_form(self, service: str) -> None:
        """Regression: parameterising the bind address broke `docker compose up`
        for everyone who had not set it.

        The mapping was briefly the long form with ``host_ip: "${APP_HOST_IP:-}"``,
        on the assumption that an empty host_ip means the same as an absent one.
        It does not — compose validates it and rejects the whole file with
        ``services.app-ai.ports.[]: invalid ip address:``, so a contributor
        cloning the repo could not start the stack at all.

        That bug survived a test asserting the YAML said ``host_ip: ""``, because
        reading the expression proves nothing about what compose makes of it. So
        assert the rendered result instead: with nothing set it must be the bare
        short form with no host component. That is also what preserves the IPv6
        binding a bare mapping gets and a spelled-out 0.0.0.0 would lose.
        """
        (spec,) = _port_specs(service)
        rendered = _render(spec)

        assert isinstance(spec, str), "must be the short form, not a long-form mapping"
        assert rendered == DEFAULT_MAPPING
        # Spelled out separately from the equality above so the failure names the
        # defect class: a host component that should not be there.
        assert rendered.count(":") == 1, f"{rendered} carries a host part by default"

    @pytest.mark.parametrize("service", APP_SERVICES)
    def test_publishes_18473_to_the_fixed_container_port(self, service: str) -> None:
        (spec,) = _port_specs(service)

        published, target = _render(spec).split(":")

        assert published == "18473"
        # The image's CMD hardcodes --port 8000, so only the host side is
        # configurable.
        assert target == "8000"


class TestComposePortOverrides:
    """The two variables that reshape the mapping, together and apart."""

    @pytest.mark.parametrize("service", APP_SERVICES)
    def test_app_port_moves_the_host_port_only(self, service: str) -> None:
        (spec,) = _port_specs(service)

        assert _render(spec, APP_PORT="8080") == "8080:8000"

    @pytest.mark.parametrize("service", APP_SERVICES)
    def test_bind_prefix_restricts_the_interface(self, service: str) -> None:
        """The prefix carries its own trailing colon, which is what lets the
        default be empty. A value without one renders a nonsense host:port that
        compose rejects — loud, which is the acceptable failure mode here."""
        (spec,) = _port_specs(service)

        assert (
            _render(spec, APP_BIND_PREFIX="127.0.0.1:", APP_PORT="9000")
            == "127.0.0.1:9000:8000"
        )
        # Independent of APP_PORT: restricting the interface must not disturb the
        # port default.
        assert _render(spec, APP_BIND_PREFIX="127.0.0.1:") == "127.0.0.1:18473:8000"


class TestComposeOverridesAreDocumented:
    """Every override the file honours is listed in its own header block.

    The header is the only place a user downloading the standalone compose file
    learns which variables exist, so an undocumented ${VAR:-default} is
    invisible. Nothing links the two, hence this test.
    """

    def test_every_interpolated_variable_is_documented(self) -> None:
        source = _compose_text()

        interpolated = {name for name, _default in _INTERPOLATION.findall(source)}
        documented = set(_DOCUMENTED.findall(source))

        # Named so a regex that silently stops matching fails loudly here
        # instead of making the subset check vacuously true.
        assert {"APP_PORT", "APP_BIND_PREFIX", "IMAGE_TAG"} <= interpolated
        assert documented, "no '#   NAME — description' overrides block found"
        assert interpolated <= documented


class TestComposeTimezonePassthrough:
    """The app services hand the operator's ``TZ`` to the container.

    Completion dates are narrowed to the calendar day of the zone the *process*
    runs in (``src.utils.dates.local_date_from_iso_timestamp``), and a container
    has no zone of its own — it runs on UTC unless ``TZ`` is set inside it.
    Setting ``TZ`` in a shell or `.env` next to the compose file reaches the
    container only for a variable the compose file names, so without a
    passthrough here the documented remedy is inert and a viewer west of UTC
    keeps seeing every evening watch dated a day forward.
    """

    @pytest.mark.parametrize("service", APP_SERVICES)
    def test_tz_reaches_the_container(self, service: str) -> None:
        """Regression test: the documented ``TZ`` override did nothing.

        Bug reported: DOCKER.md documents ``TZ`` as the way a Docker user fixes
        completion dates that land a day forward, but setting it in `.env` or
        the shell changed nothing — dates stayed on UTC.
        Root cause: ``docker-compose.yml`` never names ``TZ``. Compose passes a
        host variable into a container only via ``environment``/``env_file``, so
        an unnamed one is dropped at the container boundary.
        Fix: both app services pass ``TZ`` through, empty by default so a host
        that does not set it keeps today's UTC behaviour.
        """
        assert any(
            entry.startswith("TZ=") for entry in _environment(service)
        ), f"{service} does not pass TZ into the container"

    @pytest.mark.parametrize("service", APP_SERVICES)
    def test_tz_defaults_to_empty_rather_than_a_guessed_zone(
        self, service: str
    ) -> None:
        """An operator who sets nothing is left exactly where they were.

        Rendering with no environment must produce an empty value, not a
        hardcoded zone: guessing one would silently re-date every completion for
        someone who never asked for it.
        """
        spec = next(entry for entry in _environment(service) if entry.startswith("TZ="))
        assert _render(spec) == "TZ="
        assert _render(spec, TZ="America/Los_Angeles") == "TZ=America/Los_Angeles"


class TestRuntimeImageCarriesTheZoneDatabase:
    """The other half of the passthrough: the container can resolve the zone.

    ``TZ`` names a zone; ``astimezone()`` resolves it through the C library's
    ``/usr/share/zoneinfo``, not through Python's ``tzdata`` wheel. Passing the
    variable in and being able to look it up are separate requirements, and a
    zone that will not resolve fails silently — glibc falls back to UTC, which
    is exactly the behaviour the ``TZ`` override exists to change.
    """

    def test_the_runtime_stage_installs_the_zone_database_regression(self) -> None:
        """Regression test: the image did not ask for the zone database at all.

        Bug reported: the ``TZ`` passthrough (and the operator documentation
        promising it) could resolve to nothing in Docker, the only deployment
        mode that ships.
        Root cause: the runtime stage is ``python:3.11-slim`` and the Dockerfile
        installed nothing into it, so whether ``/usr/share/zoneinfo`` existed
        was a property of an upstream base image this repository neither pins
        nor asserts — it could stop being true on any base-image bump, silently.
        Fix: the shared runtime stage installs ``tzdata`` outright, so the
        guarantee belongs to this repository and this test can hold it.

        Matched against the install instruction rather than the stage text: the
        instruction is explained by a comment that names ``tzdata`` too, so a
        substring search over the stage would keep passing on a Dockerfile that
        had lost the install and kept the comment.
        """
        runtime_base = _stages()[RUNTIME_BASE_STAGE]

        assert "tzdata" in _apt_packages(runtime_base), (
            f"the {RUNTIME_BASE_STAGE} stage does not install tzdata, so a zone "
            "passed via TZ may not resolve inside the container"
        )

    @pytest.mark.parametrize("target", APP_TARGETS)
    def test_every_shipped_target_builds_on_the_runtime_stage(
        self, target: str
    ) -> None:
        """Both published images inherit the install rather than one of them.

        Same reasoning as the two app services in compose: a target that
        branched off somewhere else would ship without the zone database while
        the test above kept passing.
        """
        assert _stages()[target].parent == RUNTIME_BASE_STAGE
