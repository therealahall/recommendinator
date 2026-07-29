"""Guards on declared dependency floors that carry a security meaning.

``uv.lock`` pins exact versions for a locked install, but the floors in
``pyproject.toml`` are what a non-lock install (``pip install
recommendinator``) resolves against. A floor below a known CVE means that
install is vulnerable even though the lockfile is clean.

Four facts can regress independently, so each is asserted separately: the floor
declared in ``pyproject.toml``, the copy of that floor ``uv.lock`` records for
the workspace member, the exact version ``uv.lock`` resolves to (which is what
``uv sync --locked`` installs, and the only one of the four a floor cannot
guarantee), and the version actually importable in this environment.
"""

from __future__ import annotations

import re
import tomllib
from importlib.metadata import version
from pathlib import Path
from typing import Any

from packaging.version import Version

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_PYPROJECT = _PROJECT_ROOT / "pyproject.toml"
_LOCK = _PROJECT_ROOT / "uv.lock"

# A requirement string starts with the distribution name, which runs until the
# first extras bracket, comparison operator, or marker.
_REQUIREMENT_NAME = re.compile(r"^[A-Za-z0-9._-]+")

_PYTHON_MULTIPART_FLOOR = Version("0.0.18")


def _canonical(name: str) -> str:
    """Normalize a distribution name per PEP 503 so `_` and `-` compare equal."""
    return re.sub(r"[-_.]+", "-", name).lower()


def _load_toml(path: Path) -> dict[str, Any]:
    with path.open("rb") as handle:
        return tomllib.load(handle)


def _floor(spec: str, package: str) -> Version:
    """Return the ``>=`` bound in *spec*, which must declare one.

    *spec* is either a full requirement string or the bare specifier uv.lock
    records; both carry the version constraints in the same syntax.
    """
    match = re.search(r">=\s*([0-9][0-9.]*)", spec)
    assert match is not None, f"No >= floor declared for {package}: {spec!r}"
    return Version(match.group(1))


def _declared_floor(package: str) -> Version:
    """Return the ``>=`` floor declared for *package* in ``pyproject.toml``.

    Matches on the parsed distribution name rather than a string prefix, so a
    different package whose name merely starts with *package* cannot answer for
    it.
    """
    for requirement in _load_toml(_PYPROJECT)["project"]["dependencies"]:
        name = _REQUIREMENT_NAME.match(requirement)
        if name is None or _canonical(name.group()) != _canonical(package):
            continue
        return _floor(requirement, package)
    raise AssertionError(f"{package} is not declared in [project] dependencies")


def _locked_package(package: str) -> dict[str, Any]:
    """Return the ``[[package]]`` table ``uv.lock`` records for *package*."""
    for locked in _load_toml(_LOCK)["package"]:
        if _canonical(locked["name"]) == _canonical(package):
            entry: dict[str, Any] = locked
            return entry
    raise AssertionError(f"{package} is not a package in uv.lock")


def _locked_requirements() -> list[dict[str, Any]]:
    """Return the requirements ``uv.lock`` records for this project itself."""
    project_name = _load_toml(_PYPROJECT)["project"]["name"]
    requirements: list[dict[str, Any]] = _locked_package(project_name)["metadata"][
        "requires-dist"
    ]
    return requirements


def _resolved_version(package: str) -> Version:
    """Return the exact version ``uv.lock`` resolves *package* to."""
    return Version(_locked_package(package)["version"])


def _locked_floor(package: str) -> Version:
    """Return the ``>=`` floor ``uv.lock`` records for *package*."""
    for requirement in _locked_requirements():
        if _canonical(requirement["name"]) == _canonical(package):
            return _floor(requirement.get("specifier", ""), package)
    raise AssertionError(f"{package} is not in uv.lock's requires-dist")


def test_python_multipart_floor_excludes_cve_2024_53981() -> None:
    """python-multipart must be declared >= 0.0.18.

    Earlier releases carry CVE-2024-53981: a malformed multipart boundary
    makes the parser spin, and this parser sits directly in front of the
    unauthenticated ``POST /api/import`` upload endpoint.
    """
    assert _declared_floor("python-multipart") >= _PYTHON_MULTIPART_FLOOR


def test_locked_python_multipart_floor_excludes_cve_2024_53981() -> None:
    """uv.lock's copy of the declared floor must clear the same floor.

    uv mirrors the manifest's requirement strings into the workspace member's
    ``requires-dist``, and ``uv sync --locked`` compares the two. Raising the
    floor in pyproject.toml without running ``uv lock`` leaves the mirror
    quoting the vulnerable floor, and every locked install path — CI, the
    release job, ``make install`` — then refuses to install at all.
    """
    assert _locked_floor("python-multipart") >= _PYTHON_MULTIPART_FLOOR


def test_resolved_python_multipart_excludes_cve_2024_53981() -> None:
    """The version uv.lock resolves to must clear the same floor.

    The ``requires-dist`` mirror asserted above only repeats the constraint. It
    is this ``[[package]]`` entry that ``uv sync --locked`` installs into CI,
    the release job and every container image, and a resolution is free to sit
    on any version the constraint allows — including an older one pinned by a
    stale lockfile that was never re-resolved.
    """
    assert _resolved_version("python-multipart") >= _PYTHON_MULTIPART_FLOOR


def test_installed_python_multipart_excludes_cve_2024_53981() -> None:
    """The python-multipart actually resolved here must clear the same floor.

    The declaration is what a fresh install resolves against; this is what the
    running process imports. A lockfile or environment left behind on an
    affected release is exploitable regardless of what pyproject.toml says.
    """
    assert Version(version("python-multipart")) >= _PYTHON_MULTIPART_FLOOR
