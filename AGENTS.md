# Agent Instructions

This project uses **bd** (beads) for issue tracking. Run `bd onboard` to get started.

## Quick Reference

```bash
bd ready              # Find available work
bd show <id>          # View issue details
bd update <id> --status in_progress  # Claim work
bd close <id>         # Complete work
bd sync               # Sync with git
```

## Operational Essentials

### Python Version

**Python 3.11 through 3.14 are supported.** Name no minor in a command: a `make`
target, or `uv run python -m …`, takes the interpreter `.python-version` and
`uv.lock` agree on.

### Running Commands

- **Never use `cd` in front of commands.** The workspace path is already the project root.
- **Never pipe test output or use head, tail, etc.** Run each command directly.
- Use `command make check` (not bare `make check`) to bypass a zsh shell snapshot function that shadows the `make` binary in Claude Code's environment.

### Security

**NEVER use `config/config.yaml`** — contains secrets (API keys, Steam IDs). Always use `config/example.yaml` for tests and examples.

## Landing the Plane (Session Completion)

**When ending a work session**, you MUST complete ALL steps below. Work is NOT complete until `git push` succeeds.

**MANDATORY WORKFLOW:**

1. **File issues for remaining work** - Create issues for anything that needs follow-up
2. **Run quality gates** (if code changed) - Tests, linters, builds
3. **Update issue status** - Close finished work, update in-progress items
4. **PUSH TO REMOTE** - This is MANDATORY:
   ```bash
   git pull --rebase
   bd sync
   git push
   git status  # MUST show "up to date with origin"
   ```
5. **Clean up** - Clear stashes, prune remote branches
6. **Verify** - All changes committed AND pushed
7. **Hand off** - Provide context for next session

**CRITICAL RULES:**
- Work is NOT complete until `git push` succeeds
- NEVER stop before pushing - that leaves work stranded locally
- NEVER say "ready to push when you are" - YOU must push
- If push fails, resolve and retry until it succeeds
