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
    _REMEDIES,
    MANDATED_AGENTS,
    AgentScope,
    ProblemCategory,
    default_scope,
    find_agent_problems,
    find_scope_problems,
    main,
)

# parents[1] resolves /tests/test_review_agents.py -> repo root.
_REPO_ROOT = Path(__file__).resolve().parents[1]
CLAUDE_MD = _REPO_ROOT / "CLAUDE.md"
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

# `make [VAR=value ...] [-flag ...] <target> ...`, so the targets can be read
# off rather than matched as a substring of the line.
_MAKE_INVOCATION = re.compile(r"^\s*make\b(?P<arguments>.*)$", re.MULTILINE)

# The flags that leave every target unrun: -n prints the recipe, -q only sets an
# exit code, -t stamps the files. Read as targets, `make -n check` satisfies the
# gate assertion below while running neither the preflight nor the suite.
_MAKE_NO_OP_SHORT_FLAGS = frozenset("nqt")
_MAKE_NO_OP_LONG_FLAGS = frozenset(
    {"--dry-run", "--just-print", "--recon", "--question", "--touch"}
)

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


def _runs_nothing(argument: str) -> bool:
    """Whether a `make` argument stops it running any of its targets.

    Short flags cluster, so `-kn` is `-n` too.
    """
    if argument.startswith("--"):
        return argument in _MAKE_NO_OP_LONG_FLAGS
    return argument.startswith("-") and bool(
        set(argument[1:]) & _MAKE_NO_OP_SHORT_FLAGS
    )


def _make_targets(command: str) -> list[str]:
    """Return every target the `make` invocations in *command* actually run.

    `"make check" in command` is satisfied by `make check-frontend`, which runs
    neither the preflight nor the suite; `make -n check` runs nothing at all.
    """
    targets = []
    for match in _MAKE_INVOCATION.finditer(command):
        arguments = match.group("arguments").split()
        if any(_runs_nothing(argument) for argument in arguments):
            continue
        targets += [
            word for word in arguments if "=" not in word and not word.startswith("-")
        ]
    return targets


class TestFindAgentProblems:
    """Every mandated agent is present, readable, and would actually load."""

    def test_no_problems_when_every_agent_resolves(self, tmp_path: Path) -> None:
        """A checkout with every mandated agent committed is clean."""
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

    def test_frontmatter_name_mismatch_is_reported(self, tmp_path: Path) -> None:
        """A `name:` that disagrees with the filename registers under the wrong name."""
        agents = tmp_path / "agents"
        _write_agent(agents, "parity-review", declared_name="parity_review")
        problems = find_agent_problems(["parity-review"], _scope(agents))
        assert [problem.category for problem in problems] == [
            ProblemCategory.BAD_DEFINITION
        ]
        assert "declares name 'parity_review'" in problems[0].detail

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

    def test_no_argument_invocation_is_silent_when_every_agent_resolves(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The Makefile and the hook both invoke the no-argument entry point."""
        agents = tmp_path / "agents"
        _write_every_agent(agents)
        monkeypatch.setattr("sys.argv", ["check_review_agents.py"])
        assert main(scope=_scope(agents)) == 0


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

    def test_ci_reaches_the_checker_through_the_makefile(self) -> None:
        """CI runs the gate `make check` defines, so the two cannot disagree.

        `make check`'s own prerequisites are pinned above, which is what makes
        one `make check` step enough to know the review-agent check ran.
        """
        workflow = yaml.safe_load(CI_WORKFLOW_PATH.read_text(encoding="utf-8"))
        assert (
            workflow["jobs"]["check"]["uses"] == "./.github/workflows/quality-gate.yml"
        )

        steps = [
            step
            for step in _gate_steps()
            if _make_targets(str(step.get("run", ""))) == ["check"]
        ]
        assert len(steps) == 1, (
            "CI must run `make check` — that target and no other — in exactly one "
            f"step, or a branch could delete a mandated agent and go green: {steps}"
        )
        assert (
            steps[0].get("continue-on-error") is not True
        ), "the quality-gate step is allowed to fail, which makes it advisory"


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
        Fix: every mandated agent is committed under `.claude/agents/`, and this
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
