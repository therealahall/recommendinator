"""The bearer token is gone, and half a credential is worse than none.

A leftover ``web.api_token`` invites an operator to set a value nothing reads,
and a leftover reader is a second answer to "who is this request".
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

# parents[1] resolves /tests/test_bearer_token_is_retired.py -> repo root.
_REPO_ROOT = Path(__file__).resolve().parents[1]

_SCANNED = ("src", "tests", "config", "conftest.py")

_RETIRED_NAMES = (
    "api_token",
    "take_api_token",
    "MissingApiTokenError",
    "MIN_API_TOKEN_LENGTH",
)


def _scanned_files() -> list[Path]:
    """Every tracked file under the trees that must be clear of the token.

    This module is excluded: it is the one file that has to name the retired
    symbols, and scanning itself would make the sweep permanently red.
    """
    listing = subprocess.run(
        ["git", "ls-files", "--cached", "-z", "--", *_SCANNED],
        cwd=_REPO_ROOT,
        capture_output=True,
        check=True,
        text=True,
    ).stdout
    here = Path(__file__).resolve()
    return [
        path
        for name in listing.split("\0")
        if name and (path := _REPO_ROOT / name).resolve() != here
    ]


def _mentions(name: str) -> list[str]:
    """Every ``path:line`` naming *name*, across the scanned trees."""
    found = []
    for path in _scanned_files():
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for number, line in enumerate(text.splitlines(), start=1):
            if name in line:
                found.append(f"{path.relative_to(_REPO_ROOT)}:{number}")
    return found


class TestNoTokenResidue:
    """Frontend files are out of scope: the client half is its own unit."""

    @pytest.mark.parametrize("name", _RETIRED_NAMES)
    def test_the_name_appears_nowhere(self, name: str) -> None:
        assert _mentions(name) == []

    def test_the_scan_reads_the_files_it_claims_to(self) -> None:
        """An empty file list would make every case above pass by vacancy."""
        scanned = _scanned_files()

        assert len(scanned) > 100
        assert _REPO_ROOT / "config/example.yaml" in scanned
        assert _REPO_ROOT / "src/web/auth.py" in scanned

    def test_the_scan_finds_a_name_that_is_present(self) -> None:
        """Without this the sweep could hold on a matcher that never matches."""
        assert _mentions("SESSION_COOKIE")
