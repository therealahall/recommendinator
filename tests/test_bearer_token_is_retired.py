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

_EVERYWHERE = ("src", "tests", "config", "conftest.py")

#: Where the retired WEB credential could have been read from. The bare name is
#: scoped to these: a source plugin or enrichment provider whose own config
#: carries an ``api_token`` field is naming that service's credential.
_WEB_CREDENTIAL = ("src/web", "src/config", "config")

_RETIRED_NAMES = {
    "web.api_token": _EVERYWHERE,
    "take_api_token": _EVERYWHERE,
    "MissingApiTokenError": _EVERYWHERE,
    "MIN_API_TOKEN_LENGTH": _EVERYWHERE,
    "api_token": _WEB_CREDENTIAL,
}


def _scanned_files(trees: tuple[str, ...] = _EVERYWHERE) -> list[Path]:
    """Every tracked file under *trees*, which must be clear of the token.

    This module is excluded: it is the one file that has to name the retired
    symbols, and scanning itself would make the sweep permanently red.
    """
    listing = subprocess.run(
        ["git", "ls-files", "--cached", "-z", "--", *trees],
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


def _mentions(name: str, trees: tuple[str, ...] = _EVERYWHERE) -> list[str]:
    """Every ``path:line`` naming *name*, across *trees*."""
    found = []
    for path in _scanned_files(trees):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for number, line in enumerate(text.splitlines(), start=1):
            if name in line:
                found.append(f"{path.relative_to(_REPO_ROOT)}:{number}")
    return found


def _relative(trees: tuple[str, ...]) -> set[str]:
    return {path.relative_to(_REPO_ROOT).as_posix() for path in _scanned_files(trees)}


class TestNoTokenResidue:
    """Frontend files are out of scope: the client half is its own unit."""

    @pytest.mark.parametrize("name", sorted(_RETIRED_NAMES))
    def test_the_name_appears_nowhere_it_would_mean_the_web_credential(
        self, name: str
    ) -> None:
        assert _mentions(name, _RETIRED_NAMES[name]) == []

    def test_the_scan_reads_the_files_it_claims_to(self) -> None:
        """An empty file list would make every case above pass by vacancy."""
        scanned = _scanned_files()

        assert len(scanned) > 100
        assert _REPO_ROOT / "config/example.yaml" in scanned
        assert _REPO_ROOT / "src/web/auth.py" in scanned

    def test_the_scan_finds_a_name_that_is_present(self) -> None:
        """Without this the sweep could hold on a matcher that never matches."""
        assert _mentions("SESSION_COOKIE")


class TestThePluginTreesKeepTheirOwnCredentials:
    """The bare name was swept across all of ``src/``.

    Every source plugin and enrichment provider exists to hold one service's
    credentials, so a future plugin declaring an ``api_token`` config field
    would have failed a guard about a retired web setting.
    """

    @pytest.mark.parametrize(
        "tree", ["src/ingestion/sources/", "src/enrichment/providers/"]
    )
    def test_the_bare_name_is_not_swept_there(self, tree: str) -> None:
        assert not any(name.startswith(tree) for name in _relative(_WEB_CREDENTIAL))

    @pytest.mark.parametrize(
        "tree", ["src/ingestion/sources/", "src/enrichment/providers/"]
    )
    def test_those_trees_are_populated_and_swept_for_the_config_key(
        self, tree: str
    ) -> None:
        """Anchors the exclusion above, which an empty tree satisfies for free."""
        assert any(name.startswith(tree) for name in _relative(_EVERYWHERE))
