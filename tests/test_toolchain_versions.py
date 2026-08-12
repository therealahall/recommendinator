"""Toolchain versions, held equal wherever they are written down.

Nothing makes a workflow and a Dockerfile read one variable. Only setup-python
and setup-node steps are read: setup-uv's input, a `container:` and the runner
default are not.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tomllib
from collections.abc import Callable
from functools import partial
from itertools import chain
from pathlib import Path
from typing import Any

import pytest
from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from packaging.utils import canonicalize_name
from packaging.version import Version

from tests.image_layout import pulled_versions
from tests.workflow_layout import WORKFLOWS, workflow_files, workflow_jobs

# parents[1] resolves /tests/test_toolchain_versions.py -> repo root.
_REPO_ROOT = Path(__file__).resolve().parents[1]

PYTHON_PIN = _REPO_ROOT / ".python-version"
PYPROJECT = _REPO_ROOT / "pyproject.toml"
LOCK = _REPO_ROOT / "uv.lock"
MAKEFILE = _REPO_ROOT / "Makefile"
DOCKERFILE = _REPO_ROOT / "Dockerfile"
PACKAGE_JSON = _REPO_ROOT / "package.json"

# Spelled out rather than derived: a workflow that stops provisioning would
# drop silently out of the sweep.
PYTHON_WORKFLOWS = {"quality-gate.yml", "release.yml"}
NODE_WORKFLOWS = {"quality-gate.yml"}

# A new image here is a toolchain version nobody has decided how to hold still.
PULLED_IMAGES = {"python", "node", "ghcr.io/astral-sh/uv"}

_MAKEFILE_INTERPRETER = re.compile(
    r"^PYTHON \?= python(?P<version>\d+\.\d+)$", re.MULTILINE
)
_COREPACK = re.compile(r"corepack prepare (?P<manager>\S+) --activate")


def _pinned_python() -> str:
    """The pin every other site must name, patch level and all."""
    return PYTHON_PIN.read_text(encoding="utf-8").strip()


def _minor(version: str) -> str:
    """`3.11` from `3.11` and from `3.11.10` alike."""
    major, minor = version.split(".")[:2]
    return f"{major}.{minor}"


def _pinned_minor() -> str:
    return _minor(_pinned_python())


def _neighbouring_minors() -> tuple[str, str]:
    """Either side of the pinned minor, neither of them supported."""
    major, minor = (int(part) for part in _pinned_minor().split("."))
    return f"{major}.{minor - 1}.0", f"{major}.{minor + 1}.0"


def _probe_versions() -> list[str]:
    """uv writes `==3.11.*` for `>=3.11,<3.12`, so the two spellings are
    compared by what they admit rather than character for character."""
    previous, following = _neighbouring_minors()
    return [
        previous,
        f"{_pinned_minor()}.0",
        _pinned_python(),
        f"{_pinned_minor()}.99",
        following,
    ]


def _pyproject() -> dict[str, Any]:
    parsed: dict[str, Any] = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    return parsed


def _sole_match(pattern: re.Pattern[str], path: Path) -> re.Match[str]:
    """Fails naming the file when *path* does not match *pattern* exactly once."""
    matches = list(pattern.finditer(path.read_text(encoding="utf-8")))
    assert (
        len(matches) == 1
    ), f"{path.name} has {len(matches)} lines matching {pattern.pattern}"
    return matches[0]


def _provisioned(
    action: str, field: str, directory: Path = WORKFLOWS
) -> dict[str, list[str]]:
    """A step naming no version reads the pin file, so it lands here as a
    workflow with nothing recorded under it rather than one that disagrees."""
    provisioned: dict[str, list[str]] = {}
    for path in workflow_files(directory):
        inputs = [
            step.get("with") or {}
            for job in workflow_jobs(path).values()
            for step in job.get("steps", [])
            if str(step.get("uses", "")).startswith(action)
        ]
        if inputs:
            provisioned[path.name] = [
                str(given[field]) for given in inputs if field in given
            ]
    return provisioned


def _dockerfile_versions() -> dict[str, list[str]]:
    versions = pulled_versions(DOCKERFILE)
    assert set(versions) == PULLED_IMAGES, versions
    return versions


class TestTheSupportedInterpreterIsOneMinor:
    """One interpreter is provisioned and tested, so one minor is claimed."""

    def test_requires_python_admits_the_pinned_minor_and_no_later_one(self) -> None:
        """A bare `>=3.11` advertised 3.12 and 3.13, which nothing here runs; a
        floor above the pin would refuse the interpreter every site provisions."""
        requires = SpecifierSet(_pyproject()["project"]["requires-python"])
        previous, following = _neighbouring_minors()

        assert requires.contains(_pinned_python())
        assert requires.contains(f"{_pinned_minor()}.99")
        assert not requires.contains(previous)
        assert not requires.contains(following)

    def test_the_lockfile_resolves_against_that_same_minor(self) -> None:
        """The lock decides what installs, and only `uv sync --locked` compares
        the two. `make check` never runs it, so a requires-python edit without
        `make lock` is green locally and red on the pull request."""
        locked: dict[str, Any] = tomllib.loads(LOCK.read_text(encoding="utf-8"))
        resolved = SpecifierSet(locked["requires-python"])
        requires = SpecifierSet(_pyproject()["project"]["requires-python"])

        for version in _probe_versions():
            assert resolved.contains(version) == requires.contains(version), version

    @pytest.mark.parametrize("patch", ["", ".10"])
    def test_a_pin_naming_a_patch_level_still_names_its_minor(self, patch: str) -> None:
        """Every site below names a minor, and a pin naming a patch level must
        not be what stands in the way of one."""
        assert _minor(f"{_pinned_minor()}{patch}") == _pinned_minor()


class TestTheToolchainVersionsAgree:
    def test_every_site_provisions_the_pinned_interpreter(self) -> None:
        """A gate on one minor and an image on another ships what CI never ran.

        Two stages, because the runtime copies the builder's virtualenv.
        """
        minor = _pinned_minor()
        provisioned = _provisioned("actions/setup-python", "python-version")
        named = set(chain.from_iterable(provisioned.values()))

        assert _sole_match(_MAKEFILE_INTERPRETER, MAKEFILE)["version"] == minor
        assert PYTHON_WORKFLOWS <= set(
            provisioned
        ), f"stopped provisioning: {PYTHON_WORKFLOWS - set(provisioned)}"
        assert named <= {minor}, named
        assert _dockerfile_versions()["python"] == [minor, minor]

    def test_the_checkers_target_the_pinned_interpreter(self) -> None:
        """Aimed at another minor, they accept or reject the wrong syntax."""
        minor = _pinned_minor()
        short = f"py{minor.replace('.', '')}"
        tools = _pyproject()["tool"]

        assert tools["mypy"]["python_version"] == minor
        assert tools["black"]["target-version"] == [short]
        assert tools["ruff"]["target-version"] == short

    def test_the_frontend_is_built_on_the_node_the_gate_checks_it_with(self) -> None:
        """vue-tsc green on one and red on the other is a publish nobody saw fail.

        Sets, because how often each side names its node is a build's business.
        """
        provisioned = _provisioned("actions/setup-node", "node-version")

        assert NODE_WORKFLOWS <= set(
            provisioned
        ), f"stopped provisioning: {NODE_WORKFLOWS - set(provisioned)}"
        assert set(chain.from_iterable(provisioned.values())) == set(
            _dockerfile_versions()["node"]
        )

    def test_the_image_activates_the_package_manager_package_json_declares(
        self,
    ) -> None:
        """corepack would otherwise install a pnpm the lockfile was not written by."""
        declared = json.loads(PACKAGE_JSON.read_text(encoding="utf-8"))[
            "packageManager"
        ]

        assert _sole_match(_COREPACK, DOCKERFILE)["manager"] == declared


# A minor no site here provisions, so a sweep that reads it says so.
_SETUP_PYTHON_STEP = """      - uses: actions/setup-python@v5
        with:
          python-version: "3.13"
"""


def _write_workflow(directory: Path, name: str, step: str) -> None:
    (directory / name).write_text(
        f"on: push\njobs:\n  check:\n    steps:\n{step}", encoding="utf-8"
    )


class TestTheSweepReadsWhateverGitHubWouldRun:
    """A workflow the sweep never opens provisions whatever it likes, green."""

    @pytest.mark.parametrize("name", ["nightly.yml", "nightly.yaml"])
    def test_a_workflow_of_either_extension_is_swept(
        self, tmp_path: Path, name: str
    ) -> None:
        """GitHub honours both, so globbing one leaves the other unchecked."""
        _write_workflow(tmp_path, name, _SETUP_PYTHON_STEP)

        assert _provisioned("actions/setup-python", "python-version", tmp_path) == {
            name: ["3.13"]
        }

    def test_a_step_naming_no_version_is_provisioning_and_not_a_crash(
        self, tmp_path: Path
    ) -> None:
        """With no `with:`, setup-python reads `.python-version` — the pin
        itself, and the one setup that cannot drift from it."""
        _write_workflow(tmp_path, "gate.yml", "      - uses: actions/setup-python@v5\n")

        assert _provisioned("actions/setup-python", "python-version", tmp_path) == {
            "gate.yml": []
        }

    @pytest.mark.parametrize(
        "content", ["", "kept: for reference\n"], ids=["empty", "no-jobs"]
    )
    def test_a_file_that_declares_no_jobs_is_passed_over(
        self, tmp_path: Path, content: str
    ) -> None:
        """Anything else kept in the directory would crash the sweep instead."""
        _write_workflow(tmp_path, "gate.yml", _SETUP_PYTHON_STEP)
        (tmp_path / "notes.yaml").write_text(content, encoding="utf-8")

        assert set(
            _provisioned("actions/setup-python", "python-version", tmp_path)
        ) == {"gate.yml"}


_MODULE = sys.modules[__name__]

# Only these three are held to naming no cause for the pin: a page about
# ChromaDB says its name on lines about other things.
_PIN_RATIONALE_DOCUMENTS = ("CLAUDE.md", "CONTRIBUTING.md", "docs/TROUBLESHOOTING.md")

# `Python 3.11`, `python3.11`, `**Python**: 3.11+`. The trailing `+` is the claim
# that matters most: it advertises every later minor at once.
_MINOR_CLAIM = re.compile(r"[Pp]ython\*{0,2}[:\s]*(?P<version>3\.\d+)(?P<open>\+)?")

# Nothing else here names hnswlib, and the pin is not its doing.
_CHROMADB_ATTRIBUTION = re.compile(r"chromadb|hnswlib", re.IGNORECASE)

# Written by the release tooling, so a claim in one is nobody's to edit.
_UNEDITABLE = frozenset({"CHANGELOG.md"})

# The shape the two enumerators had before they were merged: the directory, or a
# name for it, listed on the spot. Only one of them ever learnt `.yaml`.
_WORKFLOW_ENUMERATION = re.compile(
    r"workflows?\b[^\n]{0,24}\.(?:glob|rglob|iterdir)\(", re.IGNORECASE
)
_ENUMERATION = re.compile(r"\.(?:glob|rglob|iterdir)\(")
_SOLE_ENUMERATOR = "tests/workflow_layout.py"


def _tracked_files(suffix: str) -> list[tuple[str, Path]]:
    """What a clone receives, and no more."""
    listing = subprocess.run(
        ["git", "ls-files", "--cached", "-z"],
        cwd=_REPO_ROOT,
        capture_output=True,
        check=True,
        text=True,
    ).stdout
    found = [
        (name, _REPO_ROOT / name)
        for name in listing.split("\0")
        if name.endswith(suffix) and name not in _UNEDITABLE
    ]
    assert found, f"no tracked {suffix} file found; the sweep would be empty"
    return found


def _setup_python_step(version: str) -> str:
    return (
        "      - uses: actions/setup-python@v5\n"
        "        with:\n"
        f'          python-version: "{version}"\n'
    )


def _stage_workflows(directory: Path) -> Path:
    """This repository's own workflow files, where a test may add to them."""
    staged = directory / "staged"
    staged.mkdir()
    for path in workflow_files():
        shutil.copy(path, staged / path.name)
    return staged


@pytest.fixture
def swept_directory(monkeypatch: pytest.MonkeyPatch) -> Callable[[Path], None]:
    """The delivered assertions call `_provisioned` with no directory, so the
    default is bound at definition and only the module attribute can be moved.
    """

    def _point_at(staged: Path) -> None:
        monkeypatch.setattr(
            _MODULE, "_provisioned", partial(_provisioned, directory=staged)
        )

    return _point_at


class TestTheAgreementItselfFailsOnAWorkflowTheSweepAdmits:
    """The sweep reading a file is half of it; these run the real assertion."""

    def test_a_yaml_workflow_on_another_minor_fails_the_agreement(
        self, tmp_path: Path, swept_directory: Callable[[Path], None]
    ) -> None:
        """The whole point of globbing both extensions: `nightly.yaml` naming
        3.13 has to be a red suite, not a workflow nobody reads."""
        staged = _stage_workflows(tmp_path)
        _write_workflow(staged, "nightly.yaml", _SETUP_PYTHON_STEP)
        swept_directory(staged)

        with pytest.raises(AssertionError, match="3.13"):
            TestTheToolchainVersionsAgree().test_every_site_provisions_the_pinned_interpreter()

    def test_a_new_workflow_on_the_pinned_minor_is_welcome(
        self, tmp_path: Path, swept_directory: Callable[[Path], None]
    ) -> None:
        """Pinned to a set of workflow names, adding one would be a red suite
        and an edit here, for a workflow doing exactly the right thing."""
        staged = _stage_workflows(tmp_path)
        _write_workflow(staged, "nightly.yaml", _setup_python_step(_pinned_minor()))
        swept_directory(staged)

        TestTheToolchainVersionsAgree().test_every_site_provisions_the_pinned_interpreter()

    def test_a_known_workflow_that_stops_provisioning_fails_the_agreement(
        self, tmp_path: Path, swept_directory: Callable[[Path], None]
    ) -> None:
        """The other direction, and the reason the set is spelled out: a release
        job that stops setting up Python runs on whatever the runner ships."""
        staged = _stage_workflows(tmp_path)
        _write_workflow(staged, "release.yml", "      - uses: actions/checkout@v4\n")
        swept_directory(staged)

        with pytest.raises(AssertionError, match="stopped provisioning"):
            TestTheToolchainVersionsAgree().test_every_site_provisions_the_pinned_interpreter()


class TestEveryShapeOfStepThatNamesNoVersion:
    """The siblings of a missing `with:`, which read the pin file just the
    same."""

    @pytest.mark.parametrize(
        "step",
        [
            "      - uses: actions/setup-python@v5\n        with:\n",
            "      - uses: actions/setup-python@v5\n"
            "        with:\n"
            '          cache: "pip"\n',
        ],
        ids=["empty-with", "another-input"],
    )
    def test_the_workflow_still_counts_as_provisioning(
        self, tmp_path: Path, step: str
    ) -> None:
        """Dropped from the sweep, a gate reading the pin would look like a gate
        that had stopped setting Python up at all."""
        _write_workflow(tmp_path, "gate.yml", step)

        assert _provisioned("actions/setup-python", "python-version", tmp_path) == {
            "gate.yml": []
        }


class TestASecondNodeStageIsNotAVersionMismatch:
    """Compared as lists, a tools stage on the same node failed the agreement."""

    @pytest.fixture
    def extra_node_stage(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> Callable[[str], None]:
        def _append(version: str) -> None:
            copy = tmp_path / "Dockerfile"
            copy.write_text(
                f"{DOCKERFILE.read_text(encoding='utf-8')}\n"
                f"FROM node:{version}-slim AS second-frontend\n",
                encoding="utf-8",
            )
            monkeypatch.setattr(_MODULE, "DOCKERFILE", copy)

        return _append

    def test_a_second_stage_on_the_same_node_agrees(
        self, extra_node_stage: Callable[[str], None]
    ) -> None:
        """One node named twice is one node version, whatever each stage is for."""
        (node,) = set(pulled_versions(DOCKERFILE)["node"])
        extra_node_stage(node)

        TestTheToolchainVersionsAgree().test_the_frontend_is_built_on_the_node_the_gate_checks_it_with()

    def test_a_stage_on_another_node_still_fails(
        self, extra_node_stage: Callable[[str], None]
    ) -> None:
        """Sets must not have made the assertion agreeable to anything."""
        (node,) = set(pulled_versions(DOCKERFILE)["node"])
        extra_node_stage(str(int(node) + 2))

        with pytest.raises(AssertionError):
            TestTheToolchainVersionsAgree().test_the_frontend_is_built_on_the_node_the_gate_checks_it_with()


class TestThePinMayNameAPatchLevel:
    """CVE-2024-4032 is a live reason to pin one, and the old assertion held
    `requires-python` against the literal `>=<pin>,<3.12` — which a `3.11.10`
    pin fails against `>=3.11,<3.12`.
    """

    @pytest.fixture
    def patch_level_pin(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        pin = tmp_path / ".python-version"
        pin.write_text(f"{_pinned_minor()}.10\n", encoding="utf-8")
        monkeypatch.setattr(_MODULE, "PYTHON_PIN", pin)

    def test_every_assertion_that_reads_the_pin_stays_green(
        self, patch_level_pin: None
    ) -> None:
        interpreter = TestTheSupportedInterpreterIsOneMinor()
        agreement = TestTheToolchainVersionsAgree()

        interpreter.test_requires_python_admits_the_pinned_minor_and_no_later_one()
        interpreter.test_the_lockfile_resolves_against_that_same_minor()
        agreement.test_every_site_provisions_the_pinned_interpreter()
        agreement.test_the_checkers_target_the_pinned_interpreter()


class TestOneSweepReadsTheWorkflowDirectory:
    """Two enumerators drift: one of them learns `.yaml` and the other does not."""

    def test_nothing_else_enumerates_the_workflow_directory(self) -> None:
        offenders = [
            name
            for name, path in _tracked_files(".py")
            if name != _SOLE_ENUMERATOR
            and _WORKFLOW_ENUMERATION.search(path.read_text(encoding="utf-8"))
        ]

        assert not offenders, f"a second workflow enumerator: {offenders}"

    @pytest.mark.parametrize(
        # Spliced: this file is scanned too, and a whole one here would be
        # indistinguishable from the thing the guard forbids.
        "spelling",
        ["WORKFLOWS.glo" + 'b("*.yml")', '(root / "workflows").iter' + "dir()"],
    )
    def test_the_guard_above_recognises_the_shape_it_forbids(
        self, spelling: str
    ) -> None:
        """Both spellings the merged enumerators used, or it forbids nothing."""
        assert _WORKFLOW_ENUMERATION.search(spelling)

    def test_the_sole_enumerator_reads_both_extensions_github_runs(self) -> None:
        assert {path.suffix for path in workflow_files(WORKFLOWS)} <= {".yml", ".yaml"}
        assert _ENUMERATION.search(
            (_REPO_ROOT / _SOLE_ENUMERATOR).read_text(encoding="utf-8")
        )

    def test_a_directory_holding_neither_extension_is_a_failure(
        self, tmp_path: Path
    ) -> None:
        """An empty sweep passes every assertion built on it."""
        (tmp_path / "README.md").write_text("nothing here\n", encoding="utf-8")

        with pytest.raises(AssertionError, match="no workflow files"):
            workflow_files(tmp_path)


class TestNoDocumentClaimsAnInterpreterThePackageRefuses:
    """`requires-python` refuses every minor but the pinned one, and a reader
    following a page that says otherwise gets a resolution error."""

    def test_no_page_names_a_minor_the_project_does_not_support(self) -> None:
        """`3.11+` advertised 3.12 and 3.13 in the same breath as the pin."""
        claims = [
            f"{name}:{number}: {claim.group()}"
            for name, path in _tracked_files(".md")
            for number, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), start=1
            )
            for claim in _MINOR_CLAIM.finditer(line)
            if claim["version"] != _pinned_minor() or claim["open"]
        ]

        assert not claims, f"{len(claims)} unsupported claim(s): {claims}"

    @pytest.mark.parametrize("document", _PIN_RATIONALE_DOCUMENTS)
    def test_no_page_attributes_the_pin_to_chromadb(self, document: str) -> None:
        """A declined CI matrix is the reason; ChromaDB ships abi3 wheels. The
        wrong cause sends whoever wants 3.12 off to fix the wrong thing."""
        attributed = [
            line
            for line in (_REPO_ROOT / document).read_text(encoding="utf-8").splitlines()
            if _CHROMADB_ATTRIBUTION.search(line) and _MINOR_CLAIM.search(line)
        ]

        assert not attributed, f"{document} blames ChromaDB: {attributed}"


# Enumerated, not swept: httpx reaches the suite through starlette's TestClient,
# so no scan of this tree's own imports would name it.
_IMPORTED_BY_THE_SUITE = frozenset({"httpx", "packaging", "pytest"})


class TestTheDevExtraDeclaresTheImportsNamedHere:
    def test_each_name_here_is_declared_in_the_dev_extra(self) -> None:
        declared = {
            canonicalize_name(Requirement(spec).name)
            for spec in _pyproject()["project"]["optional-dependencies"]["dev"]
        }

        assert _IMPORTED_BY_THE_SUITE <= declared, _IMPORTED_BY_THE_SUITE - declared

    @pytest.mark.parametrize("dropped", sorted(_IMPORTED_BY_THE_SUITE))
    def test_a_dev_extra_missing_one_fails(
        self, dropped: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Green against the delivered file proves nothing: each of these is
        installed anyway."""
        kept = [
            spec
            for spec in _pyproject()["project"]["optional-dependencies"]["dev"]
            if canonicalize_name(Requirement(spec).name) != dropped
        ]
        copy = tmp_path / "pyproject.toml"
        # json.dumps per spec: a marker carries quotes, and joining those by
        # hand emits TOML nothing can parse.
        copy.write_text(
            "[project]\n[project.optional-dependencies]\ndev = ["
            + ", ".join(json.dumps(spec) for spec in kept)
            + "]\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(_MODULE, "PYPROJECT", copy)

        with pytest.raises(AssertionError, match=dropped):
            self.test_each_name_here_is_declared_in_the_dev_extra()


class TestADeclarationMayCarryAnEnvironmentMarker:
    def test_the_missing_name_is_still_what_fails(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No dev spec carries a marker yet, so the rebuild above meets its
        first one the day someone adds it: joined by hand rather than encoded,
        its quotes make TOML tomllib refuses."""
        source = tmp_path / "declared"
        source.mkdir()
        (source / "pyproject.toml").write_text(
            "[project]\n[project.optional-dependencies]\n"
            "dev = ['tomli>=2.0; python_version < \"3.11\"', "
            '"httpx", "packaging", "pytest"]\n',
            encoding="utf-8",
        )
        monkeypatch.setattr(_MODULE, "PYPROJECT", source / "pyproject.toml")

        TestTheDevExtraDeclaresTheImportsNamedHere().test_a_dev_extra_missing_one_fails(
            "pytest", tmp_path, monkeypatch
        )


def _locked_versions() -> dict[str, str]:
    """What `uv sync` installs, under the names the lock normalises them to."""
    locked: dict[str, Any] = tomllib.loads(LOCK.read_text(encoding="utf-8"))
    return {
        canonicalize_name(package["name"]): package["version"]
        for package in locked["package"]
    }


def _declared_groups() -> dict[str, list[Requirement]]:
    """Whole groups rather than one merged list: a group emptied by an edit
    would otherwise be carried by whatever the others still declare."""
    project = _pyproject()["project"]
    groups: dict[str, list[str]] = {
        "dependencies": project["dependencies"],
        **project["optional-dependencies"],
    }
    return {
        name: [Requirement(spec) for spec in specs] for name, specs in groups.items()
    }


def _unmet(requirement: Requirement, resolved: str | None) -> str | None:
    """Prereleases are refused here and not left to `contains`, whose default
    reversed in packaging 25.0."""
    if resolved is None:
        return "no lock entry"
    if Version(resolved).is_prerelease and not requirement.specifier.prereleases:
        return f"lock has {resolved}, a prerelease no floor admits unless it names one"
    if requirement.specifier.contains(resolved, prereleases=True):
        return None
    return f"lock has {resolved}"


class TestTheLockResolvesEveryFloorTheDeclarationsAskFor:
    def test_every_declared_floor_is_met_by_the_locked_version(self) -> None:
        """A floor the lock does not resolve is green here, red on the pull request."""
        groups = _declared_groups()
        empty = sorted(name for name, declared in groups.items() if not declared)
        assert not empty, f"{empty} declares nothing to sweep"

        locked = _locked_versions()
        unmet: list[str] = []
        for declared in groups.values():
            for requirement in declared:
                failure = _unmet(
                    requirement, locked.get(canonicalize_name(requirement.name))
                )
                if failure is not None:
                    unmet.append(
                        f"{requirement.name}{requirement.specifier}: {failure}"
                    )

        assert not unmet, f"{len(unmet)} declaration(s) the lock does not meet: {unmet}"

    @pytest.mark.parametrize(
        ("declarations", "expected"),
        [
            pytest.param(
                'dependencies = ["cryptography>=999.0.0"]\n'
                "[project.optional-dependencies]\n"
                'dev = ["pytest"]\nai = ["ollama"]\n',
                "cryptography>=999.0.0: lock has ",
                id="floor-above-the-locked-version",
            ),
            pytest.param(
                'dependencies = ["not-in-the-lock>=1.0"]\n'
                "[project.optional-dependencies]\n"
                'dev = ["pytest"]\nai = ["ollama"]\n',
                "not-in-the-lock>=1.0: no lock entry",
                id="declared-and-never-locked",
            ),
            pytest.param(
                'dependencies = ["pytest"]\n'
                "[project.optional-dependencies]\n"
                'dev = []\nai = ["ollama"]\n',
                "['dev'] declares nothing to sweep",
                id="one-group-emptied",
            ),
            pytest.param(
                'dependencies = ["pytest"]\n'
                "[project.optional-dependencies]\n"
                'dev = ["pytest"]\nai = ["ollama"]\ndocs = ["not-in-the-lock>=1.0"]\n',
                "not-in-the-lock>=1.0: no lock entry",
                id="a-group-this-file-never-names",
            ),
        ],
    )
    def test_a_declaration_the_lock_does_not_meet_fails(
        self,
        declarations: str,
        expected: str,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Green against the delivered file proves only that `make lock` last ran."""
        copy = tmp_path / "pyproject.toml"
        copy.write_text("[project]\n" + declarations, encoding="utf-8")
        monkeypatch.setattr(_MODULE, "PYPROJECT", copy)

        with pytest.raises(AssertionError, match=re.escape(expected)):
            self.test_every_declared_floor_is_met_by_the_locked_version()

    def test_a_locked_prerelease_is_reported_as_one(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Refusing a prerelease is right, but reporting it as a version below
        the floor is not. The one that named a prerelease is admitted, or the
        count below would read 2."""
        copy = tmp_path / "pyproject.toml"
        copy.write_text(
            "[project]\n"
            'dependencies = ["stand-in>=1.0"]\n'
            "[project.optional-dependencies]\n"
            'dev = ["stand-in-dev>=1.0rc1"]\nai = ["stand-in-ai>=1.0"]\n',
            encoding="utf-8",
        )
        lock = tmp_path / "uv.lock"
        lock.write_text(
            '[[package]]\nname = "stand-in"\nversion = "2.0.0rc1"\n'
            '[[package]]\nname = "stand-in-dev"\nversion = "1.0rc1"\n'
            '[[package]]\nname = "stand-in-ai"\nversion = "1.0"\n',
            encoding="utf-8",
        )
        monkeypatch.setattr(_MODULE, "PYPROJECT", copy)
        monkeypatch.setattr(_MODULE, "LOCK", lock)

        with pytest.raises(
            AssertionError,
            match=re.escape(
                "1 declaration(s) the lock does not meet: "
                "['stand-in>=1.0: lock has 2.0.0rc1, a prerelease"
            ),
        ):
            self.test_every_declared_floor_is_met_by_the_locked_version()


class TestANameSpelledAnotherWayIsTheSamePackage:
    """`Stand_In` and `stand.in` install `stand-in`. Compared literally, either
    sweep misses a package that is right there."""

    def test_a_declared_import_spelled_another_way_counts_as_declared(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        copy = tmp_path / "pyproject.toml"
        copy.write_text(
            "[project]\n[project.optional-dependencies]\n"
            'dev = ["HTTPX>=0.27.0", "Packaging", "PyTest"]\n',
            encoding="utf-8",
        )
        monkeypatch.setattr(_MODULE, "PYPROJECT", copy)

        TestTheDevExtraDeclaresTheImportsNamedHere().test_each_name_here_is_declared_in_the_dev_extra()

    def test_a_floor_meets_a_lock_entry_spelled_another_way(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Neither spelling is the other's, so both sides must canonicalise."""
        copy = tmp_path / "pyproject.toml"
        copy.write_text(
            "[project]\n"
            'dependencies = ["stand_in>=1.0"]\n'
            "[project.optional-dependencies]\n"
            'dev = ["stand_in>=1.0"]\nai = ["stand_in>=1.0"]\n',
            encoding="utf-8",
        )
        lock = tmp_path / "uv.lock"
        lock.write_text(
            '[[package]]\nname = "Stand.In"\nversion = "1.0"\n', encoding="utf-8"
        )
        monkeypatch.setattr(_MODULE, "PYPROJECT", copy)
        monkeypatch.setattr(_MODULE, "LOCK", lock)

        TestTheLockResolvesEveryFloorTheDeclarationsAskFor().test_every_declared_floor_is_met_by_the_locked_version()
