# CLI Reference

The CLI provides full access to every Recommendinator feature — it is an
alternative interface to the same capabilities as the web UI, not a subset.
Commands are organized into groups.

All commands are run as `python3.11 -m src.cli <command>`. Most read-only
commands accept `--format json` for scripting.

## Sync & recommend

```bash
# Sync data from your configured (syncable) sources
python3.11 -m src.cli update --source my_steam
python3.11 -m src.cli update --source all
python3.11 -m src.cli update --source list   # show configured sources

# Get recommendations
python3.11 -m src.cli recommend --type book --count 10
python3.11 -m src.cli recommend --type video_game --count 5

# Mark content as completed
python3.11 -m src.cli complete --type book --title "Project Hail Mary" --rating 5
```

`update` drives the syncable sources (Steam, GOG, Epic, Sonarr, Radarr, Trakt,
Calibre-Web, Goodreads RSS, ROM Library). The five one-shot file imports
(`goodreads_csv`, `storygraph_csv`, `csv_import`, `json_import`,
`markdown_import`) are not sources at all and never appear here. They use the
`import` command below.

## Importing from a file

The `import` command runs a single file through a file-import plugin. It is the
CLI peer of the web **Data** tab's **Import from file** button (`POST /api/import`).
It reads a real path off disk, runs the file through the ingestion pipeline,
streams progress, prints the item counts, and exits non-zero on failure. The
import is one-shot: nothing is stored about the file and nothing re-syncs, so to
refresh, export again and import again.

The CLI has no size cap, because it reads a local file you already trust. The web
upload is capped at 50 MB and rejects anything larger with HTTP 413, and it also
bounds how many imports may run at once (HTTP 429 past the bound). Both are
properties of the upload endpoint, not of the importer.

```bash
# Import a Goodreads or StoryGraph CSV export (neither takes extra options)
python3.11 -m src.cli import --source goodreads_csv --file inputs/goodreads_library_export.csv
python3.11 -m src.cli import --source storygraph_csv --file inputs/storygraph_export.csv

# Import a generic CSV/JSON/Markdown file (content type is required for these)
python3.11 -m src.cli import --source csv_import --file my_movies.csv --content-type movie
python3.11 -m src.cli import --source json_import --file my_books.json --content-type book
python3.11 -m src.cli import --source markdown_import --file my_shows.md --content-type tv_show

# List importable sources, the file types they read, and the options they accept
python3.11 -m src.cli import --source list
python3.11 -m src.cli import --source list --format json   # matches GET /api/import/sources
```

The listing carries each importer's `accepted_extensions` (`.csv`,
`.json`/`.jsonl`, `.md`/`.markdown`) — the same field the web upload form builds
its file picker from. It is advisory: the parser decides whether a file is
usable, so a renamed export is refused by the importer, not by its extension.

Flags:

- `--source` — the file-import plugin name (`goodreads_csv`, `storygraph_csv`,
  `csv_import`, `json_import`, `markdown_import`), or `list` to show importable
  sources.
- `--file` — path to the file to import (required unless `--source list`).
- `--content-type` — `book`, `movie`, `tv_show`, or `video_game`; required by the
  three generic formats, not used by the Goodreads and StoryGraph exports (they
  are always books). Case-insensitive, and so is the same value supplied as
  `--option content_type=BOOK` or as a multipart field to `POST /api/import` —
  all three routes accept exactly the same set.
- `--option KEY=VALUE` — an extra import option, repeatable, for plugin options
  beyond `content-type`. Run `--source list` to see what a plugin accepts. It
  writes the same keys the dedicated flags do and runs after them, so
  `--option content_type=movie` overrides an earlier `--content-type book`.

Both option flags are checked against the source's own schema by the import
service itself, so `POST /api/import` refuses exactly the same keys with a 400.
A key the source does not declare (including `--content-type` on a source that is
always books) aborts the command and names the options it does accept, rather
than being silently dropped.
- `--format table|json` — output format (default `table`) for both the source
  listing (`--source list`) and the final import result. In `json`, an import
  emits `{message, source, items_synced, total_items, errors, warning}`, the same
  field set `POST /api/import` returns.

A file that parses but yields no items is still a success, and it comes back with
a `warning` rather than an error: the table output prints a `Warning:` line and
the JSON output carries the same text in `warning`. When rows fail instead, each
failure lands in `errors` and `warning` is `null`.

## System status

```bash
# Check system health, component readiness, and feature flags
python3.11 -m src.cli status
python3.11 -m src.cli status --format json
```

## Library management

```bash
# List items with filtering and sorting
python3.11 -m src.cli library list --type book --status completed --sort rating --limit 20
python3.11 -m src.cli library list --format json

# Filter by enrichment state (enriched or not_enriched)
python3.11 -m src.cli library list --enrichment not_enriched

# Search by title or creator (matches web API search). Fuzzy and typo-tolerant;
# combines with the type/status filters.
python3.11 -m src.cli library list --search "die hard"

# Surface completed items you haven't rated yet (forces completed status,
# overriding --status; composes with --type). Rate them with `library edit`.
python3.11 -m src.cli library list --needs-rating
python3.11 -m src.cli library list --needs-rating --type movie

# Show item details
python3.11 -m src.cli library show --id 42

# Edit item metadata
python3.11 -m src.cli library edit --id 42 --rating 5 --status completed

# Mark watched TV seasons (comma-separated season numbers, each 1-200)
python3.11 -m src.cli library edit --id 42 --seasons-watched 1,2,3

# Set manual enrichment metadata (repeated --genre/--tag replace the existing
# lists; any provided field marks the item enriched)
python3.11 -m src.cli library edit --id 42 --genre Action --genre RPG --tag co-op --description "A grand adventure."

# Ignore/unignore items (excluded from recommendations)
python3.11 -m src.cli library ignore --id 42
python3.11 -m src.cli library unignore --id 42

# Export library data
python3.11 -m src.cli library export --type book --format csv --output books.csv
python3.11 -m src.cli library export --type video_game --format json
```

`library list --enrichment` filters by enrichment state. An item is "enriched"
only when a provider matched it cleanly (a real provider, no error, not "not
found", and not pending re-enrichment); `not_enriched` is everything else. The
list table shows an **Enriched** column.

Passing any of `--genre`, `--tag`, or `--description` to `library edit` writes
manual metadata. Repeated `--genre`/`--tag` replace the existing lists (they do
not append), and `--description` replaces the existing text. Providing any of
these marks the item enriched via the `manual` provider, so it drops out of the
`not_enriched` set and is never re-queued for automatic enrichment.

## Source management

Add, edit, enable/disable, and remove data sources without editing YAML. Sources
can live in YAML (bootstrap), in the database (created or migrated), or both.

The `source` group manages syncable sources only. The five file imports
(`goodreads_csv`, `storygraph_csv`, `csv_import`, `json_import`,
`markdown_import`) have no stored config, so `source plugins` does not list them
and `source create` rejects them with a pointer to the `import` command above.
`source list` is the one exception: it still shows a *leftover* entry naming one
of them, so you can find the id and remove it (see below).

```bash
# Create a brand-new source directly in the database (no YAML edit needed)
python3.11 -m src.cli source plugins             # see what plugins are available
python3.11 -m src.cli source create my_shelves goodreads_rss
python3.11 -m src.cli source set my_shelves user_id 12345678

# Move an existing YAML source into the database (one-time, idempotent)
python3.11 -m src.cli source migrate my_steam

# Inspect / edit fields after migration or creation
python3.11 -m src.cli source show my_steam
python3.11 -m src.cli source schema my_steam           # list editable fields
python3.11 -m src.cli source set my_steam vanity_url my-steam-name
python3.11 -m src.cli source disable my_steam          # disabled sources are skipped during sync
python3.11 -m src.cli source enable my_steam

# Remove a DB-backed source entirely (clears stored secrets too)
python3.11 -m src.cli source remove my_shelves
```

`source remove` is also how you clear a leftover row for one of the file-import
plugins. Such a row is skipped at sync time with a warning in the log, and every
other `source` subcommand reports it as unknown — there is nothing to configure
on a one-shot import. It stays in the database until you remove it, and
`source list` keeps listing it (with `is_file_import: true`, or `file import` in
the Enabled column) so you can find the id. A leftover that only exists in
`config.yaml` has no database row: `source remove` reports it as not migrated,
and you delete its block under `inputs:` instead.

All `source` subcommands except `set-secret` and `clear-secret` accept
`--format json` for scripting parity with the web API. For atomic multi-field
updates (the CLI equivalent of `PUT /api/sync/sources/<id>/config`), use
`source apply` with a JSON dict from a file or stdin:

```bash
echo '{"steam_id": "76561198000000000", "min_playtime_minutes": 30}' \
  | python3.11 -m src.cli source apply my_steam --from-json -
```

For non-interactive secret rotation (Docker entrypoints, CI), set
`RECOMMENDINATOR_SECRET_VALUE` instead of typing at the prompt — this keeps the
secret out of shell history and the visible process list:

```bash
RECOMMENDINATOR_SECRET_VALUE="$STEAM_API_KEY" \
  python3.11 -m src.cli source set-secret my_steam api_key
```

## Global settings

Manage the global/system config (the sections that used to live in
`config.yaml`: `features`, `ollama`, `recommendations`, `conversation`, `sync`,
`enrichment`, `web`, `logging`). `web.host`, `web.port` and `web.debug` are the
exception — the server reads them to bind its socket before the database is
open, so they stay in `config.yaml`. These commands mirror
the web **Settings** page and the `/api/settings` endpoints; both call the same
`src/settings/service.py`, so behaviour is identical. Values persist to the
`settings` table, which wins over `config.yaml` and the built-in defaults
(precedence: **const default < YAML < database**).

```bash
# List every setting grouped by section (secrets show presence only).
# Advanced infra/security settings (CORS origins, logging) are hidden
# unless --advanced is given or a specific --section is requested.
python3.11 -m src.cli settings list
python3.11 -m src.cli settings list --advanced
python3.11 -m src.cli settings list --section recommendations
python3.11 -m src.cli settings list --format json   # full view, matches GET /api/settings

# Show one setting's metadata and current value (dotted registry key)
python3.11 -m src.cli settings get recommendations.default_count
python3.11 -m src.cli settings get logging.level --format json

# Set a non-sensitive setting. The value is parsed to the setting's type:
# booleans accept true/false, lists are comma-separated, numbers/strings/enums
# are parsed as written. Validation (bounds, choices) is enforced.
python3.11 -m src.cli settings set recommendations.default_count 8
python3.11 -m src.cli settings set features.ai_enabled true
python3.11 -m src.cli settings set web.allowed_origins "http://localhost:18473"

# Reset a setting to its default by dropping the database override
python3.11 -m src.cli settings reset recommendations.default_count
```

`set`, `apply`, and `reset` also accept `--format json`, which emits the full
refreshed settings view instead of the one-line confirmation — the same body
`PUT /api/settings` and `DELETE /api/settings/{key}` return, so a script gets
the resulting state from the call that changed it rather than a follow-up
`settings list`.

Non-restart settings take effect immediately; restart-required settings
(`features.*`, `web.*`, `logging.*`) persist and apply on the next boot — the
CLI tells you when a change needs a restart.

For atomic multi-key updates (the CLI equivalent of `PUT /api/settings`), use
`settings apply` with a JSON object of `{"<dotted.key>": <value>}` from a file
or stdin. Every key is validated up front through a single service call, so one
bad key rejects the whole batch and nothing is written (all-or-nothing) — the
offending key and reason are printed and the command exits non-zero. Sensitive
keys are rejected here too; store them with `settings set-secret`.

```bash
echo '{"recommendations.default_count": 8, "recommendations.max_count": 30}' \
  | python3.11 -m src.cli settings apply --from-json -
```

### Secrets

Sensitive settings (provider API keys like `enrichment.providers.tmdb.api_key`)
are stored encrypted in the `credentials` table, never in plaintext. `settings
set` refuses them; use the write-only secret commands instead:

```bash
# Store or rotate a secret. Prompts with hidden input, or reads
# RECOMMENDINATOR_SECRET_VALUE for non-interactive use (Docker entrypoints, CI)
# — this keeps the value out of shell history and the visible process list.
python3.11 -m src.cli settings set-secret enrichment.providers.tmdb.api_key
RECOMMENDINATOR_SECRET_VALUE="$TMDB_KEY" \
  python3.11 -m src.cli settings set-secret enrichment.providers.tmdb.api_key

# Delete a stored secret
python3.11 -m src.cli settings clear-secret enrichment.providers.tmdb.api_key
```

`settings list`/`get` never print a secret's value — they show only whether one
is set.

## Preferences

```bash
# View current preferences (table or JSON)
python3.11 -m src.cli preferences get
python3.11 -m src.cli preferences get --format json

# Adjust scorer weights and content length preferences
python3.11 -m src.cli preferences set-weight genre_match 3.0
python3.11 -m src.cli preferences set-length book short

# Toggle boolean preferences (series_in_order)
python3.11 -m src.cli preferences set-toggle series_in_order off

# Set the variety-after-completion penalty (0.0-5.0; 0.0 = off, 5.0 = full strength)
python3.11 -m src.cli preferences set-variety 4.0

# Reset all preferences to defaults
python3.11 -m src.cli preferences reset

# Custom rules (natural-language preferences interpreted by the LLM)
python3.11 -m src.cli preferences custom-rules add "avoid horror"
python3.11 -m src.cli preferences custom-rules list
python3.11 -m src.cli preferences custom-rules interpret "avoid horror" --use-llm
python3.11 -m src.cli preferences custom-rules remove 0
python3.11 -m src.cli preferences custom-rules clear --yes
```

See [SCORING.md](SCORING.md) for what each weight does and [CUSTOM_RULES.md](CUSTOM_RULES.md)
for custom rule syntax.

## Enrichment

```bash
# Run enrichment (all items or filtered by content type)
python3.11 -m src.cli enrichment start
python3.11 -m src.cli enrichment start --type movie

# Re-process items previously marked as not_found (providers can drift over time)
python3.11 -m src.cli enrichment start --retry-not-found
python3.11 -m src.cli enrichment start --type movie --retry-not-found

# Check enrichment statistics
python3.11 -m src.cli enrichment status

# Reset enrichment status so items are re-processed on the next run
python3.11 -m src.cli enrichment reset
```

See [ENRICHMENT_SETUP.md](ENRICHMENT_SETUP.md) — enrichment is critical for
recommendation quality.

## Authentication (GOG/Epic/Trakt)

```bash
# Check OAuth connection status
python3.11 -m src.cli auth status

# Connect via browser OAuth flow
python3.11 -m src.cli auth connect --source gog
python3.11 -m src.cli auth connect --source epic

# Connect via the Trakt device-code flow (prints a verification URL + code)
python3.11 -m src.cli auth connect --source trakt

# Disconnect stored credentials
python3.11 -m src.cli auth disconnect --source gog
python3.11 -m src.cli auth disconnect --source trakt
```

## Conversation & memories (requires AI)

```bash
# Interactive chat session
python3.11 -m src.cli chat start
python3.11 -m src.cli chat start --type book    # filter to books only

# Single-shot message
python3.11 -m src.cli chat send --message "What should I read next?"

# Conversation history
python3.11 -m src.cli chat history --limit 20
python3.11 -m src.cli chat reset

# Manage memories (persistent preference signals)
python3.11 -m src.cli memory list
python3.11 -m src.cli memory add --text "I love hard sci-fi"
python3.11 -m src.cli memory edit --id 3 --text "I love hard sci-fi and space opera"
python3.11 -m src.cli memory edit --id 3 --active            # explicitly set active
python3.11 -m src.cli memory edit --id 3 --inactive          # explicitly set inactive
python3.11 -m src.cli memory edit --id 3 --text "..." --inactive  # text + state in one call
python3.11 -m src.cli memory toggle --id 3                   # flip active/inactive
python3.11 -m src.cli memory delete --id 3
```

See [CONVERSATION_GUIDE.md](CONVERSATION_GUIDE.md) for the chat interface.

## User profile

```bash
# View your computed preference profile
python3.11 -m src.cli profile show
python3.11 -m src.cli profile show --format json

# Regenerate profile from current library data
python3.11 -m src.cli profile regenerate
```
