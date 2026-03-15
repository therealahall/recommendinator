# Recommendinator

A privacy-focused recommendation engine that learns from your ratings and reviews across books, movies, TV shows, and video games. Runs entirely on your machine with **no AI required** — AI features are opt-in for users who want them.

## Why This Project?

Most recommendation systems are black boxes that harvest your data. This one:

- **Runs locally** — Your data never leaves your machine
- **Works without AI** — Smart scoring algorithms that don't need an LLM
- **AI is optional** — Enable Ollama integration when you want deeper insights
- **You own your data** — SQLite database you can query, backup, or delete

## Features

### Core Features (No AI Required)

- **Multi-source ingestion** — Import from Goodreads, Steam, GOG, Epic Games, Sonarr, Radarr, or generic CSV/JSON/Markdown files
- **Cross-content recommendations** — Your love of sci-fi books influences game and movie suggestions via semantic genre clusters that bridge different vocabularies
- **Smart scoring pipeline** — Genre matching, creator preferences, series order, cluster-aware tag overlap, rating patterns
- **Custom rules** — Natural language preferences like "avoid horror" or "prefer short books"
- **Content length filtering** — Prefer short books, long games, any movie length
- **Multi-user support** — Each user gets their own preferences and history
- **Metadata enrichment** — Automatically fills in missing metadata from TMDB, OpenLibrary, and RAWG. **This is critical for recommendation quality** — see [Enrichment Setup Guide](docs/ENRICHMENT_SETUP.md)
- **Themeable web UI** — Ships with Nord and Snowstorm themes, or create your own
- **Dual interface** — CLI for automation, web UI for browsing

### Optional AI Features (Opt-In)

When you enable AI with a local Ollama instance:

- **Conversational chat** — Ask questions about your library, get recommendations through natural conversation with memory and user profiling
- **Semantic similarity** — Find content similar in meaning, not just tags
- **LLM reasoning** — Natural language explanations for recommendations
- **Smart rule interpretation** — LLM understands complex preference rules

## Security Notice

This is a **personal, single-user tool** designed to run on your own machine. It has **no authentication or authorization** on any endpoint. By default it binds to `127.0.0.1` (localhost only).

If you change the host to `0.0.0.0` to allow LAN access, **anyone on your network can view and modify your data**. Do not expose this application to the public internet.

## Quick Start

### Option 1: Docker (Recommended)

```bash
# Without AI (default) — runs the app only
docker compose up

# With AI — starts the app + Ollama + model auto-pull
docker compose --profile ai up app-ai
```

The `--profile ai` flag adds an Ollama sidecar container that automatically pulls the configured models on first start. Set `features.ai_enabled: true` in your config to use AI features.

### Option 2: Local Installation

```bash
# Clone and install
git clone https://github.com/ahall/recommendinator.git
cd recommendinator
curl -LsSf https://astral.sh/uv/install.sh | sh  # install uv if needed
uv sync --locked --extra ai

# Set up config
cp config/example.yaml config/config.yaml

# Import your data
python3.11 -m src.cli update --source goodreads

# Get recommendations
python3.11 -m src.cli recommend --type book --count 5

# Or start the web interface
python3.11 -m src.web
```

Access the web interface at `http://localhost:18473`

**Important:** [Set up metadata enrichment](docs/ENRICHMENT_SETUP.md) **before importing your data** and enable `auto_enrich_on_sync: true` so items are enriched automatically on every sync. Enrichment is disabled by default but is essential for recommendation quality — without it, many items will lack the genres, tags, and descriptions that the scoring pipeline depends on.

## Data Sources

| Source | Type | Description |
|--------|------|-------------|
| **Goodreads** | Books | CSV export from your Goodreads library |
| **Steam** | Games | Automatic import via Steam Web API |
| **GOG** | Games | Import from your GOG.com library and wishlist |
| **Epic Games** | Games | Import from your Epic Games library |
| **Sonarr** | TV Shows | Import from your Sonarr library |
| **Radarr** | Movies | Import from your Radarr library |
| **CSV** | Any | Generic CSV with customizable mapping |
| **JSON** | Any | Generic JSON/JSONL import |
| **Markdown** | Any | Human-readable markdown lists |

See the `templates/` directory for import file examples. Templates support the `ignored` field for excluding items from recommendations, and TV show templates use a `seasons_watched` list (e.g., `1,2,5,6` in CSV or `[1,2,5,6]` in JSON) to track specific seasons watched.

### Library Export

Export your library data from the web UI:
1. Go to the **Library** tab
2. Select a content type from the type filter
3. Choose a format (CSV or JSON)
4. Click **Export** to download

Exported files match the import template format, so you can edit them (e.g., mark items as `ignored`, update `seasons_watched`) and re-import via CSV or JSON sync.

### GOG Setup

GOG requires an OAuth refresh token for API access. The token is stored in an encrypted credential database — not in config.yaml.

**Option 1: Web UI (Recommended)**

The easiest way to connect your GOG account:

1. Enable GOG in your config.yaml:
   ```yaml
   inputs:
     gog:
       plugin: gog
       enabled: true
   ```

2. Start the web server and go to the **Data** tab
3. Follow the "Connect GOG Account" wizard — it handles the OAuth flow and stores the token securely

**Option 2: Manual Setup**

If you prefer to set up manually:

1. **Open the GOG auth URL in your browser:**
   ```
   https://auth.gog.com/auth?client_id=46899977096215655&redirect_uri=https%3A%2F%2Fembed.gog.com%2Fon_login_success%3Forigin%3Dclient&response_type=code&layout=client2
   ```

2. **Log in with your GOG account** when prompted.

3. **After login, you'll be redirected** to a URL like:
   ```
   https://embed.gog.com/on_login_success?origin=client&code=LONG_CODE_HERE
   ```
   **Copy the entire URL** (or just the code after `code=`).

4. **Paste the URL/code in the Web UI** to complete the connection. The token is encrypted and stored in the database automatically.

**Note:** The refresh token is long-lived but may eventually expire. If GOG sync fails with an authentication error, reconnect via the web UI.

**Credential storage:** All sensitive credentials (API keys, OAuth tokens) are encrypted at rest using Fernet symmetric encryption. The encryption key is stored at `data/.credential_key` by default, or at the path specified by the `RECOMMENDINATOR_KEY_PATH` environment variable. If you move the database to a new host, copy the key file too.

### Sonarr / Radarr Setup

Sonarr (TV shows) and Radarr (movies) import your media library directly from their APIs.

1. **Find your API key** in the Sonarr/Radarr web UI: **Settings > General > Security > API Key**
2. **Add to your config:**
   ```yaml
   inputs:
     sonarr:
       plugin: sonarr
       url: "http://localhost:8989"    # Your Sonarr URL
       api_key: "your-sonarr-api-key"
       content_type: "tv_show"
       enabled: true

     radarr:
       plugin: radarr
       url: "http://localhost:7878"    # Your Radarr URL
       api_key: "your-radarr-api-key"
       content_type: "movie"
       enabled: true
   ```

Radarr also imports movie collection data (e.g., trilogies, franchises), which enables series-aware recommendations across your movie library.

### Epic Games Setup

Epic Games uses the [Legendary](https://github.com/derrod/legendary) launcher's authentication:

1. **Legendary is included** in the base dependencies (`uv sync` installs it automatically).

2. **Authenticate via browser:**
   ```bash
   legendary auth
   ```

3. **Extract the refresh token** from Legendary's config:
   ```bash
   cat ~/.config/legendary/user.json | grep refresh_token
   ```

4. **Add to your config:**
   ```yaml
   inputs:
     epic_games:
       plugin: epic_games
       refresh_token: "your-refresh-token-here"
       enabled: true
   ```

## Configuration

Copy `config/example.yaml` to `config/config.yaml` and customize:

```yaml
# AI is disabled by default
features:
  ai_enabled: false
  embeddings_enabled: false
  llm_reasoning_enabled: false

# Configure your data sources
# Each entry has a user-defined name and a 'plugin' field specifying the plugin type.
# Multiple instances of the same plugin are supported (e.g., two json_import sources).
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

# Tune the scoring weights
recommendations:
  scorer_weights:
    genre_match: 2.0
    creator_match: 1.5
    series_order: 1.5
    tag_overlap: 1.0
    rating_pattern: 1.0
    content_length: 1.0
    continuation: 2.0
    series_affinity: 1.0
    custom_preference: 1.0
    semantic_similarity: 1.0  # AI only

# Per-user diversity bonus (set in user preferences, not global config)
# diversity_weight: 0.2  # 0.0 = disabled, higher = more genre variety

# Conflict resolution when items are imported from multiple sources
ingestion:
  conflict_strategy: "last_write_wins"  # or "source_priority" or "keep_existing"
  source_priority: ["goodreads", "steam"]  # highest priority first (source_priority only)
```

**Conflict strategies:** When the same item is imported from multiple sources (e.g., a game from both Steam and GOG), the `conflict_strategy` setting controls which data wins. `last_write_wins` (default) uses the most recent import. `source_priority` uses data from the highest-priority source. `keep_existing` never overwrites — only fills in missing fields. Metadata (genres, tags) is always merged additively regardless of strategy.

## CLI Usage

```bash
# Import data
python3.11 -m src.cli update --source goodreads
python3.11 -m src.cli update --source steam
python3.11 -m src.cli update --source all

# Get recommendations
python3.11 -m src.cli recommend --type book --count 10
python3.11 -m src.cli recommend --type video_game --count 5

# Mark content as completed
python3.11 -m src.cli complete --type book --title "Project Hail Mary" --rating 5

# Manage preferences
python3.11 -m src.cli preferences get
python3.11 -m src.cli preferences set-weight genre_match 3.0
python3.11 -m src.cli preferences set-length book short
python3.11 -m src.cli preferences custom-rules add "avoid horror"
python3.11 -m src.cli preferences custom-rules add "prefer sci-fi"

# Enrich metadata from external APIs (see docs/ENRICHMENT_SETUP.md)
python3.11 -m src.cli enrichment start
python3.11 -m src.cli enrichment start --type movie    # specific content type
python3.11 -m src.cli enrichment status
```

## How Scoring Works

The recommendation engine scores candidates through multiple factors:

| Scorer | What it does |
|--------|--------------|
| **Genre Match** | Boosts content matching genres you've rated highly |
| **Creator Match** | Prefers authors/directors/developers you've enjoyed |
| **Tag Overlap** | Threshold-based tag matching with semantic cluster bridging |
| **Series Order** | Prioritizes next items in series you're reading/watching/playing |
| **Continuation** | Boosts items you're actively consuming (e.g., in-progress TV show). Automatically removed from the pipeline when you have no in-progress items, so it never produces noise. |
| **Series Affinity** | Boosts items in franchises you've rated well |
| **Rating Pattern** | Learns from your rating history within genres |
| **Content Length** | Soft penalty for items that don't match your preferred length |
| **Custom Rules** | Applies your explicit preferences ("avoid X", "prefer Y") |
| **Semantic Similarity** | *(AI only)* Finds conceptually similar content |

Each scorer has a configurable weight. Set a weight to 0 to disable a scorer entirely.

### Series Filtering

When the **"Recommend series in order"** preference is enabled (the default), the engine enforces series ordering. If Book 3 in a series would otherwise be recommended but you haven't consumed Books 1 and 2, the engine automatically substitutes the earliest available entry. This works with numbered titles, Roman numerals, season indicators, and metadata-based series info from enrichment.

### Content Length Preferences

Set length preferences per content type (`short`, `medium`, `long`, or `any`) via the CLI or web UI. Items that don't match your preference still appear but rank lower — it's a soft penalty, not a hard filter.

| Content Type | Short | Medium | Long |
|---|---|---|---|
| Book | < 250 pages | 250–500 pages | 500+ pages |
| Movie | < 90 minutes | 90–150 minutes | 150+ minutes |
| TV Show | < 3 seasons | 3–6 seasons | 6+ seasons |
| Video Game | < 10 hours | 10–40 hours | 40+ hours |

Items without length metadata (common before enrichment) receive a small benefit-of-the-doubt score rather than being penalized.

### Diversity Bonus

The diversity bonus encourages genre variety in recommendations. When enabled, items with genres different from your recently completed content get a score boost (calculated via Jaccard distance on genre sets). Configure it per-user:

- **0.0** (default) — Disabled
- **0.1–0.3** — Subtle variety
- **0.5+** — Strong genre-hopping

You can also enable the **"Variety after completion"** preference, which applies a default diversity weight of 0.2 automatically.

### Ignored Items

Items can be marked as `ignored` to permanently exclude them from recommendations. Set `ignored: true` when importing via CSV or JSON templates, or use the **Ignore** button in the web UI's Library page. Ignored items remain in your library but are filtered out before recommendations are generated.

## Enabling AI Features

If you want AI-enhanced recommendations:

### Docker Users

Use `docker compose --profile ai up app-ai` — Ollama and models are set up automatically.

### Local Installation Users

1. **Install Ollama**: https://ollama.ai
2. **Pull a model**: `ollama pull mistral:7b`
3. **Enable in config**:
   ```yaml
   features:
     ai_enabled: true
     embeddings_enabled: true      # For semantic similarity
     llm_reasoning_enabled: true   # For natural language explanations
   ```

See [docs/MODEL_RECOMMENDATIONS.md](docs/MODEL_RECOMMENDATIONS.md) for model selection guidance.

## Project Structure

```
recommendinator/
├── src/
│   ├── cli/              # Command-line interface
│   ├── web/              # FastAPI web interface (themes in static/themes/)
│   ├── ingestion/        # Data source parsers (plugins in sources/)
│   ├── recommendations/  # Scoring pipeline and engine
│   ├── conversation/     # Conversational AI chat system (optional)
│   ├── enrichment/       # Background metadata enrichment (TMDB, OpenLibrary, RAWG)
│   ├── storage/          # SQLite + optional ChromaDB
│   ├── llm/              # Ollama integration (optional)
│   ├── models/           # Data models
│   └── utils/            # Utility functions
├── tests/                # Test suite
├── config/               # Configuration files
├── templates/            # Import file templates
├── inputs/               # Your data files
├── data/                 # Database and cache (gitignored)
└── docs/                 # Additional documentation
```

## Documentation

| Document | Description |
|----------|-------------|
| [QUICKSTART.md](QUICKSTART.md) | Getting started guide |
| [ARCHITECTURE.md](ARCHITECTURE.md) | System design and components |
| [CONTRIBUTING.md](CONTRIBUTING.md) | Contributing guidelines |
| [docs/ENRICHMENT_SETUP.md](docs/ENRICHMENT_SETUP.md) | Metadata enrichment setup (critical) |
| [docs/CONVERSATION_GUIDE.md](docs/CONVERSATION_GUIDE.md) | Chat interface and AI conversation |
| [docs/CUSTOM_RULES.md](docs/CUSTOM_RULES.md) | Custom preference rules |
| [docs/PLUGIN_DEVELOPMENT.md](docs/PLUGIN_DEVELOPMENT.md) | Adding new data sources |
| [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) | Common issues and solutions |
| [docs/MODEL_RECOMMENDATIONS.md](docs/MODEL_RECOMMENDATIONS.md) | Ollama model selection |
| [docs/CHROMADB_SETUP.md](docs/CHROMADB_SETUP.md) | ChromaDB setup (AI-only) |
| [docs/SECURITY.md](docs/SECURITY.md) | Security considerations |
| [docs/OLLAMA_SETUP_GUIDE.md](docs/OLLAMA_SETUP_GUIDE.md) | Ollama installation and setup |
| [docs/PYTHON_VERSION.md](docs/PYTHON_VERSION.md) | Python version requirements |
| [docs/THEME_DEVELOPMENT.md](docs/THEME_DEVELOPMENT.md) | Creating custom web UI themes |

## Requirements

- Python 3.11 (recommended; see [docs/PYTHON_VERSION.md](docs/PYTHON_VERSION.md))
- SQLite (included with Python)
- Ollama (optional, for AI features)

## License

[PolyForm Noncommercial 1.0.0](LICENSE) — Free for personal and noncommercial use. See LICENSE for details.
