"""Assert nothing in the repository tells anyone to pip install this project.

The project is not published to PyPI and is installed with uv, so a
``pip install`` of it fails for whoever follows it. Two log messages carried
that instruction and are asserted individually in
``tests/cli/test_config.py`` and ``tests/test_storage_manager.py``; this guard
covers the rest of the tree, where the next copy would otherwise be found by a
user rather than by CI.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

# parents[1] resolves /tests/test_install_instructions.py -> repo root.
_REPO_ROOT = Path(__file__).resolve().parents[1]

# Spliced, because this file is scanned along with every other — and the splice
# is why a fragment can be named here without the guard finding itself. Naming
# the package is wrong (it is not on PyPI) and so is pointing pip at the
# checkout: an editable install resolves no lockfile, which is the whole reason
# the project pins one.
_FORBIDDEN_FRAGMENTS = (
    "pip install " + "recommendinator",
    "pip install " + ".",
    "pip install " + "-e",
)

# Written by the release tooling or a package manager, so a match in one is not
# something anyone could fix by editing it.
_EXEMPTIONS = frozenset({"CHANGELOG.md", "pnpm-lock.yaml", "uv.lock"})

_CORRECT_COMMAND = "uv sync --locked --extra ai"


def _shipped_files() -> list[tuple[str, Path]]:
    """Return the files git would commit: tracked, plus untracked and unignored."""
    listing = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=_REPO_ROOT,
        capture_output=True,
        check=True,
        text=True,
    ).stdout
    return [
        (name, _REPO_ROOT / name)
        for name in listing.split("\0")
        if name and name not in _EXEMPTIONS
    ]


def _offences(name: str, path: Path) -> list[str]:
    """Return ``file:line`` for every line instructing a pip install."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    return [
        f"{name}:{number}"
        for number, line in enumerate(text.splitlines(), start=1)
        if any(fragment in line for fragment in _FORBIDDEN_FRAGMENTS)
    ]


class TestInstallInstructions:
    """The uv command is the only install instruction the project gives."""

    def test_nothing_instructs_a_pip_install_of_this_project(self) -> None:
        """No shipped file names a pip install of a package that is not on PyPI."""
        found = [
            offence
            for name, path in _shipped_files()
            for offence in _offences(name, path)
        ]

        assert not found, (
            f"{len(found)} line(s) instruct a pip install of this project; "
            f"say {_CORRECT_COMMAND!r} instead: " + ", ".join(found)
        )

    def test_the_scan_reaches_the_files_that_carried_the_instruction(self) -> None:
        """An empty result has to mean a clean tree, not an empty scan."""
        scanned = {name for name, _ in _shipped_files()}

        assert {
            "src/storage/manager.py",
            "src/cli/config.py",
            "docs/TROUBLESHOOTING.md",
        } <= scanned

    @pytest.mark.parametrize("fragment", _FORBIDDEN_FRAGMENTS)
    def test_the_guard_reports_a_line_that_carries_the_instruction(
        self, fragment: str, tmp_path: Path
    ) -> None:
        """A clean tree has to be what passes, not a scan that matches nothing.

        Every fragment is exercised, or one of them could stop matching
        anything at all and the guard would still look green.
        """
        offender = tmp_path / "README.md"
        offender.write_text(f"Install with: {fragment}\n", encoding="utf-8")

        assert _offences("README.md", offender) == ["README.md:1"]
