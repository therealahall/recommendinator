# Quick Start Guide

Get up and running with Recommendinator in under 5 minutes.

## Prerequisites

- **Python 3.11** installed (see [docs/PYTHON_VERSION.md](docs/PYTHON_VERSION.md) for details)
- Your data (Goodreads export, Steam account, etc.)

That's it. No AI, no external services required.

## Installation

### Option 1: Local Installation

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

# Set up configuration
cp config/example.yaml config/config.yaml
```

### Option 2: Docker

```bash
# Without AI (default)
docker compose up

# With AI (Ollama sidecar)
docker compose --profile ai up app-ai
```

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

**Available plugins:** Goodreads (books), Steam (games), GOG (games), Epic Games (games), Sonarr (TV shows), Radarr (movies), and generic CSV/JSON/Markdown importers for any content type.

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

If you enabled `auto_enrich_on_sync`, enrichment runs automatically after each sync. Otherwise, run it manually:

```bash
python3.11 -m src.cli enrichment start
python3.11 -m src.cli enrichment status
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

## Use the Web Interface

```bash
python3.11 -m src.web
```

Open http://localhost:18473 in your browser. The web UI provides browsing, syncing, recommendations, and (with AI enabled) a conversational chat interface. The version number in the sidebar (e.g., "v0.3.0") shows the running application version. If a new version becomes available while you have the page open, a banner will prompt you to reload.

## Customize Your Preferences

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
