# CLI Reference

The CLI provides full access to every Recommendinator feature — it is an
alternative interface to the same capabilities as the web UI, not a subset.
Commands are organized into groups.

All commands are run as `python3.11 -m src.cli <command>`. Most read-only
commands accept `--format json` for scripting.

## Sync & recommend

```bash
# Sync data from configured (syncable) sources
python3.11 -m src.cli update --source steam
python3.11 -m src.cli update --source all
python3.11 -m src.cli update --source list   # show configured sources

# Get recommendations
python3.11 -m src.cli recommend --type book --count 10
python3.11 -m src.cli recommend --type video_game --count 5

# Mark content as completed
python3.11 -m src.cli complete --type book --title "Project Hail Mary" --rating 5
```

`update` drives the syncable sources (Steam, GOG, Epic, Sonarr, Radarr, ROM
Library). One-shot file imports (Goodreads, CSV, JSON, Markdown) use the separate
`import` command below.

## Importing from a file

The `import` command runs a single file through a file-import plugin — the CLI
peer of the web **Data** tab's **Import from file** button (`POST /api/import`).
It reads a real path off disk, runs the file through the ingestion pipeline,
streams progress, prints the item counts, and exits non-zero on failure.

```bash
# Import a Goodreads CSV export (Goodreads takes no extra options)
python3.11 -m src.cli import --source goodreads --file inputs/goodreads_library_export.csv

# Import a generic CSV/JSON/Markdown file (content type is required for these)
python3.11 -m src.cli import --source csv_import --file my_movies.csv --content-type movie
python3.11 -m src.cli import --source json_import --file my_books.json --content-type book
python3.11 -m src.cli import --source markdown_import --file my_shows.md --content-type tv_show

# List importable sources and the options each one accepts
python3.11 -m src.cli import --source list
python3.11 -m src.cli import --source list --format json   # matches GET /api/import/sources
```

Flags:

- `--source` — the file-import plugin name (`goodreads`, `csv_import`,
  `json_import`, `markdown_import`), or `list` to show importable sources.
- `--file` — path to the file to import (required unless `--source list`).
- `--content-type` — `book`, `movie`, `tv_show`, or `video_game`; required by the
  three generic formats, ignored by Goodreads.
- `--option KEY=VALUE` — additional import option, repeatable (for plugin options
  beyond `content-type`).
- `--format table|json` — output format for `--source list`.

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

The `source` group manages syncable sources only — file imports (Goodreads, CSV,
JSON, Markdown) have no persistent config and are handled by `import` above.

```bash
# Create a brand-new source directly in the database (no YAML edit needed)
python3.11 -m src.cli source plugins             # see what plugins are available
python3.11 -m src.cli source create my_steam steam
python3.11 -m src.cli source set-secret my_steam api_key   # add credentials

# Move an existing YAML source into the database (one-time, idempotent)
python3.11 -m src.cli source migrate steam

# Inspect / edit fields after migration or creation
python3.11 -m src.cli source show steam
python3.11 -m src.cli source schema steam               # list editable fields
python3.11 -m src.cli source set steam vanity_url my-handle
python3.11 -m src.cli source disable steam              # disabled sources are skipped during sync
python3.11 -m src.cli source enable steam

# Remove a DB-backed source entirely (clears stored secrets too)
python3.11 -m src.cli source remove my_steam
```

All `source` subcommands except `set-secret` and `clear-secret` accept
`--format json` for scripting parity with the web API. For atomic multi-field
updates (the CLI equivalent of `PUT /api/sync/sources/<id>/config`), use
`source apply` with a JSON dict from a file or stdin:

```bash
echo '{"url": "http://localhost:8989", "content_type": "tv_show"}' \
  | python3.11 -m src.cli source apply my_sonarr --from-json -
```

For non-interactive secret rotation (Docker entrypoints, CI), set
`RECOMMENDINATOR_SECRET_VALUE` instead of typing at the prompt — this keeps the
secret out of shell history and the visible process list:

```bash
RECOMMENDINATOR_SECRET_VALUE="$STEAM_API_KEY" \
  python3.11 -m src.cli source set-secret steam api_key
```

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

## Authentication (GOG/Epic)

```bash
# Check OAuth connection status
python3.11 -m src.cli auth status

# Connect via browser OAuth flow
python3.11 -m src.cli auth connect --source gog
python3.11 -m src.cli auth connect --source epic

# Disconnect stored credentials
python3.11 -m src.cli auth disconnect --source gog
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
