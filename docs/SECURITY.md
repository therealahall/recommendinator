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

### File uploads (`POST /api/import`)

- The request body is bounded **before** it is parsed. A pure-ASGI middleware
  (`src/web/upload_limit.py`, registered in `create_app`) refuses a declared
  `content-length` over the cap outright and counts the bytes of a chunked body
  as they arrive, so an oversized request is rejected with HTTP 413 without
  reaching Starlette's multipart parser. This is the layer that keeps an
  oversized upload off the host disk: FastAPI resolves `file: UploadFile` before
  the handler body runs, which means the parser has already spooled the whole
  body to a `SpooledTemporaryFile` (spilling to the system temp directory past
  1 MB) by the time application code could look at it
- The cap is 50 MB for the file, plus a 1 MB allowance for multipart framing
  (boundaries, part headers, the `source` and option fields) so a legitimate
  50 MB file is not rejected by the limit sized for it
- **The per-request cap is not the whole story.** It bounds one request; it says
  nothing about how many are in flight. Each accepted import costs up to the
  request cap spooled by the parser, plus the handler's own up-to-50 MB temp
  copy, plus a full ingestion run — so the same middleware also bounds the
  number of concurrent `POST /api/import` calls (2), answering **HTTP 429** past
  it. The bound lives in the middleware rather than in the handler for the same
  reason the size cap does: a counter inside the handler is only reached after
  the body has already been spooled to disk. The handler's own duplicate guard
  is a different thing — it is keyed per plugin and returns 409, so on its own
  five file-import plugins meant five simultaneous imports, each of which had
  already paid the spool cost before the guard was consulted
- The concurrency bound is per process. Running multiple uvicorn workers
  multiplies it, as it does every other in-process limit here
- The handler re-checks the 50 MB file cap while copying the parsed upload to its
  own temp file, as a backstop on the copy it owns
- The temp file is removed on every exit path, including the rejection, and its
  name takes a suffix from the uploaded filename only when that suffix is a short
  alphanumeric extension (an embedded NUL or a 300-character extension would
  otherwise make `mkstemp` raise)
- Only form fields the plugin's own schema declares are read as import options,
  and only string values, so an extra file part cannot reach the plugin. The CLI
  `--option` flag applies the same schema gate, refusing anything else — without
  it, an internal pipeline key such as `_source_id` could relabel every imported
  item
- Import failures return a structured, path-free detail. The full message (which
  names the temp file and forwards plugin text) is logged server-side instead.
  Plugin *validation* errors are the deliberate exception: they describe the
  option schema the caller just filled in
- A file that is not UTF-8 text, is a directory, or is unreadable is a 4xx with
  an actionable message rather than an unhandled 500
- `python-multipart` is floored at `>=0.0.18` in `pyproject.toml`. Earlier
  releases carry CVE-2024-53981, and a lockfile only protects locked installs
- The CLI `import` command has no cap: it reads a local path the operator chose
  rather than an unauthenticated request body

### Filesystem paths in source config

Source config is written by `POST /api/sync/sources`, which stores any
schema-declared value verbatim — it never calls `validate_config`. A
caller-settable filesystem path is therefore a way for anyone who can reach the
port to make the app read a directory of the attacker's choosing and render the
filenames as library items.

- Plugins that parse a single user-supplied file declare no `path` field at all.
  They are one-shot file imports (`goodreads_csv`, `storygraph_csv`,
  `csv_import`, `json_import`, `markdown_import`) and receive the path from the
  import service
- `roms` is the one plugin that genuinely re-scans directories, so it keeps
  `paths` and contains them: each entry is resolved (following `..` and symlinks)
  and must sit under an allowed root — the user's home directory, the working
  directory, or a conventional media mount — **and** must reach that root
  without descending through a hidden, dot-prefixed directory. The second rule
  is what the first is worth anything for: the root list has to include `$HOME`,
  so on its own it would accept `~/.ssh` and render `id_rsa`, `known_hosts` and
  `authorized_keys` as game titles. `~/.aws`, `~/.gnupg` and `~/.config/*` are
  the same shape. The rule applies below the root only, so an operator who names
  a hidden directory in `RECOMMENDINATOR_SCAN_ROOTS` can scan it — a root is
  operator-chosen and never arrives from a stored source config, which is what
  makes a library at `~/.local/share/roms` reachable by naming it
- `RECOMMENDINATOR_SCAN_ROOTS` **replaces** the default roots rather than adding
  to them, so an operator who sets it must list every directory their libraries
  live under, not only the new one — naming just the new one stops every other
  `roms` source, including one under `$HOME`, from scanning. It lives in the
  environment rather than in config or the settings table, because an allow-list
  stored next to the value it contains would be settable by the same
  unauthenticated request
- What scan-path containment does **not** do: it decides which directories may
  be opened, not what is inside them. Any plain directory under an allowed root
  is still listable, so allowing `$HOME` accepts that the non-hidden parts of
  `$HOME` can come back as "games". It also does not follow through to what the
  scan *records*: a symlinked child inside an allowed root is resolved, and its
  resolved target path and byte size are written into the item's `metadata`. So
  an entry inside an allowed root can publish a path that is outside every
  allowed root. Containment gates the root, not the contents
- An empty string in `paths` is rejected. `Path("").resolve()` is the working
  directory, which is itself a default root, so a blank entry would otherwise
  pass containment and silently mean "scan wherever the app was started from".
  `"."` still means exactly that — it just has to be spelled deliberately
- `roms.extra_strip_patterns` takes arbitrary regex under two caps: at most 10
  patterns, at most 200 characters each. Those bound how much regex runs against
  every title. They do **not** make a pattern safe, and nothing else does:
  Python's `re` has no execution timeout, deciding whether a regex backtracks
  catastrophically is not something a cheap static check can do (`(a+)+` is five
  characters, and `.*.*.*.*x` has no group for a structural check to look at),
  so patterns are compiled as written. The residual risk is worse than "a slow
  scan": a catastrophically backtracking pattern against a long title does not
  finish when the scan finishes. `re` has no timeout and a Python thread cannot
  be cancelled, so the sync worker running that match is **lost until the
  process restarts**, and every later sync runs with one fewer worker — enough
  of them and syncing stops altogether. That is unlike pointing the scanner at a
  large directory, which terminates. Bounding it for real means running the
  match somewhere killable (a subprocess with a timeout) or using a
  backtracking-free engine such as RE2; neither is in place today

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

Claude Code's shared security-review agent (a project-agnostic agent at the user level, `~/.claude/agents/`) performs automated security audits before commits, applying the project-specific rules documented below.

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

security-review is a shared user-level agent (`~/.claude/agents/`); it reads this SECURITY.md and CLAUDE.md to learn the project-specific rules above. When using Claude Code, it runs automatically before commits alongside the **code-review**, **test-review**, **document-review**, and **commit-hygiene** agents. All five agents must approve changes before they are committed. Each security finding includes severity, CWE classification, evidence, impact, and remediation steps.

## Reporting Security Issues

If you discover a security vulnerability:

1. **Do not** open a public GitHub issue
2. Contact the maintainer privately
3. Allow reasonable time for a fix before disclosure
