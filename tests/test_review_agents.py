"""Tests for scripts/check_review_agents.py, the mandated-review-agent gate.

Resolution is exercised against `tmp_path` directories rather than the real
`.claude/agents/`, so nothing here depends on the process's working directory or
on anything outside the repository. The two exceptions are deliberate: the
committed agents are validated through the real parser, and the session-scope
tests set `CLAUDE_PROJECT_DIR` explicitly so the value under test is the one the
test chose.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest
import yaml

from scripts.check_review_agents import (
    _ERROR_DISPLAY_LIMIT,
    _FRONTMATTER_LINE_LIMIT,
    _HOME_PLACEHOLDER,
    _NAME_DISPLAY_LIMIT,
    _REMEDIES,
    MANDATED_AGENTS,
    AgentScope,
    Problem,
    ProblemCategory,
    default_scope,
    find_agent_problems,
    find_scope_problems,
    format_hook_context,
    main,
)

# parents[1] resolves /tests/test_review_agents.py -> repo root.
_REPO_ROOT = Path(__file__).resolve().parents[1]
CLAUDE_MD = _REPO_ROOT / "CLAUDE.md"
CONTRIBUTING_MD = _REPO_ROOT / "CONTRIBUTING.md"
MAKEFILE_PATH = _REPO_ROOT / "Makefile"
CI_WORKFLOW_PATH = _REPO_ROOT / ".github" / "workflows" / "ci.yml"
GATE_WORKFLOW_PATH = _REPO_ROOT / ".github" / "workflows" / "quality-gate.yml"
SETTINGS_JSON_PATH = _REPO_ROOT / ".claude" / "settings.json"
LOCAL_SETTINGS_RELATIVE_PATH = Path(".claude") / "settings.local.json"
COMMITTED_AGENTS_DIR = _REPO_ROOT / ".claude" / "agents"

# The headings whose content is the documented source of truth for which agents
# are mandated and how the checker is wired up, and the bullet form used under
# the first of them: `- **name** — ...`.
_AGENTS_HEADING = "### Agent-Enforced Standards"
_PREFLIGHT_HEADING = "### Review Agent Preflight"
_AGENT_BULLET = re.compile(r"^- \*\*([a-z][a-z-]*)\*\*", re.MULTILINE)
_HEADING_LINE = re.compile(r"#{2,4} ")
_FENCE = "```"

# The prerequisites `make check` is documented to run, in CLAUDE.md and
# CONTRIBUTING.md. Asserting the whole set stops one of them being dropped.
_CHECK_PREREQUISITES = (
    "check-agents",
    "format-check",
    "lint",
    "type-check",
    "test",
    "check-frontend",
)

# The tools whose invocations must cover scripts/ wherever they appear.
_SCRIPTS_AWARE_TOOLS = ("black", "ruff", "mypy")

# Exactly what tracked settings grant. Both keys are ambient authority: an entry
# in `permissions.allow` is pre-approved without a prompt for anyone who checks
# out the branch carrying it, and an enabled plugin is code. Pinning cannot stop
# a hostile branch, which can edit this file too. It makes widening the grant a
# deliberate act, visible in the diff, instead of one quiet line in a settings
# file — adding a permission is *supposed* to cost a test update, so the friction
# is the feature.
#
# WHAT THE `deny` LIST BOUNDS, AND WHAT NOTHING HERE BOUNDS. The denials cover
# execution: `git difftool` takes `--extcmd=<command>`, and it shares a prefix
# with the granted `git diff`, so it is named outright rather than left to depend
# on whether a `:*` prefix matches on the string or on whole arguments. The
# `diff-*` plumbing family is denied alongside it because no reviewer invokes
# plumbing and a future member could gain such a flag. Even that is a claim about
# a *name*: a local `alias.diffmine`, or any `git-diffmine` on PATH, extends the
# granted prefix and may be a shell. Git ignores aliases that shadow existing
# commands, so the risk is the extending name rather than the shadowing one, and
# that surface is machine-local and cannot be enumerated from here.
#
# Reads and writes are NOT bounded, deliberately. `git diff --no-index a b`
# prints any two files on the machine — confirmed from `git diff -h` — and
# `git diff --output=<file>` is documented to create or truncate any path, which
# nobody here has verified. Both are unprompted, both match the granted prefix,
# and neither is closeable by a deny, because a flag can sit anywhere in the
# arguments and a rule that misses `git diff --stat --no-index a b` reads as
# closed while being open. The grant was kept knowing this: see docs/SECURITY.md
# for the trade, and note that what actually holds the line is the agents' own
# read-only rule, which is prose rather than enforcement.
_TRACKED_GRANTS = {
    "permissions": {
        "allow": [
            "Bash(git status:*)",
            "Bash(git diff:*)",
            "Bash(git log:*)",
            "mcp__ide__getDiagnostics",
        ],
        "deny": [
            "Bash(git difftool:*)",
            "Bash(git diff-files:*)",
            "Bash(git diff-index:*)",
            "Bash(git diff-tree:*)",
            "Bash(git diff-pairs:*)",
        ],
    },
    "enabledPlugins": {
        "pyright-lsp@claude-plugins-official": True,
        "frontend-design@claude-plugins-official": True,
    },
}

# The only top-level keys tracked `.claude/settings.json` may declare. Derived
# from the value pin rather than restated, so a key cannot be admitted here while
# nothing pins what it may say. The two tests stay distinct: this one rejects a
# key nobody has thought of yet — `hooks` is not the only setting that makes
# checking out a contributed branch act as the reviewer — while the value pin
# covers a nested arrival like `permissions.defaultMode`.
_ALLOWED_SETTINGS_KEYS = frozenset(_TRACKED_GRANTS)

# The pre-commit workflow is written out twice, for two audiences. Duplicating it
# is the decision; letting the copies disagree is not, and this project has
# watched them drift twice. Matched case-insensitively so a sentence-initial
# capital in one copy is not a failure, and on phrases rather than whole steps so
# each copy keeps its own register.
_WORKFLOW_HEADING = "## Pre-commit Workflow"
_WORKFLOW_STEP = re.compile(r"^\d+\. ", re.MULTILINE)
_WORKFLOW_PHRASES = (
    "scope is frozen",
    "correctness or security defect in code that change introduced",
    "no criticals and no highs",
    "cut is stated explicitly with its reason",
    "deferral gets a tracker issue naming the stream it lands in",
    "a deferral with no tracker entry is a cut pretending otherwise",
    # The requirement the convergence rule must not weaken.
    "the **same** tree",
)


@pytest.fixture
def home_outside_the_test_tree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point `Path.home()` at a directory nothing under `tmp_path` lives in.

    The report abbreviates a home-rooted path to a tilde, so a test asserting on
    a full `tmp_path` path fails when `TMPDIR` happens to sit under the real home
    directory. Redirecting home to a child of `tmp_path` makes `tmp_path` itself
    un-abbreviated whatever the machine's layout is.
    """
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))


def _write_agent(directory: Path, name: str, declared_name: str | None = None) -> Path:
    """Write a minimal loadable agent file, returning its path."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name}.md"
    declared = name if declared_name is None else declared_name
    path.write_text(
        f"---\nname: {declared}\ndescription: A test agent.\n---\n\nBody.\n",
        encoding="utf-8",
    )
    return path


def _write_every_agent(directory: Path) -> None:
    """Write a loadable file for every mandated agent."""
    for name in MANDATED_AGENTS:
        _write_agent(directory, name)


def _frontmatter_of_length(lines: int) -> str:
    """Return a `parity-review` file whose frontmatter is exactly `lines` long."""
    filler = [f"field{index}: value" for index in range(lines - 2)]
    body = ["name: parity-review", "description: A test agent.", *filler]
    return "---\n" + "\n".join(body) + "\n---\n\nBody.\n"


def _scope(project_agents: Path, session_project_dir: Path | None = None) -> AgentScope:
    """Build a scope over test directories, rooted here unless told otherwise."""
    return AgentScope(
        session_project_dir=(
            _REPO_ROOT if session_project_dir is None else session_project_dir
        ),
        project_agents=project_agents,
    )


def _claude_md_section(heading: str) -> str:
    """Return the CLAUDE.md text under `heading`, up to the next heading."""
    return _document_section(CLAUDE_MD, heading)


def _document_section(path: Path, heading: str) -> str:
    """Return the text under `heading`, up to the next heading.

    Fenced blocks are skipped: the Preflight section contains one, and a line
    inside it beginning `## ` is JSON or shell, not a heading. Splitting on the
    raw pattern would silently shrink the section a test then scans.
    """
    text = path.read_text(encoding="utf-8")
    assert heading in text, f"{path.name} no longer has {heading!r}"
    section: list[str] = []
    inside_fence = False
    for line in text.split(heading, 1)[1].splitlines():
        if line.startswith(_FENCE):
            inside_fence = not inside_fence
        elif not inside_fence and _HEADING_LINE.match(line):
            break
        section.append(line)
    return "\n".join(section)


def _documented_agents() -> list[str]:
    """Return the agent names bulleted under CLAUDE.md's agent-standards heading."""
    return _AGENT_BULLET.findall(_claude_md_section(_AGENTS_HEADING))


def _makefile_recipe(target: str) -> list[str]:
    """Return the tab-indented recipe lines of a Makefile target.

    Asserting a target *exists* says nothing: replace its recipe with `@true` and
    a name-only assertion stays green while the target verifies nothing.
    """
    lines = MAKEFILE_PATH.read_text(encoding="utf-8").splitlines()
    starts = [
        index for index, line in enumerate(lines) if line.startswith(f"{target}:")
    ]
    assert starts, f"Makefile has no `{target}:` target"
    recipe = []
    for line in lines[starts[0] + 1 :]:
        if line.startswith("\t"):
            recipe.append(line.strip())
        elif line.strip():
            break
    assert recipe, f"Makefile target `{target}` has an empty recipe"
    return recipe


def _makefile_prerequisites(target: str) -> list[str]:
    """Return the prerequisites declared on a Makefile target."""
    makefile = MAKEFILE_PATH.read_text(encoding="utf-8")
    match = re.search(rf"^{target}:(?P<prerequisites>.*)$", makefile, re.MULTILINE)
    assert match is not None, f"Makefile has no `{target}:` target"
    return match.group("prerequisites").split()


def _gate_steps() -> list[dict[str, object]]:
    """Return the steps of the reusable workflow CI calls.

    Parsed rather than substring-matched, so a commented-out step or a path that
    only appears as a linter argument does not read as the step being present.
    """
    workflow = yaml.safe_load(GATE_WORKFLOW_PATH.read_text(encoding="utf-8"))
    steps: list[dict[str, object]] = workflow["jobs"]["check"]["steps"]
    return steps


def _gate_commands() -> list[str]:
    """Return every shell command that reusable workflow runs."""
    commands = [str(step["run"]) for step in _gate_steps() if "run" in step]
    assert commands, "the gate runs no commands; every sweep over it would be empty"
    return commands


class TestFindAgentProblems:
    """Every mandated agent is present, readable, and would actually load."""

    def test_no_problems_when_every_agent_resolves(self, tmp_path: Path) -> None:
        """A checkout with all seven agents committed is clean."""
        agents = tmp_path / "agents"
        _write_every_agent(agents)
        assert find_agent_problems(MANDATED_AGENTS, _scope(agents)) == []

    def test_missing_agent_is_reported(self, tmp_path: Path) -> None:
        """An agent with no committed file is reported by name."""
        agents = tmp_path / "agents"
        _write_agent(agents, "code-review")
        problems = find_agent_problems(["code-review", "parity-review"], _scope(agents))
        assert [problem.agent_name for problem in problems] == ["parity-review"]
        assert problems[0].category is ProblemCategory.MISSING

    def test_dangling_symlink_is_reported_as_a_missing_file(
        self, tmp_path: Path
    ) -> None:
        """A link with no target is not a file, so it lands on the missing branch."""
        agents = tmp_path / "agents"
        agents.mkdir()
        (agents / "code-review.md").symlink_to(tmp_path / "gone.md")
        problems = find_agent_problems(["code-review"], _scope(agents))
        assert [problem.category for problem in problems] == [ProblemCategory.MISSING]
        assert "code-review.md" in problems[0].detail

    def test_frontmatter_name_mismatch_is_reported(self, tmp_path: Path) -> None:
        """A `name:` that disagrees with the filename registers under the wrong name."""
        agents = tmp_path / "agents"
        _write_agent(agents, "parity-review", declared_name="parity_review")
        problems = find_agent_problems(["parity-review"], _scope(agents))
        assert [problem.category for problem in problems] == [
            ProblemCategory.BAD_DEFINITION
        ]
        assert "declares name 'parity_review'" in problems[0].detail

    def test_empty_frontmatter_name_is_reported_as_a_mismatch(
        self, tmp_path: Path
    ) -> None:
        """`name: ''` is a string, so it reaches the mismatch branch, not the missing one."""
        agents = tmp_path / "agents"
        _write_agent(agents, "parity-review", declared_name="''")
        problems = find_agent_problems(["parity-review"], _scope(agents))
        assert "declares name ''" in problems[0].detail

    @pytest.mark.parametrize(
        ("content", "expected"),
        [
            ("", "does not open with a '---'"),
            ("name: parity-review\n", "does not open with a '---'"),
            ("---\nname: parity-review\n", "never closed by a '---' line"),
            ("---\nname: [unclosed\n---\n", "not valid YAML"),
            ("---\njust a scalar\n---\n", "not a YAML mapping"),
            ("---\ndescription: no name here\n---\n\nBody.\n", "no 'name:' field"),
            ("---\nname:\n---\n\nBody.\n", "no 'name:' field"),
            ("---\nname: 42\n---\n\nBody.\n", "no 'name:' field"),
            ("---\nname: parity-review\n---\n\nBody.\n", "no 'description:' field"),
            (
                "---\nname: parity-review\ndescription: []\n---\n\nBody.\n",
                "no 'description:' field",
            ),
            (
                "---\nname: parity-review\ndescription: '  '\n---\n\nBody.\n",
                "no 'description:' field",
            ),
            (
                "---\nname: parity-review\ndescription: Fine.\n---\n\n\n",
                "no instructions after its frontmatter",
            ),
        ],
    )
    def test_a_file_that_would_not_load_is_reported(
        self, tmp_path: Path, content: str, expected: str
    ) -> None:
        """Claude Code needs `name:`, `description:` and a body; all three are checked.

        A truncated agent file is valid Markdown and an unloadable agent, which is
        the silent failure this whole module exists to catch.
        """
        agents = tmp_path / "agents"
        agents.mkdir()
        (agents / "parity-review.md").write_text(content, encoding="utf-8")
        problems = find_agent_problems(["parity-review"], _scope(agents))
        assert [problem.category for problem in problems] == [
            ProblemCategory.BAD_DEFINITION
        ]
        assert expected in problems[0].detail

    def test_an_oversized_frontmatter_is_rejected_before_it_is_parsed(
        self, tmp_path: Path
    ) -> None:
        """An agent definition is a handful of keys, so a huge one is malformed."""
        agents = tmp_path / "agents"
        agents.mkdir()
        (agents / "parity-review.md").write_text(
            _frontmatter_of_length(_FRONTMATTER_LINE_LIMIT + 1), encoding="utf-8"
        )
        problems = find_agent_problems(["parity-review"], _scope(agents))
        assert [problem.category for problem in problems] == [
            ProblemCategory.BAD_DEFINITION
        ]
        assert f"longer than {_FRONTMATTER_LINE_LIMIT} lines" in problems[0].detail

    def test_a_frontmatter_exactly_at_the_limit_still_loads(
        self, tmp_path: Path
    ) -> None:
        """Only the rejecting side of a bound is normally tested, and that is half of it.

        Tighten the comparison by one and a legitimate agent is refused as "not an
        agent definition" — a hard stop on every `make check` and every CI run, for
        a reason the report actively misdescribes.
        """
        agents = tmp_path / "agents"
        agents.mkdir()
        (agents / "parity-review.md").write_text(
            _frontmatter_of_length(_FRONTMATTER_LINE_LIMIT), encoding="utf-8"
        )
        assert find_agent_problems(["parity-review"], _scope(agents)) == []

    def test_unreadable_file_is_reported(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A file that exists but cannot be read is a problem, not a traceback.

        `read_bytes` is monkeypatched rather than the mode chmodded because CI
        containers often run as root, where a 0o000 file stays readable and the
        test would silently assert nothing.
        """
        agents = tmp_path / "agents"
        _write_agent(agents, "parity-review")

        def deny_read(self: Path) -> bytes:
            raise PermissionError(13, "Permission denied")

        monkeypatch.setattr(Path, "read_bytes", deny_read)
        problems = find_agent_problems(["parity-review"], _scope(agents))
        assert [problem.category for problem in problems] == [
            ProblemCategory.UNREADABLE
        ]
        assert "Permission denied" in problems[0].detail

    def test_undecodable_file_is_reported(self, tmp_path: Path) -> None:
        """Bytes that are not UTF-8 are reported rather than raising."""
        agents = tmp_path / "agents"
        agents.mkdir()
        (agents / "parity-review.md").write_bytes(b"---\nname: \xffbad\n---\n")
        problems = find_agent_problems(["parity-review"], _scope(agents))
        assert [problem.category for problem in problems] == [
            ProblemCategory.UNREADABLE
        ]


class TestFileDerivedTextIsBounded:
    """File content reaches a terminal, so it is escaped and length-capped."""

    def test_control_characters_in_the_declared_name_are_escaped(
        self, tmp_path: Path
    ) -> None:
        """A crafted `name:` cannot emit ANSI escapes that rewrite the report."""
        agents = tmp_path / "agents"
        _write_agent(agents, "parity-review", declared_name='"\\x1b[2Kwiped"')
        problems = find_agent_problems(["parity-review"], _scope(agents))
        assert "\x1b" not in problems[0].detail
        assert "\\x1b[2Kwiped" in problems[0].detail

    def test_a_long_declared_name_is_truncated(self, tmp_path: Path) -> None:
        """An enormous `name:` cannot flood the report.

        The bounded fragment is measured, not the whole detail: the detail also
        carries the file path, so a total-length assertion would be measuring
        `TMPDIR` depth rather than the contract.
        """
        agents = tmp_path / "agents"
        _write_agent(agents, "parity-review", declared_name="z" * 5000)
        problems = find_agent_problems(["parity-review"], _scope(agents))
        declared = problems[0].detail.split("declares name ", 1)[1]
        assert len(declared) <= _NAME_DISPLAY_LIMIT + len("...")
        assert declared.endswith("...")

    def test_a_yaml_error_is_reduced_to_a_bounded_first_line(
        self, tmp_path: Path
    ) -> None:
        """PyYAML errors are multi-line and quote the file; only line one is shown."""
        agents = tmp_path / "agents"
        agents.mkdir()
        (agents / "parity-review.md").write_text(
            "---\nname: [unclosed\n---\n", encoding="utf-8"
        )
        problems = find_agent_problems(["parity-review"], _scope(agents))
        assert "\n" not in problems[0].detail
        assert "\\n" not in problems[0].detail
        quoted = problems[0].detail.split("not valid YAML: ", 1)[1]
        assert len(quoted) <= _ERROR_DISPLAY_LIMIT + len("...")


class TestSessionProjectScope:
    """Which project directory the checker treats as the session's."""

    def test_environment_variable_pointing_here_is_clean(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`CLAUDE_PROJECT_DIR` is what decides, not the working directory."""
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(_REPO_ROOT))
        monkeypatch.chdir(tmp_path)
        assert find_scope_problems(default_scope()) == []

    def test_environment_variable_pointing_elsewhere_is_reported(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        home_outside_the_test_tree: None,
    ) -> None:
        """A session rooted outside the repo sees none of the committed agents."""
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
        monkeypatch.chdir(_REPO_ROOT)
        problems = find_scope_problems(default_scope())
        assert [problem.category for problem in problems] == [
            ProblemCategory.SESSION_SCOPE
        ]
        assert str(tmp_path.resolve()) in problems[0].detail
        assert f"{len(MANDATED_AGENTS)} agents" in problems[0].detail
        assert ".claude/agents" in problems[0].detail

    @pytest.mark.parametrize("suffix", ["", "/", "/."])
    def test_a_non_canonical_project_directory_is_still_this_repository(
        self, monkeypatch: pytest.MonkeyPatch, suffix: str
    ) -> None:
        """A gate that cries wolf gets bypassed, so spelling must not matter."""
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", f"{_REPO_ROOT}{suffix}")
        assert find_scope_problems(default_scope()) == []

    def test_a_symlinked_project_directory_is_still_this_repository(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Reaching the repo through a symlink is a common layout, not a mis-rooted session."""
        link = tmp_path / "linked-repo"
        link.symlink_to(_REPO_ROOT, target_is_directory=True)
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(link))
        assert find_scope_problems(default_scope()) == []

    @pytest.mark.parametrize("working_directory_is_the_repository", [True, False])
    def test_no_session_is_never_a_scope_problem(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        working_directory_is_the_repository: bool,
    ) -> None:
        """With no `CLAUDE_PROJECT_DIR` there is no session and no search path.

        `make check` and CI both run this from a plain shell. There the working
        directory says nothing about which agents Claude Code would resolve, so
        reporting on it would be a failure with no meaning behind it.
        """
        monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
        monkeypatch.chdir(
            _REPO_ROOT if working_directory_is_the_repository else tmp_path
        )
        scope = default_scope()
        assert scope.session_project_dir is None
        assert find_scope_problems(scope) == []

    def test_no_session_still_looks_for_the_agents_in_this_repository(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The committed agents are found from any working directory."""
        monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
        monkeypatch.chdir(tmp_path)
        assert default_scope().project_agents == COMMITTED_AGENTS_DIR

    def test_home_relative_paths_are_reported_as_tilde(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Reports get pasted into issues, so the OS username stays out of them."""
        home = (tmp_path / "home").resolve()
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(home / "elsewhere"))
        detail = find_scope_problems(default_scope())[0].detail
        assert str(home) not in detail, "the report leaked the home directory"
        assert "session project directory is ~" in detail
        assert "elsewhere" in detail

    def test_the_home_directory_itself_is_reported_as_a_bare_tilde(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A session rooted at home exactly must not render as a trailing separator."""
        home = (tmp_path / "home").resolve()
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(home))
        detail = find_scope_problems(default_scope())[0].detail
        assert "session project directory is ~," in detail

    def test_the_report_still_renders_when_there_is_no_home_directory(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`Path.home()` raises with no `HOME` and no passwd entry — some containers.

        Abbreviating the path is cosmetic, so it must not cost the whole report at
        the one moment someone needs to read it.
        """

        def no_home_directory(cls: type[Path]) -> Path:
            raise RuntimeError("Could not determine home directory.")

        monkeypatch.setattr(Path, "home", classmethod(no_home_directory))
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
        detail = find_scope_problems(default_scope())[0].detail
        assert str(tmp_path.resolve()) in detail

    def test_no_line_of_a_whole_report_leaks_the_home_directory(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Every path the report renders is abbreviated, not just the scope line.

        `_display_path` is called at several sites. Asserting one of them leaves
        the rest free to interpolate a raw path and grow an OS username, so this
        drives a report carrying a problem from every path-bearing site at once.
        """
        home = (tmp_path / "home").resolve()
        agents = home / "checkout" / ".claude" / "agents"
        _write_every_agent(agents)
        (agents / "code-review.md").unlink()
        (agents / "test-review.md").write_text("truncated\n", encoding="utf-8")
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))

        assert main([], scope=_scope(agents, session_project_dir=home / "wrong")) == 1

        report = capsys.readouterr().err
        assert str(home) not in report, "a report line leaked the home directory"
        assert f"{_HOME_PLACEHOLDER}/checkout/.claude/agents" in report
        assert f"{_HOME_PLACEHOLDER}/wrong" in report


class TestMainCommandLineMode:
    """The default mode: full report on stderr, non-zero exit."""

    def test_exit_code_is_zero_and_silent_when_every_agent_resolves(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Success prints nothing at all, on either stream."""
        agents = tmp_path / "agents"
        _write_every_agent(agents)
        assert main([], scope=_scope(agents)) == 0
        assert capsys.readouterr() == ("", "")

    def test_report_names_every_problem_and_directory_searched(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
        home_outside_the_test_tree: None,
    ) -> None:
        """With no agents at all, every mandated agent is named and counted."""
        agents = tmp_path / "agents"
        agents.mkdir()
        scope = _scope(agents)
        assert main([], scope=scope) == 1
        report = capsys.readouterr().err
        assert f"{len(MANDATED_AGENTS)} problem(s)" in report
        for name in MANDATED_AGENTS:
            assert f"  - {name}: " in report
        assert str(agents) in report
        assert "hard stop" in report

    def test_a_mixed_report_carries_the_scope_line_and_an_accurate_count(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """The realistic failure is a mis-rooted session *and* an incomplete checkout."""
        agents = tmp_path / "agents"
        _write_every_agent(agents)
        (agents / "code-review.md").unlink()
        elsewhere = tmp_path / "elsewhere"

        assert main([], scope=_scope(agents, session_project_dir=elsewhere)) == 1

        report = capsys.readouterr().err
        assert "2 problem(s)" in report
        assert "Claude Code's search path" in report
        assert "  - code-review: " in report

    def test_one_report_names_the_agent_directory_consistently(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
        home_outside_the_test_tree: None,
    ) -> None:
        """The scope bullet and the footer describe the same directory, so they must agree.

        They were rendered from different sources — one rebuilt the repository path
        while the other used the scope — so a single run could name two directories
        for one thing, and no test could catch it.
        """
        agents = tmp_path / "agents"
        _write_every_agent(agents)

        assert main([], scope=_scope(agents, session_project_dir=tmp_path)) == 1

        report = capsys.readouterr().err
        assert report.count(str(agents)) == 2

    def test_only_the_scope_remedy_is_offered_for_a_mis_rooted_session(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Every committed agent is present and correct, so restoring them is not the fix."""
        agents = tmp_path / "agents"
        _write_every_agent(agents)

        assert main([], scope=_scope(agents, session_project_dir=tmp_path)) == 1

        report = capsys.readouterr().err
        assert "Restart Claude Code at the repository root" in report
        assert "git checkout" not in report

    def test_only_the_restore_remedy_is_offered_for_a_broken_checkout(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """The session can see the agents, so where it started is not the fix."""
        agents = tmp_path / "agents"
        _write_every_agent(agents)
        (agents / "code-review.md").unlink()

        assert main([], scope=_scope(agents)) == 1

        report = capsys.readouterr().err
        assert "git checkout -- .claude/agents/" in report
        assert "Restart Claude Code" not in report

    def test_both_remedies_appear_once_each_when_both_apply(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Three categories, two remedies: the shared one must not print twice."""
        agents = tmp_path / "agents"
        _write_every_agent(agents)
        (agents / "code-review.md").unlink()
        (agents / "test-review.md").write_text("truncated\n", encoding="utf-8")

        assert main([], scope=_scope(agents, session_project_dir=tmp_path)) == 1

        report = capsys.readouterr().err
        assert "3 problem(s)" in report
        assert report.count("Restart Claude Code at the repository root") == 1
        assert report.count("git checkout -- .claude/agents/") == 1

    def test_no_argument_invocation_is_silent_when_every_agent_resolves(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The Makefile and the hook both invoke the no-argument entry point."""
        agents = tmp_path / "agents"
        _write_every_agent(agents)
        monkeypatch.setattr("sys.argv", ["check_review_agents.py"])
        assert main(scope=_scope(agents)) == 0

    def test_project_directory_from_the_environment_drives_the_exit_code(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        home_outside_the_test_tree: None,
    ) -> None:
        """End to end: a session project directory outside the repo fails the check.

        Nothing is written into `elsewhere`: the committed agents are looked for
        here regardless of where the session is rooted, so a mis-rooted session is
        one problem rather than one plus seven imaginary missing files.
        """
        elsewhere = tmp_path / "elsewhere"
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(elsewhere))
        monkeypatch.setattr("sys.argv", ["check_review_agents.py"])

        assert main() == 1

        report = capsys.readouterr().err
        assert "1 problem(s)" in report
        assert str(elsewhere) in report
        assert "Claude Code's search path" in report
        assert str(COMMITTED_AGENTS_DIR) in report
        assert str(elsewhere / ".claude") not in report
        for name in MANDATED_AGENTS:
            assert f"  - {name}: " not in report


class TestMainHookMode:
    """`--hook` mode: structured stdout at exit 0, constants only."""

    def test_hook_mode_is_silent_on_success(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A passing session start emits nothing, so it costs the model no context."""
        agents = tmp_path / "agents"
        _write_every_agent(agents)
        assert main(["--hook"], scope=_scope(agents)) == 0
        assert capsys.readouterr() == ("", "")

    def test_hook_mode_emits_session_start_json_and_exits_zero(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Only stdout at exit 0 reaches the model's context, so failure goes there."""
        agents = tmp_path / "agents"
        _write_every_agent(agents)
        (agents / "parity-review.md").unlink()
        assert main(["--hook"], scope=_scope(agents)) == 0
        captured = capsys.readouterr()
        assert captured.err == ""
        hook_output = json.loads(captured.out)["hookSpecificOutput"]
        assert hook_output["hookEventName"] == "SessionStart"
        context = hook_output["additionalContext"]
        assert "parity-review" in context
        assert "make check-agents" in context

    def test_a_problem_belonging_to_no_agent_still_emits_valid_json(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A mis-rooted session names no agent, so the affected-agents line is omitted."""
        agents = tmp_path / "agents"
        _write_every_agent(agents)
        scope = _scope(agents, session_project_dir=tmp_path / "elsewhere")

        assert main(["--hook"], scope=scope) == 0

        context = json.loads(capsys.readouterr().out)["hookSpecificOutput"][
            "additionalContext"
        ]
        assert ProblemCategory.SESSION_SCOPE.value in context
        assert "Agents affected" not in context

    def test_hook_context_contains_no_file_derived_text(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Agent files are repo-controlled, so none of their content is injected.

        Both file-derived details a contributed branch fully controls are planted:
        a declared `name:`, and the PyYAML error string, which quotes the offending
        line of the file back verbatim.
        """
        agents = tmp_path / "distinctive-directory-name"
        _write_every_agent(agents)
        _write_agent(agents, "parity-review", declared_name="INJECTED-NAME")
        (agents / "code-review.md").write_text(
            "---\nname: [INJECTED-VIA-YAML-ERROR\n---\n\nBody.\n", encoding="utf-8"
        )
        assert main(["--hook"], scope=_scope(agents)) == 0
        context = json.loads(capsys.readouterr().out)["hookSpecificOutput"][
            "additionalContext"
        ]
        assert "INJECTED-NAME" not in context
        assert "INJECTED-VIA-YAML-ERROR" not in context
        assert "distinctive-directory-name" not in context
        assert str(tmp_path) not in context
        assert "parity-review" in context

    def test_an_off_list_agent_name_cannot_reach_the_context(self) -> None:
        """The affected list is filtered through `MANDATED_AGENTS`, not just sorted.

        The constants-only property rests on two facts: categories are enum
        literals, and names come from the mandated tuple. A refactor to
        `sorted(affected_names)` would keep every other test green while opening a
        channel for a filename a contributed branch chose.
        """
        context = format_hook_context(
            [Problem(ProblemCategory.MISSING, "INJECTED-AGENT-NAME", "detail")]
        )
        assert "INJECTED-AGENT-NAME" not in context
        assert "Agents affected" not in context

    @pytest.mark.parametrize("category", list(ProblemCategory))
    def test_hook_mode_offers_no_remedy_at_all(self, category: ProblemCategory) -> None:
        """The injected text is constants-only; a remedy there is one more to keep constant."""
        context = format_hook_context([Problem(category, None, "detail")])
        assert "git checkout" not in context
        assert "Restart Claude Code" not in context
        assert "make check-agents" in context

    @pytest.mark.parametrize("category", list(ProblemCategory))
    def test_no_problem_detail_of_any_category_reaches_the_context(
        self, category: ProblemCategory
    ) -> None:
        """Exhaustive over the enum, so a new category cannot arrive uncovered."""
        marker = "INJECTED-FROM-DISK"
        context = format_hook_context(
            [Problem(category, "code-review", f"/some/path: {marker}")]
        )
        assert marker not in context
        assert "/some/path" not in context
        assert category.value in context


class TestMandatedAgentList:
    """The checker's list and CLAUDE.md's documented list must stay identical."""

    def test_script_list_matches_claude_md(self) -> None:
        """Adding or removing a mandated agent must touch both representations."""
        documented = _documented_agents()
        assert set(documented) == set(MANDATED_AGENTS), (
            "scripts/check_review_agents.py MANDATED_AGENTS and CLAUDE.md's "
            f"'{_AGENTS_HEADING}' list disagree: "
            f"only in CLAUDE.md {sorted(set(documented) - set(MANDATED_AGENTS))}, "
            f"only in the script {sorted(set(MANDATED_AGENTS) - set(documented))}"
        )
        assert len(documented) == len(
            set(documented)
        ), f"CLAUDE.md lists a mandated agent twice: {documented}"

    def test_no_agent_is_listed_twice(self) -> None:
        """Set comparison elsewhere cannot see a duplicated tuple entry."""
        assert len(MANDATED_AGENTS) == len(set(MANDATED_AGENTS))

    def test_every_problem_category_has_a_remedy(self) -> None:
        """The report indexes the mapping directly, so a gap would raise mid-report."""
        assert set(_REMEDIES) == set(ProblemCategory)
        for category, remedy in _REMEDIES.items():
            assert remedy, f"{category.name} has an empty remedy"

    def test_only_the_mandated_agents_are_committed(self) -> None:
        """The committed set is pinned, not just its lower bound.

        These files are prompts that direct the agents reviewing this repository
        and run with the reviewer's tool permissions (docs/SECURITY.md), so an
        added, renamed or left-behind file is worth noticing. It also catches an
        editor backup dropped beside a real agent.
        """
        assert COMMITTED_AGENTS_DIR.is_dir(), f"no {COMMITTED_AGENTS_DIR} to read"
        committed = sorted(path.name for path in COMMITTED_AGENTS_DIR.iterdir())
        assert committed == sorted(f"{name}.md" for name in MANDATED_AGENTS)

    def test_claude_md_documents_the_project_directory_requirement(self) -> None:
        """Starting a session outside the repository is the original incident.

        Nothing detects it from inside that session, so the requirement has to be
        written down where the agents are. Keyed on the terms the code actually
        uses: it compares the session's *project directory* against the
        *repository root*, and never looks at the working directory.
        """
        section = _claude_md_section(_AGENTS_HEADING)
        assert "project directory" in section
        assert "repository root" in section
        assert "make check-agents" in _claude_md_section(_PREFLIGHT_HEADING)


class TestCommittedAgentFrontmatter:
    """The committed agents stay inside the bound that keeps them loadable."""

    @pytest.mark.parametrize("name", MANDATED_AGENTS)
    def test_no_agents_frontmatter_approaches_the_checker_bound(
        self, name: str
    ) -> None:
        """`_FRONTMATTER_LINE_LIMIT` rejects a file as "not an agent definition".

        The bound's own comment used to claim the agents sat comfortably under it
        and nothing checked that, twice with a different wrong figure. This asserts
        the margin instead of describing it: `parity-review` is the tallest, its
        frontmatter is prose that grows, and crossing the bound is a hard stop on
        every `make check` and every CI run — reported as a malformed file rather
        than as a file that got longer, which is the confusing way to find out.
        """
        text = (COMMITTED_AGENTS_DIR / f"{name}.md").read_text(encoding="utf-8")
        lines = text.splitlines()
        closing = lines.index("---", 1)
        assert closing - 1 <= _FRONTMATTER_LINE_LIMIT // 2, (
            f"{name}'s frontmatter is {closing - 1} lines, over half the "
            f"{_FRONTMATTER_LINE_LIMIT}-line bound in check_review_agents.py — "
            "raise the bound and the comment that describes it together"
        )


class TestWorkflowDocumentsAgree:
    """The pre-commit workflow exists twice, so the two copies are pinned together."""

    def test_both_copies_have_the_same_number_of_steps(self) -> None:
        """A step added to one copy and not the other is how these drifted before.

        Counting steps catches the shape of that drift — an inserted or dropped
        step — which phrase matching cannot see, and it is what makes the two
        copies' step numbers refer to the same things.
        """
        counts = {
            path.name: len(
                _WORKFLOW_STEP.findall(_document_section(path, _WORKFLOW_HEADING))
            )
            for path in (CLAUDE_MD, CONTRIBUTING_MD)
        }
        assert (
            len(set(counts.values())) == 1
        ), f"the workflows disagree in length: {counts}"

    @pytest.mark.parametrize("phrase", _WORKFLOW_PHRASES)
    def test_both_copies_state_every_rule(self, phrase: str) -> None:
        """Scope freeze and convergence only work if both audiences are told them.

        These two files were duplicated near-verbatim with nothing asserting they
        matched — the same drift class this workflow's own rules exist to close,
        reintroduced in the change that wrote them.
        """
        for path in (CLAUDE_MD, CONTRIBUTING_MD):
            section = _document_section(path, _WORKFLOW_HEADING).lower()
            assert phrase.lower() in section, f"{path.name}'s workflow omits {phrase!r}"


class TestDocumentedHookSnippet:
    """The opt-in hook snippet in CLAUDE.md is the only tracked copy of it."""

    def test_snippet_runs_the_checker_in_hook_mode(self) -> None:
        """A snippet that drifts from the script's interface is worse than none."""
        blocks = re.findall(
            r"```json\n(.*?)```", _claude_md_section(_PREFLIGHT_HEADING), re.DOTALL
        )
        assert len(blocks) == 1, "expected exactly one JSON snippet in the section"
        settings = json.loads(blocks[0])
        commands = [
            hook["command"]
            for matcher in settings["hooks"]["SessionStart"]
            for hook in matcher["hooks"]
            if hook["type"] == "command"
        ]
        assert len(commands) == 1
        for fragment in (
            "python3.11",
            "$CLAUDE_PROJECT_DIR",
            "scripts/check_review_agents.py",
            "--hook",
        ):
            assert fragment in commands[0], f"documented hook omits {fragment}"


class TestQualityGateWiring:
    """The check must run from both entry points developers and CI actually use."""

    def test_check_agents_target_runs_the_checker(self) -> None:
        """A target that exists but no longer invokes the script verifies nothing."""
        recipe = _makefile_recipe("check-agents")
        assert any(
            "scripts/check_review_agents.py" in line for line in recipe
        ), f"`make check-agents` no longer runs the checker: {recipe}"

    def test_check_runs_exactly_the_documented_prerequisites_in_order(self) -> None:
        """`make check` runs exactly these prerequisites, in exactly this order.

        This reads the Makefile only — it pins what `make check` does, not what the
        doc says about it. Membership alone would let a reorder or an appended
        seventh pass, and `check-agents` running first is load-bearing: it fails in
        under a second, so it must not sit behind the whole test suite.
        """
        assert _makefile_prerequisites("check") == list(_CHECK_PREREQUISITES)

    @pytest.mark.parametrize("target", ["lint", "format", "format-check", "type-check"])
    def test_makefile_recipes_cover_the_scripts_directory(self, target: str) -> None:
        """Drop `scripts/` from one and the checker goes unlinted, suite still green."""
        for line in _makefile_recipe(target):
            assert "scripts/" in line, f"`make {target}` skips scripts/: {line}"

    def test_ci_reaches_the_checker_through_the_makefile(self) -> None:
        """CI runs the gate `make check` defines, so the two cannot disagree.

        `make check`'s own prerequisites are pinned above, which is what makes
        one `make check` step enough to know the review-agent check ran.
        """
        workflow = yaml.safe_load(CI_WORKFLOW_PATH.read_text(encoding="utf-8"))
        assert (
            workflow["jobs"]["check"]["uses"] == "./.github/workflows/quality-gate.yml"
        )

        steps = [step for step in _gate_steps() if "make check" in str(step.get("run"))]
        assert len(steps) == 1, (
            "CI must run `make check` in exactly one step, or a branch could "
            f"delete a mandated agent and still go green: {steps}"
        )
        assert (
            steps[0].get("continue-on-error") is not True
        ), "the quality-gate step is allowed to fail, which makes it advisory"

    @pytest.mark.parametrize("tool", _SCRIPTS_AWARE_TOOLS)
    def test_ci_never_restates_a_tool_the_makefile_owns(self, tool: str) -> None:
        """A second copy of the gate in YAML is a copy that goes stale unnoticed."""
        for command in _gate_commands():
            assert tool not in command.split(), (
                f"CI invokes {tool} itself instead of through `make check`, which "
                f"is how the two lists drift: {command}"
            )


class TestReviewAgentGateRegression:
    """Regression tests for the silently unsatisfiable review-agent gate."""

    def test_every_mandated_agent_is_committed_and_loadable_regression(self) -> None:
        """Regression test: a mandated review agent went missing silently.

        Bug reported: launching `parity-review` failed with "Agent type
        'parity-review' not found", yet the pre-commit gate reported success and
        two commits shipped with no CLI/web parity review at all. Only
        `parity-review` was even committed, so the other six could not be
        verified by anything at all.
        Root cause: nothing checked that a mandated agent could be launched, so
        an agent that never loaded was indistinguishable from one that approved.
        Fix: all seven agents are committed under `.claude/agents/`, and this
        test validates every one of them through the real parser, reading only
        this repository so it holds in CI too.
        """
        assert find_agent_problems(MANDATED_AGENTS, _scope(COMMITTED_AGENTS_DIR)) == []
        for name in MANDATED_AGENTS:
            path = COMMITTED_AGENTS_DIR / f"{name}.md"
            assert not path.is_symlink(), f"{name} is committed as a symlink"

    def test_missing_mandated_agent_fails_loudly_regression(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Regression test: a missing review agent produced no signal at all.

        Bug reported: the gate reported success while `parity-review` could not
        be launched, because nothing looked for it before the agents ran.
        Root cause: an agent that never loaded is silent, unlike a skipped
        approval, which the workflow's own wording catches.
        Fix: `make check-agents` resolves every mandated agent up front and exits
        non-zero with a report naming each one that cannot be launched.
        """
        agents = tmp_path / "agents"
        _write_every_agent(agents)
        (agents / "parity-review.md").unlink()

        assert main([], scope=_scope(agents)) == 1

        report = capsys.readouterr().err
        assert "parity-review" in report
        assert "hard stop" in report
        for name in MANDATED_AGENTS:
            if name != "parity-review":
                assert name not in report, f"{name} resolves and must not be reported"

    def test_session_rooted_outside_the_repository_is_reported_regression(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
        home_outside_the_test_tree: None,
    ) -> None:
        """Regression test: the checker passed in the case it exists to catch.

        Bug reported: the failing session was rooted outside the repository,
        where its `.claude/agents/` is not on Claude Code's search path — and the
        checker exited 0 there, because it derived the project directory from its
        own `__file__` and so always searched this repository.
        Root cause: the session's project scope was not an input at all, so
        "the file exists" was checked instead of "Claude Code can find the file".
        Fix: the session project directory (`$CLAUDE_PROJECT_DIR`) is an input,
        and a session rooted anywhere but this repository is reported — as one
        problem, since where the agents live is a separate question from whether
        the session can see them.
        """
        elsewhere = tmp_path / "elsewhere"
        agents = tmp_path / "agents"
        _write_every_agent(agents)

        scope = _scope(agents, session_project_dir=elsewhere)
        assert main([], scope=scope) == 1

        report = capsys.readouterr().err
        assert "1 problem(s)" in report
        assert str(elsewhere) in report
        assert "Claude Code's search path" in report

    def test_tracked_settings_declares_only_the_permitted_keys_regression(self) -> None:
        """Regression test: a tracked SessionStart hook ran contributed code.

        Bug reported: the hook was added to `.claude/settings.json`, which is
        tracked, so checking out a contributed branch and starting a session ran
        that branch's copy of the script as the maintainer.
        Root cause: tracked project settings are contributor-writable, and
        SessionStart hooks execute without a prompt.
        Fix: the hook is opt-in via the gitignored `.claude/settings.local.json`,
        and tracked settings carry only the two keys that configure a session
        rather than act on its behalf.
        """
        settings = json.loads(SETTINGS_JSON_PATH.read_text(encoding="utf-8"))
        unexpected = sorted(set(settings) - _ALLOWED_SETTINGS_KEYS)
        assert not unexpected, (
            f"tracked .claude/settings.json declares {unexpected}, which takes "
            "effect for anyone who checks out a contributed branch — keep "
            "anything that executes or auto-authorises in the gitignored "
            ".claude/settings.local.json"
        )

    def test_the_local_settings_file_stays_untracked_regression(self) -> None:
        """Regression test: the fix for the tracked hook rests on one gitignore line.

        Bug reported: a `SessionStart` hook in tracked settings ran a contributed
        branch's script as the maintainer.
        Root cause: the remedy moved the hook to `.claude/settings.local.json`,
        and both settings tests above assume that file can never be committed —
        an assumption neither of them checks. Delete the ignore rule and the hook
        becomes committable again with the whole suite still green.
        Fix: git itself is asked, so a rule that is present but overridden later
        in the file counts as absent.
        """
        ignored = subprocess.run(
            ["git", "check-ignore", "--quiet", str(LOCAL_SETTINGS_RELATIVE_PATH)],
            cwd=_REPO_ROOT,
            check=False,
        )
        assert ignored.returncode == 0, (
            f"{LOCAL_SETTINGS_RELATIVE_PATH} is not ignored by git, so the opt-in "
            "SessionStart hook this repository deliberately keeps local can be "
            "committed and will then run for everyone who checks the branch out"
        )

    def test_tracked_settings_grants_only_what_is_pinned_here_regression(self) -> None:
        """Regression test: tracked settings acted on a contributor's behalf unprompted.

        Bug reported: a `SessionStart` hook in the tracked `.claude/settings.json`
        ran a contributed branch's script as the maintainer. `permissions.allow`
        and `enabledPlugins` are the same shape — tracked, contributor-writable,
        and effective with no prompt for whoever checks the branch out. A branch
        adding `Bash(curl:*)`, or flipping on a plugin, widens the ambient
        authority of every clone.
        Root cause: nothing pinned what tracked settings *grant*, only that they
        declared no hooks, so a wider allow list was one quiet line in a diff.
        Fix: the grants are pinned exactly, whole-value rather than by membership,
        which also catches a nested `permissions.defaultMode` the top-level key
        allowlist cannot see.

        This cannot stop a hostile branch — it can edit this test too. It makes
        widening the repository's permissions a deliberate act, visible in the
        diff. Adding a permission is meant to cost a test update: do not remove
        that friction, update the pin and say why in the commit.

        Compared in one dict so a branch that widened both keys sees both, rather
        than fixing one and discovering the other on the next run.
        """
        settings = json.loads(SETTINGS_JSON_PATH.read_text(encoding="utf-8"))
        assert {key: settings.get(key) for key in _TRACKED_GRANTS} == _TRACKED_GRANTS
