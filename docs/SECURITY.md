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

### For Contributors

security-review is one of the six shared, project-agnostic agents committed under `.claude/agents/`; it reads this SECURITY.md and CLAUDE.md to learn the project-specific rules above. When using Claude Code it runs in parallel with **code-review**, **test-review**, **document-review**, **accessibility-review**, and the repository's own **parity-review**; **commit-hygiene** runs afterwards, once the other six have approved, to plan the commit split. All seven must approve before anything is committed. Each security finding includes severity, CWE classification, evidence, impact, and remediation steps.

## Reporting Security Issues

If you discover a security vulnerability:

1. **Do not** open a public GitHub issue
2. Contact the maintainer privately
3. Allow reasonable time for a fix before disclosure
