# Security Considerations

## Data privacy

Everything is local: SQLite for structured data, a local Ollama for LLM work, no
telemetry. Nothing leaves your machine except calls to the external APIs you
configure.

| File | Contains |
|------|----------|
| `config/config.yaml` | Bootstrap secrets, migrated to the database on startup |
| `data/recommendations.db` | Consumption history, encrypted credentials |
| `data/.credential_key` | Fernet key for those credentials |
| `data/chroma_db/` | Vector embeddings of your content, AI only |

**Never commit these files to version control.**

## Credential encryption

OAuth tokens and API keys are encrypted with Fernet and stored in the
`credentials` table. **Nothing else is encrypted.** Titles, ratings, reviews and
completion history sit in the database as plaintext, and so do the embeddings.

- The key lives at `data/.credential_key`, or wherever
  `RECOMMENDINATOR_KEY_PATH` points. It is created `0600` inside a `0700`
  directory, and loading a group or world readable key file raises
  `PermissionError` rather than decrypting anything.
- **On startup, sensitive fields in `config.yaml` are moved into the encrypted
  database and scrubbed from the in-memory config.** That covers per-source
  credentials and the global provider secrets the settings registry flags
  sensitive (`enrichment.providers.tmdb.api_key`,
  `enrichment.providers.rawg.api_key`). Global secrets are keyed under a
  reserved `settings:` namespace, so they cannot collide with a real source.
- If the encryption key changes, stale credentials are re-encrypted from config
  values, or purged when there is no config fallback.
- A rotated OAuth refresh token from GOG or Epic Games is persisted during sync,
  so you never reconnect by hand.
- **No endpoint returns a credential value.** They are write-only from the API.
- The test suite never touches the real key. An autouse fixture in the
  repository-root `conftest.py` points `RECOMMENDINATOR_KEY_PATH` at a per-test
  temporary directory.

Copy `data/.credential_key` when you move the database to another host. Without
it, stored credentials cannot be decrypted and have to be re-entered.

**Removing a source clears only the credentials its plugin currently declares
sensitive.** A secret is left behind when the plugin is no longer installed, or
when the field holding it is no longer marked sensitive. Removal deletes no
library items.

## Entering keys

Enter secrets in the app, never in `config.yaml`:

- **Source secrets** (Steam, Sonarr, Radarr, and the rest): the web **Data** tab
  or `python3.11 -m src.cli source set-secret <source> <key>`.
- **Global provider secrets**: the web **Settings** page or
  `python3.11 -m src.cli settings set-secret <key>`.

A Steam key comes from https://steamcommunity.com/dev/apikey and grants read
access to your Steam library. Rotate it by re-running `source set-secret`.

## Network

| Service | Purpose | When |
|---------|---------|------|
| Ollama | LLM and embeddings | AI enabled |
| Steam, GOG, Epic Games | Game library sync | That source enabled |
| Sonarr, Radarr | Media library sync | Configured |
| TMDB, OpenLibrary, RAWG | Metadata enrichment | Enrichment enabled |

The web interface binds `127.0.0.1` by default and has **no authentication on
any endpoint**. Running `python3.11 -m src.web --host 0.0.0.0` hands everyone on
your network read and write access to your library. Do not expose it to the
public internet. Under Docker, services talk over an internal network isolated
from the host by default.

## Input handling

Imported CSV and JSON are parsed with standard libraries, and invalid rows are
skipped rather than executed. Custom rules are parsed by pattern matching or the
LLM and sanitized before storage. Neither path executes anything from user data.

## Database and backups

SQLite has no authentication, so file permissions are the control:

```bash
chmod 600 data/recommendations.db
cp data/recommendations.db data/recommendations.db.backup
gpg -c data/recommendations.db.backup   # if the backup leaves the machine
```

## Dependencies

```bash
uv pip list --outdated
uv sync --locked --extra ai
```

ChromaDB stores embeddings locally, and Ollama is reachable on localhost only.

## Deployment checklist

- [ ] `config/config.yaml` is git-ignored
- [ ] API keys are not in code or logs
- [ ] Database file has restricted permissions
- [ ] Web interface bound to localhost, or behind a reverse proxy
- [ ] Docker containers run as a non-root user
- [ ] Ollama only reachable internally

## Automated security review

The shared security-review agent, committed at
`.claude/agents/security-review.md`, audits changes before they are committed.

### What it checks

- **Credential exposure**: hardcoded secrets, `config/config.yaml` references,
  secrets in logs or error messages
- **Injection**: SQL, command (`shell=True`), path traversal, template
- **Network and API**: CORS, missing TLS validation, SSRF, exposed internal
  errors
- **Python pitfalls**: `assert` for validation (stripped under `-O`), shell
  execution through the `os` module, mutable default arguments
- **Data handling**: unsafe deserialization, race conditions, shared state
  mutation
- **Dependencies**: known vulnerabilities, unpinned versions, needless packages
- **Type safety**: Pyright diagnostics for `Any` hiding unsafe casts, and
  missing return types on endpoints

### Project rules it enforces

- `config/config.yaml` must never be referenced in code or tests
- CORS defaults to localhost, never wildcard
- `allow_credentials=False` when wildcard origins are used
- Internal error detail never reaches an HTTP response (`detail=str(error)` is
  forbidden)
- Module-level imports only
- Copy dicts and lists before mutating data passed in from outside
- `is not None` rather than a truthy check for security-relevant values

### How the review gate is started

Nothing starts it for you. Running the agents is a step in the pre-commit
workflow, and `make check` verifies that every mandated agent is committed and
loadable, because an agent that never loaded reviews nothing and says nothing.
See [Review Agent Preflight](../CLAUDE.md#review-agent-preflight).

**"Loadable" is not "unaltered", and nothing here checks the difference.** A
`.claude/agents/security-review.md` edited to approve everything passes the
check exactly like the real one. Reviewing the `.claude/agents/` diff by hand is
the only control on that, so treat a change to those files like a change to CI
configuration: they direct the agents reviewing this repository, they run with
the reviewer's tool permissions, and an edit changes what the review does,
including the review of the branch making the edit. The
`<!-- shared-review-guidance:start -->` / `:end -->` markers every agent carries
delimit the region the out-of-repo vendoring keeps in step across repositories,
including the read-only rule the next section rests on. Nothing here reads them.

`SessionStart` hooks are deliberately kept out of the tracked
`.claude/settings.json`, because tracked settings ship to every clone and hooks
run without a prompt. The
[documented opt-in](../CLAUDE.md#review-agent-preflight) puts it in the
gitignored `.claude/settings.local.json` instead, **which makes the execution
risk yours rather than absent.** `$CLAUDE_PROJECT_DIR` is the checkout, so the
hook runs the working tree's copy of the script, including on a branch someone
else wrote. Do not enable it in a checkout used to review other people's
branches. That path is not unique to the hook: the agents' prompts tell them to
run the project's quality-check command, so reviewing a contributed branch runs
that branch's test code as you either way. The hook is the door you can decline.

**`permissions.allow` is ambient authority of the same shape, governed the same
way.** An entry is pre-approved for everyone who checks out the branch carrying
it, with no prompt at the moment it is used. The tracked list is bounded on
**execution** alone: nothing granted there runs a command of its own choosing.
Anything with an `--exec`, `--extcmd` or pager escape belongs in the gitignored
local settings, where it is one person's choice for one machine. `git grep` is
the worked example, useful for review and excluded, because
`--open-files-in-pager` runs its value through a shell.

Grants match by prefix, so some entries are **denied** rather than merely left
out. `git difftool` shares `git diff`'s prefix and takes `--extcmd=<command>`,
which runs anything, and whether a prefix grant really reaches a longer
subcommand depends on matcher behaviour this repository cannot observe. Deny
beats allow, so naming it closes the escape if the grant reaches it and costs
nothing if it does not. The `git diff-*` plumbing family is denied alongside it,
since no reviewer invokes plumbing and the family keeps growing. `git status`
and `git log` have no sibling sharing their prefix. **A deny is the one kind of
rule a project may reasonably ship to every clone**, because it only narrows
what is permitted, and removing it is visible in the pin.

That execution bound is a claim about a name, not a binary. Git ignores aliases
that shadow existing commands, so the reachable surface is a name that *extends*
a granted prefix: an `alias.diffmine`, or a `git-diffmine` anywhere on `PATH`,
is dispatched by `git diffmine`, may be a shell command, and matches the granted
`git diff`. That surface is machine-local and unenumerable, so no deny list
reaches it, and no contributed branch controls it either, since neither
`.git/config` nor `PATH` is part of a checkout.

**Reads and writes are not bounded at all, and the grant was kept knowing
that.** `git diff -h` lists `--no-index [--] <path> <path>` in its synopsis, so
`git diff --no-index` prints any two files on the machine by absolute path,
unprompted, from outside any repository. Reading an outside path through the
ordinary file tool prompts, so the grant turns a prompted read of anything into
an unprompted one, `config/config.yaml` and private keys included. The write
primitive is documented but unverified here: `git-diff(1)` describes
`--output=<file>`, which creates or truncates an arbitrary path, and `git log`
takes the same diff options. Nobody has run it, so treat the write as likely
rather than established. **No prefix rule can close either**, because both are
flags and a flag may sit anywhere in the arguments. A deny on
`git diff --no-index` misses `git diff --stat --no-index a b`, and a rule that
can be stepped around is worse than none, because it reads as closed. The grant
stays because dropping it means dozens of prompts in a single review round, and
someone clearing forty prompts is not reading the forty-first. **So the grant is
unguarded here.** What asks an agent not to use it that way is the agents' own
prose, and the two primitives are covered by different sentences: the read-only
rule answers the write, while the read is answered only by a clause in the
search section telling an agent not to route around the file tool with
`--no-index`. Both are maintained upstream, in the repository the six shared
agents are vendored from. Nothing in this checkout asserts a committed agent
still carries either, so a copy arriving without them reads as ordinary drift
and the whole suite stays green.

`enabledPlugins` is code by definition and is pinned for the same reason.
`tests/test_review_agents.py` pins both keys exactly, so widening either costs a
test update and cannot happen silently. That is not a defence, since a branch
can edit the pin in the same commit. It is the same bet as reviewing the
`.claude/agents/` diff by hand.

### For contributors

security-review is one of the six shared agents committed under
`.claude/agents/`, and reads this file and CLAUDE.md for the rules above. It
runs in parallel with **code-review**, **test-review**, **document-review**,
**accessibility-review** and this repository's own **parity-review**.
**commit-hygiene** runs afterwards, once those six have approved, to plan the
commit split. All seven must approve before anything is committed. Each finding
carries severity, CWE classification, evidence, impact and remediation.

## Reporting security issues

Do not open a public GitHub issue. Contact the maintainer privately, and allow
reasonable time for a fix before disclosure.
