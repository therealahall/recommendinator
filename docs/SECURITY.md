# Security Considerations

This document outlines security considerations for Recommendinator.

## Data Privacy

### Local-First Design

- All data is stored locally (SQLite database)
- LLM processing uses local Ollama (no cloud AI)
- No telemetry or analytics
- No data leaves your machine unless you configure external APIs

### Sensitive Data

The following contain sensitive information:

| File | Contains |
|------|----------|
| `config/config.yaml` | API keys (migrated to DB on startup), Steam ID |
| `data/recommendations.db` | Personal consumption history, encrypted credentials |
| `data/.credential_key` | Fernet encryption key for stored credentials |
| `data/chroma_db/` | Vector embeddings of your content (if AI enabled) |

**Never commit these files to version control.**

### Credential Encryption

Sensitive credentials (OAuth tokens, API keys) are encrypted at rest using Fernet symmetric encryption and stored in the `credentials` table of the SQLite database.

- **Encryption key** is stored at `data/.credential_key` (or the path set by `RECOMMENDINATOR_KEY_PATH`)
- **Key file permissions** are set to `0600` (owner-only) on creation, and verified on every load — the app refuses to start if the key file is group- or world-readable
- **Key directory permissions** are set to `0700` when created
- **Auto-migration**: On startup, sensitive fields from `config.yaml` are automatically migrated to the encrypted database and scrubbed from the in-memory config. This covers both per-source credentials (e.g., a source's `refresh_token`/`api_key`) and global provider secrets (the settings-registry leaves flagged sensitive, e.g. `enrichment.providers.tmdb.api_key` and `enrichment.providers.rawg.api_key`). Global secrets are keyed in the `credentials` table under a reserved `settings:` namespace so they never collide with a real source
- **Stale credential recovery**: If the encryption key changes, stale credentials are automatically re-encrypted from config values or purged if no config fallback exists
- **Automatic token rotation**: When OAuth-based sources (GOG, Epic Games) receive a rotated refresh token during sync, the new token is automatically persisted to the encrypted credentials table. Users do not need to manually reconnect when servers rotate tokens
- **Credentials are write-only from the API** — no endpoint returns credential values
- **The test suite never touches the real key** — an autouse fixture in the repository-root `conftest.py` points `RECOMMENDINATOR_KEY_PATH` at a per-test temporary directory. It is at the root, not in `tests/`, because tests are also collected from `src/` (plugin-local) and `private/`, and a conftest only covers its own subtree

If you move the database to a new host, copy `data/.credential_key` along with it. Without the key file, stored credentials cannot be decrypted and will need to be re-entered.

## API Key Security

All API keys and OAuth tokens — per-source credentials and global enrichment
provider keys alike — are stored encrypted in the `credentials` table, never in
plaintext. Enter them in-app rather than in `config.yaml`:

- **Source secrets** (Steam, Sonarr, Radarr, etc.): the web **Data** tab or
  `python3.11 -m src.cli source set-secret <source> <key>`.
- **Global provider secrets** (`enrichment.providers.tmdb.api_key`,
  `enrichment.providers.rawg.api_key`): the web **Settings** page or
  `python3.11 -m src.cli settings set-secret <key>`.

Anything placed in `config.yaml` for bootstrap is swept into the encrypted store
on startup and stripped from the in-memory config, so a secret never lingers in
plaintext.

### Steam API Key

- Obtain from: https://steamcommunity.com/dev/apikey
- Enter it via `source set-secret steam api_key` (or the Data tab); a value put
  in `config/config.yaml` for bootstrap is migrated to the encrypted database on
  startup
- Key allows read access to your Steam library
- Rotate if compromised — re-run `source set-secret` to replace it

## Network Security

### External Connections

The application may connect to:

| Service | Purpose | When |
|--------|---------|------|
| Ollama | LLM and embeddings | When AI enabled |
| Steam API | Game library sync | When Steam source enabled |
| GOG API | Game library sync | When GOG source enabled |
| Epic Games API | Game library sync | When Epic source enabled |
| Sonarr/Radarr | Media library sync | When configured |
| TMDB API | Movie/TV metadata enrichment | When enrichment enabled |
| OpenLibrary API | Book metadata enrichment | When enrichment enabled |
| RAWG API | Game metadata enrichment | When enrichment enabled |

### Localhost Binding

By default, the web interface binds to `localhost`:

```bash
# Safe: Only accessible from local machine
python3.11 -m src.web --host 127.0.0.1

# Caution: Accessible from network
python3.11 -m src.web --host 0.0.0.0
```

### Docker Network Isolation

When using Docker, services communicate over an internal network:

```yaml
networks:
  recommendinator-net:
    # Isolated from host network by default
```

## Input Validation

### CSV/JSON Import

- Files are parsed with standard libraries
- No code execution from imported data
- Invalid data is skipped, not executed

### Custom Rules

- Rules are parsed by pattern matching or LLM
- No code execution from rule text
- Sanitized before storage

## Database Security

### SQLite

- No authentication (local file)
- Protect file permissions:

```bash
chmod 600 data/recommendations.db
```

### Backup

```bash
# Backup your data
cp data/recommendations.db data/recommendations.db.backup

# Encrypt backups if storing off-machine
gpg -c data/recommendations.db.backup
```

## Dependencies

### Known Considerations

- **ChromaDB**: Vector database, stores embeddings locally
- **FastAPI**: Web framework, no known critical vulnerabilities
- **Ollama**: Local LLM, network access to localhost only

### Keeping Updated

```bash
# Check for outdated packages
uv pip list --outdated

# Update dependencies
uv sync --locked --extra ai
```

## Deployment Checklist

- [ ] `config/config.yaml` is git-ignored
- [ ] API keys are not in code or logs
- [ ] Database file has restricted permissions
- [ ] Web interface bound to localhost (or behind reverse proxy)
- [ ] Docker containers run as non-root user
- [ ] Ollama only accessible internally

## Automated Security Review

Claude Code's shared security-review agent (a project-agnostic agent committed at `.claude/agents/security-review.md`) audits changes for security before they are committed, applying the project-specific rules documented below. Nothing launches it for you — running it is a step in the pre-commit workflow, described under [How the Review Gate Is Started](#how-the-review-gate-is-started).

### What It Checks

- **Credential exposure** — Hardcoded secrets, `config/config.yaml` references, secrets in logs or error messages
- **Injection vulnerabilities** — SQL injection, command injection (`shell=True`), path traversal, template injection
- **Network & API security** — CORS misconfigurations, missing TLS validation, SSRF, exposed internal errors
- **Python-specific pitfalls** — `assert` for validation (stripped in `-O` mode), direct shell execution via `os` module, mutable default arguments
- **Data handling** — Unsafe deserialization, race conditions, shared state mutation
- **Dependency risks** — Known vulnerabilities, unpinned versions, unnecessary dependencies
- **Type safety** — Uses Pyright LSP diagnostics to catch `Any` types hiding unsafe casts and missing return types on endpoints

### Project-Specific Rules Enforced

- `config/config.yaml` must never be referenced in code or tests
- CORS defaults to localhost, never wildcard
- `allow_credentials=False` when wildcard origins are used
- Internal error details never exposed in HTTP responses (`detail=str(error)` is forbidden)
- Module-level imports only (inline imports obscure dependency auditing)
- Dicts/lists must be copied before mutating externally-passed data
- `is not None` checks required instead of truthy checks for security-relevant values

### How the Review Gate Is Started

Nothing starts the review gate for you. Running the agents is a step in the pre-commit workflow, and `make check` verifies that every mandated agent is committed and loadable, because an agent that never loaded reviews nothing and says nothing. See [Review Agent Preflight](../CLAUDE.md#review-agent-preflight).

**"Loadable" is not "unaltered", and nothing here checks the difference.** The check catches an agent that is missing, renamed, or malformed — not one whose instructions were rewritten. A `.claude/agents/security-review.md` edited to approve everything passes the check exactly like the real one. Reviewing the `.claude/agents/` diff by hand is the only control on that, so treat a change to any of those files like a change to CI configuration: they are prompts that direct the agents reviewing this repository, they run with the reviewer's tool permissions, and an edit changes what the review does — including the review of the branch making the edit.

Auto-executing hooks are deliberately kept out of the tracked `.claude/settings.json`, because nobody should have a `SessionStart` hook imposed on them by a repository they cloned; tracked settings ship to every clone and hooks run without a prompt. The [documented opt-in](../CLAUDE.md#review-agent-preflight) puts it in the gitignored `.claude/settings.local.json` instead — **which makes the execution risk a choice you own, not one that goes away.** `$CLAUDE_PROJECT_DIR` is the checkout, so the hook runs the working tree's copy of the script, including on a branch someone else wrote. Do not enable it in a checkout used to review other people's branches.

That residual path is not unique to the hook. The review agents' prompts instruct them to run the project's quality-check command as it already exists, so reviewing a contributed branch runs that branch's test code with your credentials and file access regardless. Reviewing someone else's code means running it; the hook is a second door, and the only one you can decline.

**`permissions.allow` is the same shape of ambient authority as a hook, and is governed the same way.** An entry there is pre-approved for everyone who checks out the branch carrying it, with no prompt at the moment it is used. What the tracked list is bounded on is **execution**: nothing granted there runs a command of its own choosing. Anything with an `--exec`, `--extcmd` or pager escape belongs in the gitignored `.claude/settings.local.json`, where it is a choice one person made for one machine. `git grep` is the worked example: useful for review, and excluded, because `--open-files-in-pager` runs its value through a shell. It prompts instead, and being asked is the control working.

**Some entries are denied rather than simply left out, and the distinction is deliberate.** `permissions.allow` grants by prefix, and `git difftool` shares its prefix with the granted `git diff` while taking `--extcmd=<command>`, which runs anything. Whether a prefix grant actually reaches a longer subcommand depends on matcher behaviour this repository cannot observe, so `permissions.deny` names `git difftool` outright: deny beats allow, so if the grant does reach it the escape is closed, and if it does not the rule is harmless surplus. Either way the question stops mattering. The `git diff-*` plumbing family is denied alongside it — no reviewer invokes plumbing, and the survey behind that list is not permanent: `git diff-pairs` is a recent addition, so a list drawn up a couple of years ago would already have had a gap in it. Denying the family narrows the standing risk from "any existing `diff-*` command gains a command-valued flag" to "a newly named one appears". `git status` and `git log` have no sibling subcommand sharing their prefix; `git diff` is the only one of the three that does. **A deny is also the one kind of rule a project may reasonably ship to every clone**, which is why it lives in tracked settings while the `SessionStart` hook does not: the objection to a tracked hook, or to a tracked grant, is that it makes someone else's checkout *do* something. A deny only narrows what is permitted, and removing it is visible in the pin.

**Even the execution bound is a claim about a name, not about a binary.** It is not that a local alias can shadow a granted command — git ignores those; `git-config(1)` says under `alias.*` that "aliases that hide existing Git commands are ignored", and `diff`, `log`, `status` and `difftool` are all existing commands. The reachable surface is the opposite one, and larger: a name that *extends* a granted prefix. An `alias.diffmine`, or a `git-diffmine` executable anywhere on `PATH`, is dispatched by `git diffmine`, may be a shell command — `!`-prefixed aliases are — and string-matches the granted `git diff` prefix. That surface is machine-local and unenumerable, so no deny list can reach it, and unlike the `diff-*` family it does not stop growing. This is not a path a contributed branch controls, since `.git/config` is not part of a checkout and `PATH` is not either. What it means is that the grant can be worth more on one machine than this document says, and nothing here can tell.

**Reads and writes are not bounded at all, and the grant was kept knowing that.** The read primitive is confirmed, from git's own usage output: `git diff -h` lists `--no-index [--] <path> <path>` in its synopsis, so `git diff --no-index` prints any two files on the machine, by absolute path, from outside any repository — unprompted, because it matches the granted prefix. That is a real widening rather than a technicality: reading a path outside the working directory through the ordinary file tool *prompts*, so the grant turns a prompted read of anything into an unprompted one, including `config/config.yaml`, which CLAUDE.md forbids reading outright, and including private keys and credentials that have nothing to do with this project. The write primitive is **documented but unverified here**: `git-diff(1)` describes `--output=<file>`, which creates or truncates an arbitrary path, and `git log` takes the same diff options. The difference between the two tiers is narrower than it may look, and worth stating exactly. Neither flag's *effect* has been observed — nobody ran either. What separates them is legibility: `--no-index`'s synopsis line shows the flag taking two arbitrary paths, so its capability is readable off the usage string itself, whereas `--output`'s capability is a claim made in prose in the manual page. The manual page is the same installed git's own documentation and is not a weaker source than `-h`, only a longer one — so treat the write primitive as likely rather than established, which is the right tier for a flag whose effect nobody may safely trigger. Note also what the argument below does and does not show: that no prefix deny could *close* a write flag, not that one exists. If `--output` turns out not to behave as documented and nothing else under the granted prefix writes, the write axis is bounded in practice — by git's feature set, though, not by anything here, and not by anything that would stay true across a git release. **No prefix rule can close either**, because both are flags rather than subcommands and a flag may sit anywhere in the arguments: a deny on `git diff --no-index` misses `git diff --stat --no-index a b`, and a rule that can be stepped around is worse than none, because it reads as closed. The grant stays because the alternative is worse: dropping it means dozens of prompts in a single review round, and someone clearing forty prompts is not reading the forty-first, so it trades a bounded and characterised risk for an unbounded and uncharacterised one. **What actually holds the line is the agents' own read-only rule** — every one of them is instructed never to write, and to treat a command succeeding as no evidence it was permitted. That is prose, exactly like the no-ephemeral-verification rule beside it. It is a standard the agents are held to, not a control that stops them, and it should be read that way.

`enabledPlugins` is code by definition, and is pinned for the same reason. `tests/test_review_agents.py` pins both keys exactly, so widening them is a deliberate act, visible in the diff, rather than one quiet line in a settings file — adding a permission is meant to cost a test update, and that cost is the point. It is not a defence: a branch that widens the grant can edit the pin in the same commit. What it buys is that the widening cannot happen *silently*, which is the same bet as reviewing the `.claude/agents/` diff by hand.

### For Contributors

security-review is one of the six shared, project-agnostic agents committed under `.claude/agents/`; it reads this SECURITY.md and CLAUDE.md to learn the project-specific rules above. When using Claude Code it runs in parallel with **code-review**, **test-review**, **document-review**, **accessibility-review**, and the repository's own **parity-review**; **commit-hygiene** runs afterwards, once the other six have approved, to plan the commit split. All seven must approve before anything is committed. Each security finding includes severity, CWE classification, evidence, impact, and remediation steps.

## Reporting Security Issues

If you discover a security vulnerability:

1. **Do not** open a public GitHub issue
2. Contact the maintainer privately
3. Allow reasonable time for a fix before disclosure
