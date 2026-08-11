"""Static checks on the workflows that publish.

A workflow only runs on GitHub, so wiring is read from parsed YAML. Every step
that decides something — the release guard, the tag guards, the alias
decision — is instead executed under `bash -e`, the shell a `run:` step gets.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tomllib
from pathlib import Path
from typing import Any, NamedTuple

import pytest
import yaml

from tests.image_layout import shipped_seed_path

# parents[1] resolves /tests/test_workflows.py -> repo root.
_REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = _REPO_ROOT / ".github" / "workflows"
CHANGELOG = _REPO_ROOT / "CHANGELOG.md"

# A release heading and a bullet under it, as python-semantic-release writes
# them. The release step's own extraction matches the same heading shape.
_VERSION_HEADING = re.compile(r"^## (?P<tag>v\d+\.\d+\.\d+) ", re.MULTILINE)
_CHANGELOG_BULLET = re.compile(r"^- \S.*$", re.MULTILINE)

CI = WORKFLOWS / "ci.yml"
DOCKER = WORKFLOWS / "docker.yml"
GATE = WORKFLOWS / "quality-gate.yml"
RELEASE = WORKFLOWS / "release.yml"

# The reusable workflow both entry points call, spelled the way a caller must.
GATE_REFERENCE = "./.github/workflows/quality-gate.yml"

# PyYAML follows YAML 1.1, where the bare `on:` key is the boolean true.
_ON = True

# `branches: [main]` filters workflow_run.head_branch, which for a fork pull
# request is the fork's own branch name. A fresh fork's default is `main`, so
# these two keep the release PAT away from a fork's tree.
RELEASE_CONDITIONS = {
    "github.event.workflow_run.conclusion == 'success'",
    "github.event.workflow_run.event == 'push'",
    "github.event.workflow_run.head_repository.full_name == github.repository",
}

# publish holds `packages: write` and inherits its event gate through `needs`
# alone, so loosening these two would let a pull request push `latest`.
TAG_BUILD_CONDITIONS = {
    "github.event_name == 'push'",
    "startsWith(github.ref, 'refs/tags/v')",
}

# Every argument `semantic_release version` is invoked with. Drop `--tag` and
# the tag detection finds nothing, so the release silently never happens.
SEMANTIC_RELEASE_FLAGS = {"--no-push", "--commit", "--tag", "--no-vcs-release"}

# The output every step after the tag detection is conditioned on, and the one
# the merge-race guard writes. Spelled from the step ids so the conditions and
# the `id:` they name cannot be moved apart.
RELEASED_STEP_ID = "released"
VALIDATED_STEP_ID = "validated"
RELEASED_TAG_CONDITION = f"steps.{RELEASED_STEP_ID}.outputs.tag != ''"
STILL_MAIN_CONDITION = f"steps.{VALIDATED_STEP_ID}.outputs.current == 'true'"

# Scopes nothing here consumes: attestations:write needs an attest-* action, and
# id-token:write mints an OIDC token identifying this repository. Buildkit's
# provenance and sbom travel under packages:write and want neither.
UNUSED_SCOPES = ("attestations", "id-token")

# The one job entitled to push to the registry, as `<workflow>:<job>`. build-pr
# runs on `pull_request`, where the scope would let a contributed branch push
# over a published tag.
PACKAGE_WRITERS = {"docker.yml:publish"}

# The steps after the merge-race guard that carry no condition. They provision
# the runner and write nothing outside it; everything else after the guard runs
# against the detached HEAD the pinned checkout left behind.
UNCONDITIONAL_SETUP_STEPS = {
    "astral-sh/setup-uv",
    "actions/setup-python",
    "Install dependencies",
    "Configure git for semantic-release",
}

# Each floating tag and the one guard output entitled to enable it. The three
# are interchangeable to every other assertion here, and a swapped pair is
# indistinguishable from the bug they exist to prevent.
FLOATING_TAG_GUARDS = {
    ("raw", "latest"): "highest_overall",
    ("semver", "{{major}}"): "highest_in_major",
    ("semver", "{{major}}.{{minor}}"): "highest_in_minor",
}

# The same three names at their other two ends: what the guard's script writes,
# and what the job exports. All three ends are held equal below.
GUARD_OUTPUTS = set(FLOATING_TAG_GUARDS.values())

# The step making those decisions. Its `id:` is what the job's `outputs:` block
# reads them from, so both are taken off the step rather than spelled twice.
ALIAS_DECISION_STEP = "Decide which floating tags this release may move"

# The step the release job's publishing steps all key on, the one deciding
# whether there is anything to release at all, and the last one, which is the
# only place the tag becomes public.
TAG_DETECTION_STEP = "Identify the release tag"
MERGE_RACE_GUARD_STEP = "Release only the commit CI validated"
RELEASE_CREATION_STEP = "Create GitHub release with docker-compose.yml asset"

# The git fixtures below inherit the developer's environment, so a global
# `commit.gpgsign` or `core.hooksPath` would decide whether they pass. Both
# config files are neutralised rather than each setting overridden by name.
GIT_ISOLATION = {
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_SYSTEM": os.devnull,
}

# What the tests for that isolation make a hostile git print.
_HOSTILE_HOOK_MARKER = "HOSTILE-HOOK-RAN"

# The only history the guards count anything from.
MAIN_TRACKING_REF = "refs/remotes/origin/main"

# Any absolute path naming the seed, however the Dockerfile spells it today.
_SEED_REFERENCE = re.compile(r"/app/\S*example\.yaml")


def _workflow(path: Path) -> dict[str, Any]:
    loaded: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))
    return loaded


def _workflow_files() -> list[Path]:
    paths = sorted(WORKFLOWS.glob("*.yml"))
    assert paths, "no workflow files found; every assertion below would be empty"
    return paths


def _jobs(path: Path) -> dict[str, Any]:
    jobs: dict[str, Any] = _workflow(path)["jobs"]
    return jobs


def _steps(path: Path, job: str) -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = _jobs(path)[job]["steps"]
    return steps


def _step_index(path: Path, job: str, name: str) -> int:
    for index, step in enumerate(_steps(path, job)):
        if step.get("name") == name:
            return index
    raise AssertionError(f"{path.name} job {job} has no step named {name!r}")


def _step_named(path: Path, job: str, name: str) -> dict[str, Any]:
    return _steps(path, job)[_step_index(path, job, name)]


def _step_identity(step: dict[str, Any]) -> str:
    """A step's `name:`, or the action it runs when it has none."""
    name = step.get("name")
    return str(name) if name is not None else str(step["uses"]).split("@")[0]


def _conditions(expression: str) -> set[str]:
    """Return an `if:` expression's `&&`-joined clauses, whitespace normalised.

    A folded scalar arrives with its line breaks already collapsed, but the
    clauses keep whatever indentation the author wrapped them at.
    """
    return {" ".join(clause.split()) for clause in expression.split("&&")}


def _every_run_command() -> list[tuple[str, str, str]]:
    """Return (workflow, job, command) for every shell command any workflow runs."""
    commands = [
        (path.name, job_name, str(step["run"]))
        for path in _workflow_files()
        for job_name, job in _jobs(path).items()
        for step in job.get("steps", [])
        if "run" in step
    ]
    assert commands, "no run steps found; the sweeps below would prove nothing"
    return commands


def _tag_entries(path: Path, job: str) -> dict[tuple[str, str], dict[str, str]]:
    """Return the metadata-action `tags` block keyed by (type, pattern or value)."""
    entries = {}
    for line in str(
        _step_named(path, job, "Generate image metadata")["with"]["tags"]
    ).splitlines():
        if not line.strip():
            continue
        attributes = dict(
            field.split("=", 1) for field in line.strip().split(",") if "=" in field
        )
        key = (attributes["type"], attributes.get("pattern") or attributes["value"])
        entries[key] = attributes
    assert entries, f"{path.name} job {job} generates no tags at all"
    return entries


def _git(repository: Path, *arguments: str) -> str:
    completed = subprocess.run(
        [
            "git",
            "-c",
            "user.email=test@example.test",
            "-c",
            "user.name=Test",
            *arguments,
        ],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ, **GIT_ISOLATION},
    )
    return completed.stdout.strip()


def _commit(repository: Path, message: str) -> str:
    """Add a commit and return its SHA."""
    (repository / "CHANGELOG.md").write_text(f"# {message}\n", encoding="utf-8")
    _git(repository, "commit", "--quiet", "--all", "-m", message)
    return _git(repository, "rev-parse", "HEAD")


def _land(repository: Path, message: str) -> str:
    """Add a commit to `main` and push it, so `origin/main` reaches it."""
    sha = _commit(repository, message)
    _git(repository, "push", "--quiet", "origin", "main")
    return sha


def _release(repository: Path, tag: str, *, annotated: bool = False) -> str:
    """Land a commit on `main` and tag it, the way a release reaches the guard.

    python-semantic-release creates annotated tags, so `annotated` is the shape
    production actually pushes; lightweight is the cheaper default here.
    """
    sha = _land(repository, f"work for {tag}")
    if annotated:
        _git(repository, "tag", "--annotate", "--message", f"Release {tag}", tag)
    else:
        _git(repository, "tag", tag)
    return sha


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    """A one-commit git repository on `main`, carrying no tags."""
    repository = tmp_path / "repository"
    repository.mkdir()
    _git(repository, "init", "--quiet", "--initial-branch=main")
    (repository / "CHANGELOG.md").write_text("# CHANGELOG\n", encoding="utf-8")
    _git(repository, "add", "CHANGELOG.md")
    _git(repository, "commit", "--quiet", "-m", "initial")
    return repository


@pytest.fixture
def published_repository(repository: Path, tmp_path: Path) -> Path:
    """`repository` with an `origin` its `main` has been pushed to.

    Both guards fetch `origin/main` and compare against it, so a remote is what
    exercises them rather than their shape.
    """
    origin = tmp_path / "origin.git"
    _git(tmp_path, "init", "--bare", "--quiet", "--initial-branch=main", str(origin))
    _git(repository, "remote", "add", "origin", str(origin))
    _git(repository, "push", "--quiet", "origin", "main")
    return repository


def _run_step(
    workflow: Path,
    job: str,
    name: str,
    *,
    repository: Path,
    tmp_path: Path,
    environment: dict[str, str] | None = None,
) -> tuple[int, str, str]:
    """Run a step's script, returning its code, its output and its step outputs.

    `bash -e` is the shell an unqualified `run:` gets, and a step's `env:` block
    holds GitHub expressions the caller has to stand in for.
    """
    script = tmp_path / f"{name.replace(' ', '-')}.sh"
    script.write_text(_step_named(workflow, job, name)["run"], encoding="utf-8")
    step_output = tmp_path / f"{script.stem}.github_output"
    step_output.touch()
    completed = subprocess.run(
        ["bash", "-e", str(script)],
        cwd=repository,
        env={
            **os.environ,
            **GIT_ISOLATION,
            "GITHUB_OUTPUT": str(step_output),
            **(environment or {}),
        },
        capture_output=True,
        text=True,
    )
    return (
        completed.returncode,
        completed.stdout + completed.stderr,
        step_output.read_text(encoding="utf-8"),
    )


def _run_release_step(
    name: str, repository: Path, tmp_path: Path
) -> tuple[int, str, str]:
    return _run_step(RELEASE, "release", name, repository=repository, tmp_path=tmp_path)


def _decide_aliases(repository: Path, tmp_path: Path, tag: str) -> dict[str, str]:
    """Run the guard's alias decision for `tag` and return the outputs it wrote."""
    code, output, step_output = _run_step(
        DOCKER,
        "guard",
        ALIAS_DECISION_STEP,
        repository=repository,
        tmp_path=tmp_path,
        environment={"TAG": tag},
    )
    assert code == 0, output
    decided = dict(
        line.split("=", 1) for line in step_output.splitlines() if "=" in line
    )
    # Held against what the job exports, not a literal: a name written here and
    # not exported there resolves to the empty string in publish's `enable=`.
    assert set(decided) == set(_jobs(DOCKER)["guard"]["outputs"]), decided
    return decided


class _StubbedGh(NamedTuple):
    """A stub `gh` on PATH, the calls it recorded, and the notes it was given."""

    environment: dict[str, str]
    log: Path
    notes: Path

    def calls(self) -> list[str]:
        recorded = self.log.read_text(encoding="utf-8").splitlines()
        assert recorded, "the step invoked gh not at all"
        return recorded


def _stub_gh(tmp_path: Path, *, release_exists: bool) -> _StubbedGh:
    """Put a recording `gh` on PATH, ahead of any real one.

    It answers `release view`, the call the recovery path branches on, and
    copies any `--notes-file` where a test can read it — the step names that
    one with `mktemp`.
    """
    binaries = tmp_path / "stub-bin"
    binaries.mkdir(exist_ok=True)
    stub = _StubbedGh(
        environment={
            "PATH": f"{binaries}{os.pathsep}{os.environ['PATH']}",
            # The step's `mktemp` writes the notes file, and the system temp
            # directory is the one place this suite would leave anything.
            "TMPDIR": str(tmp_path),
        },
        log=tmp_path / "gh-calls.log",
        notes=tmp_path / "gh-notes.txt",
    )
    executable = binaries / "gh"
    executable.write_text(
        "#!/bin/sh\n"
        f'printf "%s\\n" "$*" >> "{stub.log}"\n'
        'if [ "$1" = "release" ] && [ "$2" = "view" ]; then\n'
        f"  exit {int(not release_exists)}\n"
        "fi\n"
        "while [ $# -gt 0 ]; do\n"
        f'  if [ "$1" = "--notes-file" ]; then cp "$2" "{stub.notes}"; fi\n'
        "  shift\n"
        "done\n",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    return stub


def _sole_call(stub: _StubbedGh, prefix: str) -> str:
    """The one recorded `gh` invocation beginning with *prefix*."""
    (call,) = [line for line in stub.calls() if line.startswith(prefix)]
    return call


class _ChangelogSection(NamedTuple):
    """A release's heading tag and the first bullet written under it."""

    tag: str
    bullet: str


def _changelog_sections() -> list[_ChangelogSection]:
    """This repository's own release notes, newest first.

    python-semantic-release writes the file the step parses, so a fabricated
    fixture would hold the extraction against a format nobody produces.
    """
    source = CHANGELOG.read_text(encoding="utf-8")
    headings = list(_VERSION_HEADING.finditer(source))
    assert len(headings) >= 2, "fewer than two releases to tell apart"
    sections = []
    for index, heading in enumerate(headings):
        end = headings[index + 1].start() if index + 1 < len(headings) else len(source)
        bullet = _CHANGELOG_BULLET.search(source, heading.end(), end)
        assert bullet is not None, f"{heading.group('tag')} has no bullet under it"
        sections.append(_ChangelogSection(heading.group("tag"), bullet.group()))
    return sections


def _give_the_repository_its_changelog(repository: Path) -> None:
    """Put this repository's CHANGELOG.md where the release step reads one."""
    shutil.copy(CHANGELOG, repository / "CHANGELOG.md")


class TestWorkflowSupplyChain:
    """A workflow installs what the lockfile pins, and holds the scopes it uses."""

    def test_every_uv_run_is_frozen(self) -> None:
        """Without --frozen, uv re-resolves from the branch's own pyproject.toml.

        It governs the parent environment only. semantic-release's build_command
        re-locks on purpose, because the version bump changes what uv.lock pins.
        """
        invocations = [
            (workflow, job, command)
            for workflow, job, command in _every_run_command()
            if "uv run" in command
        ]
        assert invocations, "no `uv run` step left for this sweep to mean anything"
        for workflow, job, command in invocations:
            assert "--frozen" in command, f"{workflow} job {job} re-resolves: {command}"

    def test_every_uv_sync_is_locked(self) -> None:
        """--locked fails on a lockfile that disagrees with pyproject.toml."""
        invocations = [
            (workflow, job, command)
            for workflow, job, command in _every_run_command()
            if "uv sync" in command
        ]
        assert invocations, "no `uv sync` step left for this sweep to mean anything"
        for workflow, job, command in invocations:
            assert "--locked" in command, f"{workflow} job {job} unpinned: {command}"

    def test_every_job_that_runs_steps_declares_its_permissions(self) -> None:
        """A job with no block inherits the repository default, which may be write-all."""
        examined = []
        for path in _workflow_files():
            for job_name, job in _jobs(path).items():
                if "steps" not in job:
                    continue
                examined.append(f"{path.name}:{job_name}")
                assert isinstance(
                    job.get("permissions"), dict
                ), f"{path.name} job {job_name} does not declare its permissions"
        assert examined, "no job with steps found; this sweep examined nothing"

    @pytest.mark.parametrize("scope", UNUSED_SCOPES)
    def test_no_job_holds_a_scope_nothing_consumes(self, scope: str) -> None:
        """An unused scope is only ever useful to whoever compromises the job."""
        examined = []
        for path in _workflow_files():
            for job_name, job in _jobs(path).items():
                examined.append(f"{path.name}:{job_name}")
                assert scope not in job.get(
                    "permissions", {}
                ), f"{path.name} job {job_name} grants unused {scope}"
        assert examined, "no job found; this sweep examined nothing"

    def test_the_publish_job_holds_only_what_it_pushes_with(self) -> None:
        """Pinning the block, so a scope added later is a decision rather than a drift."""
        assert _jobs(DOCKER)["publish"]["permissions"] == {
            "contents": "read",
            "packages": "write",
        }

    def test_publish_is_the_only_job_that_may_write_to_the_registry(self) -> None:
        """Only one block is pinned above, and UNUSED_SCOPES covers just the
        scopes nothing consumes. build-pr builds a contributed tree, on
        `pull_request`, and this is what keeps the push scope away from it."""
        holders = {
            f"{path.name}:{job_name}"
            for path in _workflow_files()
            for job_name, job in _jobs(path).items()
            if "packages" in job.get("permissions", {})
        }

        assert holders == PACKAGE_WRITERS

    def test_the_pull_request_build_pushes_nothing(self) -> None:
        """The other half of that: the scope is only one of the two things a
        publishing pull-request build would need."""
        build = _step_named(DOCKER, "build-pr", "Build ${{ matrix.name }}")

        assert build["with"]["push"] is False

    def test_the_release_job_writes_nothing_with_its_own_token(self) -> None:
        """Every write it makes goes through the PAT, so `contents: write` here
        would be a second, unaudited way to push. UNUSED_SCOPES above covers
        only the scopes nothing consumes, which this is not."""
        assert _jobs(RELEASE)["release"]["permissions"] == {"contents": "read"}


class TestComposeValidationCoverage:
    """Compose resolves only the active profiles, so an inactive one goes unchecked."""

    def test_every_documented_combination_is_validated(self) -> None:
        """`--profile ai` is where depends_on, the healthcheck and the volume live."""
        validation = _step_named(GATE, "check", "Validate compose files")["run"]
        base = "docker compose -f docker-compose.yml"
        overridden = f"{base} -f docker-compose.dev.yml"
        for command in (
            f"{base} config",
            f"{base} --profile ai config",
            f"{overridden} config",
            f"{overridden} --profile ai config",
        ):
            assert command in validation, f"CI never runs `{command}`"

    def test_the_profile_the_validation_names_is_the_one_compose_declares(self) -> None:
        """A renamed profile would leave the extra invocations resolving nothing."""
        services = yaml.safe_load(
            (_REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
        )["services"]
        profiled = {
            profile
            for service in services.values()
            for profile in service.get("profiles", [])
        }
        assert profiled == {"ai"}, profiled


class TestTheSmokeTestReadsTheImageItJustBuilt:
    """Regression: the smoke test cats the seed out of the image by absolute
    path, and the image moved it out of `/app/config` so the deployment's bind
    mount could not hide it. Nothing tied the two.
    """

    def test_the_seed_is_read_from_where_the_dockerfile_ships_it(self) -> None:
        """The step runs under `bash -e` without `pipefail`, so a `cat` of the
        wrong path writes an empty config and the job dies two lines later on a
        grep that says nothing about the rename that caused it."""
        smoke = _step_named(DOCKER, "build-pr", "Smoke test (default variant only)")
        referenced = set(_SEED_REFERENCE.findall(str(smoke["run"])))

        assert referenced, "the smoke test reads no seed out of the image"
        assert referenced == {shipped_seed_path()}


class TestReleaseIntegrity:
    """What ships is the commit CI validated, and no floating tag walks backwards."""

    def test_ci_and_the_tag_build_run_the_same_gate(self) -> None:
        """Two copies would let the tag path be checked by the weaker of them."""
        assert _jobs(CI)["check"]["uses"] == GATE_REFERENCE
        assert _jobs(DOCKER)["verify"]["uses"] == GATE_REFERENCE

    def test_the_gate_they_both_name_is_a_workflow_they_may_call(self) -> None:
        """A `uses:` reference resolves at run time, so dropping `workflow_call`
        breaks CI and the tag build with the whole suite still green."""
        assert _REPO_ROOT / GATE_REFERENCE.removeprefix("./") == GATE
        assert "workflow_call" in _workflow(GATE)[_ON], _workflow(GATE)[_ON]

    def test_release_runs_only_for_a_push_to_this_repository(self) -> None:
        """Regression test: a fork's CI run could reach the release job.

        Bug reported: found by audit, not exploited.
        Root cause: `branches: [main]` matches the fork's branch name.
        Fix: gate on the upstream event and repository.
        """
        assert _conditions(_jobs(RELEASE)["release"]["if"]) == RELEASE_CONDITIONS
        assert _workflow(RELEASE)[_ON] == {
            "workflow_run": {
                "workflows": ["CI"],
                "types": ["completed"],
                "branches": ["main"],
            }
        }

    def test_the_tag_build_is_gated_on_a_pushed_tag_and_nothing_re_opens_it(
        self,
    ) -> None:
        """publish dropped its own event condition for `needs: [guard, verify]`,
        so the guard's is the only one left — and a job holding `packages: write`
        would build and push `latest` from a pull request without it."""
        assert _conditions(_jobs(DOCKER)["guard"]["if"]) == TAG_BUILD_CONDITIONS
        assert "if" not in _jobs(DOCKER)["publish"]
        assert "if" not in _jobs(DOCKER)["verify"]

    def test_every_tag_publish_queues_behind_the_last_one(self) -> None:
        """Grouped per commit, two releases decide their aliases before either
        builds, both claim `latest`, and the slower build keeps it. A pull
        request keeps a group per ref, where cancelling is what is wanted."""
        assert _workflow(DOCKER)["concurrency"] == {
            "group": (
                "docker-${{ github.event_name == 'pull_request' "
                "&& github.ref || 'publish' }}"
            ),
            "cancel-in-progress": "${{ github.event_name == 'pull_request' }}",
        }

    def test_release_checks_out_the_commit_ci_validated(self) -> None:
        """With no ref, a workflow_run job gets main's tip, a later commit. The
        guard goes next: a step slipped between the two runs on the overtaken
        run, which is the hole the sweep below exists to close."""
        checkout = _steps(RELEASE, "release")[0]
        assert checkout["with"]["ref"] == "${{ github.event.workflow_run.head_sha }}"
        assert _step_index(RELEASE, "release", MERGE_RACE_GUARD_STEP) == 1

    def test_nothing_releases_unless_the_validated_commit_is_still_main(self) -> None:
        """Otherwise semantic-release tags whatever overtook it, and the push
        step runs against the detached HEAD the checkout left behind."""
        guard = _step_named(RELEASE, "release", "Release only the commit CI validated")
        assert (
            guard["env"]["VALIDATED_SHA"] == "${{ github.event.workflow_run.head_sha }}"
        )
        assert guard["id"] == VALIDATED_STEP_ID
        for name in ("Run semantic-release", TAG_DETECTION_STEP):
            assert (
                _step_named(RELEASE, "release", name)["if"] == STILL_MAIN_CONDITION
            ), name

    def test_no_step_after_the_merge_race_guard_runs_unconditioned(self) -> None:
        """The pair above is named, so a step inserted between them and the guard
        is what nothing sees. It would run against the detached HEAD the
        overtaken run was left standing on."""
        following = _steps(RELEASE, "release")[
            _step_index(RELEASE, "release", MERGE_RACE_GUARD_STEP) + 1 :
        ]

        assert following, "nothing follows the guard; this sweep is empty"
        assert {
            _step_identity(step) for step in following if "if" not in step
        } == UNCONDITIONAL_SETUP_STEPS
        # `if: always()` is a condition too, and reopens the same hole.
        assert {str(step["if"]) for step in following if "if" in step} <= {
            STILL_MAIN_CONDITION,
            RELEASED_TAG_CONDITION,
        }

    def test_the_bumped_tree_is_verified_before_anything_is_pushed(self) -> None:
        """The version commit rewrites uv.lock, and a pushed tag cannot be taken back."""
        semantic_release = _step_named(RELEASE, "release", "Run semantic-release")
        assert "--no-push" in semantic_release["run"]
        assert _step_index(RELEASE, "release", "Verify the bumped tree") < _step_index(
            RELEASE, "release", "Push the version commit and tag"
        )

    def test_semantic_release_is_given_every_flag_the_job_reads_back(self) -> None:
        """`--no-push` alone is checked above, and it is not the only load-bearing
        one: without `--tag` the detection below finds nothing to publish and the
        release silently never happens."""
        command = str(_step_named(RELEASE, "release", "Run semantic-release")["run"])
        _before, _, arguments = command.partition("semantic_release version")

        assert arguments.strip(), f"no semantic-release invocation parsed: {command}"
        assert {
            word for word in arguments.split() if word.startswith("--")
        } == SEMANTIC_RELEASE_FLAGS

    def test_every_step_after_the_tag_is_identified_is_conditioned_on_it(self) -> None:
        """Regression test: a push with nothing to release went red.

        Bug reported: `git push origin "refs/tags/"`.
        Root cause: the publishing steps ran whether or not a tag was cut.
        Fix: each is conditioned on the detected tag.
        """
        detection = _step_named(RELEASE, "release", TAG_DETECTION_STEP)
        following = _steps(RELEASE, "release")[
            _step_index(RELEASE, "release", TAG_DETECTION_STEP) + 1 :
        ]

        assert (
            detection["id"] == RELEASED_STEP_ID
        ), "the condition below names a step id nothing carries"
        assert following, "nothing follows the tag detection; this sweep is empty"
        for step in following:
            assert (
                step.get("if") == RELEASED_TAG_CONDITION
            ), f"{step.get('name')} runs with no tag to release"

    def test_the_regenerated_lockfile_rides_in_the_version_commit(self) -> None:
        """Unstaged, the tagged tree carries a lock every `uv sync --locked` rejects.

        CONTRIBUTING.md and CLAUDE.md both describe one commit carrying the
        bump, the changelog and the lock; a second commit would not be tagged.
        """
        semantic_release = tomllib.loads(
            (_REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        )["tool"]["semantic_release"]
        build_command = semantic_release["build_command"]
        assert "uv lock" in build_command, build_command
        assert "git add uv.lock" in build_command, build_command

        committing = [
            (workflow, job)
            for workflow, job, command in _every_run_command()
            if "git commit" in command
        ]
        assert not committing, f"a commit outside semantic-release: {committing}"

    def test_publish_waits_for_the_guard_and_the_gate(self) -> None:
        """Either one skipped and a tag on any commit at all reaches GHCR."""
        assert _jobs(DOCKER)["publish"]["needs"] == ["guard", "verify"]
        assert _jobs(DOCKER)["verify"]["needs"] == "guard"

    def test_every_floating_tag_is_conditional_on_being_the_newest(self) -> None:
        """`latest`, `0` and `0.22` each move backwards when a backport publishes."""
        metadata = _step_named(DOCKER, "publish", "Generate image metadata")
        entries = [
            line for line in metadata["with"]["tags"].splitlines() if line.strip()
        ]
        floating = [entry for entry in entries if "{{version}}" not in entry]
        assert len(floating) == 3, f"expected latest, major and major.minor: {entries}"
        for entry in floating:
            assert "enable=${{ needs.guard.outputs.highest" in entry, entry

    def test_the_action_is_told_not_to_apply_latest_on_its_own(self) -> None:
        """Regression test: a backport still overwrote `:latest` (qs5i.9.8).

        Root cause: `flavor.latest` defaults to `auto`, under which
        metadata-action appends `latest` for any non-prerelease semver tag.
        Fix: pin `latest=false`, leaving the guarded entry the only source.
        """
        metadata = _step_named(DOCKER, "publish", "Generate image metadata")
        fields = {
            field.strip()
            for line in str(metadata["with"]["flavor"]).splitlines()
            for field in line.split(",")
            if field.strip()
        }
        assert "latest=false" in fields, (
            "metadata-action applies `latest` to every semver tag unless the "
            f"flavor forbids it, so the guard decides nothing: {fields}"
        )

    def test_each_floating_tag_is_enabled_by_the_guard_output_for_its_own_scope(
        self,
    ) -> None:
        """Reading `highest_in_minor`, `latest` would follow the 0.22.1 backport.

        Every alias being conditional is not enough: the three conditions are
        the same shape, and nothing else notices two of them changing places.
        """
        conditional = {
            key: attributes
            for key, attributes in _tag_entries(DOCKER, "publish").items()
            if "enable" in attributes
        }
        assert set(conditional) == set(FLOATING_TAG_GUARDS), conditional
        for key, output in FLOATING_TAG_GUARDS.items():
            expected = "${{ needs.guard.outputs." + output + " == 'true' }}"
            assert conditional[key]["enable"] == expected, (key, conditional[key])

    def test_the_version_tag_is_published_whatever_the_guard_decided(self) -> None:
        """Guarded too, a backport would reach GHCR under no tag of its own."""
        version = _tag_entries(DOCKER, "publish")[("semver", "{{version}}")]
        assert "enable" not in version, version

    def test_each_published_variant_tags_a_namespace_of_its_own(self) -> None:
        """Sharing one, the AI variant's `latest` would overwrite the default's.

        Rests on metadata-action applying the global `flavor` suffix to
        `type=raw` too — its default, stated nowhere here. Change that upstream
        and both `latest` tags land in one namespace, green.
        """
        variants = _jobs(DOCKER)["publish"]["strategy"]["matrix"]["include"]
        assert len(variants) > 1, "one variant collides with nothing"
        namespaces = [(variant["image"], variant["tag_suffix"]) for variant in variants]
        assert len(set(namespaces)) == len(namespaces), namespaces

        metadata = _step_named(DOCKER, "publish", "Generate image metadata")
        assert "suffix=${{ matrix.tag_suffix }}" in str(metadata["with"]["flavor"])

    def test_publish_reads_only_outputs_the_guard_declares(self) -> None:
        """A misspelt output resolves to the empty string, which enables nothing."""
        declared = set(_jobs(DOCKER)["guard"]["outputs"])
        assert declared, "the guard declares no outputs"
        referenced = set(
            re.findall(
                r"needs\.guard\.outputs\.([A-Za-z0-9_]+)",
                yaml.safe_dump(_jobs(DOCKER)["publish"]),
            )
        )
        assert referenced, "publish consumes none of the guard's decisions"
        assert referenced <= declared, f"undeclared: {referenced - declared}"

    def test_the_guard_exports_exactly_the_three_decisions_it_makes(self) -> None:
        """The anchor for the test below and for `_decide_aliases`: both take
        the names off this block, so this is where they are named."""
        assert set(_jobs(DOCKER)["guard"]["outputs"]) == GUARD_OUTPUTS

    def test_each_exported_decision_is_read_off_the_step_that_makes_it(self) -> None:
        """Regression test: nothing tied the job's `outputs:` to the step.

        Bug reported: found by audit, not exploited.
        Root cause: the block names a step id and three output names, checked
        against nothing.
        Fix: rebuilt from the step's own `id:`.
        """
        step_id = _step_named(DOCKER, "guard", ALIAS_DECISION_STEP)["id"]
        exported = _jobs(DOCKER)["guard"]["outputs"]

        for name in GUARD_OUTPUTS:
            assert exported[name] == f"${{{{ steps.{step_id}.outputs.{name} }}}}"


class TestReleaseTagDetectionRegression:
    """Regression tests for the release step that failed when nothing was releasable."""

    def test_no_semver_tag_at_head_is_not_a_failure_regression(
        self, repository: Path, tmp_path: Path
    ) -> None:
        """Regression test: a release run with nothing to tag.

        Bug reported: the step exited 1 silently.
        Root cause: `grep -c .` exits 1 on no matches, fatal under `bash -e`.
        Fix: detection is its own step; publishing keys on it.
        """
        code, stdout, step_output = _run_release_step(
            TAG_DETECTION_STEP, repository, tmp_path
        )
        assert code == 0, f"nothing to release must not fail the job: {stdout}"
        assert "tag=\n" in step_output
        assert "none" in stdout

    def test_a_semver_tag_at_head_is_reported(
        self, repository: Path, tmp_path: Path
    ) -> None:
        """The output every publishing step keys on."""
        _git(repository, "tag", "v1.2.3")
        code, _, step_output = _run_release_step(
            TAG_DETECTION_STEP, repository, tmp_path
        )
        assert code == 0
        assert "tag=v1.2.3\n" in step_output

    def test_a_tag_that_is_not_a_release_is_ignored(
        self, repository: Path, tmp_path: Path
    ) -> None:
        """`nightly` at HEAD must not be handed to `gh release create`."""
        _git(repository, "tag", "nightly")
        code, _, step_output = _run_release_step(
            TAG_DETECTION_STEP, repository, tmp_path
        )
        assert code == 0
        assert "tag=\n" in step_output

    def test_the_newest_of_several_tags_at_head_is_the_one_released(
        self, repository: Path, tmp_path: Path
    ) -> None:
        """Several tags on one commit is a rerun or a hand-cut tag. These three
        separate every order that could be used: refname order answers v0.1.0,
        text order v0.9.0, and only version order the unreleased v0.10.0."""
        released = "v0.10.0"
        others = ("v0.1.0", "v0.9.0")
        for name in (*others, released):
            _git(repository, "tag", name)

        code, stdout, step_output = _run_release_step(
            TAG_DETECTION_STEP, repository, tmp_path
        )

        assert code == 0, stdout
        assert f"tag={released}\n" in step_output
        # One line: the tags are newline-separated, and a warning interpolating
        # them renders as several lines with only the first one flagged.
        (warning,) = [line for line in stdout.splitlines() if "WARNING" in line]
        assert all(name in warning for name in others), warning


class TestReleaseIsCutFromTheValidatedCommit:
    """The release guard, executed rather than read."""

    def test_the_validated_commit_is_released_from_a_branch_named_main(
        self, published_repository: Path, tmp_path: Path
    ) -> None:
        """semantic-release pushes the branch it stands on, and checkout detached it."""
        head = _git(published_repository, "rev-parse", "HEAD")
        _git(published_repository, "checkout", "--quiet", "--detach", head)

        code, output, step_output = _run_step(
            RELEASE,
            "release",
            MERGE_RACE_GUARD_STEP,
            repository=published_repository,
            tmp_path=tmp_path,
            environment={"VALIDATED_SHA": head},
        )

        assert code == 0, output
        assert "current=true\n" in step_output
        assert _git(published_repository, "rev-parse", "HEAD") == head
        assert _git(published_repository, "rev-parse", "--abbrev-ref", "HEAD") == "main"

    def test_a_commit_that_has_been_overtaken_is_not_released(
        self, published_repository: Path, tmp_path: Path
    ) -> None:
        """Regression test: a merge race failed the release workflow.

        Bug reported: a red workflow with nothing wrong.
        Root cause: the overtaken run exited 1, though the newer one releases both.
        Fix: it reports the skip as a step output.
        """
        superseded = _git(published_repository, "rev-parse", "HEAD")
        overtaking = _commit(published_repository, "landed while CI ran")
        _git(published_repository, "push", "--quiet", "origin", "main")
        _git(published_repository, "checkout", "--quiet", "--detach", superseded)

        code, output, step_output = _run_step(
            RELEASE,
            "release",
            MERGE_RACE_GUARD_STEP,
            repository=published_repository,
            tmp_path=tmp_path,
            environment={"VALIDATED_SHA": superseded},
        )

        assert code == 0, output
        assert "current=false\n" in step_output
        assert superseded in output and overtaking in output


class TestTheGitHubReleaseCarriesItsAsset:
    """The publishing step, executed against a stubbed `gh`.

    Releases here are immutable, so an asset cannot be attached afterwards: the
    notes and docker-compose.yml are right in the one `gh release create` this
    makes, or the release ships without them.
    """

    def _create_release(
        self, repository: Path, tmp_path: Path, stub: _StubbedGh, tag: str
    ) -> str:
        code, output, _ = _run_step(
            RELEASE,
            "release",
            RELEASE_CREATION_STEP,
            repository=repository,
            tmp_path=tmp_path,
            environment={**stub.environment, "NEW_TAG": tag},
        )
        assert code == 0, output
        return output

    def test_the_changelog_section_for_the_version_becomes_the_notes(
        self, repository: Path, tmp_path: Path
    ) -> None:
        """Unbounded, the extraction hands every past release's notes to this
        one. The heading it stops at is the next version's, not any `##`."""
        newest, previous = _changelog_sections()[:2]
        assert newest.bullet != previous.bullet, "the two sections read alike"
        _give_the_repository_its_changelog(repository)
        stub = _stub_gh(tmp_path, release_exists=False)

        self._create_release(repository, tmp_path, stub, newest.tag)

        created = _sole_call(stub, "release create")
        assert created.startswith(f"release create {newest.tag} --title {newest.tag} ")
        assert "--notes-file" in created
        assert created.endswith("docker-compose.yml")
        assert [
            call for call in stub.calls() if call.startswith("release delete")
        ] == []
        notes = stub.notes.read_text(encoding="utf-8")
        assert newest.bullet in notes
        assert previous.bullet not in notes

    def test_a_version_with_no_changelog_section_still_gets_its_release(
        self, repository: Path, tmp_path: Path
    ) -> None:
        """The tag is already public by the time this runs, so an unreadable
        changelog cannot be what fails it. Generated notes instead."""
        unreleased = "v9999.0.0"
        _give_the_repository_its_changelog(repository)
        stub = _stub_gh(tmp_path, release_exists=False)

        output = self._create_release(repository, tmp_path, stub, unreleased)

        created = _sole_call(stub, "release create")
        assert created.startswith(f"release create {unreleased} --title {unreleased} ")
        assert "--generate-notes" in created
        assert "--notes-file" not in created
        assert created.endswith("docker-compose.yml")
        assert not stub.notes.exists()
        assert "falling back" in output

    def test_a_release_a_partial_run_left_behind_is_recreated(
        self, repository: Path, tmp_path: Path
    ) -> None:
        """A rerun after the push landed and the creation failed. The asset can
        only go on at creation, so the existing release is deleted first — and
        its tag is kept, since the push that made it public already happened."""
        (newest, *_) = _changelog_sections()
        _give_the_repository_its_changelog(repository)
        stub = _stub_gh(tmp_path, release_exists=True)

        self._create_release(repository, tmp_path, stub, newest.tag)

        deleted = _sole_call(stub, "release delete")
        assert deleted.startswith(f"release delete {newest.tag} ")
        assert "--cleanup-tag=false" in deleted
        assert stub.calls().index(deleted) < stub.calls().index(
            _sole_call(stub, "release create")
        )


class TestTheGitFixturesIgnoreTheDevelopersConfiguration:
    """Whether these tests pass must not depend on whose machine runs them.

    A maintainer's `commit.gpgsign` or `core.hooksPath` reaches every fixture
    here through the inherited environment, and both can fail a plain commit.
    """

    @pytest.fixture
    def hostile_global_configuration(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Point this process's git at hooks that refuse every commit and checkout."""
        hooks = tmp_path / "hostile-hooks"
        hooks.mkdir()
        for hook in ("pre-commit", "post-checkout"):
            (hooks / hook).write_text(
                f"#!/bin/sh\necho {_HOSTILE_HOOK_MARKER} >&2\nexit 1\n",
                encoding="utf-8",
            )
            (hooks / hook).chmod(0o755)
        configuration = tmp_path / "hostile.gitconfig"
        configuration.write_text(f"[core]\n\thooksPath = {hooks}\n", encoding="utf-8")
        for variable in GIT_ISOLATION:
            monkeypatch.setenv(variable, str(configuration))

    @pytest.mark.parametrize(
        "command",
        [["commit", "--allow-empty", "-m", "probe"], ["switch", "-C", "probe"]],
        ids=["commit", "switch"],
    )
    def test_the_hostile_configuration_reaches_a_plain_git(
        self,
        repository: Path,
        hostile_global_configuration: None,
        command: list[str],
    ) -> None:
        """The anchor. Without it the two below pass against any environment,
        including one where nothing was ever isolated."""
        refused = subprocess.run(
            ["git", "-c", "user.email=t@example.test", "-c", "user.name=T", *command],
            cwd=repository,
            capture_output=True,
            text=True,
        )

        assert refused.returncode != 0
        assert _HOSTILE_HOOK_MARKER in refused.stderr

    def test_the_commit_helper_is_unaffected(
        self, repository: Path, hostile_global_configuration: None
    ) -> None:
        """`_commit` builds the history every guard test reasons about."""
        landed = _commit(repository, "landed anyway")

        assert _git(repository, "rev-parse", "HEAD") == landed

    def test_the_step_runner_is_unaffected(
        self,
        published_repository: Path,
        tmp_path: Path,
        hostile_global_configuration: None,
    ) -> None:
        """The release guard switches branches, and a `post-checkout` hook's exit
        status becomes the command's."""
        head = _git(published_repository, "rev-parse", "HEAD")
        _git(published_repository, "checkout", "--quiet", "--detach", head)

        code, output, step_output = _run_step(
            RELEASE,
            "release",
            MERGE_RACE_GUARD_STEP,
            repository=published_repository,
            tmp_path=tmp_path,
            environment={"VALIDATED_SHA": head},
        )

        assert code == 0, output
        assert _HOSTILE_HOOK_MARKER not in output
        assert "current=true\n" in step_output


class TestOnlyMainCanBePublished:
    """A tag is pushable onto any commit; the guard is what narrows that."""

    def test_the_tag_shape_is_read_from_the_ref_that_was_pushed(self) -> None:
        """A guard reading some other variable would validate nothing."""
        assert (
            _step_named(DOCKER, "guard", "Validate semver tag")["env"]["TAG"]
            == "${{ github.ref_name }}"
        )
        assert (
            _step_named(DOCKER, "guard", ALIAS_DECISION_STEP)["env"]["TAG"]
            == "${{ github.ref_name }}"
        )

    @pytest.mark.parametrize("tag", ["v1.2.3", "v0.29.0", "v10.0.0"])
    def test_a_release_tag_is_accepted(self, tag: str, tmp_path: Path) -> None:
        """The shape every later step reads as vMAJOR.MINOR.PATCH."""
        code, output, _ = _run_step(
            DOCKER,
            "guard",
            "Validate semver tag",
            repository=tmp_path,
            tmp_path=tmp_path,
            environment={"TAG": tag},
        )
        assert code == 0, output

    @pytest.mark.parametrize(
        "tag", ["vtest", "v1.2", "v1.2.3.4", "v1.2.3-rc1", "1.2.3", "v01.2.3x"]
    )
    def test_anything_else_is_refused(self, tag: str, tmp_path: Path) -> None:
        """`on.push.tags: v*` passes all of these through to the job."""
        code, output, _ = _run_step(
            DOCKER,
            "guard",
            "Validate semver tag",
            repository=tmp_path,
            tmp_path=tmp_path,
            environment={"TAG": tag},
        )
        assert code == 1, output
        assert tag.strip() in output

    def test_a_tag_on_main_is_allowed_through(
        self, published_repository: Path, tmp_path: Path
    ) -> None:
        """The ordinary semantic-release tag must still publish."""
        code, output, _ = _run_step(
            DOCKER,
            "guard",
            "Require the tagged commit to be on main",
            repository=published_repository,
            tmp_path=tmp_path,
            environment={"GITHUB_SHA": _git(published_repository, "rev-parse", "HEAD")},
        )
        assert code == 0, output

    def test_a_tag_on_a_commit_that_never_reached_main_is_refused(
        self, published_repository: Path, tmp_path: Path
    ) -> None:
        """`git tag v1.0.0` on a feature branch would otherwise publish to GHCR."""
        _git(published_repository, "checkout", "--quiet", "-b", "side")
        off_main = _commit(published_repository, "never merged")

        code, output, _ = _run_step(
            DOCKER,
            "guard",
            "Require the tagged commit to be on main",
            repository=published_repository,
            tmp_path=tmp_path,
            environment={"GITHUB_SHA": off_main},
        )

        assert code == 1, output
        assert off_main in output


class TestFloatingTagsOnlyMoveForward:
    """The alias decision, executed against real tag sets on real history.

    Tagging one commit repeatedly satisfies every descendancy check trivially,
    so a case about ordering gives each release a commit of its own.
    """

    def test_the_guard_fetches_every_tag_before_comparing_them(self) -> None:
        """Shallow, `git tag --list` returns the pushed tag alone and it wins."""
        assert _steps(DOCKER, "guard")[0]["with"]["fetch-depth"] == 0

    def test_the_decision_reads_a_ref_an_earlier_step_fetched(self) -> None:
        """The checkout leaves `origin/main` at whatever main tipped at when the
        job started, and main moves. The step before this one refreshes it, so
        reordered the decision counts releases against a stale ref, green."""
        fetched_by = "Require the tagged commit to be on main"

        assert re.search(
            rf"git fetch\b[^\n]*{re.escape(MAIN_TRACKING_REF)}",
            str(_step_named(DOCKER, "guard", fetched_by)["run"]),
        ), f"{fetched_by} does not fetch {MAIN_TRACKING_REF}"
        assert MAIN_TRACKING_REF in str(
            _step_named(DOCKER, "guard", ALIAS_DECISION_STEP)["run"]
        )
        assert _step_index(DOCKER, "guard", fetched_by) < _step_index(
            DOCKER, "guard", ALIAS_DECISION_STEP
        )

    @pytest.mark.parametrize(
        "annotated", [False, True], ids=["lightweight", "annotated"]
    )
    def test_the_newest_release_moves_every_alias(
        self, published_repository: Path, tmp_path: Path, annotated: bool
    ) -> None:
        """The ordinary release: latest, 0 and 0.29 all follow it.

        Annotated too, the shape python-semantic-release pushes. That pins
        `git tag --list --merged` peeling tag objects: without it RELEASES
        loses every annotated tag and no alias moves.
        """
        for name in ("v0.22.0", "v0.22.1", "v0.29.0"):
            _release(published_repository, name, annotated=annotated)
        assert _decide_aliases(published_repository, tmp_path, "v0.29.0") == {
            "highest_overall": "true",
            "highest_in_major": "true",
            "highest_in_minor": "true",
        }

    def test_the_first_release_of_all_moves_every_alias(
        self, published_repository: Path, tmp_path: Path
    ) -> None:
        """A repository whose only tag is the one being published.

        One commit is the whole of this case rather than a shortcut: there is no
        earlier release for it to descend from.
        """
        _release(published_repository, "v0.1.0")
        assert _decide_aliases(published_repository, tmp_path, "v0.1.0") == {
            "highest_overall": "true",
            "highest_in_major": "true",
            "highest_in_minor": "true",
        }

    def test_a_backport_moves_only_the_line_it_belongs_to(
        self, published_repository: Path, tmp_path: Path
    ) -> None:
        """`0.22` follows the backport; `latest` and `0` stay on 0.29.

        The backport is tagged on the main commit between the two releases,
        which is the only shape the on-main guard admits.
        """
        _release(published_repository, "v0.22.0")
        backported = _land(published_repository, "the fix worth backporting")
        _release(published_repository, "v0.29.0")
        _git(published_repository, "tag", "v0.22.1", backported)

        assert _decide_aliases(published_repository, tmp_path, "v0.22.1") == {
            "highest_overall": "false",
            "highest_in_major": "false",
            "highest_in_minor": "true",
        }

    def test_re_pushing_a_superseded_tag_moves_nothing(
        self, published_repository: Path, tmp_path: Path
    ) -> None:
        """Recovering a failed publish must not hand users a downgrade."""
        for name in ("v0.22.0", "v0.22.1", "v0.29.0"):
            _release(published_repository, name)
        assert _decide_aliases(published_repository, tmp_path, "v0.22.0") == {
            "highest_overall": "false",
            "highest_in_major": "false",
            "highest_in_minor": "false",
        }

    def test_releases_are_ordered_by_version_and_not_by_text(
        self, published_repository: Path, tmp_path: Path
    ) -> None:
        """v0.9.1 is released after v0.10.0 and descends from it, so only the
        ordering refuses it `latest`. Sorted as text it tops its own scope and
        every descendancy check passes, dragging `latest` back off 0.10.0."""
        for name in ("v0.9.0", "v0.10.0", "v0.9.1"):
            _release(published_repository, name)
        assert _decide_aliases(published_repository, tmp_path, "v0.9.1") == {
            "highest_overall": "false",
            "highest_in_major": "false",
            "highest_in_minor": "true",
        }

    def test_a_major_line_is_matched_on_the_dot_and_not_on_any_character(
        self, published_repository: Path, tmp_path: Path
    ) -> None:
        """Unescaped, `^v1.` also matches v10.0.0 and the `1` alias never moves."""
        for name in ("v1.0.0", "v10.0.0"):
            _release(published_repository, name)
        decided = _decide_aliases(published_repository, tmp_path, "v1.0.0")
        assert decided["highest_overall"] == "false"
        assert decided["highest_in_major"] == "true"
        assert decided["highest_in_minor"] == "true"

    def test_a_minor_line_is_matched_on_the_dot_and_not_on_any_character(
        self, published_repository: Path, tmp_path: Path
    ) -> None:
        """The same escape one scope down, and the one this repository reaches:
        unescaped, `^v0.2.` also matches v0.29.0, so `0.2` sees a higher-sorting
        release than the tag being published and never moves again."""
        for name in ("v0.2.0", "v0.29.0"):
            _release(published_repository, name)

        decided = _decide_aliases(published_repository, tmp_path, "v0.2.0")

        assert decided["highest_in_minor"] == "true"
        assert decided["highest_overall"] == "false"
        assert decided["highest_in_major"] == "false"

    def test_an_alias_never_moves_to_a_commit_its_holder_never_reached(
        self, published_repository: Path, tmp_path: Path
    ) -> None:
        """Regression test: a tag on an old commit could take `latest`.

        Bug reported: found by audit, not exploited.
        Root cause: the guard ordered releases by tag name alone.
        Fix: the release must also descend from the alias's current holder.
        """
        stale = _git(published_repository, "rev-parse", "HEAD")
        _release(published_repository, "v0.29.0")
        _git(published_repository, "tag", "v9.0.0", stale)

        decided = _decide_aliases(published_repository, tmp_path, "v9.0.0")

        assert decided["highest_overall"] == "false"
        # `9` and `9.0` name no earlier release, so nothing is behind them to
        # walk back from and the new build is where they belong.
        assert decided["highest_in_major"] == "true"
        assert decided["highest_in_minor"] == "true"

    def test_a_second_release_on_that_commit_is_refused_too(
        self, published_repository: Path, tmp_path: Path
    ) -> None:
        """Regression test: two tags on one stale commit walked `latest` back.

        Bug reported: found by audit, not exploited.
        Root cause: the holder was read as the highest-named other release.
        Fix: descend from every other release in scope.
        """
        stale = _git(published_repository, "rev-parse", "HEAD")
        _release(published_repository, "v0.29.0")
        _git(published_repository, "tag", "v9.0.0", stale)
        _git(published_repository, "tag", "v9.0.1", stale)

        decided = _decide_aliases(published_repository, tmp_path, "v9.0.1")

        assert decided["highest_overall"] == "false"
        # The whole `9` line sits on that commit, so its two aliases are already
        # there and moving them along it walks nothing back.
        assert decided["highest_in_major"] == "true"
        assert decided["highest_in_minor"] == "true"

    def test_a_stale_release_after_a_refused_one_is_refused_too(
        self, published_repository: Path, tmp_path: Path
    ) -> None:
        """Regression test: a stale release took `latest` after a refusal.

        Bug reported: found by audit, not exploited.
        Root cause: on a linear main, that holder is just the newest other tag.
        Fix: descend from every other release in scope.
        """
        first_stale = _git(published_repository, "rev-parse", "HEAD")
        second_stale = _land(published_repository, "a little newer, still stale")
        _release(published_repository, "v0.29.0")
        _git(published_repository, "tag", "v9.0.0", first_stale)
        _git(published_repository, "tag", "v9.0.1", second_stale)

        assert _decide_aliases(published_repository, tmp_path, "v9.0.1") == {
            "highest_overall": "false",
            "highest_in_major": "true",
            "highest_in_minor": "true",
        }

    def test_a_major_alias_does_not_follow_a_tag_cut_off_the_line(
        self, published_repository: Path, tmp_path: Path
    ) -> None:
        """The same defect one scope down: `v1.2.0` cut from v1.0.0's commit
        outranks v1.1.0 by name and would drag `1` back onto a build missing it."""
        first = _git(published_repository, "rev-parse", "HEAD")
        _git(published_repository, "tag", "v1.0.0", first)
        _release(published_repository, "v1.1.0")
        _git(published_repository, "tag", "v1.2.0", first)

        decided = _decide_aliases(published_repository, tmp_path, "v1.2.0")

        assert decided["highest_overall"] == "false"
        assert decided["highest_in_major"] == "false"
        assert decided["highest_in_minor"] == "true"

    def test_a_tag_that_never_reached_main_holds_no_alias(
        self, published_repository: Path, tmp_path: Path
    ) -> None:
        """Regression test: an unpublished tag could freeze `latest` forever.

        Bug reported: found by audit, not exploited.
        Root cause: every semver tag counted, including one pushed onto a
        feature branch that published nothing.
        Fix: only tags reachable from `origin/main` count.
        """
        _release(published_repository, "v0.29.0")
        _git(published_repository, "checkout", "--quiet", "-b", "side")
        _git(
            published_repository,
            "tag",
            "v0.29.1",
            _commit(published_repository, "tagged but never merged"),
        )
        _git(published_repository, "checkout", "--quiet", "main")
        _release(published_repository, "v0.30.0")

        assert _decide_aliases(published_repository, tmp_path, "v0.30.0") == {
            "highest_overall": "true",
            "highest_in_major": "true",
            "highest_in_minor": "true",
        }

    def test_tags_that_are_not_releases_do_not_hold_an_alias_back(
        self, published_repository: Path, tmp_path: Path
    ) -> None:
        """A prerelease sorts above the release it precedes; neither is a release.

        Both sit on a later commit than the release being published, so a parse
        that let either through would refuse the alias twice over.
        """
        _release(published_repository, "v0.29.0")
        later = _land(published_repository, "unreleased work")
        for name in ("nightly", "v1.0.0-rc1"):
            _git(published_repository, "tag", name, later)
        assert _decide_aliases(published_repository, tmp_path, "v0.29.0") == {
            "highest_overall": "true",
            "highest_in_major": "true",
            "highest_in_minor": "true",
        }

    def test_every_alias_freezes_when_the_main_ref_is_missing(
        self, repository: Path, tmp_path: Path
    ) -> None:
        """Fail closed, and nothing held it. `git tag --list --merged` exits 128
        with no `origin/main`, `|| true` swallows that, and the tag being
        published is then absent from its own scope — so nothing moves."""
        _git(repository, "tag", "v0.29.0")

        assert _decide_aliases(repository, tmp_path, "v0.29.0") == {
            "highest_overall": "false",
            "highest_in_major": "false",
            "highest_in_minor": "false",
        }

    def test_a_refusal_names_the_registry_alias_that_stayed_put(
        self, published_repository: Path, tmp_path: Path
    ) -> None:
        """The message is the only artefact a refusal leaves, and what did not
        move is a registry tag: `docker pull` asks for `0.22`, never `v0.22`."""
        for name in ("v0.22.0", "v0.22.1"):
            _release(published_repository, name)

        _code, output, _ = _run_step(
            DOCKER,
            "guard",
            ALIAS_DECISION_STEP,
            repository=published_repository,
            tmp_path=tmp_path,
            environment={"TAG": "v0.22.0"},
        )

        assert "latest stays put" in output
        assert "0.22 stays put" in output
        assert "v0.22 stays put" not in output
        assert "v0 stays put" not in output
