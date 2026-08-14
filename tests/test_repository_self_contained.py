"""Assert that the repository's own developer files point only inside it.

How this project is built, reviewed, and documented has to be describable from
inside this checkout. A developer-facing file that points somewhere else — a home
directory, one machine's layout, a repository that is not this one — is
unverifiable noise to every reader who is not the person who wrote it, and
neither they nor CI can tell whether it is still true.

Scope is every root-level file except an explicit exemption set, plus `.claude/`,
`.github/`, `docker/`, `docs/`, `resources/vite/` and `tests/`. Root
files are in by default rather than by allowlist, because an allowlist silently
omits whatever gets added next. `src/` and the rest of `resources/` are
deliberately out: application and interface code legitimately addresses the
machine it runs on — a user's configured ROM directory, another launcher's
credential file — and those paths explain themselves to any reader. Someone's own
directory in a developer doc does not.

The patterns cover a couple of API names as well as literal paths, so a
legitimate hostile-input test can trip on its own payload. That is what the
`self-contained: allow <reason>` comment marker is for; a security test must
never be watered down to satisfy this guard.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

# parents[1] resolves /tests/test_repository_self_contained.py -> repo root.
_REPO_ROOT = Path(__file__).resolve().parents[1]

_SCANNED_DIRECTORIES = (
    ".claude/",
    ".github/",
    "docker/",
    "docs/",
    "resources/vite/",
    "tests/",
)

# Root files nobody authors by hand: written by the release tooling or by a
# package manager, so a match in one is not something anyone could fix by editing
# it. Everything else at the root is in scope.
_ROOT_EXEMPTIONS = frozenset({"CHANGELOG.md", "pnpm-lock.yaml", "uv.lock"})

# Exempts the single line carrying it, and only inside a comment, and only with a
# reason. Intended for a test whose subject IS an outside path — a traversal
# payload, or a regression test asserting a user path is not naively expanded.
_ALLOW_MARKER = "self-contained: allow"
_COMMENT_INTRODUCERS = ("#", "//", "<!--")

# The offending line reaches a terminal and a CI log, so it is escaped and
# capped: a crafted file could otherwise emit control characters that rewrite
# the report being trusted.
_LINE_DISPLAY_LIMIT = 200

# Every pattern is spliced from fragments, because this file is itself in scope
# and a literal here would be indistinguishable from the thing it forbids.
_FORBIDDEN_PATTERNS: tuple[tuple[str, str], ...] = (
    ("~" + "/", "a path under a home directory"),
    ("/home" + "/", "an absolute path under the Linux home directory"),
    ("/Users" + "/", "an absolute path under the macOS home directory"),
    ("$" + "HOME", "the home-directory environment variable"),
    ("$" + "{HOME}", "the home-directory environment variable"),
    ("%USER" + "PROFILE%", "the Windows home-directory variable"),
    ("expand" + "user", "expansion of a home-relative path"),
    ("Path.home()" + " /", "a path joined onto the home directory"),
    (
        "dot" + "files",
        "a repository that is not this one (if you mean hidden files, mark the line)",
    ),
)


# The documents that describe the opt-out marker to a contributor. Prose and
# constant have drifted once already — `//` was added for the TypeScript files in
# scope and neither document said so — with nothing failing, so the phrasing is a
# contract: each lists the introducers, in order, inside the parenthesis this
# lead-in opens.
_MARKER_DOCUMENTS = ("CONTRIBUTING.md", "docs/DEVELOPMENT_PATTERNS.md")
_INTRODUCER_LEAD_IN = "opens a comment ("
_INLINE_CODE = re.compile(r"`([^`]+)`")


def _documented_introducers(relative_path: str) -> tuple[str, ...]:
    """Return the comment introducers a document lists, in the order it lists them."""
    text = (_REPO_ROOT / relative_path).read_text(encoding="utf-8")
    _, lead_in, rest = text.partition(_INTRODUCER_LEAD_IN)
    assert lead_in, f"{relative_path} no longer says {_INTRODUCER_LEAD_IN!r}"
    listed, closing_parenthesis, _ = rest.partition(")")
    assert closing_parenthesis, f"{relative_path} never closes the introducer list"
    return tuple(_INLINE_CODE.findall(listed))


def _is_in_scope(name: str) -> bool:
    """Return whether a repository-relative path is developer-facing."""
    if "/" not in name:
        return name not in _ROOT_EXEMPTIONS
    return name.startswith(_SCANNED_DIRECTORIES)


def _shipped_files() -> list[Path]:
    """Return the in-scope files git would commit: tracked, plus untracked.

    `-z` NUL-*terminates* rather than separating, so the final split yields an
    empty name. Dropping it matters now that root files are in scope by default:
    an empty name has no separator and no exemption, so it would put the
    repository root itself into the scan.
    """
    listing = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=_REPO_ROOT,
        capture_output=True,
        check=True,
        text=True,
    ).stdout
    return [
        _REPO_ROOT / name for name in listing.split("\0") if name and _is_in_scope(name)
    ]


def _numbered_lines(path: Path) -> list[tuple[int, str]]:
    """Return the file's numbered lines.

    Undecodable bytes are replaced rather than abandoning the file, so a single
    stray byte cannot silence the guard for everything after it.

    A file that will not open at all yields nothing, deliberately: `git ls-files
    --cached` lists a tracked file whose deletion is staged, and the guard has no
    business erroring on a file that is on its way out. The quiet is narrow to
    `OSError` for that reason — broadening it would mute the guard for anything.
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    return list(enumerate(text.splitlines(), start=1))


def _bounded(text: str) -> str:
    """Return `text` as an escaped, length-capped literal."""
    escaped = repr(text)
    if len(escaped) <= _LINE_DISPLAY_LIMIT:
        return escaped
    return f"{escaped[:_LINE_DISPLAY_LIMIT]}..."


def _is_exempt(line: str) -> bool:
    """Return whether the line carries the marker, in a comment, with a reason.

    The marker must open a comment, not merely appear on the line. Documenting
    the convention means quoting it in prose, and a doc that quotes it must not
    thereby exempt itself — that is a free pass granted by coincidence.
    """
    before, marker, reason = line.partition(_ALLOW_MARKER)
    if not marker or not reason.strip():
        return False
    opening = before.rstrip()
    for introducer in _COMMENT_INTRODUCERS:
        if not opening.endswith(introducer):
            continue
        preceding = opening[: -len(introducer)]
        return preceding == "" or preceding[-1].isspace()
    return False


def _offences(path: Path, display_name: str) -> list[str]:
    """Return one line per outside-the-repo reference in `path`, naming where it is.

    A symlink is an offence in itself rather than something to read through. Its
    target can be anywhere on the machine, so following it would both make the
    invariant this guard asserts false — the contents inspected are not the file
    the repository ships — and echo an arbitrary file into a CI log.

    The name is bounded for the same reason the line is: `git ls-files -z` emits
    names as unquoted bytes, so a committed file whose *name* carries a control
    character would otherwise reach a terminal raw — and on the symlink branch it
    does so with no content match needed at all.
    """
    name = _bounded(display_name)
    if path.is_symlink():
        return [f"  {name} is a symlink, whose target need not be in the repo"]
    return [
        f"  {name}:{number} mentions {description}: {_bounded(line.strip())}"
        for number, line in _numbered_lines(path)
        for pattern, description in _FORBIDDEN_PATTERNS
        if pattern in line and not _is_exempt(line)
    ]


class TestSelfContainedRepositoryRegression:
    """Regression test for developer docs pointing at a directory no clone has."""

    def test_no_developer_file_references_a_path_outside_the_repository_regression(
        self,
    ) -> None:
        """Regression test: four committed docs sent readers to a directory they lack.

        Bug reported: `ARCHITECTURE.md`, `CLAUDE.md`, `CONTRIBUTING.md` and
        `docs/SECURITY.md` all told the reader that six of the seven review agents
        live in one particular home directory, and the checker compared the
        committed agents against that directory. Nobody cloning the repository has
        it, so the docs described a layout that does not exist for them and the
        comparison silently did nothing.
        Root cause: nothing stopped a committed file from naming a path outside the
        repository, and such a path cannot be verified by a reader or by CI.
        Fix: the outside-directory comparison was deleted and the docs rewritten to
        describe only this checkout. This test scans the developer-facing tree so
        the next one fails here instead of shipping.
        """
        offences = [
            offence
            for path in _shipped_files()
            for offence in _offences(path, str(path.relative_to(_REPO_ROOT)))
        ]
        assert not offences, (
            "The repository must be self-contained: every path its developer files "
            "mention lives inside it. These do not:\n"
            + "\n".join(offences)
            + f"\n\nIf one of these IS the subject of a test, exempt that single "
            f"line with a `# {_ALLOW_MARKER} <reason>` comment rather than "
            "weakening the test. A symlink has no line to mark — replace it with "
            "the real file."
        )


class TestForbiddenPatterns:
    """What counts as an outside-the-repo reference, and what deliberately does not."""

    def test_the_pattern_list_is_pinned(self) -> None:
        """Every other pattern test is parametrized *from* this list.

        Delete an entry and the parametrization quietly shrinks by one case, the
        suite stays green, and the guard stops catching that shape forever. The
        descriptions are pinned too: the detection test builds its expected message
        *from* the description under test, so drift is invisible there, and the
        descriptions are the guard's user-facing product, and the last one carries
        the instruction for resolving its own false positive. The literals are
        spliced the same way the module splices them.
        """
        assert _FORBIDDEN_PATTERNS == (
            ("~" + "/", "a path under a home directory"),
            ("/home" + "/", "an absolute path under the Linux home directory"),
            ("/Users" + "/", "an absolute path under the macOS home directory"),
            ("$" + "HOME", "the home-directory environment variable"),
            ("$" + "{HOME}", "the home-directory environment variable"),
            ("%USER" + "PROFILE%", "the Windows home-directory variable"),
            ("expand" + "user", "expansion of a home-relative path"),
            ("Path.home()" + " /", "a path joined onto the home directory"),
            (
                "dot" + "files",
                "a repository that is not this one "
                "(if you mean hidden files, mark the line)",
            ),
        )

    @pytest.mark.parametrize(("pattern", "description"), _FORBIDDEN_PATTERNS)
    def test_every_forbidden_pattern_is_detected_and_located(
        self, tmp_path: Path, pattern: str, description: str
    ) -> None:
        """A guard that cannot fire would pass forever while proving nothing."""
        path = tmp_path / "example.md"
        path.write_text(f"first line\nsomewhere {pattern} else\n", encoding="utf-8")
        quoted = _bounded(f"somewhere {pattern} else")
        assert _offences(path, "example.md") == [
            f"  'example.md':2 mentions {description}: {quoted}"
        ]

    def test_ordinary_prose_is_not_an_offence(self, tmp_path: Path) -> None:
        """The false-positive direction: a guard that flags everything gets bypassed."""
        path = tmp_path / "example.md"
        path.write_text(
            "Paths are resolved relative to the repository root, and the user's\n"
            "configured directories are read from the settings table.\n",
            encoding="utf-8",
        )
        assert _offences(path, "example.md") == []

    def test_the_reported_line_is_escaped_and_capped(self, tmp_path: Path) -> None:
        """A crafted file must not emit escapes that rewrite the report it appears in."""
        pattern = _FORBIDDEN_PATTERNS[0][0]
        path = tmp_path / "example.md"
        path.write_text(
            f"\x1b[2K{pattern}{'z' * 5000}\n",
            encoding="utf-8",
        )
        offence = _offences(path, "example.md")[0]
        assert "\x1b" not in offence
        assert "\\x1b[2K" in offence
        assert len(offence) < _LINE_DISPLAY_LIMIT + 100
        assert offence.endswith("...")

    def test_the_reported_file_name_is_escaped(self, tmp_path: Path) -> None:
        """The name is as file-derived as the line, and was the half left unescaped.

        `git ls-files -z` emits names as unquoted bytes, so a committed file whose
        *name* carries an escape would rewrite the report just as a crafted line
        would. The same helper bounds its length too.
        """
        pattern = _FORBIDDEN_PATTERNS[0][0]
        hostile_name = "\x1b[2Kwiped.md"
        path = tmp_path / hostile_name
        path.write_text(f"somewhere {pattern} else\n", encoding="utf-8")
        offence = _offences(path, hostile_name)[0]
        assert "\x1b" not in offence
        assert "\\x1b[2K" in offence

    def test_the_reported_file_name_is_capped(self, tmp_path: Path) -> None:
        """The name is a repository-relative path and paths have no useful bound.

        Nothing on disk has to be long for this: the display name is a parameter,
        and `git ls-files` will hand over whatever depth of directory nesting a
        branch commits. Escaping without capping still lets one name flood the
        report the reader needs.
        """
        pattern = _FORBIDDEN_PATTERNS[0][0]
        path = tmp_path / "example.md"
        path.write_text(f"somewhere {pattern} else\n", encoding="utf-8")
        offence = _offences(path, "z" * 5000)[0]
        name = offence.split(":", 1)[0].strip()
        assert len(name) <= _LINE_DISPLAY_LIMIT + len("...")
        assert name.endswith("...")

    def test_an_undecodable_byte_does_not_hide_the_rest_of_the_file(
        self, tmp_path: Path
    ) -> None:
        """A guard that goes quiet on a file it cannot decode can be silenced."""
        pattern = _FORBIDDEN_PATTERNS[0][0]
        path = tmp_path / "example.md"
        path.write_bytes(
            b"\xff binary noise\nsomewhere " + pattern.encode() + b" else\n"
        )
        assert len(_offences(path, "example.md")) == 1

    def test_a_file_that_will_not_open_is_quietly_skipped(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`git ls-files --cached` lists a tracked file whose deletion is staged.

        The guard has no business erroring on a file on its way out, so that one
        branch is deliberately quiet — and narrow to `OSError`, since broadening it
        would mute the guard for every file with nothing failing.
        """
        pattern = _FORBIDDEN_PATTERNS[0][0]
        path = tmp_path / "example.md"
        path.write_text(f"somewhere {pattern} else\n", encoding="utf-8")

        def deny_read(self: Path, **kwargs: object) -> str:
            raise FileNotFoundError(2, "No such file or directory")

        monkeypatch.setattr(Path, "read_text", deny_read)
        assert _offences(path, "example.md") == []


class TestSymlinks:
    """A committed symlink could aim the scan at anything on the machine."""

    def test_a_symlink_is_reported_rather_than_followed(self, tmp_path: Path) -> None:
        """Git tracks symlinks, so the target is not what the repository ships."""
        outside = tmp_path / "outside.md"
        outside.write_text("nothing to see\n", encoding="utf-8")
        link = tmp_path / "link.md"
        link.symlink_to(outside)
        assert _offences(link, "link.md") == [
            "  'link.md' is a symlink, whose target need not be in the repo"
        ]

    def test_a_symlinks_reported_name_is_escaped(self, tmp_path: Path) -> None:
        """This branch reports a name with no content match, so nothing else bounds it.

        A crafted line has to match a forbidden pattern to be printed. A symlink's
        name is printed for existing at all, which makes it the cheaper way to get
        an escape into the report if the name were interpolated raw.
        """
        hostile_name = "\x1b[2Kwiped.md"
        link = tmp_path / hostile_name
        link.symlink_to(tmp_path / "target.md")
        offence = _offences(link, hostile_name)[0]
        assert "\x1b" not in offence
        assert "\\x1b[2K" in offence

    def test_a_marker_cannot_exempt_a_symlink(self, tmp_path: Path) -> None:
        """The exemption works on line text, and a symlink's own text is its target's.

        Pinned against a refactor that hoists the exemption check above the
        symlink check, which would let a link exempt itself by what it points at.
        """
        target = tmp_path / "target.md"
        target.write_text(f"anything  # {_ALLOW_MARKER} nice try\n", encoding="utf-8")
        link = tmp_path / "link.md"
        link.symlink_to(target)
        assert len(_offences(link, "link.md")) == 1


class TestAllowMarker:
    """The escape hatch, which must not be openable by accident."""

    def test_a_marked_line_with_a_reason_is_exempt(self, tmp_path: Path) -> None:
        """A test whose payload IS an outside path must stay writable as intended."""
        pattern = _FORBIDDEN_PATTERNS[0][0]
        path = tmp_path / "example.py"
        path.write_text(
            f'payload = "{pattern}.ssh/id_rsa"  # {_ALLOW_MARKER} traversal payload\n',
            encoding="utf-8",
        )
        assert _offences(path, "example.py") == []

    def test_the_introducers_are_pinned(self) -> None:
        """Removing one breaks a behavioural test; adding one widens the hatch silently."""
        assert _COMMENT_INTRODUCERS == ("#", "//", "<!--")

    def test_no_other_shipped_document_describes_the_introducers(self) -> None:
        """The list is pinned by discovery, which is stronger than pinning it.

        This uses the same file discovery the guard itself runs on, so dropping an
        entry from the constant fails here, adding a bogus one fails here, and a
        third document describing the introducers has to join the list — and
        therefore has to agree with the constant — rather than quietly
        contradicting it. A literal pin beside this would add only the ability to
        catch a reorder, and order means nothing to a `parametrize` and a sorted
        comparison.
        """
        describing = sorted(
            str(path.relative_to(_REPO_ROOT))
            for path in _shipped_files()
            if path.suffix == ".md"
            and not path.is_symlink()
            and _INTRODUCER_LEAD_IN
            in path.read_text(encoding="utf-8", errors="replace")
        )
        assert describing == sorted(_MARKER_DOCUMENTS)

    @pytest.mark.parametrize("document", _MARKER_DOCUMENTS)
    def test_the_documented_introducers_match_the_constant(self, document: str) -> None:
        """A contributor opens the hatch from the docs, so the docs must be complete.

        `//` was added to the constant and neither document was updated, leaving a
        TypeScript contributor with no documented way to mark a line. Pinning the
        tuple alone could not catch that — nothing compared it to the prose.
        """
        assert _documented_introducers(document) == _COMMENT_INTRODUCERS

    @pytest.mark.parametrize(
        ("suffix", "introducer"),
        [(".md", "<!--"), (".ts", "//")],
    )
    def test_every_comment_syntax_in_scope_can_exempt_a_line(
        self, tmp_path: Path, suffix: str, introducer: str
    ) -> None:
        """Markdown and TypeScript are both in scope, and `#` is neither's comment."""
        pattern = _FORBIDDEN_PATTERNS[0][0]
        path = tmp_path / f"example{suffix}"
        path.write_text(
            f"payload {pattern}x {introducer} {_ALLOW_MARKER} documented example\n",
            encoding="utf-8",
        )
        assert _offences(path, path.name) == []

    def test_a_marked_line_without_a_reason_is_still_reported(
        self, tmp_path: Path
    ) -> None:
        """An unexplained exemption is the thing this guard exists to prevent."""
        pattern = _FORBIDDEN_PATTERNS[0][0]
        path = tmp_path / "example.py"
        path.write_text(
            f'payload = "{pattern}x"  # {_ALLOW_MARKER}\n', encoding="utf-8"
        )
        assert len(_offences(path, "example.py")) == 1

    def test_a_marker_after_a_non_comment_introducer_exempts_nothing(
        self, tmp_path: Path
    ) -> None:
        """A marker inside quoted prose must not grant that line a free pass.

        Reaches the rejection *inside* the introducer loop: the backtick-hash makes
        the endswith check true, and the character before it is not whitespace.
        Sibling test covers the other rejection, after the loop.
        """
        pattern = _FORBIDDEN_PATTERNS[0][0]
        path = tmp_path / "example.md"
        path.write_text(
            f"See {pattern}notes: mark the line `# {_ALLOW_MARKER} <reason>` first.\n",
            encoding="utf-8",
        )
        assert len(_offences(path, "example.md")) == 1

    def test_a_marker_with_no_introducer_at_all_exempts_nothing(
        self, tmp_path: Path
    ) -> None:
        """Reaches the rejection *after* the introducer loop, which nothing else does.

        This is the guard's only bypass. Turn that final rejection into an
        acceptance and every in-scope line that merely says the marker's words —
        no comment syntax anywhere — would exempt itself.
        """
        pattern = _FORBIDDEN_PATTERNS[0][0]
        path = tmp_path / "example.md"
        path.write_text(
            f"The path {pattern}notes is fine because {_ALLOW_MARKER} I said so.\n",
            encoding="utf-8",
        )
        assert len(_offences(path, "example.md")) == 1


class TestScope:
    """Which files the scan covers, and which are deliberately exempt."""

    def test_the_scan_covers_the_developer_facing_files(self) -> None:
        """A scope that quietly matched nothing would pass while proving nothing."""
        scanned = {str(path.relative_to(_REPO_ROOT)) for path in _shipped_files()}
        for expected in (
            "CLAUDE.md",
            "CONTRIBUTING.md",
            "Dockerfile",
            "Makefile",
            "pyproject.toml",
            "vite.config.ts",
            "vitest.config.ts",
            "docker-compose.yml",
            "docker/entrypoint.sh",
            ".claude/agents/parity-review.md",
            ".github/workflows/ci.yml",
            "docs/SECURITY.md",
            "resources/vite/devServer.ts",
            "tests/test_repository_self_contained.py",
        ):
            assert expected in scanned, f"{expected} is not being scanned"

    def test_the_scanned_directories_are_pinned(self) -> None:
        """Dropping a prefix would shrink the scan with nothing else failing."""
        assert set(_SCANNED_DIRECTORIES) == {
            ".claude/",
            ".github/",
            "docker/",
            "docs/",
            "resources/vite/",
            "tests/",
        }

    def test_the_root_exemptions_are_pinned(self) -> None:
        """Every root file is in scope, so this set is the whole of the decision."""
        assert _ROOT_EXEMPTIONS == {"CHANGELOG.md", "pnpm-lock.yaml", "uv.lock"}

    @pytest.mark.parametrize(
        "exempt",
        [
            # Machine-written, so a match could not be fixed by editing it.
            "CHANGELOG.md",
            "uv.lock",
            # Application code legitimately addresses the machine it runs on.
            "src/ingestion/sources/roms/roms.py",
            # Plugin-local tests and plugin READMEs are developer-facing by this
            # project's layout, but they live under src/ and share its exemption.
            # Recorded so the half-covered test tree reads as a decision.
            "src/ingestion/sources/roms/test_roms.py",
            "src/ingestion/sources/roms/README.md",
            # Interface source, as opposed to the build tooling in resources/vite/.
            "resources/js/stores/app.ts",
            # The example config shows a user what to configure, which can
            # legitimately include a directory on their own machine.
            "config/example.yaml",
            # Import data for users to fill in, not documentation of this project.
            "templates/books.csv",
        ],
    )
    def test_the_deliberate_exemptions_stay_narrow(self, exempt: str) -> None:
        """The exemptions are a decision, so they are written down and cannot creep."""
        assert not _is_in_scope(exempt)
