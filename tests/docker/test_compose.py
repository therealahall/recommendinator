"""Static checks on docker-compose.yml's published port mapping.

The port mapping is parsed out of the YAML and then *rendered* the way compose
interpolates it, so the assertions are about the string compose actually gets
rather than the expression written in the file. No Docker CLI or daemon needed.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

# parents[2] resolves /tests/docker/test_compose.py -> repo root.
COMPOSE = Path(__file__).resolve().parents[2] / "docker-compose.yml"

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


def _port_specs(service: str) -> list[str]:
    """Return the raw ``ports`` entries of ``service``, un-interpolated."""
    compose = yaml.safe_load(_compose_text())
    return compose["services"][service]["ports"]


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
