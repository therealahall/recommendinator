"""Static checks on the deployment's compose file and the image it runs.

The port mapping is parsed out of the YAML and then *rendered* the way compose
interpolates it, so the assertions are about the string compose actually gets
rather than the expression written in the file. The timezone assertions span
both files, because passing ``TZ`` into a container and being able to resolve
it there are separate requirements and either one alone is inert. No Docker CLI
or daemon needed.
"""

from __future__ import annotations

import json
import posixpath
import re
from pathlib import Path
from typing import NamedTuple
from urllib.parse import urlparse

import pytest
import yaml

from src.web import healthcheck
from tests.image_layout import STAGE_HEADER, pulled_images, shipped_seed_path

# parents[2] resolves /tests/docker/test_compose.py -> repo root.
_REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE = _REPO_ROOT / "docker-compose.yml"
DEV_COMPOSE = _REPO_ROOT / "docker-compose.dev.yml"
DOCKERFILE = _REPO_ROOT / "Dockerfile"
DOCKERIGNORE = _REPO_ROOT / ".dockerignore"
ENTRYPOINT = _REPO_ROOT / "docker" / "entrypoint.sh"
DOCKER_WORKFLOW = _REPO_ROOT / ".github" / "workflows" / "docker.yml"

# The stage the published image is: everything the deployment runs is installed
# here, and the builder stages above it ship nothing.
RUNTIME_STAGE = "runtime"

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

# The service that publishes the web UI.
APP_SERVICE = "app"

# The mapping compose renders when nothing is set. Container port 8000 is fixed
# by the image's CMD; the host side is the part operators change.
DEFAULT_MAPPING = "127.0.0.1:18473:8000"

# The host part of that mapping: what an operator overrides to publish any
# further than this machine.
LOOPBACK_PREFIX = "127.0.0.1:"

# The two forms this file uses, which differ on an empty value —
# APP_BIND_PREFIX relies on that. `${NAME}` and `${NAME:?err}` would be left
# untouched here and fail the assertions loudly rather than render something
# nobody gets.
_INTERPOLATION = re.compile(r"\$\{([A-Z][A-Z0-9_]*):?-([^}]*)\}")

# Every variable named in the "Environment overrides" comment block at the top,
# written as `#   NAME — description`.
_DOCUMENTED = re.compile(r"^#\s{3}([A-Z][A-Z0-9_]*)\s+—", re.MULTILINE)

# Every service in the deployment. Named here rather than derived so that adding
# one without deciding how it is confined fails a test.
ALL_SERVICES = ["app"]

# Paths that must never reach a builder. Some are gitignored secrets, the rest
# are simply nobody's business on the far side of a `docker build`.
UNSHIPPABLE = [
    "private",
    "private/plugins/personal_site_games.py",
    ".env",
    ".env.local",
    "config/config.yaml",
    "data/recommendinator.db",
    "data/.credential_key",
    ".git/config",
    ".claude/settings.local.json",
    "docker-compose.override.yml",
]

# A `COPY` instruction's arguments, whether or not it carries flags.
_COPY = re.compile(r"^COPY\s+(?P<arguments>\S.*)$", re.MULTILINE)

# An image reference pinned the way this repository requires: the tag stays for
# legibility, the digest is what actually resolves.
_PINNED_IMAGE = re.compile(r"^[^\s@]+:[^\s@:]+@sha256:[0-9a-f]{64}$")

# The entrypoint's fallback for the seed it copies on first run, as `sh`'s
# assign-if-unset form spells it.
_SEED_DEFAULT = re.compile(r'^:\s*"\$\{SEED_CONFIG:=(?P<path>[^}"]+)\}"', re.MULTILINE)


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
    """Return *stage*'s body with its comment lines dropped.

    Prose explaining an install or a healthcheck names the same words it does,
    and must not be able to stand in for it.
    """
    return "\n".join(
        line for line in stage.body.splitlines() if not line.lstrip().startswith("#")
    )


def _instruction(stage: _Stage, name: str) -> str:
    """Return *name*'s arguments in *stage*, collapsed onto one line."""
    match = re.search(
        _INSTRUCTION.format(name=name), _instructions(stage), re.MULTILINE
    )
    assert match is not None, f"no {name} instruction in this stage"
    return " ".join(match.group("arguments").split())


def _apt_packages(stage: _Stage) -> set[str]:
    """Return the packages ``apt-get install`` asks for in *stage*.

    Only the install's own arguments are read, so a flag or a following
    command cannot be mistaken for a package.
    """
    instructions = _instructions(stage)
    packages: set[str] = set()
    for match in _APT_INSTALL.finditer(instructions):
        arguments = match.group("arguments").replace("\\\n", " ").split("&&")[0]
        packages.update(word for word in arguments.split() if not word.startswith("-"))
    return packages


def _services(compose: Path = COMPOSE) -> dict[str, dict]:
    """Return every service in *compose*, with its ``<<:`` merges flattened."""
    return yaml.safe_load(compose.read_text())["services"]


def _port_specs(service: str) -> list[str]:
    """Return the raw ``ports`` entries of ``service``, un-interpolated."""
    return _services()[service]["ports"]


def _environment(service: str) -> list[str]:
    """Return the raw ``environment`` entries of ``service``, un-interpolated."""
    return list(_services()[service].get("environment") or [])


def _render(spec: str, **env: str) -> str:
    """Substitute ``${NAME:-default}`` the way compose does, from ``env`` only.

    Anything absent from ``env`` falls back to its inline default, so calling
    this with no keyword arguments renders the file exactly as a contributor
    with no `.env` and no exported variables would get it.
    """
    return _INTERPOLATION.sub(
        lambda match: env.get(match.group(1), match.group(2)), spec
    )


def _build_contexts() -> dict[str, set[Path]]:
    """Map each build context ``docker.yml`` names to the Dockerfiles built in it.

    BuildKit resolves ``.dockerignore`` against the context root, so the
    repository's own file governs only the builds rooted at the repository.
    """
    workflow = yaml.safe_load(DOCKER_WORKFLOW.read_text())
    contexts: dict[str, set[Path]] = {}
    for job in workflow["jobs"].values():
        for step in job.get("steps", []):
            if not str(step.get("uses", "")).startswith("docker/build-push-action@"):
                continue
            arguments = step["with"]
            contexts.setdefault(arguments["context"], set()).add(
                _REPO_ROOT / arguments["file"]
            )
    assert contexts, "docker.yml builds nothing; every sweep below would be empty"
    return contexts


def _dockerignore(context: str) -> Path:
    return (_REPO_ROOT / context).resolve() / ".dockerignore"


def _ignore_rules(dockerignore: Path = DOCKERIGNORE) -> list[str]:
    """Return a .dockerignore's patterns in file order, comments dropped."""
    return [
        line.strip()
        for line in dockerignore.read_text().splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def _allowlist(dockerignore: Path = DOCKERIGNORE) -> set[str]:
    """Return the paths a .dockerignore puts back into its context."""
    return {rule[1:] for rule in _ignore_rules(dockerignore) if rule.startswith("!")}


def _copied_from_context(dockerfile: Path = DOCKERFILE) -> set[str]:
    """Return the paths *dockerfile* copies out of its build context.

    ``COPY --from=`` reads another stage or another image, never the context, so
    those are not part of what the context has to carry.
    """
    sources: set[str] = set()
    for match in _COPY.finditer(dockerfile.read_text()):
        words = match.group("arguments").split()
        if any(word.startswith("--from=") for word in words):
            continue
        paths = [word for word in words if not word.startswith("--")]
        # The last argument is the destination inside the image.
        sources.update(path.rstrip("/") for path in paths[:-1])
    return sources


def _reaches_the_builder(path: str) -> bool:
    """Whether *path* survives .dockerignore's allowlist.

    An over-approximation: the narrowing rules that follow the allowlist are not
    modelled, so a path this calls reachable may still be dropped. That is the
    safe direction — nothing it calls unreachable can arrive.
    """
    return any(
        path == allowed or path.startswith(f"{allowed}/") for allowed in _allowlist()
    )


def _runtime_workdir() -> str:
    """The directory a relative COPY destination in the shipped stage lands in."""
    match = re.search(
        r"^WORKDIR\s+(?P<path>\S+)",
        _instructions(_stages()[RUNTIME_STAGE]),
        re.MULTILINE,
    )
    assert match is not None, "the runtime stage sets no WORKDIR"
    return match.group("path")


def _bundle_copy() -> str:
    """The runtime stage's ``COPY`` of the built Vue bundle, on one line."""
    match = re.search(
        r"^COPY --from=frontend-builder\s.*$",
        _instructions(_stages()[RUNTIME_STAGE]),
        re.MULTILINE,
    )
    assert match is not None, "the runtime stage does not copy the built bundle"
    return match.group()


def _built_frontend_path() -> str:
    """The container path the image copies the built Vue bundle to."""
    return posixpath.normpath(
        posixpath.join(_runtime_workdir(), _bundle_copy().split()[-1])
    )


def _shipped_paths() -> set[str]:
    """Every container path the *shipped* stage copies a file to.

    Builder stages are excluded: what they write never leaves them.
    """
    paths: set[str] = set()
    for match in _COPY.finditer(_instructions(_stages()[RUNTIME_STAGE])):
        words = [
            word
            for word in match.group("arguments").split()
            if not word.startswith("--")
        ]
        paths.add(posixpath.normpath(posixpath.join(_runtime_workdir(), words[-1])))
    return paths


def _mount_targets(compose: Path) -> set[str]:
    """Every container path *compose* mounts something over."""
    return {
        posixpath.normpath(entry.split(":")[1] if ":" in entry else entry)
        for service in _services(compose).values()
        for entry in service.get("volumes") or []
    }


def _image_owned_mounts(compose: Path) -> set[str]:
    """The mount points in *compose* seeded from the image: named and anonymous
    volumes. A bind mount takes the host's ownership instead."""
    mounts: set[str] = set()
    for service in _services(compose).values():
        for entry in service.get("volumes") or []:
            source, _colon, rest = entry.partition(":")
            if not rest:
                mounts.add(posixpath.normpath(source))
            elif not source.startswith((".", "/")):
                mounts.add(posixpath.normpath(rest.split(":")[0]))
    return mounts


def _chowned_to(user: str) -> re.Pattern[str]:
    """A ``chown`` or a ``COPY --chown=`` that names *user* as the new owner."""
    return re.compile(rf"chown=?\s*(?:-R\s+)?{re.escape(user)}[:\s]")


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

    def test_no_service_publishes_beyond_loopback_by_default(self) -> None:
        """Every mapping in the file, not only the app service.

        A port added to another service later is published on the same terms
        or not at all, and this is what says so before it ships.
        """
        compose = yaml.safe_load(_compose_text())
        published = {
            (name, _render(spec))
            for name, service in compose["services"].items()
            for spec in service.get("ports") or []
        }

        assert published, "no published ports found — the parse went wrong"
        assert {
            entry for entry in published if not entry[1].startswith(LOOPBACK_PREFIX)
        } == set()

    def test_publishes_18473_to_the_fixed_container_port(self) -> None:
        (spec,) = _port_specs(APP_SERVICE)

        _host, published, target = _render(spec).split(":")

        assert published == "18473"
        # The image's CMD hardcodes --port 8000, so only the host side is
        # configurable.
        assert target == "8000"


class TestComposePortOverrides:
    """The two variables that reshape the mapping, together and apart."""

    def test_app_port_moves_the_host_port_only(self) -> None:
        (spec,) = _port_specs(APP_SERVICE)

        assert _render(spec, APP_PORT="8080") == "127.0.0.1:8080:8000"

    def test_bind_prefix_picks_another_interface(self) -> None:
        """The prefix carries its own trailing colon, which is what lets the
        every-interface value be empty. A value without one renders a nonsense
        host:port that compose rejects — loud, which is acceptable here."""
        (spec,) = _port_specs(APP_SERVICE)

        assert (
            _render(spec, APP_BIND_PREFIX="192.168.1.10:", APP_PORT="9000")
            == "192.168.1.10:9000:8000"
        )
        # Independent of APP_PORT: moving the interface must not disturb the
        # port default.
        assert (
            _render(spec, APP_BIND_PREFIX="192.168.1.10:") == "192.168.1.10:18473:8000"
        )

    def test_an_empty_prefix_publishes_on_every_interface(self) -> None:
        """The documented opt-in, and the reason the default uses ``-``.

        With ``:-`` compose reads an empty value as unset and hands back the
        loopback default, so the escape hatch would silently do nothing.
        """
        (spec,) = _port_specs(APP_SERVICE)

        assert _render(spec, APP_BIND_PREFIX="") == "18473:8000"


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
    """The app service hands the operator's ``TZ`` to the container.

    Completion dates are narrowed to the calendar day of the zone the *process*
    runs in (``src.utils.dates.local_date_from_iso_timestamp``), and a container
    has no zone of its own — it runs on UTC unless ``TZ`` is set inside it.
    Setting ``TZ`` in a shell or `.env` next to the compose file reaches the
    container only for a variable the compose file names, so without a
    passthrough here the documented remedy is inert and a viewer west of UTC
    keeps seeing every evening watch dated a day forward.
    """

    def test_tz_reaches_the_container(self) -> None:
        """Regression test: the documented ``TZ`` override did nothing.

        Bug reported: DOCKER.md documents ``TZ`` as the way a Docker user fixes
        completion dates that land a day forward, but setting it in `.env` or
        the shell changed nothing — dates stayed on UTC.
        Root cause: ``docker-compose.yml`` never names ``TZ``. Compose passes a
        host variable into a container only via ``environment``/``env_file``, so
        an unnamed one is dropped at the container boundary.
        Fix: the app service passes ``TZ`` through, empty by default so a host
        that does not set it keeps today's UTC behaviour.
        """
        assert any(
            entry.startswith("TZ=") for entry in _environment(APP_SERVICE)
        ), f"{APP_SERVICE} does not pass TZ into the container"

    def test_tz_defaults_to_empty_rather_than_a_guessed_zone(self) -> None:
        """An operator who sets nothing is left exactly where they were.

        Rendering with no environment must produce an empty value, not a
        hardcoded zone: guessing one would silently re-date every completion for
        someone who never asked for it.
        """
        spec = next(
            entry for entry in _environment(APP_SERVICE) if entry.startswith("TZ=")
        )
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
        Fix: the runtime stage installs ``tzdata`` outright, so the guarantee
        belongs to this repository and this test can hold it.

        Matched against the install instruction rather than the stage text: the
        instruction is explained by a comment that names ``tzdata`` too, so a
        substring search over the stage would keep passing on a Dockerfile that
        had lost the install and kept the comment.
        """
        assert "tzdata" in _apt_packages(_stages()[RUNTIME_STAGE]), (
            f"the {RUNTIME_STAGE} stage does not install tzdata, so a zone "
            "passed via TZ may not resolve inside the container"
        )


class TestTheShippedImageRunsTheLivenessProbe:
    """A probe is only worth writing if the image actually invokes it.

    ``test_healthcheck.py`` holds what it answers; these hold that the image
    asks it, about the port it serves.
    """

    def test_the_healthcheck_runs_the_probe(self) -> None:
        """Regression: the healthcheck called ``urlopen`` on ``/api/status``.

        Bearer auth made 401 the only answer and ``urlopen`` raises on it, so
        the image went permanently unhealthy.
        """
        assert _instruction(_stages()[RUNTIME_STAGE], "HEALTHCHECK").endswith(
            HEALTHCHECK_COMMAND
        )

    def test_the_probe_asks_the_port_the_image_serves(self) -> None:
        """Nothing else ties the two, and a probe aimed past the server would
        report an outage that is entirely its own."""
        command = json.loads(_instruction(_stages()[RUNTIME_STAGE], "CMD"))

        assert "--port" in command
        assert urlparse(healthcheck.STATUS_URL).port == int(
            command[command.index("--port") + 1]
        )


class TestTheBuildContextIsAnAllowlist:
    """Denying by default makes an unreviewed path a build failure rather than a
    disclosure: the narrow COPYs keep secrets out of the image, everything else
    still crosses to the builder. One file per context root, where BuildKit
    reads it."""

    def test_every_context_the_workflow_builds_carries_a_dockerignore(self) -> None:
        """``docker/`` had none, so that build denied nothing while the root
        build was a strict allowlist and the difference was invisible."""
        assert [
            context
            for context in _build_contexts()
            if not _dockerignore(context).is_file()
        ] == []

    @pytest.mark.parametrize("context", sorted(_build_contexts()))
    def test_nothing_is_in_the_context_until_a_rule_names_it(
        self, context: str
    ) -> None:
        """Regression: the file listed what to leave out, so ``private/`` and
        ``.env`` — which nobody had thought of — were in by default."""
        assert _ignore_rules(_dockerignore(context))[0] == "*"

    @pytest.mark.parametrize("context", sorted(_build_contexts()))
    def test_the_exceptions_are_exactly_what_the_dockerfiles_copy(
        self, context: str
    ) -> None:
        """A COPY with no exception fails the build; an exception with no COPY is
        context nobody asked for, and only this notices that one."""
        copied: set[str] = set()
        for dockerfile in _build_contexts()[context]:
            copied |= _copied_from_context(dockerfile)

        # Named so a COPY parse that silently stops matching fails here rather
        # than making the comparison true over two empty sets.
        assert copied, f"nothing is copied out of {context}; the parse went wrong"
        assert _allowlist(_dockerignore(context)) == copied

    @pytest.mark.parametrize("context", sorted(_build_contexts()))
    def test_every_exception_is_a_literal_path(self, context: str) -> None:
        """A glob would decide more than it appears to, and "not listed" would
        stop meaning "not in the context"."""
        allowed = _allowlist(_dockerignore(context))

        assert allowed, f"{context} puts nothing back; the parse found no exceptions"
        assert {rule for rule in allowed if set("*?[]") & set(rule)} == set()

    def test_the_narrowing_rules_follow_the_exceptions_they_cut_into(self) -> None:
        """Last match wins. ``src/web/static/dist`` written above ``!src`` would
        put the host's stale bundle back in the context, where it is invisible
        until someone who has run ``pnpm`` builds an image and ships it."""
        rules = _ignore_rules()
        last_exception = max(
            index for index, rule in enumerate(rules) if rule.startswith("!")
        )
        narrowing = [
            index for index, rule in enumerate(rules) if index and rule[0] != "!"
        ]

        assert narrowing, "no narrowing rules found — the parse went wrong"
        assert min(narrowing) > last_exception

    @pytest.mark.parametrize("path", UNSHIPPABLE)
    def test_sensitive_paths_cannot_reach_the_builder(self, path: str) -> None:
        assert not _reaches_the_builder(path)

    def test_the_application_source_does_reach_the_builder(self) -> None:
        """The model has to be able to say yes, or the exclusions above pass
        against a parse that found no exceptions at all."""
        assert _reaches_the_builder("src/web/app.py")


class TestBaseImagesArePinnedByDigest:
    """``node:20-slim`` and ``python:3.11-slim`` are republished continuously, so
    on tags alone a PR build and the release build a week later are different
    artifacts, with nothing in the repository to say so."""

    def test_every_pulled_image_carries_a_tag_and_a_digest(self) -> None:
        """The tag is for whoever reads it; the digest is what resolves."""
        references = pulled_images(DOCKERFILE)

        assert references, f"no image references parsed out of {DOCKERFILE.name}"
        assert [
            reference for reference in references if not _PINNED_IMAGE.match(reference)
        ] == []

    def test_the_two_python_stages_share_one_digest(self) -> None:
        """The runtime stage receives a venv the builder stage produced, so they
        are one interpreter and one set of shared libraries, or neither."""
        python = [
            reference
            for reference in pulled_images(DOCKERFILE)
            if reference.startswith("python:")
        ]

        assert len(python) == 2
        assert len(set(python)) == 1


class TestTheDevOverrideKeepsTheBuiltFrontend:
    """The bind mount that makes hot reload work must not eat the bundle."""

    def test_the_bundle_directory_is_exempt_from_the_source_mount(self) -> None:
        """Regression: the dev container served a blank page on a fresh clone.

        ``./src:/app/src`` mounts the host tree over the image's, and the built
        bundle underneath it is gitignored. A volume at that path survives it.
        """
        volumes = _services(DEV_COMPOSE)[APP_SERVICE]["volumes"]

        assert "./src:/app/src" in volumes
        assert _built_frontend_path() in volumes

    def test_that_is_every_service_that_mounts_the_source_tree(self) -> None:
        """A second dev service added later keeps the bundle or fails here."""
        mounting = {
            name
            for name, service in _services(DEV_COMPOSE).items()
            if "./src:/app/src" in (service.get("volumes") or [])
        }

        assert mounting == {APP_SERVICE}

    def test_python_edits_still_restart_uvicorn(self) -> None:
        """The exemption is worthless if it costs the reason the file exists."""
        service_definition = _services(DEV_COMPOSE)[APP_SERVICE]

        assert "--reload" in service_definition["command"]
        assert "./src:/app/src" in service_definition["volumes"]


class TestNothingTheImageShipsIsHiddenByAMount:
    """Regression: a fresh `docker compose up -d` wrote no config.yaml.

    Root cause: the image shipped ``example.yaml`` inside ``/app/config``, the
    directory the deployment bind-mounts the host's ``./config`` over, so the
    entrypoint read the seed through the mount that hid it.
    """

    def test_no_shipped_file_sits_under_a_deployment_mount(self) -> None:
        """Whole class, not the one file: a mount hides everything beneath it,
        and the image builds green either way. The dev override is excluded —
        it mounts the source tree over the image's on purpose."""
        shipped = _shipped_paths()
        mounted = _mount_targets(COMPOSE)

        # Named so a COPY or volume parse that stops matching fails loudly
        # rather than comparing two empty sets.
        assert f"{_runtime_workdir()}/src" in shipped
        assert "/app/config" in mounted
        assert {
            path
            for path in shipped
            if any(
                path == target or path.startswith(f"{target}/") for target in mounted
            )
        } == set()


class TestTheEntrypointReadsTheSeedWhereTheImagePutsIt:
    """The Dockerfile picks the destination, the entrypoint hardcodes what it
    reads, and nothing connects them. Moved on one side only, the image builds,
    starts, writes no config.yaml and exits on a missing token.
    """

    def test_the_seed_default_names_the_path_the_runtime_stage_copies_it_to(
        self,
    ) -> None:
        """Both ends read out, so a rename on either fails here rather than in
        somebody's first `docker compose up`."""
        default = _SEED_DEFAULT.search(ENTRYPOINT.read_text())

        assert default is not None, "the entrypoint sets no SEED_CONFIG default"
        assert default.group("path") == shipped_seed_path()

    def test_the_path_it_agrees_on_is_one_a_shipped_stage_copies_to(self) -> None:
        """`shipped_seed_path` reads the whole Dockerfile, and `builder` holds
        the same `WORKDIR /app`. Moved there the seed reaches no image, while
        the entrypoint and the smoke test still agree with it."""
        assert shipped_seed_path() in _shipped_paths()


class TestEveryImageOwnedMountPointExistsInItsImage:
    """Docker seeds a fresh named or anonymous volume from the image's directory
    at the mount point, ownership included. With nothing there it creates one
    root-owned, which a container that dropped root cannot write."""

    def test_that_is_every_mount_that_takes_its_ownership_from_an_image(
        self,
    ) -> None:
        """The population the one below covers. A second volume added to either
        compose file is checked against its image or it fails here."""
        assert _image_owned_mounts(COMPOSE) | _image_owned_mounts(DEV_COMPOSE) == {
            _built_frontend_path(),
        }

    def test_the_bundle_volume_mounts_over_a_directory_the_image_owns(self) -> None:
        """The dev override's anonymous volume, and the reason to check every
        mount rather than the reported one."""
        user = _instruction(_stages()[RUNTIME_STAGE], "USER")

        assert _chowned_to(user).search(_bundle_copy()), (
            f"the bundle is not copied to {user}; the dev override's anonymous "
            "volume would mount root-owned"
        )


class TestEveryServiceIsConfined:
    """A bearer token guards the API; this guards what a compromised dependency
    reaches once it is past that. The docs recommend hardware where one runaway
    container is the whole machine, so it is every service or it is decoration.
    """

    def test_the_hardened_list_covers_the_whole_file(self) -> None:
        """A service added later is confined too, or this fails."""
        assert set(_services()) == set(ALL_SERVICES)

    @pytest.mark.parametrize("service", ALL_SERVICES)
    def test_privileges_cannot_be_escalated(self, service: str) -> None:
        assert "no-new-privileges:true" in _services()[service]["security_opt"]

    @pytest.mark.parametrize("service", ALL_SERVICES)
    def test_all_capabilities_are_dropped(self, service: str) -> None:
        assert _services()[service]["cap_drop"] == ["ALL"]

    @pytest.mark.parametrize("compose", [COMPOSE, DEV_COMPOSE])
    def test_nothing_hands_a_capability_back(self, compose: Path) -> None:
        """Compose merges these lists, so an override can only add. The dev file
        is the likeliest to grow a "just for local" line that then ships."""
        services = _services(compose)

        assert services, f"no services parsed out of {compose.name}"
        assert {
            name
            for name, service in services.items()
            if service.get("cap_add") or service.get("privileged")
        } == set()
