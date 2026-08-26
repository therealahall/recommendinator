"""Static checks on this repository's workflows.

A workflow only runs on GitHub, so wiring is read from parsed YAML. Every step
that decides something is instead executed under `bash -e`, the shell a `run:`
step gets.
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
from packaging.specifiers import SpecifierSet

# parents[1] resolves /tests/test_workflows.py -> repo root.
_REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = _REPO_ROOT / ".github" / "workflows"
CHANGELOG = _REPO_ROOT / "CHANGELOG.md"

# A release heading and a bullet under it, as python-semantic-release writes
# them. The release step's own extraction matches the same heading shape.
_VERSION_HEADING = re.compile(r"^## (?P<tag>v\d+\.\d+\.\d+) ", re.MULTILINE)
_CHANGELOG_BULLET = re.compile(r"^- \S.*$", re.MULTILINE)

DOCKER = WORKFLOWS / "docker.yml"
RELEASE = WORKFLOWS / "release.yml"
AUDIT = WORKFLOWS / "audit.yml"
DEPENDABOT = _REPO_ROOT / ".github" / "dependabot.yml"

# A surface with no entry here is one nothing ever bumps, and nothing says so.
PINNED_SURFACES = {
    "github-actions": WORKFLOWS,
    "uv": _REPO_ROOT / "uv.lock",
    "npm": _REPO_ROOT / "pnpm-lock.yaml",
    "docker": _REPO_ROOT / "Dockerfile",
}

# python-semantic-release cuts a release from a commit wearing one of these, so
# a weekly bump carrying one releases this project for someone else's patch.
RELEASE_TRIGGERING_TYPES = frozenset({"feat", "fix", "perf"})

# Comments are gone by the time YAML is parsed, and the version trailing a sha
# is a comment.
_ACTION_REFERENCE = re.compile(
    r"^\s*-?\s*uses:\s*(?P<ref>\S+)(?P<trailing>.*)$", re.MULTILINE
)

# Every spelling GitHub honours in a head commit's message. The tag points at
# the version commit, so a tag push is a push event carrying whichever is there.
CI_SKIP_MARKERS = (
    "[skip ci]",
    "[ci skip]",
    "[no ci]",
    "[skip actions]",
    "[actions skip]",
)

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


def _semantic_release_config() -> dict[str, Any]:
    """The `[tool.semantic_release]` table the release job runs under."""
    configured: dict[str, Any] = tomllib.loads(
        (_REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )["tool"]["semantic_release"]
    return configured


def _workflow_jobs(path: Path) -> dict[str, Any]:
    jobs: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))["jobs"]
    return jobs


def _triggers(path: Path) -> dict[str, Any]:
    """The workflow's `on:` block. YAML 1.1 reads the bare key as a boolean."""
    workflow: dict[Any, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))
    triggers: dict[str, Any] = workflow[True]
    return triggers


def _uses(job: dict[str, Any]) -> list[str]:
    if "uses" in job:
        return [str(job["uses"])]
    return [str(step["uses"]) for step in job["steps"] if "uses" in step]


def _called_transitively(entry: Path) -> set[Path]:
    """*entry* and every local reusable workflow its jobs reach from there."""
    reached = {entry}
    for job in _workflow_jobs(entry).values():
        called = str(job.get("uses", ""))
        if called.startswith("./"):
            reached |= _called_transitively(_REPO_ROOT / called.removeprefix("./"))
    return reached


def _dependency_updates() -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = yaml.safe_load(
        DEPENDABOT.read_text(encoding="utf-8")
    )["updates"]
    return entries


def _jobs_querying_an_advisory_feed(path: Path) -> set[str]:
    querying = set()
    for name, job in _workflow_jobs(path).items():
        for step in job.get("steps", []):
            # A whole word, so `requirements-audit.txt` stays an argument.
            words = str(step.get("run", "")).split()
            if "audit" in str(step.get("uses", "")).split("@")[0] or any(
                word == "audit" or word.endswith("-audit") for word in words
            ):
                querying.add(name)
    return querying


def _steps(path: Path, job: str) -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = _workflow_jobs(path)[job]["steps"]
    return steps


def _step_index(path: Path, job: str, name: str) -> int:
    for index, step in enumerate(_steps(path, job)):
        if step.get("name") == name:
            return index
    raise AssertionError(f"{path.name} job {job} has no step named {name!r}")


def _step_named(path: Path, job: str, name: str) -> dict[str, Any]:
    return _steps(path, job)[_step_index(path, job, name)]


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
    assert set(decided) == set(_workflow_jobs(DOCKER)["guard"]["outputs"]), decided
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


def _stub_gh(
    tmp_path: Path, *, release_exists: bool, assets: tuple[str, ...] = ()
) -> _StubbedGh:
    """Put a recording `gh` on PATH, ahead of any real one.

    Every `release view` answers the asset names the step's `--jq` shapes them
    into. A `--notes-file` is copied where a test can read it.
    """
    view_response = (
        "".join(f"  echo {name}\n" for name in assets) + "  exit 0\n"
        if release_exists
        else "  exit 1\n"
    )
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
        f"{view_response}"
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


class TestTheVersionCommitReachesTheReleaseJobAgain:
    """The tag points at the version commit, so a skip marker there skipped the
    tag push too: 58 releases shipped no image. The cost is one CI run per
    release, whose own release job must no-op.
    """

    def test_the_version_commit_message_carries_no_ci_skip_marker(self) -> None:
        """Regression test: 58 tags shipped no image.

        Bug reported: an anonymous GHCR pull answers NAME_UNKNOWN.
        Root cause: `[skip ci]` in the version commit GitHub sees the tag push at.
        Fix: no marker; every spelling GitHub honours is refused here.
        """
        message = _semantic_release_config().get("commit_message", "")

        assert message, "nothing configures the message this reads"
        for marker in CI_SKIP_MARKERS:
            assert marker not in message.lower(), message

    def test_pushing_a_commit_and_tag_that_are_already_there_is_not_a_failure(
        self, published_repository: Path, tmp_path: Path
    ) -> None:
        """The re-entry finds the tag already at HEAD and on origin, so this step
        pushes what is already there. Git answers "up to date"; anything else
        would end every release on a red job."""
        tag = "v1.2.3"
        _release(published_repository, tag)
        _git(published_repository, "push", "--quiet", "origin", f"refs/tags/{tag}")

        code, output, _ = _run_step(
            RELEASE,
            "release",
            "Push the version commit and tag",
            repository=published_repository,
            tmp_path=tmp_path,
            environment={"NEW_TAG": tag},
        )

        assert code == 0, output


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

    def test_a_release_a_partial_run_left_behind_is_recreated(
        self, repository: Path, tmp_path: Path
    ) -> None:
        """A rerun after the push landed and the creation failed. The asset can
        only go on at creation, so the existing release is deleted first — and
        its tag is kept, since the push that made it public already happened."""
        newest, *_ = _changelog_sections()
        _give_the_repository_its_changelog(repository)
        stub = _stub_gh(tmp_path, release_exists=True)

        self._create_release(repository, tmp_path, stub, newest.tag)

        deleted = _sole_call(stub, "release delete")
        assert deleted.startswith(f"release delete {newest.tag} ")
        assert "--cleanup-tag=false" in deleted
        assert stub.calls().index(deleted) < stub.calls().index(
            _sole_call(stub, "release create")
        )

    def test_a_release_that_already_carries_its_asset_is_left_alone(
        self, repository: Path, tmp_path: Path
    ) -> None:
        """The version commit's own CI run reaches this step a second time, by
        which point the release is whole. Recreating it notifies every watcher
        twice and reissues the asset's id, for a release identical to the one
        deleted."""
        newest, *_ = _changelog_sections()
        _give_the_repository_its_changelog(repository)
        stub = _stub_gh(tmp_path, release_exists=True, assets=("docker-compose.yml",))

        output = self._create_release(repository, tmp_path, stub, newest.tag)

        assert [
            call for call in stub.calls() if call.startswith("release delete")
        ] == []
        assert [
            call for call in stub.calls() if call.startswith("release create")
        ] == []
        assert "nothing to do" in output


class TestOnlyMainCanBePublished:
    """A tag is pushable onto any commit; the guard is what narrows that."""

    @pytest.mark.parametrize("tag", ["v1.2.3", "v10.0.0"])
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

    @pytest.mark.parametrize("tag", ["vtest", "v1.2", "v1.2.3-rc1", "1.2.3"])
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

    def test_the_action_is_told_not_to_apply_latest_on_its_own(self) -> None:
        metadata = _step_named(DOCKER, "publish", "Generate image metadata")
        fields = {
            field.strip()
            for line in str(metadata["with"]["flavor"]).splitlines()
            for field in line.split(",")
            if field.strip()
        }
        assert "latest=false" in fields, (
            "qs5i.9.8: metadata-action applies `latest` to every semver tag "
            "unless the flavor forbids it, so a backport moved GHCR's `:latest` "
            f"backwards and the next `docker compose pull` downgraded: {fields}"
        )

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


class TestTheDependencyAuditStaysOffTheReleasePath:
    """The audit reds a pull request. It must not withhold a published image."""

    def test_the_audit_runs_weekly_and_never_on_a_tag_push(self) -> None:
        published = {
            reached
            for workflow in sorted(WORKFLOWS.glob("*.yml"))
            if "tags" in (_triggers(workflow).get("push") or {})
            for reached in _called_transitively(workflow)
        }

        assert DOCKER in published, "no workflow publishes on a tag any more"
        assert AUDIT not in published
        # Pasting the audit's steps into the gate costs the same image.
        for workflow in published:
            assert not _jobs_querying_an_advisory_feed(workflow), workflow.name

        # Or the workflow is a green tick over an audit nobody runs.
        assert _jobs_querying_an_advisory_feed(AUDIT) == set(_workflow_jobs(AUDIT))
        assert "schedule" in _triggers(AUDIT)


class TestEverythingPinnedHereIsOfferedUpdates:
    def test_every_pinned_surface_present_in_the_tree_has_an_ecosystem(self) -> None:
        covered = {entry["package-ecosystem"] for entry in _dependency_updates()}

        for ecosystem, surface in PINNED_SURFACES.items():
            if surface.exists():
                assert ecosystem in covered, surface.name

    def test_no_ecosystem_bumps_under_a_type_that_cuts_a_release(self) -> None:
        for entry in _dependency_updates():
            assert (
                entry["commit-message"]["prefix"] not in RELEASE_TRIGGERING_TYPES
            ), entry["package-ecosystem"]


class TestEveryClaimedInterpreterIsOneCiRuns:
    def test_requires_python_and_the_gate_name_the_same_minors(self) -> None:
        declared = tomllib.loads(
            (_REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        )["project"]["requires-python"]
        claimed = SpecifierSet(declared)
        run = set()
        for job in _workflow_jobs(WORKFLOWS / "quality-gate.yml").values():
            matrix = job.get("strategy", {}).get("matrix", {})
            run |= {str(minor) for minor in matrix.get("python-version", [])}
            for step in job.get("steps", []):
                asked = str(step.get("with", {}).get("python-version", ""))
                if asked and "${{" not in asked:
                    run.add(asked)
        assert run, "no job in the gate installs a python"
        assert all(claimed.contains(f"{minor}.0") for minor in run), declared
        beyond = max(int(minor.split(".")[1]) for minor in run) + 1
        assert not claimed.contains(f"3.{beyond}.0"), f"3.{beyond} is claimed, not run"


class TestEveryActionRunsCodeThatCannotBeSwappedOut:
    def test_third_party_actions_are_pinned_to_a_sha_naming_its_version(self) -> None:
        """A mutable tag lets its owner run new code in a job holding
        `packages: write`, and the trailing version is what makes a sha
        reviewable."""
        for workflow in sorted(WORKFLOWS.glob("*.yml")):
            read = _ACTION_REFERENCE.findall(workflow.read_text(encoding="utf-8"))
            declared = [
                used for job in _workflow_jobs(workflow).values() for used in _uses(job)
            ]
            # A pattern that skipped a ref would pass the loop below vacuously.
            assert [reference for reference, _ in read] == declared, workflow.name

            for reference, trailing in read:
                if reference.startswith("./"):
                    continue
                assert re.fullmatch(
                    r"[^@]+@[0-9a-f]{40}", reference
                ), f"{workflow.name}: {reference}"
                assert re.fullmatch(
                    r"\s+#\s*\S+", trailing
                ), f"{workflow.name}: {reference} names no version"
