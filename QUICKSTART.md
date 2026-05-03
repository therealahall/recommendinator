# Quick Start Guide

Get up and running with Recommendinator in under 5 minutes.

## Prerequisites

- **Docker** (recommended) — or Python 3.11 if you'd rather run from source
- Your data (Goodreads export, Steam account, etc.)

That's it. No AI, no external services required.

## Installation

### Option 1: Docker (recommended)

No git clone needed. Pull a published image and mount your data directories:

```bash
mkdir -p recommendinator/{config,data,inputs} && cd recommendinator

docker run -d \
  --name recommendinator \
  -p 18473:8000 \
  -v "$(pwd)/config:/app/config" \
  -v "$(pwd)/data:/app/data" \
  -v "$(pwd)/inputs:/app/inputs:ro" \
  --restart unless-stopped \
  ghcr.io/therealahall/recommendinator:latest
```

The container generates a starter `config/config.yaml` from the bundled example
on first run — edit it on the host with your API keys and run `docker restart recommendinator`.

For AI features (Ollama sidecar with auto model download), use Docker Compose:

```bash
curl -L https://github.com/therealahall/recommendinator/releases/latest/download/docker-compose.yml \
  -o docker-compose.yml
docker compose --profile ai up -d app-ai
```

Naming `app-ai` is required — the default `app` service has no profile and
would otherwise start alongside the AI variant, colliding on the same host port.

See [docs/DOCKER.md](docs/DOCKER.md) for parameters, GPU setup, reverse proxy
notes, and troubleshooting.

### Option 2: Local Installation (for contributors)

If you're contributing to Recommendinator or prefer running from source:

```bash
# Clone the repository
git clone https://github.com/ahall/recommendinator.git
cd recommendinator

# Install uv (if not already installed)
curl -LsSf https://astral.sh/uv/install.sh | sh

# Install dependencies (base only, no AI)
uv sync --locked

# Or install with AI features (ollama, chromadb)
uv sync --locked --extra ai

# Install and build the frontend (Node.js 18+ required for web UI)
corepack enable    # enables pnpm via Node.js corepack
pnpm install --frozen-lockfile
pnpm build

# Set up configuration
cp config/example.yaml config/config.yaml
```

> **Note:** The web UI requires Node.js 18+ to build. If you only use the CLI, Node.js is not required.

Access the web interface at http://localhost:18473.

## Set Up Enrichment (Do This First)

**Before importing any data**, set up metadata enrichment. This is the most important step for getting useful recommendations. Enrichment fills in missing genres, tags, and descriptions from external databases — without it, the scoring pipeline has little to work with and recommendations will be poor.

All three providers are **free**:

1. **TMDB** (movies/TV) — Get API key from [themoviedb.org/settings/api](https://www.themoviedb.org/settings/api)
2. **RAWG** (games) — Get API key from [rawg.io/apidocs](https://rawg.io/apidocs)
3. **OpenLibrary** (books) — No API key needed

Add this to your `config/config.yaml`:

```yaml
enrichment:
  enabled: true
  auto_enrich_on_sync: true   # Automatically enrich after every data sync

  providers:
    tmdb:
      api_key: "your-tmdb-key"
      enabled: true
    openlibrary:
      enabled: true
    rawg:
      api_key: "your-rawg-key"
      enabled: true
```

With `auto_enrich_on_sync: true`, enrichment runs automatically every time you sync data — no extra step needed. See [docs/ENRICHMENT_SETUP.md](docs/ENRICHMENT_SETUP.md) for the full setup guide.

## Import Your Data

The system supports multiple data sources through a plugin architecture. See `config/example.yaml` for the full list of available plugins and their configuration options.

**Available plugins:** Goodreads (books), Steam (games), GOG (games), Epic Games (games), Sonarr (TV shows), Radarr (movies), ROM Library (games), and generic CSV/JSON/Markdown importers for any content type.

### Configure a source

Each source is configured under `inputs:` in `config/config.yaml`. Enable the ones you want and fill in any required fields (API keys, file paths, etc.):

```yaml
inputs:
  goodreads:
    plugin: goodreads
    path: "inputs/goodreads_library_export.csv"
    enabled: true

  steam:
    plugin: steam
    api_key: "your-steam-api-key"
    steam_id: "your-steam-id"
    enabled: true
```

Some sources (GOG, Epic Games) require OAuth setup — see the [README.md](README.md) for step-by-step instructions.

### Generic CSV/JSON/Markdown

For sources without a dedicated plugin, use the generic importers. Templates for each content type are in the `templates/` directory:

```bash
# Copy a template and fill in your data
cp templates/movies.csv inputs/my_movies.csv
```

Then configure the source in your config:

```yaml
inputs:
  my_movies:
    plugin: csv_import
    path: "inputs/my_movies.csv"
    content_type: "movie"
    enabled: true
```

You can have multiple instances of the same plugin (e.g., two `json_import` sources for different files) — just give each a unique name.

### Sync your data

```bash
# Sync a specific source
python3.11 -m src.cli update --source goodreads

# Sync all enabled sources
python3.11 -m src.cli update --source all

# List configured sources
python3.11 -m src.cli update --source list
```

### Edit a source's configuration

Once you've started up the app you can manage each source from the web
**Data** tab (each source is an accordion that expands to reveal settings) or
from the CLI:

```bash
# Move a YAML source into the database (one-time, idempotent)
python3.11 -m src.cli source migrate goodreads

# Inspect / edit fields after migration
python3.11 -m src.cli source show goodreads
python3.11 -m src.cli source set goodreads path inputs/new_export.csv
python3.11 -m src.cli source disable goodreads
python3.11 -m src.cli source set-secret steam api_key   # hidden prompt
```

All `source` subcommands except `set-secret` and `clear-secret` accept
`--format json` for scripting parity with the web API:

```bash
python3.11 -m src.cli source show goodreads --format json
python3.11 -m src.cli source migrate goodreads --format json
```

For non-interactive secret rotation (Docker entrypoints, CI), set
`RECOMMENDINATOR_SECRET_VALUE` instead of typing at the prompt:

```bash
RECOMMENDINATOR_SECRET_VALUE="$STEAM_API_KEY" \
  python3.11 -m src.cli source set-secret steam api_key
```

The env-var path keeps the secret out of shell history and the visible
process list (unlike a `--value` flag would).

If you enabled `auto_enrich_on_sync`, enrichment runs automatically after each sync. Otherwise, run it manually:

```bash
python3.11 -m src.cli enrichment start
python3.11 -m src.cli enrichment status

# Retry items that providers couldn't find previously
python3.11 -m src.cli enrichment start --retry-not-found
```

## Get Recommendations

```bash
# Books
python3.11 -m src.cli recommend --type book --count 5

# Movies
python3.11 -m src.cli recommend --type movie --count 5

# Video games
python3.11 -m src.cli recommend --type video_game --count 5

# TV shows
python3.11 -m src.cli recommend --type tv_show --count 5
```

## Check System Status

```bash
# See component health, database stats, and feature flags
python3.11 -m src.cli status
```

## Browse & Edit Your Library

```bash
# List your completed books, sorted by rating
python3.11 -m src.cli library list --type book --status completed --sort rating

# Show full details for a single item
python3.11 -m src.cli library show --id 42

# Edit an item's rating or status
python3.11 -m src.cli library edit --id 42 --rating 5 --status completed

# Exclude an item from recommendations (or reverse it)
python3.11 -m src.cli library ignore --id 42
python3.11 -m src.cli library unignore --id 42
```

## Authenticate Game Sources (GOG/Epic)

```bash
# Connect your GOG account via browser OAuth
python3.11 -m src.cli auth connect --source gog

# Check connection status
python3.11 -m src.cli auth status
```

## Chat with Your Library (requires AI)

```bash
# Start an interactive chat session
python3.11 -m src.cli chat start

# Or send a single question
python3.11 -m src.cli chat send --message "What should I read next?"
```

## Use the Web Interface

```bash
python3.11 -m src.web
```

Open http://localhost:18473 in your browser. The web UI provides browsing, syncing, recommendations, and (with AI enabled) a conversational chat interface. The version number in the sidebar (e.g., "v0.3.0") shows the running application version. If a new version becomes available while you have the page open, a banner will prompt you to reload.

## Customize Your Preferences

### View current preferences

```bash
# Show current weights, length preferences, and custom rules
python3.11 -m src.cli preferences get
```

### Set scoring weights

```bash
# Emphasize genre matching
python3.11 -m src.cli preferences set-weight genre_match 3.0

# De-emphasize creator matching
python3.11 -m src.cli preferences set-weight creator_match 0.5
```

### Add custom rules

```bash
python3.11 -m src.cli preferences custom-rules add "avoid horror"
python3.11 -m src.cli preferences custom-rules add "prefer science fiction"
python3.11 -m src.cli preferences custom-rules add "only short books"
```

### Set length preferences

```bash
python3.11 -m src.cli preferences set-length book short
python3.11 -m src.cli preferences set-length movie any
python3.11 -m src.cli preferences set-length video_game long
```

## Optional: Enable AI Features

If you want semantic similarity and LLM-powered explanations:

1. Install Ollama: https://ollama.ai
2. Pull a model: `ollama pull mistral:7b`
3. Pull an embedding model: `ollama pull nomic-embed-text`
4. Enable in `config/config.yaml`:
   ```yaml
   features:
     ai_enabled: true
     embeddings_enabled: true
     llm_reasoning_enabled: true
   ```

See [docs/MODEL_RECOMMENDATIONS.md](docs/MODEL_RECOMMENDATIONS.md) for model guidance.

## Next Steps

- [docs/ENRICHMENT_SETUP.md](docs/ENRICHMENT_SETUP.md) — Metadata enrichment setup (do this first)
- [README.md](README.md) — Full feature overview, source-specific setup guides
- [docs/CONVERSATION_GUIDE.md](docs/CONVERSATION_GUIDE.md) — Chat interface setup (AI only)
- [ARCHITECTURE.md](ARCHITECTURE.md) — How the system works
- [docs/PLUGIN_DEVELOPMENT.md](docs/PLUGIN_DEVELOPMENT.md) — Creating custom data source plugins
- [docs/CUSTOM_RULES.md](docs/CUSTOM_RULES.md) — Advanced preference rules
- [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) — Common issues
