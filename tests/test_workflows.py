"""Static checks on the workflows that publish.

A workflow only runs on GitHub, so wiring is read from parsed YAML. Every step
that decides something — the release guard, the tag guards, the alias
decision — is instead executed under `bash -e`, the shell a `run:` step gets.
"""

from __future__ import annotations

import os
import posixpath
import re
import subprocess
import tomllib
from pathlib import Path
from typing import Any

import pytest
import yaml

# parents[1] resolves /tests/test_workflows.py -> repo root.
_REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = _REPO_ROOT / ".github" / "workflows"

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
# the merge-race guard writes.
RELEASED_TAG_CONDITION = "steps.released.outputs.tag != ''"
STILL_MAIN_CONDITION = "steps.validated.outputs.current == 'true'"

# Scopes nothing here consumes: attestations:write needs an attest-* action, and
# id-token:write mints an OIDC token identifying this repository. Buildkit's
# provenance and sbom travel under packages:write and want neither.
UNUSED_SCOPES = ("attestations", "id-token")

# Each floating tag and the one guard output entitled to enable it. The three
# are interchangeable to every other assertion here, and a swapped pair is
# indistinguishable from the bug they exist to prevent.
FLOATING_TAG_GUARDS = {
    ("raw", "latest"): "highest_overall",
    ("semver", "{{major}}"): "highest_in_major",
    ("semver", "{{major}}.{{minor}}"): "highest_in_minor",
}

DOCKERFILE = _REPO_ROOT / "Dockerfile"

# `COPY [flags] config/example.yaml <destination>` — the first-run seed, whose
# container path the PR smoke test has to name to read it back out of the image.
_SEED_COPY = re.compile(
    r"^COPY\s+(?:--\S+\s+)*config/example\.yaml\s+(?P<destination>\S+)\s*$",
    re.MULTILINE,
)
_WORKDIR = re.compile(r"^WORKDIR\s+(?P<path>\S+)\s*$", re.MULTILINE)

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


def _shipped_seed_path() -> str:
    """The container path `Dockerfile` copies the first-run seed to.

    Resolved against the WORKDIR in force at that COPY, since the destination
    is written relative.
    """
    source = DOCKERFILE.read_text(encoding="utf-8")
    copied = _SEED_COPY.search(source)
    assert copied is not None, "the Dockerfile copies no config/example.yaml"
    preceding = [
        match for match in _WORKDIR.finditer(source) if match.start() < copied.start()
    ]
    assert preceding, "no WORKDIR precedes the seed COPY"
    return posixpath.normpath(
        posixpath.join(preceding[-1].group("path"), copied.group("destination"))
    )


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
    )
    return completed.stdout.strip()


def _commit(repository: Path, message: str) -> str:
    """Add a commit and return its SHA."""
    (repository / "CHANGELOG.md").write_text(f"# {message}\n", encoding="utf-8")
    _git(repository, "commit", "--quiet", "--all", "-m", message)
    return _git(repository, "rev-parse", "HEAD")


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
        "Decide which floating tags this release may move",
        repository=repository,
        tmp_path=tmp_path,
        environment={"TAG": tag},
    )
    assert code == 0, output
    decided = dict(
        line.split("=", 1) for line in step_output.splitlines() if "=" in line
    )
    assert set(decided) == {
        "highest_overall",
        "highest_in_major",
        "highest_in_minor",
    }, decided
    return decided


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
        for path in _workflow_files():
            for job_name, job in _jobs(path).items():
                assert scope not in job.get(
                    "permissions", {}
                ), f"{path.name} job {job_name} grants unused {scope}"

    def test_the_publish_job_holds_only_what_it_pushes_with(self) -> None:
        """Pinning the block, so a scope added later is a decision rather than a drift."""
        assert _jobs(DOCKER)["publish"]["permissions"] == {
            "contents": "read",
            "packages": "write",
        }

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
        assert referenced == {_shipped_seed_path()}


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

    def test_release_checks_out_the_commit_ci_validated(self) -> None:
        """With no ref, a workflow_run job gets main's tip, which may be a later commit."""
        checkout = _steps(RELEASE, "release")[0]
        assert checkout["with"]["ref"] == "${{ github.event.workflow_run.head_sha }}"

    def test_nothing_releases_unless_the_validated_commit_is_still_main(self) -> None:
        """Otherwise semantic-release tags whatever overtook it, and the push
        step runs against the detached HEAD the checkout left behind."""
        guard = _step_named(RELEASE, "release", "Release only the commit CI validated")
        assert (
            guard["env"]["VALIDATED_SHA"] == "${{ github.event.workflow_run.head_sha }}"
        )
        assert guard["id"] == "validated"
        for name in ("Run semantic-release", "Identify the release tag"):
            assert (
                _step_named(RELEASE, "release", name)["if"] == STILL_MAIN_CONDITION
            ), name

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
        steps = _steps(RELEASE, "release")
        following = steps[
            _step_index(RELEASE, "release", "Identify the release tag") + 1 :
        ]

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

    def test_the_tagged_commit_must_be_on_main(self) -> None:
        """A `v1.0.0` pushed onto a feature branch is otherwise a published release."""
        guard = _step_named(DOCKER, "guard", "Require the tagged commit to be on main")
        assert "git merge-base --is-ancestor" in guard["run"]
        assert "exit 1" in guard["run"]

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
        """Sharing one, the AI variant's `latest` would overwrite the default's."""
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
            "Identify the release tag", repository, tmp_path
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
            "Identify the release tag", repository, tmp_path
        )
        assert code == 0
        assert "tag=v1.2.3\n" in step_output

    def test_a_tag_that_is_not_a_release_is_ignored(
        self, repository: Path, tmp_path: Path
    ) -> None:
        """`nightly` at HEAD must not be handed to `gh release create`."""
        _git(repository, "tag", "nightly")
        code, _, step_output = _run_release_step(
            "Identify the release tag", repository, tmp_path
        )
        assert code == 0
        assert "tag=\n" in step_output


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
            "Release only the commit CI validated",
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
            "Release only the commit CI validated",
            repository=published_repository,
            tmp_path=tmp_path,
            environment={"VALIDATED_SHA": superseded},
        )

        assert code == 0, output
        assert "current=false\n" in step_output
        assert superseded in output and overtaking in output


class TestOnlyMainCanBePublished:
    """A tag is pushable onto any commit; the guard is what narrows that."""

    def test_the_tag_shape_is_read_from_the_ref_that_was_pushed(self) -> None:
        """A guard reading some other variable would validate nothing."""
        assert (
            _step_named(DOCKER, "guard", "Validate semver tag")["env"]["TAG"]
            == "${{ github.ref_name }}"
        )
        assert (
            _step_named(
                DOCKER, "guard", "Decide which floating tags this release may move"
            )["env"]["TAG"]
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
    """The alias decision, executed against real tag sets."""

    def test_the_guard_fetches_every_tag_before_comparing_them(self) -> None:
        """Shallow, `git tag --list` returns the pushed tag alone and it wins."""
        assert _steps(DOCKER, "guard")[0]["with"]["fetch-depth"] == 0

    def test_the_newest_release_moves_every_alias(
        self, repository: Path, tmp_path: Path
    ) -> None:
        """The ordinary release: latest, 0 and 0.29 all follow it."""
        for name in ("v0.22.0", "v0.22.1", "v0.29.0"):
            _git(repository, "tag", name)
        assert _decide_aliases(repository, tmp_path, "v0.29.0") == {
            "highest_overall": "true",
            "highest_in_major": "true",
            "highest_in_minor": "true",
        }

    def test_the_first_release_of_all_moves_every_alias(
        self, repository: Path, tmp_path: Path
    ) -> None:
        """A repository whose only tag is the one being published."""
        _git(repository, "tag", "v0.1.0")
        assert _decide_aliases(repository, tmp_path, "v0.1.0") == {
            "highest_overall": "true",
            "highest_in_major": "true",
            "highest_in_minor": "true",
        }

    def test_a_backport_moves_only_the_line_it_belongs_to(
        self, repository: Path, tmp_path: Path
    ) -> None:
        """`0.22` follows the backport; `latest` and `0` stay on 0.29."""
        for name in ("v0.22.0", "v0.29.0", "v0.22.1"):
            _git(repository, "tag", name)
        assert _decide_aliases(repository, tmp_path, "v0.22.1") == {
            "highest_overall": "false",
            "highest_in_major": "false",
            "highest_in_minor": "true",
        }

    def test_re_pushing_a_superseded_tag_moves_nothing(
        self, repository: Path, tmp_path: Path
    ) -> None:
        """Recovering a failed publish must not hand users a downgrade."""
        for name in ("v0.22.0", "v0.22.1", "v0.29.0"):
            _git(repository, "tag", name)
        assert _decide_aliases(repository, tmp_path, "v0.22.0") == {
            "highest_overall": "false",
            "highest_in_major": "false",
            "highest_in_minor": "false",
        }

    def test_releases_are_ordered_by_version_and_not_by_text(
        self, repository: Path, tmp_path: Path
    ) -> None:
        """Sorted as text, v0.9.1 outranks v0.10.0 and drags latest backwards."""
        for name in ("v0.9.0", "v0.9.1", "v0.10.0"):
            _git(repository, "tag", name)
        decided = _decide_aliases(repository, tmp_path, "v0.9.1")
        assert decided["highest_overall"] == "false"
        assert decided["highest_in_minor"] == "true"

    def test_a_major_line_is_matched_on_the_dot_and_not_on_any_character(
        self, repository: Path, tmp_path: Path
    ) -> None:
        """Unescaped, `^v1.` also matches v10.0.0 and the `1` alias never moves."""
        for name in ("v1.0.0", "v10.0.0"):
            _git(repository, "tag", name)
        decided = _decide_aliases(repository, tmp_path, "v1.0.0")
        assert decided["highest_overall"] == "false"
        assert decided["highest_in_major"] == "true"
        assert decided["highest_in_minor"] == "true"

    def test_the_newest_release_moves_every_alias_across_commits_too(
        self, repository: Path, tmp_path: Path
    ) -> None:
        """Every other case here tags one commit repeatedly, where descendancy is
        trivially satisfied. The ordinary release is a commit ahead of the last
        one, and must still take all three."""
        _git(repository, "tag", "v0.28.0")
        _commit(repository, "the release after")
        _git(repository, "tag", "v0.29.0")

        assert _decide_aliases(repository, tmp_path, "v0.29.0") == {
            "highest_overall": "true",
            "highest_in_major": "true",
            "highest_in_minor": "true",
        }

    def test_an_alias_never_moves_to_a_commit_its_holder_never_reached(
        self, repository: Path, tmp_path: Path
    ) -> None:
        """Regression test: a tag on an old commit could take `latest`.

        Bug reported: found by audit, not exploited.
        Root cause: the guard ordered releases by tag name alone.
        Fix: the release must also descend from the alias's current holder.
        """
        stale = _git(repository, "rev-parse", "HEAD")
        _commit(repository, "everything since")
        _git(repository, "tag", "v0.29.0")
        _git(repository, "tag", "v9.0.0", stale)

        decided = _decide_aliases(repository, tmp_path, "v9.0.0")

        assert decided["highest_overall"] == "false"
        # `9` and `9.0` name no earlier release, so nothing is behind them to
        # walk back from and the new build is where they belong.
        assert decided["highest_in_major"] == "true"
        assert decided["highest_in_minor"] == "true"

    def test_a_major_alias_does_not_follow_a_tag_cut_off_the_line(
        self, repository: Path, tmp_path: Path
    ) -> None:
        """The same defect one scope down: `v1.2.0` cut from v1.0.0's commit
        outranks v1.1.0 by name and would drag `1` back onto a build missing it."""
        first = _git(repository, "rev-parse", "HEAD")
        _git(repository, "tag", "v1.0.0")
        _commit(repository, "the minor release")
        _git(repository, "tag", "v1.1.0")
        _git(repository, "tag", "v1.2.0", first)

        decided = _decide_aliases(repository, tmp_path, "v1.2.0")

        assert decided["highest_overall"] == "false"
        assert decided["highest_in_major"] == "false"
        assert decided["highest_in_minor"] == "true"

    def test_tags_that_are_not_releases_do_not_hold_an_alias_back(
        self, repository: Path, tmp_path: Path
    ) -> None:
        """A prerelease sorts above the release it precedes; neither is a release."""
        for name in ("v0.29.0", "nightly", "v1.0.0-rc1"):
            _git(repository, "tag", name)
        assert _decide_aliases(repository, tmp_path, "v0.29.0") == {
            "highest_overall": "true",
            "highest_in_major": "true",
            "highest_in_minor": "true",
        }
