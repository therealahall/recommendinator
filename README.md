# Recommendinator

A privacy-focused recommendation engine that learns from your ratings and reviews
across books, movies, TV shows, and video games. Runs entirely on your machine
with **no AI required** — AI features are opt-in for users who want them.

- **Runs locally** — your data never leaves your machine
- **Works without AI** — smart scoring algorithms that don't need an LLM
- **AI is optional** — enable Ollama integration when you want deeper insights
- **You own your data** — a SQLite database you can query, back up, or delete

It imports from sources you already use (Goodreads, Steam, GOG, Epic, Sonarr,
Radarr, Trakt, ROM libraries, or plain CSV/JSON/Markdown), enriches items with metadata,
and ranks recommendations through a transparent scoring pipeline. Your love of
sci-fi books can influence game and movie suggestions via semantic genre clusters.
Browse and tune everything from a themeable web UI or the CLI — they are
[interchangeable interfaces](ARCHITECTURE.md#7-interface-layer) to the same engine.

## 30-second start

Pull the published Docker image, mount your config and data, run:

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

The container writes a starter `config/config.yaml` on first run. Edit it with
your API keys, run `docker restart recommendinator`, then open
**http://localhost:18473**.

Then, in order:

1. **[Set up enrichment](docs/ENRICHMENT_SETUP.md) first** — it fills in the
   genres, tags, and descriptions the scoring pipeline depends on. Skipping it
   produces poor recommendations.
2. **Connect a data source** — pick yours from the table below.
3. **Get recommendations** — in the web UI, or `python3.11 -m src.cli recommend --type book --count 5`.

> Running from source instead of Docker? See the [Quick Start guide](QUICKSTART.md).
> For AI features, GPU support, reverse proxies, and the full deployment
> reference, see [docs/DOCKER.md](docs/DOCKER.md).

## Security notice

This is a **personal, single-user tool** designed to run on your own machine. It
has **no authentication or authorization** on any endpoint. By default it binds
to `127.0.0.1` (localhost only).

If you change the host to `0.0.0.0` to allow LAN access, **anyone on your network
can view and modify your data**. Do not expose this application to the public
internet. See [docs/SECURITY.md](docs/SECURITY.md).

## Data sources

Each source has its own setup guide. Pick the ones you use — you can ignore the
rest.

| Source | Type | Setup |
|--------|------|-------|
| **Goodreads (CSV export)** | Books | [goodreads_csv](src/ingestion/sources/goodreads_csv/README.md) |
| **Goodreads (public shelves via RSS)** | Books | [goodreads_rss](src/ingestion/sources/goodreads_rss/README.md) |
| **The StoryGraph** | Books | [storygraph_csv](src/ingestion/sources/storygraph_csv/README.md) |
| **Steam** | Games | [steam](src/ingestion/sources/steam/README.md) |
| **GOG** | Games | [gog](src/ingestion/sources/gog/README.md) |
| **Epic Games** | Games | [epic_games](src/ingestion/sources/epic_games/README.md) |
| **Sonarr** | TV Shows | [sonarr](src/ingestion/sources/sonarr/README.md) |
| **Radarr** | Movies | [radarr](src/ingestion/sources/radarr/README.md) |
| **Trakt** | TV Shows / Movies | [trakt](src/ingestion/sources/trakt/README.md) |
| **ROM Library** | Games | [roms](src/ingestion/sources/roms/README.md) |
| **CSV / JSON / Markdown** | Any | [generic_csv](src/ingestion/sources/generic_csv/README.md) · [generic_json](src/ingestion/sources/generic_json/README.md) · [markdown](src/ingestion/sources/markdown/README.md) |

For adding/editing/removing sources in the UI, parallel sync, and library export,
see **[docs/DATA_SOURCES.md](docs/DATA_SOURCES.md)**.

## Features

**Core (no AI required)**

- Multi-source ingestion with cross-content recommendations via semantic genre clusters
- Transparent scoring pipeline — genre, creator, series order, tag overlap, rating patterns ([how it works](docs/SCORING.md))
- Natural-language [custom rules](docs/CUSTOM_RULES.md) like "avoid horror" or "prefer short books"
- Content-length filtering, multi-user support, [metadata enrichment](docs/ENRICHMENT_SETUP.md) (TMDB/OpenLibrary/RAWG) — automatic, plus manual editing of genres, tags, and descriptions and a library filter by enrichment state
- Searchable library — fuzzy, typo-tolerant matching on title and creator (author/director/creators/developer), in both the web UI and CLI
- Themeable web UI (ships with Nord and Snowstorm) with version display and update detection
- Dual interface — CLI for automation, web UI for browsing

**Optional AI (opt-in, local Ollama)**

- Conversational chat over your library with memory and user profiling
- Semantic similarity, LLM-reasoned explanations, smart rule interpretation

See [Enabling AI features](#enabling-ai-features) below.

## Configuration

Copy `config/example.yaml` to `config/config.yaml` and customize. The essentials:

```yaml
# AI is disabled by default
features:
  ai_enabled: false
  embeddings_enabled: false
  llm_reasoning_enabled: false

# Configure your data sources (see each source's setup guide for fields)
inputs:
  goodreads_csv:
    plugin: goodreads_csv
    path: "inputs/goodreads_library_export.csv"
    enabled: true

# Conflict resolution when an item is imported from multiple sources
ingestion:
  conflict_strategy: "last_write_wins"  # or "source_priority" or "keep_existing"
```

`config/example.yaml` documents every option (scorer weights, sync workers,
enrichment providers, conversation tuning). Scorer weights are explained in
[docs/SCORING.md](docs/SCORING.md).

**Conflict strategies:** when the same item is imported from multiple sources,
`conflict_strategy` controls which data wins. `last_write_wins` (default) uses
the most recent import; `source_priority` uses the highest-priority source;
`keep_existing` only fills missing fields. Metadata (genres, tags) is always
merged additively.

### Upgrading

The Goodreads CSV plugin was renamed from `goodreads` to `goodreads_csv`.
Existing Goodreads items and any DB-stored source configs are relabeled from
`goodreads` to `goodreads_csv` automatically on first startup, so no action is
needed there. If you configure Goodreads via `config.yaml`, rename
`plugin: goodreads` to `plugin: goodreads_csv`.

## CLI usage

The CLI is a full peer to the web UI. A taste:

```bash
python3.11 -m src.cli update --source all          # import everything
python3.11 -m src.cli recommend --type book --count 10
python3.11 -m src.cli library list --type book --status completed --sort rating
python3.11 -m src.cli library list --search "die hard"   # fuzzy title/creator search
python3.11 -m src.cli chat start                   # conversational mode (AI)
```

Full command reference: **[docs/CLI.md](docs/CLI.md)**.

## Enabling AI features

AI is entirely optional. To enable semantic similarity and LLM-powered
explanations:

- **Docker:** `docker compose --profile ai up -d app-ai` — Ollama and models are
  set up automatically.
- **Local:** install [Ollama](https://ollama.ai), `ollama pull mistral:7b`, then
  set `ai_enabled`, `embeddings_enabled`, and `llm_reasoning_enabled` to `true`
  in `config.yaml`.

See [docs/MODEL_RECOMMENDATIONS.md](docs/MODEL_RECOMMENDATIONS.md) for model
selection guidance.

## Documentation

| Document | Description |
|----------|-------------|
| [QUICKSTART.md](QUICKSTART.md) | Getting started guide (Docker and from-source) |
| [docs/DATA_SOURCES.md](docs/DATA_SOURCES.md) | Managing sources, parallel sync, export |
| [docs/CLI.md](docs/CLI.md) | Full CLI command reference |
| [docs/SCORING.md](docs/SCORING.md) | How the recommendation engine scores |
| [docs/ENRICHMENT_SETUP.md](docs/ENRICHMENT_SETUP.md) | Metadata enrichment setup (critical) |
| [ARCHITECTURE.md](ARCHITECTURE.md) | System design and components |
| [CONTRIBUTING.md](CONTRIBUTING.md) | Contributing guidelines |
| [docs/DOCKER.md](docs/DOCKER.md) | Docker deployment, AI mode, GPU, reverse proxy |
| [docs/CONVERSATION_GUIDE.md](docs/CONVERSATION_GUIDE.md) | Chat interface and AI conversation |
| [docs/CUSTOM_RULES.md](docs/CUSTOM_RULES.md) | Custom preference rules |
| [docs/PLUGIN_DEVELOPMENT.md](docs/PLUGIN_DEVELOPMENT.md) | Adding new data sources |
| [docs/THEME_DEVELOPMENT.md](docs/THEME_DEVELOPMENT.md) | Creating custom web UI themes |
| [docs/MODEL_RECOMMENDATIONS.md](docs/MODEL_RECOMMENDATIONS.md) | Ollama model selection |
| [docs/CHROMADB_SETUP.md](docs/CHROMADB_SETUP.md) | ChromaDB setup (AI-only) |
| [docs/OLLAMA_SETUP_GUIDE.md](docs/OLLAMA_SETUP_GUIDE.md) | Ollama installation and setup |
| [docs/SECURITY.md](docs/SECURITY.md) | Security considerations |
| [docs/PYTHON_VERSION.md](docs/PYTHON_VERSION.md) | Python version requirements |
| [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) | Common issues and solutions |

## Requirements

- Python 3.11 (recommended; see [docs/PYTHON_VERSION.md](docs/PYTHON_VERSION.md))
- SQLite (included with Python)
- Ollama (optional, for AI features)

## License

[PolyForm Noncommercial 1.0.0](LICENSE) — free for personal and noncommercial
use. See LICENSE for details.
