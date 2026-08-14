"""Checks on the Makefile that CI and every contributor run the gate through.

The recipes are never executed — `make check` is the suite this file is part of.
`make --dry-run` against a scratch tree is.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

# parents[1] resolves /tests/test_makefile.py -> repo root.
_REPO_ROOT = Path(__file__).resolve().parents[1]
MAKEFILE = _REPO_ROOT / "Makefile"

# The two documents that told a contributor how to get a tree checked.
CONTRIBUTOR_DOCUMENTS = ("CONTRIBUTING.md", "QUICKSTART.md")

# The one rule whose target is a path on disk rather than a name. Everything
# else must be phony, or a file appearing under that name silently satisfies it.
FILE_TARGETS = {"node_modules/.make-install"}
(FRONTEND_STAMP,) = FILE_TARGETS

_TARGET = re.compile(r"^([A-Za-z0-9_][A-Za-z0-9_./-]*):(?!=)(?P<prerequisites>.*)$")
_PHONY = re.compile(r"^\.PHONY:(?P<targets>.*)$")


def _makefile_lines() -> list[str]:
    return MAKEFILE.read_text(encoding="utf-8").splitlines()


def _dry_run(*targets: str, working_directory: Path) -> str:
    """Return what `make` says it would run, having run none of it.

    MAKEFLAGS is dropped because this suite is usually reached through `make`,
    whose flags and command-line variables a child would otherwise inherit.
    """
    environment = {
        key: value
        for key, value in os.environ.items()
        if key not in {"MAKEFLAGS", "MFLAGS", "MAKELEVEL", "PYTHON"}
    }
    completed = subprocess.run(
        ["make", "--dry-run", "--file", str(MAKEFILE), *targets],
        cwd=working_directory,
        env=environment,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    return completed.stdout


def _frontend_tree(working_directory: Path) -> None:
    """The two files the install stamp is rebuilt from, and nothing else."""
    (working_directory / "package.json").write_text("{}\n", encoding="utf-8")
    (working_directory / "pnpm-lock.yaml").write_text("lockfileVersion: '9.0'\n")


def _installed_tree(working_directory: Path, *, age: float = 0.0) -> None:
    """Add the stamp a finished install leaves, *age* seconds older than now."""
    stamp = working_directory / FRONTEND_STAMP
    stamp.parent.mkdir(parents=True, exist_ok=True)
    stamp.touch()
    when = stamp.stat().st_mtime - age
    os.utime(stamp, (when, when))


def _targets() -> dict[str, list[str]]:
    targets = {
        match.group(1): match.group("prerequisites").split()
        for line in _makefile_lines()
        if (match := _TARGET.match(line))
    }
    assert targets, "no targets parsed; every assertion below would be empty"
    return targets


def _phony_targets() -> set[str]:
    declared: set[str] = set()
    for line in _makefile_lines():
        match = _PHONY.match(line)
        if match:
            declared.update(match.group("targets").split())
    assert declared, "no .PHONY declaration found"
    return declared


class TestPhonyDeclaration:
    """Every target that builds no file of its own name is declared phony."""

    def test_phony_covers_every_target_that_is_not_a_path(self) -> None:
        """A stray file named `check` otherwise makes the gate a silent no-op."""
        assert set(_targets()) - FILE_TARGETS == _phony_targets()

    def test_the_file_targets_are_not_declared_phony(self) -> None:
        """A phony install stamp would reinstall the frontend on every check."""
        assert not FILE_TARGETS & _phony_targets()


class TestFrontendBootstrap:
    """The frontend checks install their own dependencies when a tree has none."""

    def test_the_frontend_targets_wait_on_the_install(self) -> None:
        """node_modules is gitignored, so a fresh worktree starts without one."""
        targets = _targets()
        for target in ("check-frontend", "build-frontend", "install-frontend"):
            assert (
                FRONTEND_STAMP in targets[target]
            ), f"`make {target}` assumes an install"

    def test_the_install_is_rebuilt_from_the_lockfile_alone(self) -> None:
        """Both prerequisites, so a dependency change reinstalls and nothing else does."""
        assert _targets()[FRONTEND_STAMP] == ["package.json", "pnpm-lock.yaml"]

    def test_the_install_command_is_written_once(self) -> None:
        """A second copy is one `make install-frontend` can drift from what check runs."""
        text = MAKEFILE.read_text(encoding="utf-8")
        assert text.count("pnpm install --frozen-lockfile") == 1


class TestTheDocumentedGateIsThisOne:
    """A document that spells the gate out is a second copy of it."""

    @pytest.mark.parametrize("name", CONTRIBUTOR_DOCUMENTS)
    def test_no_contributor_document_runs_the_gate_by_hand(self, name: str) -> None:
        """Both drifted from `make check` once already, silently, for a release."""
        text = (_REPO_ROOT / name).read_text(encoding="utf-8")
        assert text.strip(), f"{name} is empty"
        assert "black --check" not in text, f"{name} spells the gate out"
        assert "pnpm install" not in text, f"{name} asks for an install make does"

    def test_the_gate_command_is_the_one_contributors_are_given(self) -> None:
        """`make check` unnamed, the section above forbids without instructing."""
        text = (_REPO_ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")
        assert "make check" in text


class TestInterpreterSelection:
    """CI installs into a virtualenv, so the interpreter has to be overridable."""

    def test_the_python_variable_is_overridable(self) -> None:
        """A plain `=` would ignore the interpreter CI hands make on the command
        line. Which version it defaults to belongs to
        tests/test_toolchain_versions.py, which ties it to `.python-version`."""
        assert any(line.startswith("PYTHON ?= python") for line in _makefile_lines())

    def test_no_recipe_hardcodes_the_interpreter(self) -> None:
        """One hardcoded python3.11 in CI runs the checks outside the synced venv."""
        recipes = [line for line in _makefile_lines() if line.startswith("\t")]
        assert recipes, "no recipe lines parsed"
        for line in recipes:
            assert "python3.11" not in line, f"recipe pins the interpreter: {line}"


class TestTheGateRunsWhateverTheTreeLooksLike:
    """`make` posed the trees the parse tests only describe."""

    def test_a_file_named_after_a_target_does_not_satisfy_the_gate(
        self, tmp_path: Path
    ) -> None:
        """One artefact named `format-check` otherwise drops black from the gate.

        Every declared target at once, because `check` alone proves nothing: its
        prerequisites are phony, which already forces it to be remade.
        """
        _frontend_tree(tmp_path)
        for target in _phony_targets():
            (tmp_path / target).write_text("", encoding="utf-8")

        printed = _dry_run("check", working_directory=tmp_path)

        for command in (
            "black --check",
            "ruff check",
            "mypy",
            "pytest",
            "vue-tsc",
            "vitest run",
        ):
            assert command in printed, f"`make check` skipped {command}: {printed}"

    def test_a_tree_with_no_node_modules_installs_before_it_type_checks(
        self, tmp_path: Path
    ) -> None:
        """A fresh worktree has none, and vue-tsc would be reported missing instead."""
        _frontend_tree(tmp_path)

        printed = _dry_run("check-frontend", working_directory=tmp_path)

        assert "pnpm install --frozen-lockfile" in printed
        assert printed.index("pnpm install") < printed.index("vue-tsc")

    def test_a_warm_tree_does_not_reinstall(self, tmp_path: Path) -> None:
        """The bootstrap has to cost nothing on every run after the first."""
        _frontend_tree(tmp_path)
        _installed_tree(tmp_path)
        stale = (tmp_path / FRONTEND_STAMP).stat().st_mtime - 60
        for name in ("package.json", "pnpm-lock.yaml"):
            os.utime(tmp_path / name, (stale, stale))

        printed = _dry_run("check-frontend", working_directory=tmp_path)

        assert "pnpm install" not in printed, printed
        assert "vue-tsc" in printed

    def test_a_changed_lockfile_reinstalls(self, tmp_path: Path) -> None:
        """A dependency bump has to reach the tree the checks then run against."""
        _frontend_tree(tmp_path)
        _installed_tree(tmp_path, age=60)

        printed = _dry_run("check-frontend", working_directory=tmp_path)

        assert "pnpm install --frozen-lockfile" in printed

    def test_an_interrupted_install_reinstalls_regression(self, tmp_path: Path) -> None:
        """Regression test: the gate type-checked a half-installed tree.

        Bug reported: vue-tsc ran against an incomplete node_modules.
        Root cause: the directory was the target, and pnpm creates it before it
        can fail.
        Fix: a stamp written only once pnpm succeeds.
        """
        _frontend_tree(tmp_path)
        (tmp_path / "node_modules").mkdir()

        printed = _dry_run("check-frontend", working_directory=tmp_path)

        assert "pnpm install --frozen-lockfile" in printed
