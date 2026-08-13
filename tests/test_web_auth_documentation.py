"""Guard the operator-facing prose about signing in to the web UI.

``tests/test_bearer_token_is_retired.py`` sweeps the code trees and leaves the
prose, which is where the token instructions lived. A doc still naming the
retired config key sends an operator to a key nothing reads.

Every mention of that key here is spliced, because the code-tree sweep reads
this file too and a literal would make the two guards permanently disagree.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import click
import pytest
from fastapi.routing import APIRoute

from src.cli.main import cli
from src.storage.accounts import MIN_PASSWORD_LENGTH, SESSION_LIFETIME
from src.web.auth_api import router as auth_router
from src.web.csrf import refuse_cross_origin

# parents[1] resolves /tests/test_web_auth_documentation.py -> repo root.
_REPO_ROOT = Path(__file__).resolve().parents[1]

# Spliced so this file can name the fragments without matching itself, the same
# reason ``tests/test_install_instructions.py`` splices its own.
#
# The config key means the retired credential wherever it is written.
_RETIRED_KEY = ("web.api" + "_token", "api" + "_token")

# Loose prose means it only in the documents that told an operator to set it.
# ``docs/ENRICHMENT_SETUP.md`` describes TMDB's own API token, and one reword
# there would otherwise turn a provider document red over a web setting.
_RETIRED_PROSE = ("api " + "token", "bearer " + "token")

_RETIRED_INSTRUCTIONS = _RETIRED_KEY + _RETIRED_PROSE

#: Where the web sign-in instructions lived, and the only prose in which "API
#: token" can only have meant this app's own.
_WEB_SIGN_IN_DOCUMENTS = frozenset(
    {
        "README.md",
        "QUICKSTART.md",
        "ARCHITECTURE.md",
        "CONTRIBUTING.md",
        "docs/SECURITY.md",
        "docs/DOCKER.md",
        "docs/TROUBLESHOOTING.md",
        "docker-compose.yml",
        "docker/entrypoint.sh",
        "config/example.yaml",
    }
)

# An upgrade note has to name the key to tell an operator it is dead, which is
# the one mention worth having. Saying it is *no longer* read is not something
# an instruction to set one ever says.
_REMOVAL_NOTICE = "no longer"

#: Where a reader is asked to choose a password, and so told the rule. README
#: carries the same first-run passage QUICKSTART and DOCKER do.
_PASSWORD_DOCUMENTS = (
    "README.md",
    "QUICKSTART.md",
    "docs/CLI.md",
    "docs/DOCKER.md",
    "docs/SECURITY.md",
)

_STATED_MINIMUM = re.compile(r"at least (\d+) characters")

#: The sentence in docs/SECURITY.md that bounds what an allowed origin gets.
_CORS_CLAIM = "cannot authenticate a cross-origin client"

# Written by the release tooling from commit messages, so a match there records
# a release that happened rather than an instruction anyone can follow.
_GENERATED = frozenset({"CHANGELOG.md"})

# Plugin docs and agent prompts are excluded together: a bearer token in the
# first is Trakt's or GOG's mechanism, and nobody installs this by reading the
# second.
_NOT_OPERATOR_PROSE = ("src/ingestion/sources/", ".claude/")

_OPERATOR_FILES = ("docker-compose.yml", "docker/entrypoint.sh", "config/example.yaml")

# A blank line, or the marker that starts a list item, heading, quote or table
# row: unwrapping a bullet list whole would let "no longer" in one item forgive
# the instruction in the next.
_BLOCK_BREAK = re.compile(r"\n\s*\n|\n(?=\s*(?:[-*+]\s|\d+\.\s|#{1,6}\s|>|\|))")

# End of sentence, which is the granularity the removal notice forgives at.
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


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


def _instructs_a_token(sentence: str, fragments: tuple[str, ...]) -> bool:
    """Whether *sentence* sends an operator to the retired token."""
    lowered = sentence.lower()
    return (
        any(fragment in lowered for fragment in fragments)
        and _REMOVAL_NOTICE not in lowered
    )


def _offences(name: str, path: Path) -> list[str]:
    """Return ``file: sentence`` for every retired token instruction.

    Reads without ``errors=``, so an undecodable file raises rather than
    scoring the same as a clean one.
    """
    fragments = _RETIRED_KEY + (
        _RETIRED_PROSE if name in _WEB_SIGN_IN_DOCUMENTS else ()
    )
    return [
        f"{name}: {sentence}"
        for sentence in _sentences(path.read_text(encoding="utf-8"))
        if _instructs_a_token(sentence, fragments)
    ]


def _read(name: str) -> str:
    return (_REPO_ROOT / name).read_text(encoding="utf-8")


def _prose(name: str) -> str:
    """Return *name*'s text with its line wrapping collapsed.

    A sentence stating the minimum wraps across a line break as readily as not.
    """
    return " ".join(_read(name).split())


def _paragraphs(name: str) -> list[str]:
    """Return *name*'s blank-line-separated blocks, each unwrapped."""
    return [
        " ".join(block.split()) for block in _read(name).split("\n\n") if block.strip()
    ]


def _sentences(text: str) -> list[str]:
    """Return *text*'s sentences, each with its line wrapping collapsed.

    The sentence, not the line: a reflowed paragraph moves the key and the
    removal notice apart. Not the paragraph either, or that notice would
    forgive a setup instruction standing beside it.
    """
    return [
        sentence
        for block in _BLOCK_BREAK.split(text)
        for sentence in _SENTENCE_END.split(" ".join(block.split()))
        if sentence
    ]


class TestNoTokenInstructionSurvives:
    """No operator is sent to a credential nothing reads."""

    def test_no_operator_document_names_the_retired_token(self) -> None:
        found = [
            offence
            for name, path in _operator_documentation()
            for offence in _offences(name, path)
        ]

        assert not found, (
            f"{len(found)} sentence(s) still instruct an API token; the web UI "
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
    def test_the_sweep_reports_the_sentence_carrying_the_instruction(
        self, fragment: str, tmp_path: Path
    ) -> None:
        """Every fragment is exercised, or one could match nothing and hide."""
        offender = tmp_path / "README.md"
        instruction = f"Set the {fragment.upper()} first."
        offender.write_text(f"{instruction}\n", encoding="utf-8")

        assert _offences("README.md", offender) == [f"README.md: {instruction}"]

    def test_a_document_the_sweep_cannot_read_is_not_scored_clean(
        self, tmp_path: Path
    ) -> None:
        with pytest.raises(OSError):
            _offences("gone.md", tmp_path / "gone.md")


class TestAProviderDocumentKeepsItsOwnTokens:
    """The loose prose was matched across all of ``docs/``.

    ``docs/ENRICHMENT_SETUP.md`` already distinguishes TMDB's v3 key from its
    v4 token, and one reword to "API token" would have gone red over a web
    setting the document never mentions.
    """

    @pytest.fixture()
    def provider_doc(self, tmp_path: Path) -> Path:
        offender = tmp_path / "ENRICHMENT_SETUP.md"
        offender.write_text("Paste the TMDB API token.\n", encoding="utf-8")
        return offender

    def test_the_prose_is_not_swept_there(self, provider_doc: Path) -> None:
        assert _offences("docs/ENRICHMENT_SETUP.md", provider_doc) == []

    def test_the_same_line_in_a_sign_in_document_is_an_offence(
        self, provider_doc: Path
    ) -> None:
        """Anchors the exclusion: the fragment does match, under the right name."""
        assert _offences("README.md", provider_doc) == [
            "README.md: Paste the TMDB API token."
        ]

    def test_the_config_key_is_still_swept_there(self, tmp_path: Path) -> None:
        """Narrowed prose, not a narrowed sweep: the key is dead in every file."""
        offender = tmp_path / "ENRICHMENT_SETUP.md"
        offender.write_text(f"Set {_RETIRED_KEY[0]} first.\n", encoding="utf-8")

        assert _offences("docs/ENRICHMENT_SETUP.md", offender) == [
            f"docs/ENRICHMENT_SETUP.md: Set {_RETIRED_KEY[0]} first."
        ]

    def test_that_document_is_in_the_sweep_at_all(self) -> None:
        """Anchors all three: an unscanned file passes every one of them."""
        assert "docs/ENRICHMENT_SETUP.md" in {
            name for name, _ in _operator_documentation()
        }


class TestTheUpgradeNoteMayNameWhatItRetires:
    """An operator holding the dead key is told so, and nothing more is said."""

    def test_the_note_is_the_one_sentence_naming_the_key(self) -> None:
        """Anchor: with no note to forgive, the exemption proves nothing."""
        naming_it = [
            sentence
            for sentence in _sentences(_read("README.md"))
            if _RETIRED_KEY[0] in sentence.lower()
        ]

        assert len(naming_it) == 1
        assert _REMOVAL_NOTICE in naming_it[0].lower()

    def test_setup_instructions_beside_the_note_are_still_refused(
        self, tmp_path: Path
    ) -> None:
        offender = tmp_path / "README.md"
        instruction = f"Set `{_RETIRED_KEY[0]}` to a long random string."
        offender.write_text(
            f"`{_RETIRED_KEY[0]}` is no longer read.\n{instruction}\n",
            encoding="utf-8",
        )

        assert _offences("README.md", offender) == [f"README.md: {instruction}"]

    def test_a_reflowed_paragraph_keeps_its_notice_regression(
        self, tmp_path: Path
    ) -> None:
        """Regression: the sweep read one line at a time.

        Bug reported: rewrapping README's upgrade note failed it.
        Root cause: the notice forgave a line, not the sentence it sits in.
        Fix: unwrap each block before splitting into sentences.
        """
        offender = tmp_path / "README.md"
        offender.write_text(
            f"`{_RETIRED_KEY[0]}`\nis no longer read.\n", encoding="utf-8"
        )

        assert _offences("README.md", offender) == []


class TestThePasswordMinimumReachesEveryReader:
    """Whoever is asked to choose a password is told what will be accepted."""

    @pytest.mark.parametrize("document", _PASSWORD_DOCUMENTS)
    def test_no_document_promises_a_password_the_code_refuses(
        self, document: str
    ) -> None:
        """Asking for more than is enforced is safe; asking for less is not."""
        stated = [int(length) for length in _STATED_MINIMUM.findall(_prose(document))]

        assert stated
        assert min(stated) >= MIN_PASSWORD_LENGTH


class TestStatedPasswordMinimumRegression:
    """The number in the prose is the number the code turns away."""

    def test_the_stated_minimum_is_the_enforced_one_regression(self) -> None:
        """Regression test: the docs promise more than the code requires.

        Bug reported: four documents say 12 characters; MIN_PASSWORD_LENGTH is 8.
        Root cause: the sibling guard compares with >=, so a larger number passes.
        Fix: pin it to the constant.
        """
        stated = {
            int(length)
            for document in _PASSWORD_DOCUMENTS
            for length in _STATED_MINIMUM.findall(_prose(document))
        }

        assert stated == {MIN_PASSWORD_LENGTH}


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


class TestAllowedOriginsSurfaceRegression:
    """What an allowed origin reaches is what the app leaves ungated."""

    def test_the_cors_paragraph_is_the_whole_ungated_surface_regression(self) -> None:
        """Regression test: the paragraph understates an allowed origin's reach.

        Bug reported: naming only the SPA shell omits `POST /api/auth/setup`.
        Root cause: session-exempt read as unguarded.
        Fix: those routes carry ``refuse_cross_origin``.
        """
        session_exempt = {
            route.path for route in auth_router.routes if isinstance(route, APIRoute)
        }
        assert session_exempt, "no session-exempt routes for the guard to cover"
        assert any(
            depends.dependency is refuse_cross_origin
            for depends in auth_router.dependencies
        ), f"a cross-origin caller could drive {sorted(session_exempt)}"

        claiming_it = [
            block for block in _paragraphs("docs/SECURITY.md") if _CORS_CLAIM in block
        ]

        assert len(claiming_it) == 1
        assert "`GET /` and `/static/*`" in claiming_it[0]
