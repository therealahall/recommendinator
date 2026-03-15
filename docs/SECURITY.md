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
- **Auto-migration**: On startup, sensitive fields from `config.yaml` (e.g., `refresh_token`, `api_key`) are automatically migrated to the encrypted database. The plaintext is scrubbed from in-memory config after migration
- **Stale credential recovery**: If the encryption key changes, stale credentials are automatically re-encrypted from config values or purged if no config fallback exists
- **Credentials are write-only from the API** — no endpoint returns credential values

If you move the database to a new host, copy `data/.credential_key` along with it. Without the key file, stored credentials cannot be decrypted and will need to be re-entered.

## API Key Security

### Steam API Key

- Obtain from: https://steamcommunity.com/dev/apikey
- Store only in `config/config.yaml` (git-ignored)
- Key allows read access to your Steam library
- Rotate if compromised

### Best Practices

```yaml
# config/config.yaml - NEVER COMMIT THIS FILE
inputs:
  steam:
    api_key: "YOUR_ACTUAL_KEY_HERE"  # Keep secret!
    steam_id: "YOUR_STEAM_ID"
```

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

The project includes a Claude Code security-review agent (`.claude/agents/security-review.md`) that performs automated security audits before commits.

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

### For Contributors

The agent definition is at `.claude/agents/security-review.md`. When using Claude Code, it runs automatically before commits alongside the **code-review**, **test-review**, **document-review**, and **commit-hygiene** agents. All five agents must approve changes before they are committed. Each security finding includes severity, CWE classification, evidence, impact, and remediation steps.

## Reporting Security Issues

If you discover a security vulnerability:

1. **Do not** open a public GitHub issue
2. Contact the maintainer privately
3. Allow reasonable time for a fix before disclosure
