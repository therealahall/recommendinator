# Security Considerations

## Data privacy

Everything is local: SQLite for structured data, no telemetry. Nothing leaves
your machine except calls to the external APIs you configure.

| File | Contains |
|------|----------|
| `config/config.yaml` | Bootstrap secrets migrated to the database on startup |
| `data/recommendations.db` | Consumption history, encrypted credentials |
| `data/.credential_key` | Fernet key for those credentials |
| `data/chroma_db/` | Vector embeddings of your content, left by a pre-AI-removal release |

**Never commit these files to version control.**

## Credential encryption

OAuth tokens and API keys are encrypted with Fernet and stored in the
`credentials` table. **Nothing else is encrypted.** Titles, ratings, reviews and
completion history sit in the database as plaintext.

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
- **Once a source has a database row it is the only authority for that
  source's secrets**, and a value left in `config.yaml` is discarded on every
  startup rather than read.
- If the encryption key changes, stale credentials for a source still defined
  in `config.yaml` alone are re-encrypted from it, or purged when there is no
  config fallback.
- A GOG, Epic Games or Trakt refresh token is persisted under its source's id —
  both the one the connect flow obtains and the one a sync rotates — so a source
  reads back what it was connected with and removing it takes the token too.
- **Every OAuth route now takes a `source_id` query parameter**, defaulted to
  the plugin's own name so an older client still addresses the source it used
  to, and validated against the source-id pattern before anything reads it.
  `auth connect` and `auth disconnect` take `--source-id` to match.
- A connect route refuses an id whose plugin is not its own. The id is the
  credential key, so an unchecked one files a GOG token where Trakt reads its
  own.
- Revoking asks the weaker question deliberately: only a source running another
  plugin puts an id out of reach. An id no source claims is nobody's to protect,
  and refusing it would leave the credential with no verb left to delete it.
- Running a plugin and being enabled are separate questions, and each verb gates
  on the one it needs: connecting requires an enabled source, disconnecting does
  not. A credential you cannot delete is worse than one you cannot use.
- A status read's `connected` reports the stored credential row, not the
  resolved config, so it never offers a control that answers 404. A token left
  in `config.yaml` reads as connected, because startup moved it into that row
  before any command or request ran. The exception is a source that already has
  a database row: its file-held token is discarded rather than read, so it
  reads as not connected and there is nothing for disconnect to delete.
- Trakt's `enabled` also means its client credentials resolve. Clearing the
  client secret leaves `connected` true and the token revocable.
- An upgrade does not move tokens an earlier release stored under the plugin's
  name: several sources can share a plugin, and nothing records which owns the
  token. Reconnect the source to store one where it reads it.
- **No endpoint returns a credential value.** They are write-only from the API.
- The test suite never touches the real key. An autouse fixture in the
  repository-root `conftest.py` points `RECOMMENDINATOR_KEY_PATH` at a per-test
  temporary directory.

Copy `data/.credential_key` when you move the database to another host. Without
it, stored credentials cannot be decrypted and have to be re-entered.

**Removing a source deletes every credential row stored under its id**, plugin
installed or not, field still marked sensitive or not. Removal deletes no
library items. Removing the last source on a plugin — through the API or
`source remove` — also clears anything stranded under that plugin's own name,
since no source can read it any more. Another source on the same plugin, in
`config.yaml` or the database and enabled or not, keeps it.

**Pointing a `credential_bound` field at a different host is refused** — `url`
on Sonarr, Radarr and Calibre-Web. Host and port decide, so the same endpoint
switching between `http` and `https` goes through untouched in either
direction: a downgrade to `http` is not refused either, and the credential then
crosses the network in cleartext.

To move a source, clear its secret (`source clear-secret` or the **Data** tab),
save the new URL, then enter the credential the new host expects. Creating a
source clears anything left under that id. A `url` whose port cannot be read is
refused outright, since nothing can say who it addresses.

A source `url` must be `http` or `https`, must name a host, and must not embed
`user:password@`. Source config is validated when it is written and again at
sync, and neither 400 carries the plugin's own message: a write names the field
it blames, or repeats a path-containment refusal verbatim, and a sync answers a
fixed string. The reason goes to the log instead.

**One carve-out: a plugin module that failed to import.** `GET
/api/sync/sources`, `GET /api/plugins` and the 400 from `POST /api/update`
carry the module name and the exception that lost it, because "No module named
'defusedxml'" is the answer the operator needs and every one of those routes
requires a session on a single-user instance.

## Web sign-in

**One account, username and password, and a session cookie.** A fresh instance
has none: the first visitor to the web UI names the account and sets its
password, and is signed in by that request. Change it later from **Settings →
Account**, or with `python3.11 -m src.cli account set-password` on the machine
holding the database — there is no email and no reset link.

- **The claim window stays open until someone uses it** — boot warns while it
  is, and the loopback default bounds who can reach it.
- **Nothing under `/api` is exempt** but the four `/api/auth` routes.
  `GET /api/status` stays gated: its feature report is a free fingerprint, and
  the container health check reads that 401 as healthy.
- **The SPA shell (`/`, `/static/*`) is not gated**: it draws the sign-in form,
  and carries the app's assets rather than your data.
- The cookie is `HttpOnly`, `SameSite=Strict` and good for 30 idle days, rolling
  forward on use. **No `Secure` flag** — this app serves no TLS, so a Secure
  cookie would never be sent at all. Beyond loopback, put a
  [reverse proxy](DOCKER.md#reverse-proxy) in front.
- **A password is at least 12 characters**, wherever it is set: the setup
  screen, **Settings → Account** and `account set-password`.
- Passwords are scrypt digests under a per-account salt, session tokens SHA-256
  digests. Sessions are revoked server-side, so signing out or changing the
  password really ends them.

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
| Steam, GOG, Epic Games | Game library sync | That source enabled |
| Sonarr, Radarr | Media library sync | Configured |
| TMDB, OpenLibrary, RAWG | Metadata enrichment | Enrichment enabled |

**`web.allowed_origins` cannot authenticate a cross-origin client.** The session
cookie is `SameSite=Strict`, so a browser never attaches it to a request from
another origin, whatever CORS allows. What the setting still reaches is the
ungated surface: `GET /` and `/static/*`, the SPA shell. It defaults to
`http://localhost:18473` and applies on restart.

The web interface binds `127.0.0.1` by default, and Docker publishes its port on
`127.0.0.1` too (`APP_BIND_PREFIX`). Reaching it from another machine means a
reverse proxy terminating TLS, or accepting that your password and session
cookie cross your network in cleartext — the app never serves TLS itself. Do not
expose it to the public internet without the proxy. Under Docker, services talk
over an internal network isolated from the host by default.

## Where a source may read

Source config is writable over the API, so a plugin whose config names a
filesystem path (`roms`, and private scanners) refuses one resolving outside
`security.allowed_source_roots`; `validate_config` and `fetch` both check. An
import is outside it, never touching disk.

```yaml
# config/config.yaml — defaults to ["inputs"] when absent.
security:
  allowed_source_roots:
    - "inputs"
    - "/srv/roms"
```

The list is **read from `config.yaml` and is not a settings-registry leaf**, so
`PUT /api/settings` cannot widen it and point a source at `/home`; adding a root
is a `config.yaml` edit the watcher picks up without a restart. Both sides are
resolved before comparison, so a symlink under an allowed root cannot escape it,
and an empty list allows nothing.

## Input handling

Imported CSV and JSON are parsed with standard libraries, and invalid rows are
skipped rather than executed. Custom rules are stored as typed and collapsed to
a single line before the interpreter reads them. See
[CUSTOM_RULES.md](CUSTOM_RULES.md). Neither path executes anything from user
data.

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
uv sync --locked
```

## Deployment checklist

- [ ] `config/config.yaml` is git-ignored and `0600`
- [ ] The web account is claimed, with a password only you know
- [ ] API keys are not in code
- [ ] `logs/` is treated as sensitive and not shipped anywhere
- [ ] Database file has restricted permissions
- [ ] Web interface on localhost (Docker's default), or behind a TLS proxy
- [ ] Docker containers run as a non-root user

**The logs are not guaranteed key-free.** The integrations that put a credential
in the request URL — Steam, TMDB, RAWG and GOG — render a request failure as its
status code or error class, and every OAuth connect flow logs only an error type
name or a status code. None of them attaches a traceback.

Each provider's own module-local test holds its request failures to
`scrub_request_error`, and the chain scan below covers the traceback half.

Rendering the message is only half of it: a traceback walks `__cause__`, so an
exception chained from a request error prints that request's URL. `auth connect`
logs with `exc_info=True`, so GOG's token refresh and code exchange, Steam's two
Web API calls and every TMDB and RAWG request raise `from None`.
`tests/test_credential_url_chains.py` holds every such caller to both halves — a
chain-free handler and an entry in its `_CREDENTIAL_URL_FUNCTIONS` list — and
enrols new ones by scanning `src/auth/`, `src/config/`,
`src/enrichment/providers/`, `src/ingestion/sources/`, `src/sources/`,
`src/utils/` and `src/web/` for a credential key beside a `params=` call.

That covers how a failure is rendered. The transports carry the same URLs.
`urllib3.connectionpool` logs each request target, query string included, at
DEBUG — and at WARNING on its retry path. The shared wiring holds `httpx`,
`httpcore` and `urllib3` at WARNING, which closes the DEBUG half alone; the
retry line never runs because `requests`' default adapter builds `Retry(0)`.
Mounting an adapter with retries would leak keys at any level.

A refused config write is redacted before logging, but the match is exact, so a
truncated or encoded form of the secret survives it.

## Automated security review

Changes are audited for the following before they are committed.

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
  forbidden), with the plugin-import carve-out above as the one exception
- Module-level imports only
- Copy dicts and lists before mutating data passed in from outside
- `is not None` rather than a truthy check for security-relevant values

### How the review gate is started

Nothing starts it for you. Running the agents is a step in the pre-commit
workflow, and an agent that never loaded reviews nothing — a run that cannot
launch one is a hard stop, not a skip.

**The one committed agent is a prompt with the reviewer's permissions.** Treat a
change to `.claude/agents/parity-review.md` like a change to CI configuration:
an edit changes what the review does, including the review of the branch making
the edit. Reviewing that diff by hand is the only control.

`SessionStart` hooks are deliberately kept out of the tracked
`.claude/settings.json`, because tracked settings ship to every clone and hooks
run without a prompt. The risk is not unique to hooks: the agents' prompts tell
them to run the project's quality-check command, so reviewing a contributed
branch runs that branch's test code as you either way.

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
`--no-index`. Both rules live in the review agents' own prompts, not in
anything this repository enforces.

`enabledPlugins` is code by definition and warrants the same care: widening
either settings key should be a deliberate act, visible in the diff, not one
quiet line in a settings file.

### For contributors

Security review is part of the pre-commit workflow in
[CONTRIBUTING.md](../CONTRIBUTING.md) and reads this file and CLAUDE.md for the
rules above. Each finding carries severity, CWE classification, evidence,
impact and remediation.

## Reporting security issues

Do not open a public GitHub issue. Contact the maintainer privately, and allow
reasonable time for a fix before disclosure.
