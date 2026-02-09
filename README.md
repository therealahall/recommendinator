# Personal Recommendations

A privacy-focused recommendation system that learns from your ratings and reviews across books, movies, TV shows, and video games. Runs entirely on your machine with **no AI required** — AI features are opt-in for users who want them.

## Why This Project?

Most recommendation systems are black boxes that harvest your data. This one:

- **Runs locally** — Your data never leaves your machine
- **Works without AI** — Smart scoring algorithms that don't need an LLM
- **AI is optional** — Enable Ollama integration when you want deeper insights
- **You own your data** — SQLite database you can query, backup, or delete

## Features

### Core Features (No AI Required)

- **Multi-source ingestion** — Import from Goodreads, Steam, Sonarr, Radarr, or generic CSV/JSON/Markdown files
- **Cross-content recommendations** — Your love of sci-fi books influences game and movie suggestions
- **Smart scoring pipeline** — Genre matching, creator preferences, series order, tag overlap, rating patterns
- **Custom rules** — Natural language preferences like "avoid horror" or "prefer short books"
- **Content length filtering** — Prefer short books, long games, any movie length
- **Multi-user support** — Each user gets their own preferences and history
- **Dual interface** — CLI for automation, web UI for browsing

### Optional AI Features (Opt-In)

When you enable AI with a local Ollama instance:

- **Semantic similarity** — Find content similar in meaning, not just tags
- **LLM reasoning** — Natural language explanations for recommendations
- **Smart rule interpretation** — LLM understands complex preference rules

## Quick Start

### Option 1: Local Installation

```bash
# Clone and install
git clone https://github.com/ahall/personal-recommendations.git
cd personal-recommendations
pip install -r requirements.txt

# Set up config
cp config/example.yaml config/config.yaml

# Import your data
python3.11 -m src.cli update --source goodreads

# Get recommendations
python3.11 -m src.cli recommend --type book --count 5

# Or start the web interface
python3.11 -m src.web
```

### Option 2: Docker

```bash
# Without AI (default)
docker compose up

# With AI (Ollama sidecar)
docker compose --profile ai up
```

Access the web interface at `http://localhost:18473`

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

See the `templates/` directory for import file examples.

### GOG Setup

GOG requires an OAuth refresh token for API access.

**Option 1: Web UI (Recommended)**

The easiest way to connect your GOG account:

1. Enable GOG in your config.yaml:
   ```yaml
   inputs:
     gog:
       enabled: true
   ```

2. Start the web server and go to the **Sync** tab
3. Follow the "Connect GOG Account" wizard - it handles the OAuth flow for you

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

4. **Use the Web UI** to paste the URL/code, or manually exchange it:
   ```
   https://auth.gog.com/token?client_id=46899977096215655&client_secret=9d85c43b1482497dbbce61f6e4aa173a433796eeae2571571f7c3a315a91b&grant_type=authorization_code&code=YOUR_CODE&redirect_uri=https%3A%2F%2Fembed.gog.com%2Fon_login_success%3Forigin%3Dclient
   ```

5. **Copy the `refresh_token`** from the JSON response and add it to your config:
   ```yaml
   inputs:
     gog:
       refresh_token: "your-refresh-token-here"
       include_wishlist: true
       enabled: true
   ```

**Note:** The refresh token is long-lived but may eventually expire. If GOG sync fails with an authentication error, reconnect via the web UI or repeat the manual steps.

### Epic Games Setup

Epic Games uses the [Legendary](https://github.com/derrod/legendary) launcher's authentication:

1. **Install Legendary:**
   ```bash
   pip install legendary-gl
   ```

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
inputs:
  goodreads:
    path: "inputs/goodreads_library_export.csv"
    enabled: true
  
  steam:
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
```

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
```

## How Scoring Works

The recommendation engine scores candidates through multiple factors:

| Scorer | What it does |
|--------|--------------|
| **Genre Match** | Boosts content matching genres you've rated highly |
| **Creator Match** | Prefers authors/directors/developers you've enjoyed |
| **Tag Overlap** | Jaccard similarity of tags and themes |
| **Series Order** | Prioritizes next items in series you're reading/watching |
| **Rating Pattern** | Learns from your rating history within genres |
| **Custom Rules** | Applies your explicit preferences ("avoid X", "prefer Y") |
| **Semantic Similarity** | *(AI only)* Finds conceptually similar content |

Each scorer has a configurable weight. Set a weight to 0 to disable a scorer entirely.

## Enabling AI Features

If you want AI-enhanced recommendations:

1. **Install Ollama**: https://ollama.ai
2. **Pull a model**: `ollama pull mistral:7b`
3. **Enable in config**:
   ```yaml
   features:
     ai_enabled: true
     embeddings_enabled: true      # For semantic similarity
     llm_reasoning_enabled: true   # For natural language explanations
   ```
4. **For Docker**: Use `docker compose --profile ai up`

See [docs/MODEL_RECOMMENDATIONS.md](docs/MODEL_RECOMMENDATIONS.md) for model selection guidance.

## Project Structure

```
personal-recommendations/
├── src/
│   ├── cli/              # Command-line interface
│   ├── web/              # FastAPI web interface
│   ├── ingestion/        # Data source parsers
│   ├── recommendations/  # Scoring pipeline and engine
│   ├── storage/          # SQLite + optional ChromaDB
│   ├── llm/              # Ollama integration (optional)
│   └── models/           # Data models
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
| [CONTRIBUTING.md](CONTRIBUTING.md) | Development guidelines |
| [docs/CUSTOM_RULES.md](docs/CUSTOM_RULES.md) | Custom preference rules |
| [docs/PLUGIN_DEVELOPMENT.md](docs/PLUGIN_DEVELOPMENT.md) | Adding new data sources |
| [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) | Common issues and solutions |
| [docs/MODEL_RECOMMENDATIONS.md](docs/MODEL_RECOMMENDATIONS.md) | Ollama model selection |
| [docs/SECURITY.md](docs/SECURITY.md) | Security considerations |

## Requirements

- Python 3.11+
- SQLite (included with Python)
- Ollama (optional, for AI features)

## License

[PolyForm Noncommercial 1.0.0](LICENSE) — Free for personal and noncommercial use. See LICENSE for details.
