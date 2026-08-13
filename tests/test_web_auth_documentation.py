"""Guard the operator-facing prose about signing in to the web UI.

``tests/test_bearer_token_is_retired.py`` sweeps the code trees and leaves the
prose, which is where the token instructions lived. A doc still naming the
retired config key sends an operator to a key nothing reads.

Every mention of that key here is spliced, because the code-tree sweep reads
this file too and a literal would make the two guards permanently disagree.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import click
import pytest

from src.cli.main import cli
from src.storage.accounts import SESSION_LIFETIME

# parents[1] resolves /tests/test_web_auth_documentation.py -> repo root.
_REPO_ROOT = Path(__file__).resolve().parents[1]

# Spliced so this file can name the fragments without matching itself, the same
# reason ``tests/test_install_instructions.py`` splices its own.
_RETIRED_INSTRUCTIONS = (
    "web.api" + "_token",
    "api" + "_token",
    "api " + "token",
    "bearer " + "token",
)

# Written by the release tooling from commit messages, so a match there records
# a release that happened rather than an instruction anyone can follow.
_GENERATED = frozenset({"CHANGELOG.md"})

# Plugin docs and agent prompts are excluded together: a bearer token in the
# first is Trakt's or GOG's mechanism, and nobody installs this by reading the
# second.
_NOT_OPERATOR_PROSE = ("src/ingestion/sources/", ".claude/")

_OPERATOR_FILES = ("docker-compose.yml", "docker/entrypoint.sh", "config/example.yaml")


def _operator_documentation(root: Path = _REPO_ROOT) -> list[tuple[str, Path]]:
    """Return the tracked files an operator reads for instructions.

    Tracked only: an untracked draft is one developer's, and scanning it would
    fail the suite on their machine alone.
    """
    listing = subprocess.run(
        ["git", "ls-files", "--cached", "-z"],
        cwd=root,
        capture_output=True,
        check=True,
        text=True,
    ).stdout
    return [
        (name, root / name)
        for name in listing.split("\0")
        if name
        and name not in _GENERATED
        and not name.startswith(_NOT_OPERATOR_PROSE)
        and (
            name in _OPERATOR_FILES
            or name.startswith("docs/")
            or (name.endswith(".md") and "/" not in name)
        )
    ]


def _offences(name: str, path: Path) -> list[str]:
    """Return ``file:line`` for every line naming a retired token instruction.

    Reads without ``errors=``, so an undecodable file raises rather than
    scoring the same as a clean one.
    """
    text = path.read_text(encoding="utf-8")
    return [
        f"{name}:{number}"
        for number, line in enumerate(text.splitlines(), start=1)
        if any(fragment in line.lower() for fragment in _RETIRED_INSTRUCTIONS)
    ]


def _read(name: str) -> str:
    return (_REPO_ROOT / name).read_text(encoding="utf-8")


class TestNoTokenInstructionSurvives:
    """No operator is sent to a credential nothing reads."""

    def test_no_operator_document_names_the_retired_token(self) -> None:
        found = [
            offence
            for name, path in _operator_documentation()
            for offence in _offences(name, path)
        ]

        assert not found, (
            f"{len(found)} line(s) still instruct an API token; the web UI "
            "signs in with a username and password: " + ", ".join(found)
        )

    def test_the_sweep_reaches_the_documents_that_carried_it(self) -> None:
        """An empty result has to mean clean prose, not an empty sweep."""
        scanned = {name for name, _ in _operator_documentation()}

        assert {
            "README.md",
            "QUICKSTART.md",
            "ARCHITECTURE.md",
            "CONTRIBUTING.md",
            "docs/SECURITY.md",
            "docs/DOCKER.md",
            "docs/TROUBLESHOOTING.md",
            "docker-compose.yml",
            "config/example.yaml",
        } <= scanned

    @pytest.mark.parametrize("fragment", _RETIRED_INSTRUCTIONS)
    def test_the_sweep_reports_a_line_that_carries_the_instruction(
        self, fragment: str, tmp_path: Path
    ) -> None:
        """Every fragment is exercised, or one could match nothing and hide."""
        offender = tmp_path / "README.md"
        offender.write_text(f"Set the {fragment.upper()} first.\n", encoding="utf-8")

        assert _offences("README.md", offender) == ["README.md:1"]

    def test_a_document_the_sweep_cannot_read_is_not_scored_clean(
        self, tmp_path: Path
    ) -> None:
        with pytest.raises(OSError):
            _offences("gone.md", tmp_path / "gone.md")


class TestSecurityDocMatchesTheShippedMechanism:
    """The claims a reader relies on, tied to the code that makes them true."""

    def test_the_missing_secure_flag_is_explained(self) -> None:
        """A Secure cookie over this app's plain HTTP would never be sent."""
        assert "No `Secure` flag" in _read("docs/SECURITY.md")

    def test_the_gate_on_the_status_route_is_explained(self) -> None:
        """The health check reads that 401 as healthy; a 200 would be a defect."""
        security = _read("docs/SECURITY.md")

        assert "`GET /api/status` stays gated" in security
        assert "health check" in security

    @pytest.mark.parametrize(
        ("document", "phrase"),
        (
            ("docs/SECURITY.md", "{days} idle days"),
            ("QUICKSTART.md", "{days} days"),
        ),
    )
    def test_the_documented_session_lifetime_is_the_shipped_one(
        self, document: str, phrase: str
    ) -> None:
        """A change to SESSION_LIFETIME must not leave the prose behind."""
        assert phrase.format(days=SESSION_LIFETIME.days) in _read(document)

    def test_the_recovery_command_the_documents_name_exists(self) -> None:
        """Three documents offer ``account set-password`` as the way back in."""
        account = cli.commands["account"]

        assert isinstance(account, click.Group)
        assert "set-password" in account.commands
