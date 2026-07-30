"""Verify every review agent CLAUDE.md mandates can actually be launched.

An agent that never loaded is a silent gap: unlike an approval that got skipped,
nothing says so, and the pre-commit gate reports success while a whole domain
went unreviewed. This is the preflight for that failure mode. CLAUDE.md's "Review
Agent Preflight" section carries what it checks, what it deliberately cannot
check, and the incident it came from; this module does not repeat it.

Two output modes, because the two consumers need opposite things:

* default — the whole report on stderr and a non-zero exit, which is what
  `make check-agents` needs in order to fail;
* `--hook` — a `SessionStart` payload on stdout and exit 0, because a
  `SessionStart` hook can never block and its stderr never reaches the model's
  context. Only stdout at exit 0 does, and that text is built from constants
  alone (see `format_hook_context`).

Both modes are silent on success.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

import yaml

# The review agents CLAUDE.md's "Agent-Enforced Standards" section mandates.
# tests/test_review_agents.py asserts these names and that section agree, so the
# two cannot drift apart.
MANDATED_AGENTS = (
    "code-review",
    "security-review",
    "test-review",
    "document-review",
    "commit-hygiene",
    "accessibility-review",
    "parity-review",
)

_FRONTMATTER_DELIMITER = "---"
_NAME_DISPLAY_LIMIT = 80
_ERROR_DISPLAY_LIMIT = 160

# An agent definition is a handful of keys, so frontmatter longer than this is not
# one and is rejected as malformed before the parser is handed it rather than
# after. It is a sanity bound on shape, not a resource limit: the file is already
# read and split by this point, and line length is not capped, so it stops nothing
# an attacker would try. The committed seven are validated through this same path
# by a test, which also fails if any of their frontmatter exceeds half of this
# bound — so check that test, not this comment, before assuming there is room.
_FRONTMATTER_LINE_LIMIT = 40

# A home-rooted path is reported with a leading tilde instead of the real
# directory, so a pasted report carries no OS username. The prefix is assembled
# from this constant because tests/test_repository_self_contained.py reads the
# literal as a committed path pointing outside the repository.
_HOME_PLACEHOLDER = "~"

# The repository whose agents are checked, and the directory a session is expected
# to be rooted in. Both, always: neither follows the environment.
_REPO_ROOT = Path(__file__).resolve().parents[1]
_COMMITTED_AGENTS = _REPO_ROOT / ".claude" / "agents"


class ProblemCategory(str, Enum):
    """Why a mandated review agent would not launch.

    Every value is a constant, so `--hook` mode can name the category without
    quoting anything read off the filesystem.
    """

    SESSION_SCOPE = (
        "the session's project directory is not this repository, so none of "
        "this repository's committed review agents are on the search path"
    )
    MISSING = "a mandated agent has no file in .claude/agents/"
    UNREADABLE = "a mandated agent's file cannot be read"
    BAD_DEFINITION = "a mandated agent's file is not a usable agent definition"


_RESTORE_AGENTS_REMEDY = (
    "Restore the committed agents with `git checkout -- .claude/agents/`.",
)

# What to do about each category. The report prints only the remedies for the
# categories actually present: a reader who spots one line that does not apply to
# the run in front of them discounts the next line too, and the whole product of
# this tool is a report someone trusts. tests/test_review_agents.py asserts every
# category has an entry, so indexing this cannot fail at report time.
_REMEDIES: dict[ProblemCategory, tuple[str, ...]] = {
    ProblemCategory.SESSION_SCOPE: (
        "Restart Claude Code at the repository root, so that the agents in",
        ".claude/agents/ are discovered at all.",
    ),
    ProblemCategory.MISSING: _RESTORE_AGENTS_REMEDY,
    ProblemCategory.UNREADABLE: _RESTORE_AGENTS_REMEDY,
    ProblemCategory.BAD_DEFINITION: _RESTORE_AGENTS_REMEDY,
}


@dataclass(frozen=True)
class Problem:
    """Something that stops a mandated review agent from launching.

    `detail` is derived from the filesystem and only ever goes to stderr;
    `category` and `agent_name` are constants, and are all `--hook` mode emits.
    `agent_name` is None for problems that belong to no single agent.
    """

    category: ProblemCategory
    agent_name: str | None
    detail: str


@dataclass(frozen=True)
class AgentScope:
    """The two independent facts a review-agent check works over.

    `project_agents` answers "is this checkout complete?" and always names this
    repository's own directory. `session_project_dir` answers "can this session
    see it?" — Claude Code resolves project-local agents relative to the session's
    project directory, so a session rooted anywhere else sees none of this
    repository's agents. Conflating the two would report seven missing agents for
    one mis-rooted session. It is None when no session is running.
    """

    session_project_dir: Path | None
    project_agents: Path


def session_project_dir() -> Path | None:
    """Return the project directory Claude Code resolves project-local agents for.

    Resolved, because the comparison against this repository is by path: a
    trailing separator, or reaching the repository through a symlinked parent —
    a common home-directory layout — must not read as a different directory. A
    gate that cries wolf gets bypassed.

    None when `CLAUDE_PROJECT_DIR` is unset, which means no Claude Code session
    is running: a plain shell or CI has no project directory to be wrong about.
    """
    from_environment = os.environ.get("CLAUDE_PROJECT_DIR")
    return Path(from_environment).resolve() if from_environment else None


def default_scope() -> AgentScope:
    """Return the scope for the current session.

    `project_agents` never follows the environment. A session rooted in another
    repository is one problem — the session's — and looking for this repository's
    agents over there would turn it into eight, six of them false.
    """
    return AgentScope(
        session_project_dir=session_project_dir(),
        project_agents=_COMMITTED_AGENTS,
    )


def find_scope_problems(scope: AgentScope) -> list[Problem]:
    """Return a problem when a session cannot see this repository's agents.

    Only a Claude Code session has a project directory, so outside one there is
    nothing to report: the agent search path does not exist to be wrong.
    """
    if scope.session_project_dir is None or scope.session_project_dir == _REPO_ROOT:
        return []
    repo_agents = _display_path(scope.project_agents)
    return [
        Problem(
            ProblemCategory.SESSION_SCOPE,
            None,
            f"session project directory is {_display_path(scope.session_project_dir)}, "
            f"so none of the {len(MANDATED_AGENTS)} agents in {repo_agents} are on "
            "Claude Code's search path",
        )
    ]


def find_agent_problems(agent_names: Sequence[str], scope: AgentScope) -> list[Problem]:
    """Return a problem for every named agent that would not launch."""
    problems = [_find_agent_problem(name, scope) for name in agent_names]
    return [problem for problem in problems if problem is not None]


def format_failure_report(problems: Sequence[Problem], scope: AgentScope) -> str:
    """Render the stderr report: every problem, and where it looked."""
    lines = [
        "MANDATED REVIEW AGENT CHECK FAILED",
        "",
        f"{len(problems)} problem(s) stop CLAUDE.md's mandated review agents "
        "from launching:",
    ]
    lines += [_problem_line(problem) for problem in problems]
    lines += [
        "",
        f"Committed agents: {_display_path(scope.project_agents)}",
        "",
        "The pre-commit gate is only satisfied when every mandated agent approves",
        "the final tree, and an agent that never loaded reviewed nothing, so this",
        "is a hard stop rather than a step to work around.",
    ]
    lines += _remedy_lines(problems)
    return "\n".join(lines)


def format_hook_context(problems: Sequence[Problem]) -> str:
    """Render the text `--hook` mode injects into the model's context.

    Built from constants only: `ProblemCategory` values, and agent names taken
    from `MANDATED_AGENTS`. Agent files are repository-controlled, so quoting
    anything read out of one here would turn a contributed `.claude/agents/*.md`
    into a prompt-injection channel. The file-derived detail lives in
    `format_failure_report`, which only ever reaches a terminal.
    """
    categories = {problem.category for problem in problems}
    affected_names = {problem.agent_name for problem in problems}
    lines = [
        "MANDATED REVIEW AGENT CHECK FAILED",
        "",
        "The pre-commit review gate in CLAUDE.md cannot run as documented:",
    ]
    lines += [
        f"  - {category.value}"
        for category in ProblemCategory
        if category in categories
    ]
    affected = [name for name in MANDATED_AGENTS if name in affected_names]
    if affected:
        lines += ["", f"Agents affected: {', '.join(affected)}."]
    lines += [
        "",
        "Do not run the pre-commit review gate until this is fixed: an agent that",
        "never loaded reviewed nothing, and unlike a skipped approval that gap is",
        "silent. Run `make check-agents` from the repository root for the full",
        "report, including the paths this message deliberately leaves out.",
    ]
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None, scope: AgentScope | None = None) -> int:
    """Report every mandated agent that would not launch, silently passing if none."""
    arguments = _parse_arguments(argv)
    resolved_scope = default_scope() if scope is None else scope
    problems = find_scope_problems(resolved_scope) + find_agent_problems(
        MANDATED_AGENTS, resolved_scope
    )
    if not problems:
        return 0
    if arguments.hook:
        payload = {
            "hookSpecificOutput": {
                "hookEventName": "SessionStart",
                "additionalContext": format_hook_context(problems),
            }
        }
        print(json.dumps(payload))
        return 0
    print(format_failure_report(problems, resolved_scope), file=sys.stderr)
    return 1


def _parse_arguments(argv: Sequence[str] | None) -> argparse.Namespace:
    """Parse the command line, whose only flag selects hook output."""
    parser = argparse.ArgumentParser(
        description="Verify every review agent CLAUDE.md mandates can be launched."
    )
    parser.add_argument(
        "--hook",
        action="store_true",
        help=(
            "emit a SessionStart hook payload on stdout and exit 0, instead of "
            "reporting on stderr and exiting non-zero"
        ),
    )
    return parser.parse_args(argv)


def _find_agent_problem(name: str, scope: AgentScope) -> Problem | None:
    """Return the reason `name` would not launch, or None when it would."""
    path = scope.project_agents / f"{name}.md"
    if not path.is_file():
        return Problem(
            ProblemCategory.MISSING,
            name,
            f"no {path.name} in {_display_path(path.parent)}",
        )
    try:
        declared_name = _declared_agent_name(path.read_bytes().decode("utf-8"))
    except (OSError, UnicodeDecodeError) as error:
        return Problem(
            ProblemCategory.UNREADABLE,
            name,
            f"{_display_path(path)} cannot be read: "
            f"{_bounded_repr(str(error), _ERROR_DISPLAY_LIMIT)}",
        )
    except ValueError as error:
        return Problem(
            ProblemCategory.BAD_DEFINITION, name, f"{_display_path(path)}: {error}"
        )
    if declared_name != name:
        return Problem(
            ProblemCategory.BAD_DEFINITION,
            name,
            f"{_display_path(path)}: frontmatter declares name "
            f"{_bounded_repr(declared_name, _NAME_DISPLAY_LIMIT)}",
        )
    return None


def _declared_agent_name(text: str) -> str:
    """Return the `name:` an agent file declares, if the file would load at all.

    Claude Code needs frontmatter carrying both `name:` and `description:`, and a
    body holding the agent's instructions. A file missing any of those is valid
    Markdown and still never becomes a launchable agent, which is exactly the
    silent failure this module exists to catch — a truncated file is the likely
    way it happens.

    Raises:
        ValueError: the file would not load, saying which requirement it fails.
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != _FRONTMATTER_DELIMITER:
        raise ValueError("does not open with a '---' frontmatter delimiter")
    end = next(
        (
            index
            for index, line in enumerate(lines[1:], start=1)
            if line.strip() == _FRONTMATTER_DELIMITER
        ),
        None,
    )
    if end is None:
        raise ValueError("frontmatter is never closed by a '---' line")
    if end - 1 > _FRONTMATTER_LINE_LIMIT:
        raise ValueError(
            f"frontmatter is longer than {_FRONTMATTER_LINE_LIMIT} lines, so it is "
            "not an agent definition"
        )
    try:
        frontmatter = yaml.safe_load("\n".join(lines[1:end]))
    except yaml.YAMLError as error:
        first_line = next(iter(str(error).splitlines()), "")
        raise ValueError(
            "frontmatter is not valid YAML: "
            f"{_bounded_repr(first_line, _ERROR_DISPLAY_LIMIT)}"
        ) from error
    if not isinstance(frontmatter, dict):
        raise ValueError("frontmatter is not a YAML mapping")
    declared_name = frontmatter.get("name")
    if not isinstance(declared_name, str):
        raise ValueError("frontmatter has no 'name:' field")
    description = frontmatter.get("description")
    if not isinstance(description, str) or not description.strip():
        raise ValueError("frontmatter has no 'description:' field")
    if not "\n".join(lines[end + 1 :]).strip():
        raise ValueError("has no instructions after its frontmatter")
    return declared_name


def _remedy_lines(problems: Sequence[Problem]) -> list[str]:
    """Return the remedies for the categories in this run, each printed once.

    Iterating the enum rather than the problems keeps the order stable, and two
    categories sharing a remedy contribute it once.
    """
    categories = {problem.category for problem in problems}
    remedies: list[tuple[str, ...]] = []
    for category in ProblemCategory:
        remedy = _REMEDIES[category]
        if category in categories and remedy not in remedies:
            remedies.append(remedy)
    return [line for remedy in remedies for line in ("", *remedy)]


def _problem_line(problem: Problem) -> str:
    """Render one report bullet, prefixed by the agent name when there is one."""
    if problem.agent_name is None:
        return f"  - {problem.detail}"
    return f"  - {problem.agent_name}: {problem.detail}"


def _bounded_repr(text: str, limit: int) -> str:
    """Return `text` as an escaped, length-capped literal.

    Filesystem-derived text lands in a terminal, and `repr` escapes the control
    characters a crafted agent file could otherwise use to rewrite the very
    report the reader is trusting.
    """
    escaped = repr(text)
    if len(escaped) <= limit:
        return escaped
    return f"{escaped[:limit]}..."


def _display_path(path: Path) -> str:
    """Render a path, replacing any home-directory prefix with a tilde.

    Reports get pasted into issues; the OS username need not go with them.

    `Path.home()` raises `RuntimeError` where there is no home directory to
    resolve — an arbitrary-UID container with no passwd entry and no `HOME`.
    Abbreviating is cosmetic, so the report is rendered unabbreviated rather than
    lost at exactly the moment someone needs to read it.
    """
    try:
        relative = path.relative_to(Path.home())
    except (ValueError, RuntimeError):
        return str(path)
    if relative == Path():
        return _HOME_PLACEHOLDER
    return f"{_HOME_PLACEHOLDER}/{relative}"


if __name__ == "__main__":
    sys.exit(main())
